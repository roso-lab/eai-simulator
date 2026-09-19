"""Deterministic offline communication execution; no simulator integration.

``CommunicationRuntime(contract)`` owns monotonic simulation time. Publish at
the current time (or supply a later ``published_at_s``); call ``advance(time_s)``
or its alias ``tick`` before reading/consuming. Times are absolute seconds.

Contracts accept delay_s, drop, bytes_per_window (None = unlimited), window_s,
and rules. Rules match recipient/channel (omission = wildcard); later matching
rules override earlier fields. ``set_conditions`` changes the current policy
for future publications and records a condition-change event; it never exposes
an upcoming change to participants. Each recipient/channel has an independent FIFO
link. Windows are anchored at zero. Bytes are served immediately from that
window's remaining budget, with no carry-over; a large message spans windows.
Delay starts after its final byte. Conditions are frozen per published copy, so
an in-flight message is never silently rescheduled. Equal-time publications use call order;
recipient order is canonicalized. This is a window-budget model, not a physical
network throughput/serialization model. Advance granularity cannot change it.

Freshness is half-open: published_at_s <= now < valid_until_s. Expiry cancels
unsent bytes and in-flight delivery, retaining all already spent byte evidence.
An already stale observation can be published, but never delivered or consumed.
Consume is once per message/recipient by default. Participant observation
messages may declare ``estimate.consumer_ids``; each listed downstream task may
then consume the same delivered copy once. This component does not authenticate
caller identities.

``public_messages(recipient=...)`` returns only fresh, delivered, unconsumed
Factory-compatible payloads: message_id, producer_id, published_at_s,
valid_until_s, estimate. The latter two times are publisher metadata; hidden
conditions, queue state and scheduled arrival times never enter this view.
Feed these payloads to ParticipantSession.emit('observation_message', ...).
``publish_legacy`` accepts Factory's producer spelling without mutating its API;
Factory delivered_messages/decide_dispatch retain their existing semantics.
Only the evaluator should receive ``report()`` or publish/consume return values.

Report v2 binds an immutable ``initial_contract`` to the final ``contract`` via
the condition-change ledger. Replay always starts from the initial contract.
Validation proves internal consistency, not provenance: an evaluator can supply
``expected_initial_contract`` from its frozen episode configuration to check
that binding independently of the report. Legacy v1 reports remain replayable
when the initial policy is recoverable; global changes in v1 require that
external initial contract because the overwritten values were not recorded.
"""

from collections import deque
from copy import deepcopy
from fractions import Fraction
import heapq
import json
import math
from collections.abc import Mapping


def _time(value, name, *, positive=False):
    try:
        finite = math.isfinite(value)
    except (TypeError, OverflowError):
        finite = False
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not finite or value < 0 or (positive and value == 0)):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    # The external API and ledger use float seconds. Normalize integers once,
    # then use exact decimal arithmetic internally for window boundaries.
    return Fraction(str(float(value)))


