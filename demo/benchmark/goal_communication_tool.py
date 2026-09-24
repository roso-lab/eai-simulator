"""Offline contract layer for the P2 p2p participant service (phase 1).

This module implements the frozen interface draft in
``P2P_ENDPOINT_INTERFACE_DESIGN.zh-CN.md`` (2026-09-24) as an offline,
deterministic contract layer: no Isaac, ROS, GPU, network, or simulator
integration.  It is the companion-contract v1 service surface that
``GoalAlgorithmHost`` will dispatch in phase 2; it never registers a Goal v4
action/event and it leaves the frozen ``goal_driven_contract`` /
``goal_session`` vocabularies untouched.

Contract summary (frozen):

- Tool-call envelope ``eai.benchmark.goal_communication_tool_call`` v1 with a
  closed field set; unknown/missing fields, an unsupported version, or an
  injected host-owned field (``pool``/``direction``/``message_id``/
  ``access_robot_id``) fail closed with a stable error code.
- Endpoints are registered once per episode as ``cloud`` (exactly one),
  ``edge:<edge_id>`` brains with their owned robots, and ``robot:<agent_id>``
  bodies.  Participants may only reference registered endpoints.
- Pool and direction are host-derived from the endpoint pair (design 3.3);
  only ``robot:* -> robot:*`` is p2p.  Everything else - including edge to
  edge and cross-jurisdiction edge/robot traffic - is
  ``endpoint_pair_forbidden``.
- ``message_id`` is host-allocated as ``f"{episode_id}:comm:{n}"`` so
  ``GoalSession.emit("observation")`` uniqueness holds by construction.
- ``accepted`` means "entered the transport ledger", never "delivered".
  Delivery reaches the participant only as an ``observation`` event with
  ``kind="communication_message"``; expiry and drops are evaluator-only and
  never participant events.
- Consumption keeps the single frozen verb: the existing
  ``consume_message`` goal action.  This layer provides the offline
  ``owns_message``/``can_consume`` prevalidation and the transport consume
  with action-ID retry idempotence, mirroring ``GoalPoseTelemetry``.
- Every report is development-track evidence: ``evidence_class=contract``,
  blocker ``communication_formal_episode_pending``, and all release flags
  false.  Wall clock, notification delivery delay, and planning compute
  duration remain ``null`` until a trusted component exists.

Interpretation notes (design does not contradict these; they are pinned here
so tests stay deterministic):

- ``robot:* -> cloud`` uplink is allowed whether or not the robot also has an
  owning edge (frozen table row 2 lists "owning edge or cloud").
- A robot publishing to itself matches the frozen table row ``robot:* ->
  robot:*`` mechanically and is therefore p2p; STAGE22 prose says "another
  robot", so tightening this would be an explicit v2 revision.
- The closed channel vocabulary is host configuration; the default set covers
  the STAGE22 directions (relay/handoff/negotiation for robots,
  telemetry/observation up, plan/dispatch/cancel down).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
import json
import math
from typing import Any

from .communication import CommunicationRuntime
from .communication_capacity_experiments import COMMUNICATION_FORMAL_BLOCKER
from .development_track_marker import (
    build_development_marker,
    stamp_evidence_record,
)
from .provenance import canonical_sha256


COMMUNICATION_TOOLS_SCHEMA = "eai.benchmark.goal_communication_tools"
COMMUNICATION_TOOLS_VERSION = 1
COMMUNICATION_TOOL_SCHEMA = "eai.benchmark.goal_communication_tool_call"
COMMUNICATION_TOOL_CALL_VERSION = 1

ENVELOPE_FIELDS = frozenset(
    {"schema", "version", "call_id", "submitted_at_s", "operation", "arguments"})
PUBLISH_ARGUMENT_FIELDS = frozenset(
    {"producer_id", "recipients", "channel", "estimate", "valid_until_s"})
OPTIONAL_ARGUMENT_FIELDS = frozenset({"information_id"})
# Host-derived fields can never be supplied by the participant (design 3.2).
PARTICIPANT_FORBIDDEN_ARGUMENT_FIELDS = frozenset(
    {"pool", "direction", "message_id", "access_robot_id"})
OPERATIONS = frozenset({"publish"})

DEFAULT_CHANNELS = (
    "relay", "handoff", "negotiation", "telemetry", "observation",
    "plan", "dispatch", "cancel",
)

PUBLISH_ERROR_CODES = frozenset({
    "envelope_invalid",
    "version_unsupported",
    "call_id_invalid_or_reused",
    "call_id_collision",
    "submission_time_mismatch",
    "budget_exhausted",
    "operation_unknown",
    "producer_unknown",
    "producer_not_current_member",
    "recipient_unknown",
    "recipient_not_current_member",
    "recipient_invalid",
    "endpoint_pair_forbidden",
    "channel_unknown",
    "estimate_not_canonical_json",
    "validity_invalid",
    "transport_internal",
})
CONSUME_ERROR_CODES = frozenset({
    "consume_unknown_message",
    "consume_not_delivered",
    "consume_stale",
    "consume_already_consumed",
})
ERROR_CODES = PUBLISH_ERROR_CODES | CONSUME_ERROR_CODES

INGRESS_ACCOUNTING_SCOPE = "model_to_benchmark_ingress_not_radio_transport"
REPORT_SCHEMA = "eai.benchmark.goal_communication_tools_report"
REPORT_VERSION = 1
MESSAGE_ID_TEMPLATE = "{episode_id}:comm:{index}"

_ALLOWED_DIRECTION_TABLE = (
    {"producer": "robot:<agent_id>", "recipient": "robot:<agent_id>",
     "pool": "p2p", "direction": "p2p"},
    {"producer": "robot:<agent_id>",
     "recipient": "owning edge:<edge_id> or cloud",
     "pool": "backhaul_uplink", "direction": "uplink"},
    {"producer": "edge:<edge_id>", "recipient": "cloud",
     "pool": "backhaul_uplink", "direction": "uplink"},
    {"producer": "cloud", "recipient": "edge:<edge_id> or robot:<agent_id>",
     "pool": "backhaul_downlink", "direction": "downlink"},
    {"producer": "edge:<edge_id>", "recipient": "jurisdiction robot:<agent_id>",
     "pool": "backhaul_downlink", "direction": "downlink"},
)


class GoalCommunicationToolError(ValueError):
    """Deterministic failure carrying one frozen stable error code.

    The bridge fails closed exactly like the pose tool ``submit`` path: the
    exception propagates, the half batch is never replayed, and the stable
    code plus the original text are retained in the service call ledger.
    ``error_code`` is ``None`` for bridge-level protocol violations that have
    no service-side code (for example a submit while the interface is not
    connected).
    """

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _finite_number(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _json_copy(value: Any, label: str) -> Any:
    """Copy strict finite JSON without tuple or non-string-key coercion."""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{label} JSON object keys must be strings")
        return {key: _json_copy(item, label) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_copy(item, label) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError(f"{label} must contain only finite JSON values")


def _canonical_size(value: Any) -> int | None:
    """Ingress bytes of the canonical call envelope; None if not JSON."""
    try:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
                   .encode("utf-8"))
    except (TypeError, ValueError):
        return None


def communication_message_observation(payload: Mapping[str, Any], *,
                                      recipient_id: str,
                                      effective_valid_until_s: float | None = None
                                      ) -> dict[str, Any]:
    """Build the participant-visible delivery event payload (design 3.5).

    The payload keeps the frozen ``observation`` field set.  ``data`` carries
    the host-side delivery view (recipient endpoint, estimate, information
    id); queue state, conditions, drop reasons, and scheduled arrivals never
    enter this view.  ``published_at_s`` is intentionally not exposed.
    ``effective_valid_until_s`` overrides the requested validity with the
    transport-effective window (pool TTL truncation), keeping the delivery
    view consistent with the publish receipt and the consume stale check.
    """
    recipient_id = _name(recipient_id, "recipient_id")
    if not isinstance(payload, Mapping):
        raise ValueError("delivery payload must be a mapping")
    estimate = payload.get("estimate")
    if not isinstance(estimate, Mapping):
        raise ValueError("delivery payload estimate must be a mapping")
    validity = (payload["valid_until_s"] if effective_valid_until_s is None
                else effective_valid_until_s)
    return {
        "message_id": _name(payload["message_id"], "message_id"),
        "producer_id": _name(payload["producer_id"], "producer_id"),
        "kind": "communication_message",
        "data": {
            "recipient_id": recipient_id,
            "estimate": _json_copy(dict(estimate), "estimate"),
            "information_id": payload.get("information_id"),
        },
        "valid_until_s": _finite_number(validity, "valid_until_s"),
    }


class CommunicationEndpointTable:
    """Immutable episode-frozen cloud/edge/robot endpoint registry.

    ``cloud`` is the single cloud endpoint.  ``edges`` maps each edge id to
    the robots under its jurisdiction; ``robots`` is the full registered
    robot roster (standalone robots report directly to the cloud).  Endpoint
    strings are ``cloud``, ``edge:<edge_id>``, and ``robot:<agent_id>``; ids
    may not contain ``:`` so the string encoding stays bijective.
    """

    CLOUD = "cloud"

    def __init__(self, *, edges: Mapping[str, Sequence[str]] | None = None,
                 robots: Iterable[str] = ()) -> None:
        edge_map: dict[str, tuple[str, ...]] = {}
        owner: dict[str, str] = {}
        for edge_id, members in dict(edges or {}).items():
            edge_id = _name(edge_id, "edge id")
            if ":" in edge_id:
                raise ValueError("edge id must not contain ':'")
            rows = members if isinstance(members, list) else list(members or ())
            if not rows:
                raise ValueError("edge jurisdiction must list at least one robot")
            normalized: list[str] = []
            for agent in rows:
                agent = _name(agent, "edge member")
                if ":" in agent:
                    raise ValueError("robot agent id must not contain ':'")
                if agent in owner:
                    raise ValueError(
                        f"robot {agent!r} belongs to more than one edge")
                owner[agent] = edge_id
                normalized.append(agent)
            edge_map[edge_id] = tuple(normalized)
        roster: list[str] = []
        for agent in robots:
            agent = _name(agent, "robot agent id")
            if ":" in agent:
                raise ValueError("robot agent id must not contain ':'")
            if agent in roster:
                raise ValueError(f"duplicate robot registration {agent!r}")
            roster.append(agent)
        for agent in owner:
            if agent not in roster:
                raise ValueError(
                    f"edge member {agent!r} is not a registered robot")
        self._edges = {edge_id: tuple(members) for edge_id, members in edge_map.items()}
        self._owner = dict(owner)
        self._robots = frozenset(roster)
        self._endpoints = frozenset(
            ["cloud", *(f"edge:{edge_id}" for edge_id in self._edges),
             *(f"robot:{agent}" for agent in roster)])

    @staticmethod
    def parse(endpoint: Any) -> tuple[str | None, str | None]:
        """Split an endpoint string into (kind, identifier)."""
        if endpoint == "cloud":
            return "cloud", None
        if isinstance(endpoint, str) and endpoint.startswith("edge:"):
            return "edge", endpoint[len("edge:"):]
        if isinstance(endpoint, str) and endpoint.startswith("robot:"):
            return "robot", endpoint[len("robot:"):]
        return None, None

    def is_registered(self, endpoint: Any) -> bool:
        return isinstance(endpoint, str) and endpoint in self._endpoints

    def endpoints(self) -> frozenset[str]:
        return self._endpoints

    def robots(self) -> frozenset[str]:
        return self._robots

    def edges(self) -> dict[str, tuple[str, ...]]:
        return dict(self._edges)

    def edge_for(self, agent_id: str) -> str | None:
        return self._owner.get(agent_id)

    def edge_owns(self, edge_id: str, agent_id: str) -> bool:
        return self._owner.get(agent_id) == edge_id

    def derive(self, producer_id: str, recipient_id: str) -> tuple[str, str]:
        """Return the frozen (pool, direction) for a registered endpoint pair.

        Unregistered or unparsable endpoints and every pair outside the
        design-3.3 table raise ``endpoint_pair_forbidden``.  This method is
        the single derivation authority used by both submit and the tests.
        """
        producer_kind, producer_edge_id = self.parse(producer_id)
        recipient_kind, recipient_agent_or_edge = self.parse(recipient_id)
        if not self.is_registered(producer_id) or not self.is_registered(recipient_id):
            raise GoalCommunicationToolError(
                "endpoint pair is outside the allowed direction table "
                "(unregistered endpoint)", error_code="endpoint_pair_forbidden")
        if producer_kind == "cloud":
            if recipient_kind in {"edge", "robot"}:
                return "backhaul_downlink", "downlink"
        elif producer_kind == "edge":
            if recipient_kind == "cloud":
                return "backhaul_uplink", "uplink"
            if (recipient_kind == "robot"
                    and self.edge_owns(producer_edge_id, recipient_agent_or_edge)):
                return "backhaul_downlink", "downlink"
        else:  # robot producer
            if recipient_kind == "robot":
                return "p2p", "p2p"
            if recipient_kind == "cloud":
                return "backhaul_uplink", "uplink"
            if (recipient_kind == "edge"
                    and self.edge_for(producer_edge_id) == recipient_agent_or_edge):
                return "backhaul_uplink", "uplink"
        raise GoalCommunicationToolError(
            "endpoint pair is outside the allowed direction table",
            error_code="endpoint_pair_forbidden")


def derive_pool_direction(table: CommunicationEndpointTable, producer_id: str,
                          recipient_id: str) -> tuple[str, str]:
    """Module-level derivation wrapper; raises on a forbidden pair."""
    return table.derive(producer_id, recipient_id)


def is_communication_tool_call(item: Any) -> bool:
    """Host dispatch predicate mirroring the pose/camera schema branch."""
    return isinstance(item, Mapping) and item.get("schema") == COMMUNICATION_TOOL_SCHEMA


def submit_communication_tool_call(
    service: GoalCommunicationTools | None,
    call: Mapping[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Fail-closed bridge helper: submitting without a connected service is
    a protocol violation, never a silent no-op or empty success."""
    if service is None:
        raise GoalCommunicationToolError(
            "communication tool interface was not connected")
    return service.submit(call, **kwargs)


