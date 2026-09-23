"""Deterministic offline communication execution; no simulator integration.

``CommunicationRuntime(contract)`` owns monotonic simulation time. Publish at
the current time (or supply a later ``published_at_s``); call ``advance(time_s)``
or its alias ``tick`` before reading/consuming. Times are absolute seconds.

Contracts accept delay_s, drop, bytes_per_window (None = unlimited), window_s,
and rules. Rules match recipient/channel (omission = wildcard); later matching
rules override earlier fields. An opt-in ``pools``/``access`` contract models
shared cloud/edge/backhaul, robot-to-robot (p2p), and per-robot last-mile
capacity. ``set_conditions`` changes the current policy
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

AoI keeps the existing delivery/consume samples for compatibility and also
tracks a continuous age curve per ``(recipient, information_id)``. The curve
starts when the first update selected by ``aoi_sample_policy`` is accepted and
is integrated until the report time. Time-weighted mean and P95 are reported
separately from the event-age sample mean and P95; time before the first
accepted update is uncovered rather than silently treated as a zero-age value.

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


_POOL_POLICY_FIELDS = {
    "capacity_bps", "bytes_per_second", "fixed_delay_s", "delay_s",
    "buffer_bytes", "buffer_capacity_bytes", "drop", "active_drop",
    "ttl_s", "bytes_per_window", "window_s", "c0_bps", "baseline_capacity_bps",
}
_POOL_ALIASES = {
    "backhaul": "backhaul_uplink",
    "uplink": "backhaul_uplink",
    "downlink": "backhaul_downlink",
    "backhaul_up": "backhaul_uplink",
    "backhaul_down": "backhaul_downlink",
    "robot": "p2p",
    "robot_to_robot": "p2p",
}


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
        if "capacity_bps" in result:
            _time(result["capacity_bps"], "capacity_bps", positive=True)
        if "bytes_per_second" in result:
            _time(result["bytes_per_second"], "bytes_per_second", positive=True)
        for key in ("fixed_delay_s", "buffer_bytes", "buffer_capacity_bytes", "ttl_s"):
            if key in result:
                value = result[key]
                if key in {"buffer_bytes", "buffer_capacity_bytes"}:
                    if type(value) is not int or value <= 0:
                        raise ValueError(f"{key} must be a positive integer")
                else:
                    _time(value, key, positive=key == "ttl_s")
        if "active_drop" in result and type(result["active_drop"]) is not bool:
            raise ValueError("active_drop must be a boolean")
        for key in ("recipient", "channel"):
            if key in result:
                _name(result[key], key)
        return result

    base_allowed = fields | {"rules", "pools", "access", "default_pool",
                             "default_access", "aoi_sample_policy"}
    normalized = validate(contract, base_allowed)
    rules = normalized.pop("rules", [])
    if not isinstance(rules, (list, tuple)):
        raise ValueError("rules must be a sequence of mappings")
    pools = normalized.pop("pools", {})
    if pools is None:
        pools = {}
    if not isinstance(pools, Mapping):
        raise ValueError("pools must be a mapping")
    normalized_pools = {}
    for name, policy in pools.items():
        _name(name, "pool")
        if not isinstance(policy, Mapping):
            raise ValueError("pool policy must be a mapping")
        normalized_pools[str(name)] = _validate_pool_policy(policy)
    access = normalized.pop("access", {})
    if access is None:
        access = {}
    if not isinstance(access, Mapping):
        raise ValueError("access must be a mapping")
    normalized_access = {}
    for name, policy in access.items():
        _name(name, "access robot")
        if not isinstance(policy, Mapping):
            raise ValueError("access policy must be a mapping")
        normalized_access[str(name)] = _validate_pool_policy(policy)
    result = {"delay_s": 0., "drop": False, "bytes_per_window": None,
              "window_s": 1., **normalized,
              "rules": [validate(rule, fields | {"recipient", "channel"}) for rule in rules]}
    if normalized_pools:
        result["pools"] = normalized_pools
    if normalized_access:
        result["access"] = normalized_access
    if "default_pool" in contract:
        result["default_pool"] = _name(contract["default_pool"], "default_pool")
    if "default_access" in contract:
        result["default_access"] = _name(contract["default_access"], "default_access")
    if "aoi_sample_policy" in contract:
        policy = contract["aoi_sample_policy"]
        if policy not in {"delivery", "consume"}:
            raise ValueError("aoi_sample_policy must be delivery or consume")
        result["aoi_sample_policy"] = policy
    return result


def _validate_pool_policy(policy):
    unknown = set(policy) - _POOL_POLICY_FIELDS
    if unknown:
        raise ValueError(f"unknown pool policy fields: {sorted(unknown)}")
    row = deepcopy(dict(policy))
    # Normalize the public spelling while retaining only one capacity field.
    if "bytes_per_second" in row and "capacity_bps" in row:
        if float(row["bytes_per_second"]) != float(row["capacity_bps"]):
            raise ValueError("capacity_bps and bytes_per_second disagree")
        row.pop("bytes_per_second")
    if "bytes_per_second" in row:
        row["capacity_bps"] = row.pop("bytes_per_second")
    if "c0_bps" in row and "baseline_capacity_bps" in row:
        if float(row["c0_bps"]) != float(row["baseline_capacity_bps"]):
            raise ValueError("c0_bps and baseline_capacity_bps disagree")
        row.pop("baseline_capacity_bps")
    if "baseline_capacity_bps" in row:
        row["c0_bps"] = row.pop("baseline_capacity_bps")
    if "buffer_capacity_bytes" in row and "buffer_bytes" in row:
        if row["buffer_capacity_bytes"] != row["buffer_bytes"]:
            raise ValueError("buffer_bytes and buffer_capacity_bytes disagree")
        row.pop("buffer_capacity_bytes")
    if "buffer_capacity_bytes" in row:
        row["buffer_bytes"] = row.pop("buffer_capacity_bytes")
    if "fixed_delay_s" in row and "delay_s" in row:
        if float(row["fixed_delay_s"]) != float(row["delay_s"]):
            raise ValueError("fixed_delay_s and delay_s disagree")
        row.pop("delay_s")
    for key in ("capacity_bps", "fixed_delay_s", "ttl_s", "c0_bps"):
        if key in row and row[key] is not None:
            _time(row[key], key, positive=key in {"capacity_bps", "ttl_s", "c0_bps"})
    if ("buffer_bytes" in row and row["buffer_bytes"] is not None
            and (type(row["buffer_bytes"]) is not int or row["buffer_bytes"] <= 0)):
        raise ValueError("buffer_bytes must be a positive integer")
    if "bytes_per_window" in row:
        if type(row["bytes_per_window"]) is not int or row["bytes_per_window"] <= 0:
            raise ValueError("bytes_per_window must be a positive integer")
        _time(row.get("window_s", 1.), "window_s", positive=True)
    if "drop" in row and type(row["drop"]) is not bool:
        raise ValueError("drop must be a boolean")
    if "active_drop" in row and type(row["active_drop"]) is not bool:
        raise ValueError("active_drop must be a boolean")
    if "capacity_bps" not in row and "bytes_per_window" not in row:
        # A shared pool without an explicit capacity remains unlimited, which
        # is useful for deterministic delay/loss controls.
        row["capacity_bps"] = None
    row.setdefault("fixed_delay_s", 0.)
    row.setdefault("drop", False)
    row.setdefault("active_drop", False)
    row.setdefault("buffer_bytes", None)
    return row


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
        self._pool_states = {}
        self._access_states = {}
        self._aoi_samples = []
        # Tracks are keyed by (recipient, information_id). Completed segments
        # are immutable; the current tail is materialized by ``aoi()``.
        self._aoi_tracks = {}

    @property
    def time_s(self):
        return float(self._now)

    def next_event_time_s(self):
        """Earliest scheduled service, delivery, or expiry time, if any.

        After ``advance(t)``, remaining heap entries are strictly later than
        ``t``. Callers may jump directly to this instant instead of polling.
        """
        if not self._events:
            return None
        return float(self._events[0][0])

    def _record(self, event, time, **data):
        self._ledger.append({"sequence": len(self._ledger), "event": event,
                             "time_s": float(time), **deepcopy(data)})

    def _direction_fields(self, message_id):
        """Return the explicit publication direction for transport evidence.

        Direction is optional for legacy links.  When supplied by a pooled
        publication it is copied into every later service/drop/expiry row so a
        scorer can attribute bytes without joining mutable ledger history.
        """
        message = self._messages.get(message_id)
        direction = message.get("direction") if message else None
        return {"direction": direction} if direction is not None else {}

    def _schedule(self, time, kind, key):
        # Expiry wins ties with service and arrival: TTL is half-open.
        priority = {"expire": 0, "pool_service": 1, "service": 2, "deliver": 3}[kind]
        heapq.heappush(self._events, (time, priority, self._sequence, kind, key))
        self._sequence += 1

    def _conditions(self, recipient, channel):
        result = {k: v for k, v in self._contract.items() if k != "rules"}
        for rule in self._contract["rules"]:
            if (rule.get("recipient", recipient) == recipient
                    and rule.get("channel", channel) == channel):
                result.update({k: v for k, v in rule.items() if k not in {"recipient", "channel"}})
        return result

    def _pool_mode(self):
        return bool(self._contract.get("pools") or self._contract.get("access"))

    def _pool_name(self, *, producer_id, recipient, channel, pool=None, direction=None):
        if pool is None:
            pool = direction
        if pool is None:
            lower = channel.lower()
            if any(token in lower for token in ("p2p", "relay", "handoff", "negotiat")):
                pool = "p2p"
            elif any(token in lower for token in ("command", "plan", "dispatch", "downlink")):
                pool = "backhaul_downlink"
            else:
                pool = self._contract.get("default_pool", "backhaul_uplink")
        pool = _POOL_ALIASES.get(pool, pool)
        if pool not in self._contract.get("pools", {}):
            # Permit a single backhaul declaration to serve both directions,
            # while preserving explicit direction names when available.
            if pool in {"backhaul_uplink", "backhaul_downlink"} and "backhaul" in self._contract.get("pools", {}):
                pool = "backhaul"
            elif pool not in self._contract.get("pools", {}):
                raise ValueError(f"unknown communication pool {pool!r}")
        return pool

    def _pool_policy(self, pool_name):
        policy = self._contract.get("pools", {}).get(pool_name)
        if policy is None:
            raise ValueError(f"pool {pool_name!r} has no policy")
        return policy

    def _access_endpoints(self, *, producer_id, recipient, pool_name, access_robot_id=None):
        if access_robot_id is not None:
            return [_name(access_robot_id, "access_robot_id")]
        if pool_name == "p2p":
            return sorted({producer_id, recipient})
        if pool_name == "backhaul_downlink":
            return [recipient]
        return [producer_id]

    def _access_policy(self, endpoint):
        access = self._contract.get("access", {})
        return access.get(endpoint) or access.get("*" ) or access.get(
            self._contract.get("default_access", ""))

    @staticmethod
    def _pool_capacity(policy):
        capacity = policy.get("capacity_bps")
        if capacity is not None:
            return float(capacity)
        budget = policy.get("bytes_per_window")
        if budget is not None:
            return float(budget) / float(policy.get("window_s", 1.))
        return None

    def _drop_delivery(self, delivery, *, time, reason, message_id, recipient):
        delivery["status"] = "dropped"
        delivery["drop_reason"] = reason
        self._record("dropped", time, message_id=message_id, recipient=recipient,
                     reason=reason, **self._direction_fields(message_id))

    def _queue_pool_copy(self, message_id, recipient, *, pool_name, endpoints,
                         conditions, published, expiry):
        policy = self._pool_policy(pool_name)
        pool = self._pool_states.setdefault(pool_name, {
            "queue": deque(), "queued_bytes": 0, "next_available": published,
            "policy": deepcopy(policy),
        })
        buffer = policy.get("buffer_bytes")
        if buffer is not None and pool["queued_bytes"] + self._messages[message_id]["size_bytes"] > buffer:
            self._drop_delivery(self._messages[message_id]["recipients"][recipient],
                                time=published, reason="buffer_overflow",
                                message_id=message_id, recipient=recipient)
            return
        starts = [published, pool["next_available"]]
        for endpoint in endpoints:
            access_policy = self._access_policy(endpoint)
            if access_policy is not None:
                state = self._access_states.setdefault(endpoint, {"next_available": published})
                starts.append(state["next_available"])
        start = max(starts)
        capacity = self._pool_capacity(policy)
        size = self._messages[message_id]["size_bytes"]
        durations = [size / capacity] if capacity is not None else [0.]
        for endpoint in endpoints:
            access_policy = self._access_policy(endpoint)
            access_capacity = (self._pool_capacity(access_policy)
                               if access_policy is not None else None)
            if access_capacity is not None:
                durations.append(size / access_capacity)
        duration = max(durations)
        finish = start + Fraction(str(duration))
        pool["next_available"] = finish
        for endpoint in endpoints:
            if self._access_policy(endpoint) is not None:
                self._access_states.setdefault(endpoint, {"next_available": published})[
                    "next_available"] = finish
        key = (message_id, recipient)
        pool["queue"].append(key)
        pool["queued_bytes"] += self._messages[message_id]["size_bytes"]
        delivery = self._messages[message_id]["recipients"][recipient]
        delivery.update(pool=pool_name, access_endpoints=list(endpoints),
                        scheduled_transmit_at_s=float(start),
                        scheduled_finish_at_s=float(finish),
                        transport_valid_until_s=float(expiry))
        self._record("queued", published, message_id=message_id, recipient=recipient,
                     pool=pool_name, access_endpoints=list(endpoints))
        self._schedule(expiry, "expire", key)
        self._schedule(start, "pool_service", pool_name)

    def _service_pool(self, pool_name, time):
        pool = self._pool_states[pool_name]
        while pool["queue"]:
            message_id, recipient = pool["queue"][0]
            message = self._messages[message_id]
            delivery = message["recipients"][recipient]
            pool["queue"].popleft()
            pool["queued_bytes"] = max(0, pool["queued_bytes"] - message["size_bytes"])
            if delivery["status"] != "queued":
                continue
            expiry = _time(delivery.get("transport_valid_until_s",
                                         message["payload"]["valid_until_s"]),
                           "valid_until_s")
            if expiry <= time:
                delivery["status"] = "expired"
                delivery["drop_reason"] = "ttl"
                self._record("expired", time, message_id=message_id, recipient=recipient,
                             bytes_sent=0, reason="ttl", **self._direction_fields(message_id))
                continue
            policy = self._pool_policy(pool_name)
            finish = _time(delivery["scheduled_finish_at_s"], "scheduled_finish_at_s")
            delivery.update(status="in_transit", bytes_sent=message["size_bytes"],
                            transmitted_at_s=float(time),
                            scheduled_delivery_at_s=float(finish + _time(
                                policy.get("fixed_delay_s", 0.), "fixed_delay_s")))
            direction = message.get("direction")
            self._record("bytes_served", time, message_id=message_id, recipient=recipient,
                         channel=message["channel"], pool=pool_name,
                         **({"direction": direction} if direction is not None else {}),
                         bytes=message["size_bytes"], bytes_sent=message["size_bytes"])
            delay = _time(policy.get("fixed_delay_s", 0.), "fixed_delay_s")
            arrival = finish + delay
            if arrival >= expiry:
                self._schedule(expiry, "expire", (message_id, recipient))
            else:
                self._schedule(arrival, "deliver", (message_id, recipient))
            # A pool reservation schedules each copy independently.  The next
            # reservation is already reflected in its own pool_service event.
            break

    def _request_service(self, link, time):
        key = (link, time)
        if key not in self._scheduled_services:
            self._scheduled_services.add(key)
            self._schedule(time, "service", link)

    def set_conditions(self, conditions, *, recipient=None, channel=None, pool=None,
                       time_s=None):
        """Apply a policy at the current time; it affects subsequent publishes.

        A bandwidth/window change is rejected while that selected link has
        queued bytes. The rejection is recorded before raising, so an evaluator
        can distinguish an attempted intervention from an ignored one. Existing
        queued/in-flight copies retain their publication-time conditions.
        """
        if pool is not None:
            if not self._pool_mode():
                raise ValueError("pool condition requires a pool contract")
            pool = _POOL_ALIASES.get(_name(pool, "pool"), pool)
            if pool not in self._contract.get("pools", {}):
                raise ValueError(f"unknown communication pool {pool!r}")
            if not isinstance(conditions, Mapping) or set(conditions) - _POOL_POLICY_FIELDS:
                raise ValueError("pool conditions contain an unknown field")
            if any(key in conditions for key in ("capacity_bps", "bytes_per_second",
                                                 "buffer_bytes", "buffer_capacity_bytes",
                                                 "fixed_delay_s", "ttl_s", "bytes_per_window",
                                                 "window_s")):
                # Validate the partial patch without introducing defaults into
                # the recorded condition-change event.
                normalized_patch = _validate_pool_policy(dict(conditions))
                values = {key: normalized_patch[key] for key in conditions
                          if key in normalized_patch}
                if "bytes_per_second" in conditions:
                    values.pop("bytes_per_second", None)
                    values["capacity_bps"] = normalized_patch["capacity_bps"]
                if "buffer_capacity_bytes" in conditions:
                    values.pop("buffer_capacity_bytes", None)
                    values["buffer_bytes"] = normalized_patch["buffer_bytes"]
                if "delay_s" in conditions:
                    values.pop("delay_s", None)
                    values["fixed_delay_s"] = normalized_patch["fixed_delay_s"]
            else:
                values = deepcopy(dict(conditions))
            now = self._now if time_s is None else _time(time_s, "time_s")
            if now != self._now:
                if now < self._now:
                    raise ValueError("condition-change time must equal current time")
                self.advance(float(now))
            self._contract["pools"][pool].update(values)
            self._record("channel_condition_changed", self._now, recipient=None,
                         channel=None, pool=pool, conditions=values,
                         applies_to="future_publications")
            return deepcopy(values)
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

    def update_contract(self, conditions, *, recipient=None, channel=None, pool=None,
                        time_s=None):
        """Named alias for current-time condition intervention."""
        return self.set_conditions(conditions, recipient=recipient, channel=channel,
                                   pool=pool, time_s=time_s)

    def publish(self, *, message_id, producer_id, recipients, channel, estimate,
                valid_until_s, published_at_s=None, pool=None, direction=None,
                access_robot_id=None, information_id=None):
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
        if information_id is not None:
            payload["information_id"] = _name(information_id, "information_id")
        size = len(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self.advance(float(published))
        # Preserve explicit direction for service/drop ledger rows emitted
        # after publication.  Without this immutable metadata, pooled traffic
        # can be replayed but cannot be attributed by direction in scoring.
        message = {"payload": payload, "channel": channel, "size_bytes": size,
                   "recipients": {}}
        if direction is not None:
            message["direction"] = direction
        self._messages[message_id] = message
        selected_pool = None
        if self._pool_mode():
            selected_pool = self._pool_name(producer_id=producer_id,
                                             recipient=recipients[0], channel=channel,
                                             pool=pool, direction=direction)
        self._record("published", published, message_id=message_id, producer_id=producer_id,
                     channel=channel, recipients=sorted(recipients), size_bytes=size,
                     **({"pool": selected_pool, "direction": direction,
                         "access_robot_id": access_robot_id,
                         "information_id": information_id}
                        if self._pool_mode() else {}))
        for recipient in sorted(recipients):
            conditions = self._conditions(recipient, channel)
            delivery = {"status": "queued", "conditions": conditions, "bytes_sent": 0,
                        "transmitted_at_s": None, "scheduled_delivery_at_s": None,
                        "delivered_at_s": None, "consumption": None,
                        "drop_reason": None}
            message["recipients"][recipient] = delivery
            key = (message_id, recipient)
            pool_conditions = self._pool_policy(selected_pool) if selected_pool else {}
            effective_expiry = expiry
            if selected_pool and pool_conditions.get("ttl_s") is not None:
                effective_expiry = min(
                    expiry, published + _time(pool_conditions["ttl_s"], "ttl_s"))
            if conditions["drop"] or pool_conditions.get("drop") or pool_conditions.get("active_drop"):
                self._drop_delivery(delivery, time=published,
                                    reason="active", message_id=message_id,
                                    recipient=recipient)
            elif effective_expiry <= published:
                delivery["status"] = "expired"
                delivery["drop_reason"] = "ttl"
                self._record("expired", published, message_id=message_id, recipient=recipient,
                             bytes_sent=0, reason="ttl", **self._direction_fields(message_id))
            elif selected_pool:
                endpoints = self._access_endpoints(producer_id=producer_id,
                                                   recipient=recipient,
                                                   pool_name=selected_pool,
                                                   access_robot_id=access_robot_id)
                self._queue_pool_copy(message_id, recipient, pool_name=selected_pool,
                                      endpoints=endpoints, conditions=conditions,
                                      published=published, expiry=effective_expiry)
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
                             bytes_sent=delivery["bytes_sent"],
                             **self._direction_fields(message_id))
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
            if kind == "pool_service":
                self._service_pool(key, time)
                continue
            if kind == "service":
                self._service(key, time)
                continue
            message_id, recipient = key
            delivery = self._messages[message_id]["recipients"][recipient]
            if kind == "expire" and delivery["status"] not in {"dropped", "expired"}:
                if delivery["status"] == "queued" and delivery.get("pool") in self._pool_states:
                    pool = self._pool_states[delivery["pool"]]
                    pool["queued_bytes"] = max(0, pool["queued_bytes"] -
                                                 self._messages[message_id]["size_bytes"])
                delivery["status"] = "expired"
                delivery["drop_reason"] = "ttl"
                self._record("expired", time, message_id=message_id, recipient=recipient,
                             bytes_sent=delivery["bytes_sent"], reason="ttl",
                             **self._direction_fields(message_id))
            elif kind == "deliver" and delivery["status"] == "in_transit":
                delivery.update(status="delivered", delivered_at_s=float(time))
                self._record("delivered", time, message_id=message_id, recipient=recipient)
                if self._contract.get("aoi_sample_policy", "delivery") == "delivery":
                    self._record_aoi(message_id, recipient, time)
        self._now = target
        return self.time_s

    tick = advance

    def _aoi_information_id(self, message_id):
        """Resolve the evaluator's logical information-item identity."""
        message = self._messages[message_id]
        payload = message["payload"]
        info_id = payload.get("information_id")
        if info_id is None:
            estimate = payload.get("estimate", {})
            info_id = estimate.get("information_id") or estimate.get("observation_id")
        # Generic messages remain measurable; their message ID is a stable
        # fallback and preserves the pre-continuous AoI behavior.
        return (info_id if isinstance(info_id, str) and info_id
                else message_id)

    @staticmethod
    def _append_aoi_segment(track, start, end, generated):
        if end <= start:
            return
        age_start = start - generated
        age_end = end - generated
        track["segments"].append((start, end, age_start, age_end))

    def _record_aoi(self, message_id, recipient, sampled_at):
        """Record one accepted update and advance its continuous age curve."""
        message = self._messages[message_id]
        payload = message["payload"]
        info_id = self._aoi_information_id(message_id)
        sampled_at = (sampled_at if isinstance(sampled_at, Fraction)
                      else _time(sampled_at, "aoi sample time"))
        generated = _time(payload["published_at_s"], "published_at_s")
        delivery = message["recipients"][recipient]
        delivered_at = delivery.get("delivered_at_s")
        delivered = (None if delivered_at is None else
                     _time(delivered_at, "delivered_at_s"))
        self._aoi_samples.append({"information_id": info_id, "message_id": message_id,
                                  "recipient": recipient,
                                  "age_s": float(sampled_at - generated),
                                  "generated_at_s": float(generated),
                                  "delivered_at_s": (None if delivered is None
                                                     else float(delivered)),
                                  "sampled_at_s": float(sampled_at)})

        key = (recipient, info_id)
        track = self._aoi_tracks.setdefault(key, {
            "recipient": recipient, "information_id": info_id,
            "first_sampled_at": sampled_at, "last_sampled_at": None,
            "latest_generated_at": None, "sample_count": 0, "segments": [],
        })
        track["sample_count"] += 1
        previous_at = track["last_sampled_at"]
        previous_generated = track["latest_generated_at"]
        if previous_at is None:
            track["first_sampled_at"] = sampled_at
        else:
            self._append_aoi_segment(track, previous_at, sampled_at,
                                     previous_generated)
        # A late packet with an older generation timestamp must not make the
        # information appear newer. It is still retained as an event sample.
        if previous_generated is None or generated >= previous_generated:
            track["latest_generated_at"] = generated
        track["last_sampled_at"] = sampled_at

    @staticmethod
    def _weighted_aoi_p95(segments):
        """Return the 95th percentile of age weighted by time duration."""
        intervals = [(age_start, age_end)
                     for _, _, age_start, age_end in segments
                     if age_end > age_start]
        if not intervals:
            return None
        boundaries = sorted({point for pair in intervals for point in pair})
        total = sum(end - start for start, end in intervals)
        target = total * Fraction("0.95")
        cumulative = Fraction(0)
        for left, right in zip(boundaries, boundaries[1:]):
            active = sum(start <= left < end for start, end in intervals)
            if not active:
                continue
            bucket = (right - left) * active
            if target <= cumulative + bucket:
                return float(left + (target - cumulative) / active)
            cumulative += bucket
        return float(boundaries[-1])

    def _aoi_continuous(self):
        """Build immutable continuous AoI statistics at the current time."""
        now = self._now
        items = []
        aggregate_segments = []
        aggregate_duration = Fraction(0)
        aggregate_area = Fraction(0)
        for key in sorted(self._aoi_tracks, key=lambda value: (value[0], str(value[1]))):
            track = self._aoi_tracks[key]
            segments = list(track["segments"])
            if track["last_sampled_at"] is not None:
                if now > track["last_sampled_at"]:
                    start = track["last_sampled_at"]
                    generated = track["latest_generated_at"]
                    end = now
                    age_start, age_end = start - generated, end - generated
                    segments.append((start, end, age_start, age_end))
            duration = sum(end - start for start, end, _, _ in segments)
            area = sum((age_start + age_end) * (end - start) / 2
                       for start, end, age_start, age_end in segments)
            aggregate_segments.extend(segments)
            aggregate_duration += duration
            aggregate_area += area
            item = {
                "recipient": track["recipient"],
                "information_id": track["information_id"],
                "sample_count": track["sample_count"],
                "start_s": float(track["first_sampled_at"]),
                "end_s": float(now),
                "covered_duration_s": float(duration),
                "area_s2": float(area),
                "time_mean_s": (float(area / duration) if duration else None),
                "time_p95_s": self._weighted_aoi_p95(segments),
                "segments": [{"start_s": float(start), "end_s": float(end),
                              "age_start_s": float(age_start),
                              "age_end_s": float(age_end)}
                             for start, end, age_start, age_end in segments],
            }
            items.append(item)
        return {
            "horizon_s": float(now),
            "item_count": len(items),
            "covered_duration_s": float(aggregate_duration),
            "area_s2": float(aggregate_area),
            "time_mean_s": (float(aggregate_area / aggregate_duration)
                             if aggregate_duration else None),
            "time_p95_s": self._weighted_aoi_p95(aggregate_segments),
            "items": items,
        }

    def aoi(self):
        """Return event samples plus continuous time-weighted AoI statistics."""
        samples = deepcopy(self._aoi_samples)
        values = sorted(item["age_s"] for item in samples)
        continuous = self._aoi_continuous()
        if not values:
            return {"sample_count": 0, "samples": [], "mean_s": None, "p95_s": None,
                    "time_mean_s": continuous["time_mean_s"],
                    "time_p95_s": continuous["time_p95_s"],
                    "continuous": continuous}
        position = (len(values) - 1) * 0.95
        lower = math.floor(position)
        upper = math.ceil(position)
        p95 = values[lower] if lower == upper else values[lower] + (
            values[upper] - values[lower]) * (position - lower)
        return {"sample_count": len(values), "samples": samples,
                "mean_s": sum(values) / len(values), "p95_s": p95,
                "time_mean_s": continuous["time_mean_s"],
                "time_p95_s": continuous["time_p95_s"],
                "continuous": continuous}

    def pool_utilization(self):
        """Evaluator-only capacity summary with absolute and C/C0 values."""
        summary = {}
        for name, policy in self._contract.get("pools", {}).items():
            capacity = self._pool_capacity(policy)
            baseline = policy.get("c0_bps")
            summary[name] = {
                "capacity_bps": capacity,
                "c0_bps": baseline,
                "capacity_ratio_c0": (capacity / float(baseline)
                                       if capacity is not None and baseline else None),
                "bytes_served": sum(event.get("bytes", 0) for event in self._ledger
                                     if event.get("event") == "bytes_served"
                                     and event.get("pool") == name),
            }
        return summary

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
        if self._contract.get("aoi_sample_policy", "delivery") == "consume":
            self._record_aoi(message_id, recipient, self._now)
        return deepcopy(event)

    def report(self):
        """Evaluator snapshot with initial and final policy plus causal evidence."""
        return {"schema": "eai.benchmark.communication", "version": 2,
                "visibility": "evaluator_only", "time_s": self.time_s,
                "initial_contract": deepcopy(self._initial_contract),
                "contract": deepcopy(self._contract), "messages": deepcopy(self._messages),
                "ledger": deepcopy(self._ledger), "aoi": self.aoi(),
                "pool_utilization": self.pool_utilization(),
                "isaac_integrated": False}


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
                            channel="observation", pool=None, direction=None,
                            access_robot_id=None):
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
            pool=pool, direction=direction, access_robot_id=access_robot_id,
            information_id=observation.get("observation_id"),
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

    def set_conditions(self, conditions, *, time_s, channel=None, pool=None):
        return self.runtime.set_conditions(
            conditions, recipient=self.recipient_id,
            channel=channel, pool=pool, time_s=time_s)

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