def _name(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def validate_communication_contract(contract=None):
    """Return a detached normalized contract; reject unknown/misspelled fields."""
    if contract is None:
        contract = {}
    if not isinstance(contract, Mapping):
        raise ValueError("communication contract must be a mapping")
    fields = {"delay_s", "drop", "bytes_per_window", "window_s"}

    def validate(row, allowed):
        if not isinstance(row, Mapping) or set(row) - allowed:
            raise ValueError("unknown communication contract fields")
        result = deepcopy(dict(row))
        for key in ("delay_s", "window_s"):
            if key in result:
                _time(result[key], key, positive=key == "window_s")
        if "drop" in result and type(result["drop"]) is not bool:
            raise ValueError("drop must be a boolean")
        budget = result.get("bytes_per_window")
        if budget is not None and (type(budget) is not int or budget <= 0):
            raise ValueError("bytes_per_window must be a positive integer or None")
        for key in ("recipient", "channel"):
            if key in result:
                _name(result[key], key)
        return result

    normalized = validate(contract, fields | {"rules"})
    rules = normalized.pop("rules", [])
    if not isinstance(rules, (list, tuple)):
        raise ValueError("rules must be a sequence of mappings")
    return {"delay_s": 0., "drop": False, "bytes_per_window": None,
            "window_s": 1., **normalized,
            "rules": [validate(rule, fields | {"recipient", "channel"}) for rule in rules]}


def _json_copy(value):
    # Reject silent tuple/key coercion and NaN; byte accounting uses the exact
    # canonical UTF-8 JSON payload, not Python character counts or repr().
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("estimate JSON keys must be strings")
        return {key: _json_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError("estimate must contain only finite JSON values")


class CommunicationRuntime:
    """One episode's deterministic transport and evaluator-only causal ledger."""

    def __init__(self, contract=None):
        self._contract = validate_communication_contract(contract)
        self._initial_contract = deepcopy(self._contract)
        self._now = Fraction(0)
        self._messages = {}
        self._links = {}
        self._events = []
        self._scheduled_services = set()
        self._sequence = 0
        self._ledger = []

    @property
    def time_s(self):
        return float(self._now)

    def _record(self, event, time, **data):
        self._ledger.append({"sequence": len(self._ledger), "event": event,
                             "time_s": float(time), **deepcopy(data)})

    def _schedule(self, time, kind, key):
        # Expiry wins ties with service and arrival: TTL is half-open.
        priority = {"expire": 0, "service": 1, "deliver": 2}[kind]
        heapq.heappush(self._events, (time, priority, self._sequence, kind, key))
        self._sequence += 1

    def _conditions(self, recipient, channel):
        result = {k: v for k, v in self._contract.items() if k != "rules"}
        for rule in self._contract["rules"]:
            if (rule.get("recipient", recipient) == recipient
                    and rule.get("channel", channel) == channel):
                result.update({k: v for k, v in rule.items() if k not in {"recipient", "channel"}})
        return result

    def _request_service(self, link, time):
        key = (link, time)
        if key not in self._scheduled_services:
            self._scheduled_services.add(key)
            self._schedule(time, "service", link)

    def set_conditions(self, conditions, *, recipient=None, channel=None, time_s=None):
        """Apply a policy at the current time; it affects subsequent publishes.

        A bandwidth/window change is rejected while that selected link has
        queued bytes. The rejection is recorded before raising, so an evaluator
        can distinguish an attempted intervention from an ignored one. Existing
        queued/in-flight copies retain their publication-time conditions.
        """
        if recipient is None and channel is None:
            selector = {}
        else:
            selector = {}
            if recipient is not None:
                selector["recipient"] = _name(recipient, "recipient")
            if channel is not None:
                selector["channel"] = _name(channel, "channel")
        if not isinstance(conditions, Mapping) or set(conditions) - {
                "delay_s", "drop", "bytes_per_window", "window_s"}:
            raise ValueError("conditions must contain only communication policy fields")
        # Validate policy fields separately; selector names are validated above
        # and are not top-level contract fields.
        patch = validate_communication_contract(dict(conditions))
        # A patch is not a complete contract: defaults are irrelevant, while
        # selector fields belong to the rule rather than policy values.
        values = {key: patch[key] for key in ("delay_s", "drop", "bytes_per_window", "window_s")
                  if key in conditions}
        now = self._now if time_s is None else _time(time_s, "time_s")
        if now != self._now:
            if now < self._now:
                raise ValueError("condition-change time must equal current time")
            self.advance(float(now))
        affected = [key for key, link in self._links.items()
                    if (recipient is None or key[0] == recipient)
                    and (channel is None or key[1] == channel)
                    and link["queue"]]
        if any(key in values for key in ("bytes_per_window", "window_s")) and affected:
            self._record("channel_condition_change_rejected", self._now,
                         recipient=recipient, channel=channel, conditions=values,
                         reason="selected_fifo_queue_not_empty")
            raise ValueError("cannot change bandwidth/window while selected queue is not empty")
        if recipient is None and channel is None:
            self._contract.update(values)
        else:
            rule = dict(selector, **values)
            self._contract["rules"].append(rule)
        self._record("channel_condition_changed", self._now, recipient=recipient,
                     channel=channel, conditions=values, applies_to="future_publications")
        return deepcopy(values)

    def update_contract(self, conditions, *, recipient=None, channel=None, time_s=None):
        """Named alias for current-time condition intervention."""
        return self.set_conditions(conditions, recipient=recipient, channel=channel,
                                   time_s=time_s)

    def publish(self, *, message_id, producer_id, recipients, channel, estimate,
                valid_until_s, published_at_s=None):
        """Publish one global ID to distinct recipients; return evaluator record.

        Wire size includes the five public payload fields; each unicast copy
        spends its link's own budget. Caller-declared size cannot bypass this.
        Validation happens before advancing time or reserving the identifier.
        """
        _name(message_id, "message_id")
        if message_id in self._messages:
            raise ValueError(f"duplicate message_id {message_id!r}")
        _name(producer_id, "producer_id")
        _name(channel, "channel")
        if not isinstance(recipients, (list, tuple)) or not recipients:
            raise ValueError("recipients must be a nonempty sequence")
        recipients = [_name(item, "recipient") for item in recipients]
        if len(set(recipients)) != len(recipients):
            raise ValueError("recipients must be unique")
        published = self._now if published_at_s is None else _time(published_at_s, "published_at_s")
        expiry = _time(valid_until_s, "valid_until_s")
        if published < self._now:
            raise ValueError("publish time must be monotonic")
        if not isinstance(estimate, Mapping):
            raise ValueError("estimate must be a mapping")
        payload = {"message_id": message_id, "producer_id": producer_id,
                   "published_at_s": float(published), "valid_until_s": float(expiry),
                   "estimate": _json_copy(dict(estimate))}
        size = len(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.advance(float(published))
        message = {"payload": payload, "channel": channel, "size_bytes": size, "recipients": {}}
        self._messages[message_id] = message
        self._record("published", published, message_id=message_id, producer_id=producer_id,
                     channel=channel, recipients=sorted(recipients), size_bytes=size)
        for recipient in sorted(recipients):
            conditions = self._conditions(recipient, channel)
            delivery = {"status": "queued", "conditions": conditions, "bytes_sent": 0,
                        "transmitted_at_s": None, "scheduled_delivery_at_s": None,
                        "delivered_at_s": None, "consumption": None}
            message["recipients"][recipient] = delivery
            key = (message_id, recipient)
            if conditions["drop"]:
                delivery["status"] = "dropped"
                self._record("dropped", published, message_id=message_id, recipient=recipient)
            elif expiry <= published:
                delivery["status"] = "expired"
                self._record("expired", published, message_id=message_id, recipient=recipient,
                             bytes_sent=0)
            else:
                link_key = (recipient, channel)
                link = self._links.setdefault(link_key, {"queue": deque(), "window": None,
                                                       "used_bytes": 0, "conditions": conditions})
                link["queue"].append(key)
                self._record("queued", published, message_id=message_id, recipient=recipient)
                self._schedule(expiry, "expire", key)
                self._request_service(link_key, published)
        self.advance(float(published))
        return deepcopy(message)

    def publish_legacy(self, message, *, recipients, channel="observation"):
        """Adapt Factory view_messages output; existing Factory APIs are untouched."""
        if not isinstance(message, Mapping):
            raise ValueError("legacy message must be a mapping")
        return self.publish(message_id=message["message_id"], producer_id=message["producer"],
                            published_at_s=message["published_at_s"],
                            valid_until_s=message["valid_until_s"], estimate=message["estimate"],
                            recipients=recipients, channel=channel)

    def _service(self, link_key, time):
        self._scheduled_services.discard((link_key, time))
        link = self._links[link_key]
        # The head copy freezes its publication-time link conditions. The
        # persistent used_bytes counter prevents a new policy from resetting
        # the current window budget when a queue was empty.
        message_id, recipient = link["queue"][0]
        head_conditions = self._messages[message_id]["recipients"][recipient]["conditions"]
        width = _time(head_conditions["window_s"], "window_s", positive=True)
        window = time // width
        if link["window"] != window:
            link.update(window=window, used_bytes=0)
        budget = head_conditions["bytes_per_window"]
        while link["queue"]:
            message_id, recipient = link["queue"][0]
            message = self._messages[message_id]
            delivery = message["recipients"][recipient]
            if delivery["status"] != "queued":
                link["queue"].popleft()
                continue
            remaining = message["size_bytes"] - delivery["bytes_sent"]
            served = remaining if budget is None else min(remaining, budget - link["used_bytes"])
            if served:
                delivery["bytes_sent"] += served
                link["used_bytes"] += served
                self._record("bytes_served", time, message_id=message_id, recipient=recipient,
                             channel=link_key[1], window_index=int(window), bytes=served,
                             bytes_sent=delivery["bytes_sent"])
            if delivery["bytes_sent"] < message["size_bytes"]:
                self._request_service(link_key, (window + 1) * width)
                break
            link["queue"].popleft()
            arrival = time + _time(delivery["conditions"]["delay_s"], "delay_s")
            expiry = _time(message["payload"]["valid_until_s"], "valid_until_s")
            # Delivery beyond TTL can never happen. Do not convert/schedule
            # an unbounded sum (two finite floats can overflow when added).
            arrival_s = float(arrival) if arrival < expiry else None
            delivery.update(status="in_transit", transmitted_at_s=float(time),
                            scheduled_delivery_at_s=arrival_s)
            self._record("transmitted", time, message_id=message_id, recipient=recipient,
                         scheduled_delivery_at_s=arrival_s,
                         delivery_exceeds_validity=arrival >= expiry)
            if arrival < expiry:
                self._schedule(arrival, "deliver", (message_id, recipient))

    def advance(self, time_s):
        """Process all due events, including intermediate windows, and return time."""
        target = _time(time_s, "time_s")
        if target < self._now:
            raise ValueError("simulation time must be monotonic")
        while self._events and self._events[0][0] <= target:
            time, _, _, kind, key = heapq.heappop(self._events)
            self._now = time
            if kind == "service":
                self._service(key, time)
                continue
            message_id, recipient = key
            delivery = self._messages[message_id]["recipients"][recipient]
            if kind == "expire" and delivery["status"] not in {"dropped", "expired"}:
                delivery["status"] = "expired"
                self._record("expired", time, message_id=message_id, recipient=recipient,
                             bytes_sent=delivery["bytes_sent"])
            elif kind == "deliver" and delivery["status"] == "in_transit":
                delivery.update(status="delivered", delivered_at_s=float(time))
                self._record("delivered", time, message_id=message_id, recipient=recipient)
        self._now = target
        return self.time_s

    tick = advance

    def public_messages(self, *, recipient, channel=None):
        """Fresh inbox payloads only; repeat reads do not consume or advance time."""
        _name(recipient, "recipient")
        if channel is not None:
            _name(channel, "channel")
        result = []
        for message in self._messages.values():
            delivery = message["recipients"].get(recipient)
            if (delivery and delivery["status"] == "delivered" and delivery["consumption"] is None
                    and (channel is None or message["channel"] == channel)):
                result.append(deepcopy(message["payload"]))
        return result

    def consume(self, message_id, *, recipient, consumer_id, time_s=None):
        """Authorize a fresh delivery once per declared logical consumer.

        Generic messages preserve the original once-per-recipient behavior.
        Observation messages can list ``estimate.consumer_ids`` so one delivered
        sensor result can satisfy several dependent DAG nodes without duplicating
        bytes on the communication link.
        """
        _name(message_id, "message_id")
        _name(recipient, "recipient")
        _name(consumer_id, "consumer_id")
        if time_s is not None:
            self.advance(time_s)
        message = self._messages.get(message_id)
        delivery = message["recipients"].get(recipient) if message else None
        declared_consumers = (
            message["payload"].get("estimate", {}).get("consumer_ids")
            if message is not None else None
        )
        multi_consumer = isinstance(declared_consumers, list)
        previous_consumptions = []
        if delivery is not None and delivery["consumption"] is not None:
            previous_consumptions.append(delivery["consumption"])
            previous_consumptions.extend(delivery.get("additional_consumptions", ()))
        already_consumed = any(
            item.get("consumer_id") == consumer_id for item in previous_consumptions)
        reason = ("unknown_message" if message is None else
                  "not_a_recipient" if delivery is None else
                  delivery["status"] if delivery["status"] != "delivered" else
                  "consumer_not_declared" if multi_consumer and consumer_id not in declared_consumers else
                  "already_consumed" if already_consumed else
                  "already_consumed" if not multi_consumer and delivery["consumption"] is not None else
                  None)
        if reason:
            self._record("consume_rejected", self._now, message_id=message_id,
                         recipient=recipient, consumer_id=consumer_id, reason=reason)
            raise ValueError(f"message cannot be consumed: {reason}")
        event = {"message_id": message_id, "recipient": recipient,
                 "consumer_id": consumer_id, "time_s": self.time_s}
        if delivery["consumption"] is None:
            delivery["consumption"] = deepcopy(event)
        else:
            delivery.setdefault("additional_consumptions", []).append(deepcopy(event))
        self._record("consumed", self._now, **{k: v for k, v in event.items() if k != "time_s"})
        return deepcopy(event)

    def report(self):
        """Evaluator snapshot with initial and final policy plus causal evidence."""
        return {"schema": "eai.benchmark.communication", "version": 2,
                "visibility": "evaluator_only", "time_s": self.time_s,
                "initial_contract": deepcopy(self._initial_contract),
                "contract": deepcopy(self._contract), "messages": deepcopy(self._messages),
                "ledger": deepcopy(self._ledger), "isaac_integrated": False}


class ParticipantCommunicationBoundary:
    """Deliver runtime observations through the deterministic public channel.

    The participant sees only delivered payloads. Conditions, pending queues,
    drops, and future schedule entries remain evaluator-owned in ``report``.
    """

    def __init__(self, participant_session, *, contract=None,
                 recipient_id="participant"):
        if not hasattr(participant_session, "emit"):
            raise ValueError("participant communication requires an event session")
        self.participant = participant_session
        self.runtime = CommunicationRuntime(contract)
        self.recipient_id = _name(recipient_id, "recipient_id")
        self._emitted = set()

    def publish_observation(self, observation, *, consumer_ids=(),
                            channel="observation"):
        if not isinstance(observation, dict):
            raise ValueError("observation message source must be an object")
        consumers = list(consumer_ids)
        if (len(consumers) != len(set(consumers))
                or any(not isinstance(value, str) or not value for value in consumers)):
            raise ValueError("observation consumer_ids must be unique nonempty strings")
        estimate = {
            "observation_id": observation["observation_id"],
            "target_ref": observation["target_ref"],
            "target_estimate": deepcopy(observation["target_estimate"]),
            "quality": deepcopy(observation["quality"]),
            "consumer_ids": consumers,
        }
        return self.runtime.publish(
            message_id=observation["observation_id"],
            producer_id=observation["producer"],
            recipients=[self.recipient_id], channel=channel,
            estimate=estimate,
            valid_until_s=observation["valid_until_s"],
            published_at_s=observation["timestamp_s"],
        )

    def advance(self, time_s):
        self.runtime.advance(time_s)
        delivered = []
        for payload in self.runtime.public_messages(recipient=self.recipient_id):
            message_id = payload["message_id"]
            if message_id in self._emitted:
                continue
            self.participant.emit("observation_message", time_s=float(time_s),
                                  payload=payload)
            self._emitted.add(message_id)
            delivered.append(deepcopy(payload))
        return delivered

    def consume(self, message_id, *, consumer_id, time_s):
        return self.runtime.consume(
            message_id, recipient=self.recipient_id,
            consumer_id=consumer_id, time_s=time_s)

    def set_conditions(self, conditions, *, time_s, channel=None):
        return self.runtime.set_conditions(
            conditions, recipient=self.recipient_id,
            channel=channel, time_s=time_s)

    def report(self):
        report = self.runtime.report()
        report["isaac_integrated"] = True
        report["publicly_emitted_message_ids"] = sorted(self._emitted)
        return report


def _canonical_json(value):
    # Python equality equates False and 0 (and accepts tuples as sequences in
    # other paths). Evidence must be finite JSON and retain its recorded types.
    return json.dumps(_json_copy(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _report_contract(report, field):
    contract = report.get(field)
    if not isinstance(contract, Mapping):
        raise ValueError(f"{field} must be a contract mapping")
    normalized = validate_communication_contract(contract)
    if _canonical_json(normalized) != _canonical_json(dict(contract)):
        raise ValueError(f"{field} is not a normalized report contract")
    return normalized


def _legacy_initial_contract(final_contract, ledger):
    """Undo only provably appended selector rules; never guess old globals."""
    appended = []
    for event in ledger:
        if event["event"] != "channel_condition_changed":
            continue
        selector = {key: event[key] for key in ("recipient", "channel")
                    if event.get(key) is not None}
        if not selector:
            raise ValueError("legacy report with global condition changes requires expected_initial_contract")
        appended.append({**selector, **event["conditions"]})
    initial = deepcopy(final_contract)
    if appended:
        if _canonical_json(initial["rules"][-len(appended):]) != _canonical_json(appended):
            raise ValueError("legacy final contract does not bind the appended condition rules")
        del initial["rules"][-len(appended):]
    return initial


def validate_communication_report(report, *, expected_initial_contract=None):
    """Replay a report and compare evaluator state, rather than trusting it.

    The report's published records provide the immutable replay inputs. The
    original ledger supplies the order/timestamps of publications, consumption
    and condition changes; service, expiry and delivery events are regenerated.
    Both the resulting contract and the complete ledger/message state must
    match. ``expected_initial_contract`` optionally binds the starting policy to
    a trusted evaluator input. Self-consistent rewrites of all untrusted inputs
    cannot be authenticated by replay alone. The replay snapshot is omitted
    from the return value, which is safe for an evaluator to store.
    """
    result = {"schema": "eai.benchmark.communication_replay", "validated": False,
              "mismatches": [], "isaac_integrated": False}
    if not isinstance(report, Mapping):
        result["mismatches"].append("report is not a mapping")
        return result
    version = report.get("version")
    if (report.get("schema") != "eai.benchmark.communication"
            or type(version) is not int or version not in (1, 2)):
        result["mismatches"].append("unsupported report schema/version")
        return result
    result["isaac_integrated"] = report.get("isaac_integrated") is True
    messages = report.get("messages")
    ledger = report.get("ledger")
    if not isinstance(messages, Mapping) or not isinstance(ledger, list):
        result["mismatches"].append("messages and ledger have invalid types")
        return result
    try:
        final_contract = _report_contract(report, "contract")
        horizon = _time(report["time_s"], "report time_s")
        previous_time = Fraction(0)
        for sequence, event in enumerate(ledger):
            if not isinstance(event, Mapping):
                raise ValueError("ledger contains a non-mapping event")
            if type(event.get("sequence")) is not int or event["sequence"] != sequence:
                raise ValueError("ledger sequence is not contiguous")
            event_time = _time(event["time_s"], "ledger time_s")
            if not previous_time <= event_time <= horizon:
                raise ValueError("ledger time is outside monotonic report history")
            previous_time = event_time
        expected = (validate_communication_contract(expected_initial_contract)
                    if expected_initial_contract is not None else None)
        if version == 2:
            initial_contract = _report_contract(report, "initial_contract")
        else:
            if "initial_contract" in report:
                raise ValueError("initial_contract requires report version 2")
            initial_contract = (expected if expected is not None
                                else _legacy_initial_contract(final_contract, ledger))
        if expected is not None and initial_contract != expected:
            raise ValueError("initial contract differs from expected_initial_contract")
        replay = CommunicationRuntime(initial_contract)
        for event in ledger:
            if event.get("event") == "channel_condition_changed":
                replay.set_conditions(event["conditions"], recipient=event.get("recipient"),
                                      channel=event.get("channel"), time_s=event["time_s"])
            elif event.get("event") == "channel_condition_change_rejected":
                try:
                    replay.set_conditions(event["conditions"], recipient=event.get("recipient"),
                                          channel=event.get("channel"), time_s=event["time_s"])
                except ValueError:
                    pass
                else:
                    raise ValueError("rejected condition change was accepted during replay")
            elif event.get("event") == "published":
                message_id = event["message_id"]
                message = messages.get(message_id)
                if not isinstance(message, Mapping):
                    raise ValueError(f"published message {message_id!r} is absent")
                payload = message["payload"]
                replay.publish(message_id=message_id,
                               producer_id=payload["producer_id"],
                               recipients=event["recipients"],
                               channel=event["channel"],
                               estimate=payload["estimate"],
                               published_at_s=payload["published_at_s"],
                               valid_until_s=payload["valid_until_s"])
            elif event.get("event") == "consumed":
                replay.consume(event["message_id"], recipient=event["recipient"],
                               consumer_id=event["consumer_id"], time_s=event["time_s"])
            elif event.get("event") == "consume_rejected":
                # Rejected consumption is part of the causal evidence.  Replay
                # the attempted call and require the same rejection path; do
                # not treat the private ledger's rejection as authoritative.
                try:
                    replay.consume(event["message_id"], recipient=event["recipient"],
                                   consumer_id=event["consumer_id"], time_s=event["time_s"])
                except ValueError:
                    pass
                else:
                    raise ValueError("rejected consumption was accepted during replay")
        replay.advance(report["time_s"])
        actual = replay.report()
        if _canonical_json(actual["contract"]) != _canonical_json(final_contract):
            result["mismatches"].append("final contract differs after deterministic replay")
        if _canonical_json(actual["ledger"]) != _canonical_json(ledger):
            result["mismatches"].append("ledger differs after deterministic replay")
        if _canonical_json(actual["messages"]) != _canonical_json(dict(messages)):
            result["mismatches"].append("message delivery/consumption state differs after replay")
        result["published_count"] = len(messages)
        result["ledger_event_count"] = len(ledger)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        result["mismatches"].append(f"replay failed: {error}")
    result["validated"] = not result["mismatches"]
    return result


__all__ = ["CommunicationRuntime", "ParticipantCommunicationBoundary",
           "validate_communication_contract",
           "validate_communication_report"]