class GoalCommunicationTools:
    """Opt-in p2p participant service contract (offline, phase 1).

    ``submit`` validates one ``publish`` tool call against the frozen
    envelope, endpoint table, and roster, derives pool/direction host-side,
    allocates ``message_id``, and meters the message through the shared
    ``CommunicationRuntime``.  ``advance`` returns newly delivered
    ``communication_message`` observation payloads.  ``consume`` keeps the
    frozen ``consume_message`` verb semantics with stable rejection codes.
    """

    def __init__(
        self,
        public_task: Mapping[str, Any],
        *,
        episode_id: str,
        endpoints: CommunicationEndpointTable | Mapping[str, Any],
        runtime: CommunicationRuntime | None = None,
        channels: Sequence[str] = DEFAULT_CHANNELS,
        scene_key: str = "goal_communication_tools",
    ) -> None:
        if not isinstance(public_task, Mapping):
            raise ValueError("public_task must be a mapping")
        pool_rows = public_task.get("candidate_robot_pool")
        if not isinstance(pool_rows, (list, tuple)) or not pool_rows:
            raise ValueError("public_task requires a candidate_robot_pool")
        pool_ids = set()
        for row in pool_rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
                raise ValueError("candidate_robot_pool rows require string ids")
            pool_ids.add(row["id"])
        budgets = public_task.get("budgets")
        if not isinstance(budgets, Mapping):
            raise ValueError("public_task requires budgets")
        simulation_budget = _finite_number(
            budgets.get("simulation_s"), "budgets.simulation_s")
        self.episode_id = _name(episode_id, "episode_id")
        if isinstance(endpoints, CommunicationEndpointTable):
            self.endpoints = endpoints
        elif isinstance(endpoints, Mapping):
            self.endpoints = CommunicationEndpointTable(
                edges=endpoints.get("edges"), robots=endpoints.get("robots", ()))
        else:
            raise ValueError(
                "endpoints must be a CommunicationEndpointTable or a mapping")
        unregistered = sorted(self.endpoints.robots() - pool_ids)
        if unregistered:
            raise ValueError(
                "registered robots outside the public candidate pool: "
                f"{unregistered}")
        if runtime is not None and not isinstance(runtime, CommunicationRuntime):
            raise ValueError("runtime must be a CommunicationRuntime")
        channel_rows = list(channels)
        if (not channel_rows
                or any(not isinstance(item, str) or not item.strip()
                       for item in channel_rows)
                or len(set(channel_rows)) != len(channel_rows)):
            raise ValueError("channels must be distinct nonempty strings")
        self._scene_key = _name(scene_key, "scene_key")
        self.public_task_sha256 = canonical_sha256(public_task)
        self._simulation_budget_s = simulation_budget
        self.runtime = runtime if runtime is not None else CommunicationRuntime()
        self._channels = frozenset(channel_rows)
        self._now = 0.0
        self._next_message_index = 0
        self._call_ids: set[str] = set()
        self._call_rows: list[dict[str, Any]] = []
        self._consume_rows: list[dict[str, Any]] = []
        self._owned_messages: dict[str, str] = {}
        # Transport-effective validity per message (pool-TTL truncated); the
        # publish receipt, delivery observation, and stale check all read it.
        self._effective_valid_until: dict[str, float] = {}
        self._emitted_messages: set[str] = set()
        self._emitted_valid_until: dict[str, float] = {}
        self._actions: dict[str, tuple[str, dict[str, Any]]] = {}
        self._marker: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Profile and reports
    # ------------------------------------------------------------------
    def public_profile(self) -> dict[str, Any]:
        """Participant-visible capability declaration (design 3.1)."""
        return {
            "schema": COMMUNICATION_TOOLS_SCHEMA,
            "version": COMMUNICATION_TOOLS_VERSION,
            "episode_id": self.episode_id,
            "public_task_sha256": self.public_task_sha256,
            "optional": True,
            "endpoints": {
                "cloud": "cloud",
                "edges": {edge_id: list(members)
                          for edge_id, members in sorted(self.endpoints.edges().items())},
                "robots": sorted(self.endpoints.robots()),
            },
            "allowed_directions": {
                "table": [dict(row) for row in _ALLOWED_DIRECTION_TABLE],
                "everything_else": "endpoint_pair_forbidden",
            },
            "channels": sorted(self._channels),
            "validity": (
                "participants declare valid_until_s strictly after submission; "
                "the effective transport validity is "
                "min(requested, published_at_s + pool ttl_s)"),
            "call_schema": COMMUNICATION_TOOL_SCHEMA,
            "call_version": COMMUNICATION_TOOL_CALL_VERSION,
            "call_fields": ["call_id", "submitted_at_s", "operation", "arguments"],
            "consumption": (
                "existing consume_message goal action; the publish response is "
                "a local admission receipt, not proof of delivery or of "
                "decision dependence"),
            "cost": (
                "canonical ingress call bytes are recorded separately from the "
                "three radio transport pools; message bytes are metered by the "
                "runtime from the five public payload fields; no token price"),
        }

    def _development_marker(self) -> dict[str, Any]:
        if self._marker is None:
            self._marker = build_development_marker(
                self._scene_key,
                asset_status="development_unverified",
                evidence_class="contract",
                infrastructure_blockers=[COMMUNICATION_FORMAL_BLOCKER])
        return self._marker

    def communication_report(self) -> dict[str, Any]:
        """Unannotated transport archive accepted by replay validation."""
        return self.runtime.report()

    def report(self) -> dict[str, Any]:
        """Evaluator-only service report stamped with the development marker."""
        record = {
            "schema": REPORT_SCHEMA,
            "version": REPORT_VERSION,
            "visibility": "evaluator_only",
            "episode_id": self.episode_id,
            "profile": self.public_profile(),
            "calls": deepcopy(self._call_rows),
            "consume_attempts": deepcopy(self._consume_rows),
            "ingress": {
                "accounting_scope": INGRESS_ACCOUNTING_SCOPE,
                "total_call_bytes": sum(
                    row["size_bytes"] or 0 for row in self._call_rows),
                "attempt_count": len(self._call_rows),
            },
            "communication": self.runtime.report(),
            "timing": {
                "time_authority": (
                    "goal session trusted simulation clock; phase 2 binds "
                    "publish/delivery/consume timestamps to it"),
                "wall_clock_s": None,
                "notification_delivery_delay_s": None,
                "planning_compute_duration_s": None,
            },
            "execution_validated": False,
            "business_outcomes_validated": False,
            "scoreable": False,
            "release_eligible": False,
        }
        return stamp_evidence_record(record, self._development_marker())

    # ------------------------------------------------------------------
    # Publish path
    # ------------------------------------------------------------------
    def owns_call(self, call_id: Any) -> bool:
        return isinstance(call_id, str) and call_id in self._call_ids

    def has_pending_work(self) -> bool:
        """True while an owned message still awaits transport resolution.

        The runner uses this (with ``next_wakeup_s``) to keep the trusted
        session clock advancing while a participant waits for a delayed
        delivery instead of having to dispatch unrelated work first.
        """
        undelivered = bool(self._owned_messages.keys() - self._emitted_messages)
        return undelivered and self.runtime.next_event_time_s() is not None

    def next_wakeup_s(self) -> float | None:
        """Earliest scheduled transport event time, if any."""
        return self.runtime.next_event_time_s()

    def _validate_time(self, time_s: Any) -> float:
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s) or time_s < self._now):
            raise ValueError(
                "communication tool time must be finite and monotonic")
        return float(time_s)

    def submit(
        self,
        call: Mapping[str, Any],
        *,
        time_s: float,
        current_agents: set[str] | None = None,
        reserved_call_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Validate and meter one publish tool call; failures raise and record.

        ``reserved_call_ids`` carries goal action ids and other tools' call
        ids so ``call_id_collision`` stays deterministic at the service
        boundary (phase 2: the host passes them like its pose/camera checks).
        """
        now = self._validate_time(time_s)
        snapshot = (deepcopy(dict(call)) if isinstance(call, Mapping)
                    else deepcopy(call))
        row: dict[str, Any] = {
            "call": snapshot,
            "call_id": None,
            "received_at_s": now,
            "size_bytes": _canonical_size(snapshot),
            "status": "rejected",
            "error_code": None,
            "reason": None,
            "accounting_scope": INGRESS_ACCOUNTING_SCOPE,
        }
        self._call_rows.append(row)

        def reject(code: str, text: str) -> None:
            row.update(error_code=code, reason=text)
            raise GoalCommunicationToolError(
                f"communication tool call rejected ({code}): {text}",
                error_code=code)

        if (not isinstance(call, Mapping)
                or call.get("schema") != COMMUNICATION_TOOL_SCHEMA):
            reject("envelope_invalid",
                   "call must be an object with schema "
                   f"{COMMUNICATION_TOOL_SCHEMA}")
        fields = set(call)
        missing = ENVELOPE_FIELDS - fields
        extra = fields - ENVELOPE_FIELDS
        if missing or extra:
            reject("envelope_invalid",
                   f"envelope fields mismatch; missing={sorted(missing)}, "
                   f"extra={sorted(extra)}")
        if (type(call["version"]) is not int
                or call["version"] != COMMUNICATION_TOOL_CALL_VERSION):
            reject("version_unsupported",
                   "only tool call version 1 is supported")
        call_id = call["call_id"]
        if not isinstance(call_id, str) or not call_id.strip():
            reject("call_id_invalid_or_reused",
                   "call_id must be a nonempty string")
        row["call_id"] = call_id
        if call_id in self._call_ids:
            reject("call_id_invalid_or_reused",
                   f"call_id {call_id!r} has already been used by this service")
        if call_id in set(reserved_call_ids):
            reject("call_id_collision",
                   f"call_id {call_id!r} collides with a goal action or "
                   "another tool call id")
        self._call_ids.add(call_id)
        submitted = call["submitted_at_s"]
        if (isinstance(submitted, bool)
                or not isinstance(submitted, (int, float))
                or not math.isfinite(submitted) or float(submitted) != now):
            reject("submission_time_mismatch",
                   "submitted_at_s must equal the current episode time")
        if now >= self._simulation_budget_s:
            reject("budget_exhausted",
                   "episode simulation budget has ended")
        operation = call["operation"]
        if not isinstance(operation, str) or operation not in OPERATIONS:
            reject("operation_unknown",
                   f"operation must be one of {sorted(OPERATIONS)}")
        arguments = call["arguments"]
        if not isinstance(arguments, Mapping):
            reject("envelope_invalid", "arguments must be an object")
        argument_fields = set(arguments)
        missing_arguments = PUBLISH_ARGUMENT_FIELDS - argument_fields
        extra_arguments = (argument_fields - PUBLISH_ARGUMENT_FIELDS
                           - OPTIONAL_ARGUMENT_FIELDS)
        if missing_arguments or extra_arguments:
            injected = sorted(extra_arguments
                              & PARTICIPANT_FORBIDDEN_ARGUMENT_FIELDS)
            detail = (f"; host-derived fields cannot be injected: {injected}"
                      if injected else "")
            reject("envelope_invalid",
                   f"arguments fields mismatch; missing={sorted(missing_arguments)}, "
                   f"extra={sorted(extra_arguments)}{detail}")

        producer_id = arguments["producer_id"]
        if (not isinstance(producer_id, str) or not producer_id.strip()
                or not self.endpoints.is_registered(producer_id)):
            reject("producer_unknown",
                   f"producer endpoint {producer_id!r} is not registered")
        producer_kind, producer_agent = self.endpoints.parse(producer_id)
        if (producer_kind == "robot" and current_agents is not None
                and producer_agent not in current_agents):
            reject("producer_not_current_member",
                   f"producer endpoint {producer_id!r} is not a current member")
        recipients = arguments["recipients"]
        if (not isinstance(recipients, list) or len(recipients) != 1
                or not isinstance(recipients[0], str) or not recipients[0].strip()):
            reject("recipient_invalid",
                   "recipients must be a single-element list with one "
                   "endpoint string")
        recipient_id = recipients[0]
        if not self.endpoints.is_registered(recipient_id):
            reject("recipient_unknown",
                   f"recipient endpoint {recipient_id!r} is not registered")
        recipient_kind, recipient_agent = self.endpoints.parse(recipient_id)
        if (recipient_kind == "robot" and current_agents is not None
                and recipient_agent not in current_agents):
            reject("recipient_not_current_member",
                   f"recipient endpoint {recipient_id!r} is not a current member")
        try:
            pool, direction = self.endpoints.derive(producer_id, recipient_id)
        except GoalCommunicationToolError as exc:
            reject("endpoint_pair_forbidden", str(exc))
        channel = arguments["channel"]
        if not isinstance(channel, str) or channel not in self._channels:
            reject("channel_unknown",
                   f"channel must be one of {sorted(self._channels)}")
        estimate = arguments["estimate"]
        if not isinstance(estimate, Mapping):
            reject("estimate_not_canonical_json",
                   "estimate must be a JSON object of finite values")
        try:
            normalized_estimate = _json_copy(dict(estimate), "estimate")
        except ValueError as exc:
            reject("estimate_not_canonical_json", str(exc))
        valid_until = arguments["valid_until_s"]
        if (isinstance(valid_until, bool)
                or not isinstance(valid_until, (int, float))
                or not math.isfinite(valid_until)
                or not float(valid_until) > now):
            reject("validity_invalid",
                   "valid_until_s must be finite and strictly later than "
                   "submitted_at_s")
        information_id = arguments.get("information_id")
        if information_id is not None and (
                not isinstance(information_id, str) or not information_id.strip()):
            reject("envelope_invalid",
                   "information_id must be a nonempty string when provided")

        message_id = MESSAGE_ID_TEMPLATE.format(
            episode_id=self.episode_id, index=self._next_message_index)
        try:
            record = self.runtime.publish(
                message_id=message_id,
                producer_id=producer_id,
                recipients=[recipient_id],
                channel=channel,
                estimate=normalized_estimate,
                valid_until_s=float(valid_until),
                published_at_s=now,
                pool=pool,
                direction=direction,
                access_robot_id=None,
                information_id=information_id,
            )
        except Exception as exc:  # platform gap, never a model failure
            row.update(error_code="transport_internal",
                       reason=f"{type(exc).__name__}: {exc}")
            raise GoalCommunicationToolError(
                f"communication tool call rejected (transport_internal): "
                f"{type(exc).__name__}: {exc}",
                error_code="transport_internal") from exc
        self._next_message_index += 1
        self._owned_messages[message_id] = recipient_id
        self._now = now
        delivery = record["recipients"][recipient_id]
        effective_validity = delivery.get(
            "transport_valid_until_s", record["payload"]["valid_until_s"])
        self._effective_valid_until[message_id] = float(effective_validity)
        row.update(status="accepted", message_id=message_id, pool=pool,
                   direction=direction,
                   published_size_bytes=record["size_bytes"])
        return {
            "accepted": True,
            "call_id": call_id,
            "operation": "publish",
            "message_id": message_id,
            "producer_id": producer_id,
            "recipients": [recipient_id],
            "channel": channel,
            "direction": direction,
            "pool": pool,
            "size_bytes": record["size_bytes"],
            "submitted_at_s": now,
            "valid_until_s": float(effective_validity),
            "execution_validated": False,
        }

    # ------------------------------------------------------------------
    # Delivery visibility boundary
    # ------------------------------------------------------------------
    def advance(self, time_s: float, *,
                current_agents: set[str] | None = None) -> list[dict[str, Any]]:
        """Advance transport time and return new delivery observations.

        Only fresh, delivered, unconsumed messages owned by this service
        become ``communication_message`` payloads, each exactly once.
        Recipient robots that left the roster stop receiving observations
        (their already-metered bytes are not rolled back), and messages that
        expired before this pump are never participant events.
        """
        now = self._validate_time(time_s)
        self.runtime.advance(now)
        self._now = now
        observations: list[dict[str, Any]] = []
        for endpoint in sorted(set(self._owned_messages.values())):
            kind, agent = self.endpoints.parse(endpoint)
            if (current_agents is not None and kind == "robot"
                    and agent not in current_agents):
                continue
            for payload in self.runtime.public_messages(recipient=endpoint):
                message_id = payload["message_id"]
                if (self._owned_messages.get(message_id) != endpoint
                        or message_id in self._emitted_messages):
                    continue
                effective_validity = self._effective_valid_until.get(
                    message_id, payload["valid_until_s"])
                if now >= effective_validity:
                    continue
                observations.append(
                    communication_message_observation(
                        payload, recipient_id=endpoint,
                        effective_valid_until_s=effective_validity))
                self._emitted_messages.add(message_id)
                self._emitted_valid_until[message_id] = effective_validity
        return observations

    # ------------------------------------------------------------------
    # Consume path (frozen consume_message verb; design 3.4)
    # ------------------------------------------------------------------
    def owns_message(self, message_id: Any) -> bool:
        return (isinstance(message_id, str)
                and message_id in self._owned_messages)

    def can_consume(self, message_id: str, *, time_s: float) -> bool:
        """Current delivered-and-fresh eligibility without prediction."""
        if (not isinstance(message_id, str)
                or message_id not in self._owned_messages
                or message_id not in self._emitted_messages):
            return False
        try:
            now = _finite_number(time_s, "time_s")
        except ValueError:
            return False
        if now != self.runtime.time_s:
            return False
        for payload in self.runtime.public_messages(
                recipient=self._owned_messages[message_id]):
            if payload["message_id"] == message_id:
                effective_validity = self._effective_valid_until.get(
                    message_id, payload["valid_until_s"])
                return now < effective_validity
        return False

    def consume(self, message_id: str, *, action_id: str,
                time_s: float) -> dict[str, Any]:
        """Account one consume_message action with retry idempotence.

        Mirrors ``GoalPoseTelemetry.consume``: ``consumer_id`` is the accepted
        goal ``action_id``; a retried action id returns the original receipt;
        the same action id bound to another message is a protocol error.
        """
        message_id = _name(message_id, "message_id")
        action_id = _name(action_id, "action_id")
        now = self._validate_time(time_s)
        row: dict[str, Any] = {"message_id": message_id, "action_id": action_id,
                               "time_s": now, "status": "rejected",
                               "error_code": None, "reason": None}
        self._consume_rows.append(row)

        def reject(code: str, text: str) -> None:
            row.update(error_code=code, reason=text)
            raise GoalCommunicationToolError(
                f"communication consume rejected ({code}): {text}",
                error_code=code)

        previous = self._actions.get(action_id)
        if previous is not None:
            previous_message_id, receipt = previous
            if previous_message_id != message_id:
                raise ValueError("action_id is already bound to another message")
            row.update(status="accepted", idempotent=True)
            return deepcopy(receipt)
        if message_id not in self._owned_messages:
            reject("consume_unknown_message",
                   f"message {message_id!r} was not published by this service")
        if message_id not in self._emitted_messages:
            reject("consume_not_delivered",
                   "message has not been delivered to the algorithm")
        # Judge staleness against the validity the participant saw on the
        # delivered observation, before the transport flips the delivery to
        # its terminal expired state at the same instant.
        if now >= self._emitted_valid_until[message_id]:
            reject("consume_stale",
                   "message is expired at the current simulation time")
        self.runtime.advance(now)
        self._now = now
        try:
            receipt = self.runtime.consume(
                message_id, recipient=self._owned_messages[message_id],
                consumer_id=action_id)
        except ValueError as exc:
            reason = str(exc).rsplit(":", 1)[-1].strip()
            if reason in {"unknown_message", "not_a_recipient"}:
                code = "consume_unknown_message"
            elif reason == "already_consumed":
                code = "consume_already_consumed"
            else:
                # queued/in_transit/dropped/expired/consumer_not_declared all
                # mean "this delivered copy is not consumable now".
                code = "consume_not_delivered"
            reject(code, f"transport rejected the consume: {reason}")
        row.update(status="accepted")
        self._actions[action_id] = (message_id, deepcopy(receipt))
        return deepcopy(receipt)


__all__ = [
    "COMMUNICATION_TOOL_CALL_VERSION",
    "COMMUNICATION_TOOL_SCHEMA",
    "COMMUNICATION_TOOLS_SCHEMA",
    "COMMUNICATION_TOOLS_VERSION",
    "CONSUME_ERROR_CODES",
    "DEFAULT_CHANNELS",
    "ERROR_CODES",
    "INGRESS_ACCOUNTING_SCOPE",
    "PUBLISH_ERROR_CODES",
    "CommunicationEndpointTable",
    "GoalCommunicationToolError",
    "GoalCommunicationTools",
    "communication_message_observation",
    "derive_pool_direction",
    "is_communication_tool_call",
    "submit_communication_tool_call",
]