def _aoi_report_matches(actual, expected):
    """Compare an AoI report while accepting fields absent from old reports.

    Continuous AoI and ``sampled_at_s`` were added after the original v1/v2
    report shape. Existing reports remain replayable when their recorded
    fields match; reports that include the new fields still compare them
    exactly. This compatibility is scoped to the AoI subtree only.
    """
    if isinstance(expected, Mapping):
        return (isinstance(actual, Mapping)
                and all(key in actual and _aoi_report_matches(actual[key], value)
                        for key, value in expected.items()))
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(_aoi_report_matches(left, right)
                        for left, right in zip(actual, expected)))
    return _canonical_json(actual) == _canonical_json(expected)


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
                                      channel=event.get("channel"), pool=event.get("pool"),
                                      time_s=event["time_s"])
            elif event.get("event") == "channel_condition_change_rejected":
                try:
                    replay.set_conditions(event["conditions"], recipient=event.get("recipient"),
                                          channel=event.get("channel"), pool=event.get("pool"),
                                          time_s=event["time_s"])
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
                               valid_until_s=payload["valid_until_s"],
                               pool=event.get("pool"),
                               direction=event.get("direction"),
                               access_robot_id=event.get("access_robot_id"),
                               information_id=payload.get("information_id"))
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
        if "aoi" in report and not _aoi_report_matches(actual["aoi"], report["aoi"]):
            result["mismatches"].append("AoI statistics differ after deterministic replay")
        if "pool_utilization" in report and _canonical_json(actual["pool_utilization"]) != _canonical_json(report["pool_utilization"]):
            result["mismatches"].append("pool utilization differs after deterministic replay")
        result["published_count"] = len(messages)
        result["ledger_event_count"] = len(ledger)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        result["mismatches"].append(f"replay failed: {error}")
    result["validated"] = not result["mismatches"]
    return result


__all__ = ["CommunicationRuntime", "ParticipantCommunicationBoundary",
           "validate_communication_contract",
           "validate_communication_report"]
