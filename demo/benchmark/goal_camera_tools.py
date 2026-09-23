"""Optional participant camera tools; caller owns the trusted simulation clock.

This companion leaves frozen GoalSession task/API packets unchanged. Image
payloads meter through CommunicationRuntime channel ``camera``. Tool-call and
control-response bytes are recorded as local ingress/control volume, not radio
downlink. Capture runs only on the trusted host/session-owner thread.

Budget accounting (documented contract):
- ``quality.max_bytes`` and episode ``max_payload_bytes_per_episode`` bound
  **raw** image payload bytes (pre-base64).
- CommunicationRuntime offered/link bytes meter the **canonical UTF-8 JSON**
  transport size (includes base64). Those link bytes are never rolled back
  after publish, including when a late frame is suppressed after timeout.
"""

from __future__ import annotations

from base64 import b64encode
from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .goal_camera_telemetry import GoalCameraTelemetry
from .provenance import canonical_sha256


CAMERA_TOOL_SCHEMA = "eai.benchmark.goal_camera_tool_call"
_TOOLS = frozenset({"request_observation", "cancel_request"})
_FRESHNESS = frozenset({"capture_at_or_after_submit"})
_QUALITY_FIELDS = frozenset({"encoding", "max_bytes"})
_REQUEST_ARGUMENT_FIELDS = frozenset({
    "robot_id", "sensor_id", "modalities", "deadline_s",
    "resolution", "quality", "freshness",
})
_TERMINAL_STATUSES = frozenset({
    "delivered", "timeout", "cancelled", "failed", "episode_ended",
})
_ERROR_CODES = frozenset({
    "duplicate_request_id",
    "unknown_robot",
    "unknown_sensor",
    "inactive_robot",
    "invalid_modality",
    "resolution_exceeded",
    "budget_exceeded",
    "busy",
    "invalid_encoding",
    "invalid_deadline",
    "invalid_arguments",
    "episode_budget_ended",
    "episode_ended",
    "capture_failed",
    "cancelled",
    "timeout",
    "not_found",
    "unsupported_field",
    "publish_failed",
})


@runtime_checkable
class CameraCaptureBackend(Protocol):
    """Owner-thread capture backend with idle_destroy lifecycle hooks."""

    def capture(self, request: Mapping[str, Any], time_s: float) -> Mapping[str, Any]:
        ...

    def tick(self, time_s: float) -> None:
        ...

    def close(self, *, now_s: float = 0.0) -> None:
        ...

    def lifecycle_report(self) -> Mapping[str, Any]:
        ...


def _finite(value: Any, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be finite")
    number = float(value)
    if positive and number <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def normalize_sensor_catalog(catalog: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate robot → sensor_id → profile mappings for the public tool surface."""
    if not isinstance(catalog, Mapping) or not catalog:
        raise ValueError("sensor catalog must be a nonempty mapping")
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for robot_id, sensors in catalog.items():
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise ValueError("sensor catalog robot IDs must be nonempty strings")
        if not isinstance(sensors, Mapping) or not sensors:
            raise ValueError(f"robot {robot_id!r} must declare sensors")
        row: dict[str, dict[str, Any]] = {}
        for sensor_id, profile in sensors.items():
            if not isinstance(sensor_id, str) or not sensor_id.strip():
                raise ValueError("sensor IDs must be nonempty strings")
            if not isinstance(profile, Mapping):
                raise ValueError(f"sensor profile for {sensor_id!r} must be an object")
            member = profile.get("member", "primary")
            modalities = profile.get("modalities", ["rgb"])
            encodings = profile.get("encodings", ["png"])
            if not isinstance(member, str) or not member.strip():
                raise ValueError("sensor member must be a nonempty string")
            if (not isinstance(modalities, list) or not modalities
                    or any(item != "rgb" for item in modalities)):
                raise ValueError("first edition supports RGB-only modalities")
            if (not isinstance(encodings, list) or not encodings
                    or any(not isinstance(item, str) or not item for item in encodings)):
                raise ValueError("encodings must be nonempty strings")
            row[sensor_id] = {
                "member": member,
                "modalities": ["rgb"],
                "encodings": list(encodings),
                "max_width": _positive_int(profile.get("max_width", 1280), "max_width"),
                "max_height": _positive_int(profile.get("max_height", 720), "max_height"),
                "max_bytes": _positive_int(profile.get("max_bytes", 2_000_000), "max_bytes"),
            }
        result[robot_id] = row
    return result


def estimate_camera_transport_size(estimate: Mapping[str, Any]) -> int:
    """Canonical UTF-8 JSON size of a camera estimate (link-metered shape)."""
    payload = {
        "message_id": "size-probe",
        "producer_id": "size-probe",
        "published_at_s": 0.0,
        "valid_until_s": 1.0,
        "estimate": dict(estimate),
    }
    return len(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8"))


class GoalCameraTools:
    """Single-frame on-demand camera capture; no frames until the model opts in.

    ``capture`` / backend ``capture`` must run on the simulator/session owner
    thread. Accepted requests are not deliveries: frames still cross
    ``CommunicationRuntime``. Late frames after timeout/cancel/fail are
    suppressed from the model while retaining already-metered link bytes.
    """

    def __init__(
        self,
        public_task: Mapping[str, Any],
        *,
        episode_id: str,
        sensor_catalog: Mapping[str, Any],
        telemetry: GoalCameraTelemetry,
        capture: Callable[[Mapping[str, Any], float], Mapping[str, Any]] | None = None,
        backend: CameraCaptureBackend | None = None,
        validity_s: float = 5.0,
        max_outstanding_per_sensor: int = 1,
        max_requests_per_episode: int = 64,
        max_payload_bytes_per_episode: int = 32_000_000,
        default_encoding: str = "png",
    ):
        validity = _finite(validity_s, "validity_s", positive=True)
        if backend is not None and capture is not None:
            raise ValueError("provide backend or capture, not both")
        if backend is None and capture is None:
            raise ValueError("camera tools require a trusted capture backend or callable")
        if backend is not None and not isinstance(backend, CameraCaptureBackend):
            # Structural check for duck-typed backends in tests.
            for name in ("capture", "tick", "close", "lifecycle_report"):
                if not callable(getattr(backend, name, None)):
                    raise ValueError(f"backend missing {name}")
        if not isinstance(episode_id, str) or not episode_id:
            raise ValueError("camera tools require episode identity")
        if default_encoding not in {"png", "jpeg", "jpg", "raw"}:
            raise ValueError("unsupported default encoding")
        self.episode_id = episode_id
        self.public_task_sha256 = canonical_sha256(public_task)
        self.robot_ids = frozenset(row["id"] for row in public_task["candidate_robot_pool"])
        self._known_robot_ids = set(self.robot_ids)
        self._simulation_budget_s = float(public_task["budgets"]["simulation_s"])
        # Trusted host registry may include fixed_hidden_identity join candidates.
        # Public profile / request surface only see initial pool until member_join.
        self._private_sensor_registry = normalize_sensor_catalog(sensor_catalog)
        self._public_sensors: dict[str, dict[str, dict[str, Any]]] = {
            robot_id: deepcopy(sensors)
            for robot_id, sensors in self._private_sensor_registry.items()
            if robot_id in self.robot_ids
        }
        self.sensor_catalog = self._public_sensors
        self.telemetry = telemetry
        self._backend = backend
        self._capture = backend.capture if backend is not None else capture
        self.validity_s = validity
        self.max_outstanding_per_sensor = _positive_int(
            max_outstanding_per_sensor, "max_outstanding_per_sensor")
        self.max_requests_per_episode = _positive_int(
            max_requests_per_episode, "max_requests_per_episode")
        self.max_payload_bytes_per_episode = _positive_int(
            max_payload_bytes_per_episode, "max_payload_bytes_per_episode")
        self.default_encoding = default_encoding
        self._now = 0.0
        self._closed = False
        self._episode_finalized = False
        self._activated_join_indices: set[int] = set()
        self._bound_goal_session: Any | None = None
        self._calls: dict[str, dict[str, Any]] = {}
        self._requests: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._awaiting_delivery: dict[str, dict[str, Any]] = {}
        self._suppressed_messages: set[str] = set()
        self._outstanding: dict[tuple[str, str], set[str]] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._request_count = 0
        self._payload_bytes_spent = 0
        self._transport_bytes_published = 0
        self._control_events: list[dict[str, Any]] = []
        self._control_ledger: list[dict[str, Any]] = []
        self._event_index = 0
        self._lifecycle_errors: list[dict[str, Any]] = []
        self._lifecycle_report: dict[str, Any] = {
            "render_product_active_s": 0.0,
            "create_count": 0,
            "destroy_count": 0,
            "warmup_frames": 0,
            "first_frame_latency_s": [],
            "native_orsus_rtx_verified": False,
            "failures": [],
            "cleanup_pending": False,
            "cleanup_complete": True,
        }

    def public_profile(self) -> dict[str, Any]:
        sensors = {
            robot_id: {
                sensor_id: {
                    "member": profile["member"],
                    "modalities": list(profile["modalities"]),
                    "encodings": list(profile["encodings"]),
                    "max_width": profile["max_width"],
                    "max_height": profile["max_height"],
                    "max_bytes": profile["max_bytes"],
                }
                for sensor_id, profile in row.items()
            }
            for robot_id, row in self._public_sensors.items()
        }
        return {
            "schema": "eai.benchmark.goal_camera_tools",
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": self.public_task_sha256,
            "optional": True,
            "robot_ids": sorted(self._known_robot_ids),
            "robot_scope": (
                "initial public pool plus trusted-host member_join activations; "
                "hidden join identities are absent until a GoalSession member_join "
                "event is activated by GoalAlgorithmHost"),
            "sensors": sensors,
            "modalities": ["rgb"],
            "subscription": False,
            "freshness": sorted(_FRESHNESS),
            "validity_s": self.validity_s,
            "simulation_budget_s": self._simulation_budget_s,
            "max_outstanding_per_sensor": self.max_outstanding_per_sensor,
            "max_requests_per_episode": self.max_requests_per_episode,
            "max_payload_bytes_per_episode": self.max_payload_bytes_per_episode,
            "budget_units": {
                "request_max_bytes": "raw_payload",
                "episode_max_payload_bytes": "raw_payload",
                "communication_link_bytes": "canonical_utf8_json_including_base64",
            },
            "call_schema": CAMERA_TOOL_SCHEMA,
            "call_version": 1,
            "call_fields": ["request_id", "submitted_at_s", "tool", "arguments"],
            "tools": {
                "request_observation": {
                    "arguments": sorted(_REQUEST_ARGUMENT_FIELDS),
                    "quality_fields": sorted(_QUALITY_FIELDS),
                    "unsupported_arguments": ["requested_sim_time_s", "payload_ref"],
                },
                "cancel_request": {"arguments": ["request_id"]},
            },
            "response": "observation(kind=camera_frame); delivered messages only",
            "control_response": (
                "camera_tool_result acknowledges local admission; "
                "camera_request_timeout/cancelled/failed/episode_ended are terminal; "
                "camera_tools_profile_delta publishes join-scoped sensor unlocks; "
                "timeouts do not diagnose drop vs delay"),
            "profile_delta": (
                "trusted host activates via activate_joined_member(session event); "
                "participant receives update_camera_tools(delta) and/or "
                "observation(kind=camera_tools_profile_delta)"),
            "consumption": "existing consume_message action; receipt is not proof of decision dependence",
            "cache": (
                "same capture key may reuse validated pixels inside one episode; "
                "cache write happens only after CommunicationRuntime accepts the message; "
                "every delivery remeters transport bytes and sets cache_hit"),
            "render_lifecycle": "idle_destroy; warmup/first-frame latency recorded separately",
            "cost": "canonical ingress call bytes and metered camera-channel response bytes; no token price",
            "execution_validated": False,
            "scoreable": False,
        }

    def owns_call(self, call_id: str) -> bool:
        return isinstance(call_id, str) and call_id in self._calls

    def register_public_robot(self, robot_id: str) -> None:
        """Disabled: bare robot-id activation would leak / spoof hidden join IDs."""
        raise RuntimeError(
            "direct register_public_robot is disabled; GoalAlgorithmHost must call "
            "activate_joined_member with a committed GoalSession member_join event")

    def bind_goal_session(self, session: Any) -> None:
        """Bind the trusted host session used to authenticate member joins.

        The participant never receives this object.  Keeping the binding on the
        host side prevents a caller that only holds the camera companion from
        fabricating a plausible event and sequence number.
        """
        if session is None or not callable(getattr(session, "report", None)):
            raise ValueError("camera tools require a GoalSession report provider")
        if self._bound_goal_session is not None and self._bound_goal_session is not session:
            raise RuntimeError("camera tools are already bound to another GoalSession")
        self._bound_goal_session = session

    def _validate_bound_join_event(
        self, event: Mapping[str, Any], session_event_index: int,
    ) -> None:
        session = self._bound_goal_session
        if session is None:
            raise RuntimeError(
                "camera join activation requires a trusted GoalSession binding")
        report = session.report()
        events = report.get("events") if isinstance(report, Mapping) else None
        if not isinstance(events, list) or session_event_index >= len(events):
            raise ValueError("member_join event index is not committed in GoalSession")
        committed = events[session_event_index]
        if committed != event:
            raise ValueError("member_join event does not match committed GoalSession event")
        if committed.get("type") != "member_join":
            raise ValueError("committed GoalSession event is not member_join")

    def activate_joined_member(
        self,
        event: Mapping[str, Any],
        *,
        session_event_index: int,
        time_s: float,
    ) -> dict[str, Any]:
        """Host-only activation bound to a committed GoalSession member_join event.

        Returns a profile delta that contains only the newly public member's
        sensors. Idempotent for the same ``session_event_index``.
        """
        if self._episode_finalized or self._closed:
            raise RuntimeError("camera tools are finalized; cannot activate joins")
        if type(session_event_index) is not int or isinstance(session_event_index, bool):
            raise ValueError("session_event_index must be an int")
        if session_event_index < 0:
            raise ValueError("session_event_index must be nonnegative")
        self._validate_bound_join_event(event, session_event_index)
        if not isinstance(event, Mapping) or event.get("type") != "member_join":
            raise ValueError("activation requires a member_join session event")
        payload = event.get("payload")
        if not isinstance(payload, Mapping) or "agent_id" not in payload:
            raise ValueError("member_join payload must include agent_id")
        agent_id = payload["agent_id"]
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("member_join agent_id must be nonempty")
        now = self._validate_time(time_s)
        self._now = now

        if session_event_index in self._activated_join_indices:
            # Idempotent replay of the same committed event index.
            return self._profile_delta_for(agent_id, session_event_index=session_event_index,
                                           already_active=True)

        if agent_id not in self._private_sensor_registry:
            # Do not echo the unknown ID into public profiles; reject only.
            raise ValueError(
                "member_join agent is not present in the trusted private sensor registry")

        newly_public = agent_id not in self._public_sensors
        if newly_public:
            self._public_sensors[agent_id] = deepcopy(
                self._private_sensor_registry[agent_id])
            self.sensor_catalog = self._public_sensors
        self._known_robot_ids.add(agent_id)
        self._activated_join_indices.add(session_event_index)
        delta = self._profile_delta_for(
            agent_id, session_event_index=session_event_index, already_active=False)
        self._control("camera_tools_profile_delta", delta, now)
        return delta

    def _profile_delta_for(
        self,
        agent_id: str,
        *,
        session_event_index: int,
        already_active: bool,
    ) -> dict[str, Any]:
        sensors = {
            agent_id: {
                sensor_id: {
                    "member": profile["member"],
                    "modalities": list(profile["modalities"]),
                    "encodings": list(profile["encodings"]),
                    "max_width": profile["max_width"],
                    "max_height": profile["max_height"],
                    "max_bytes": profile["max_bytes"],
                }
                for sensor_id, profile in self._public_sensors[agent_id].items()
            }
        }
        profile = self.public_profile()
        return {
            "schema": "eai.benchmark.goal_camera_tools_profile_delta",
            "version": 1,
            "episode_id": self.episode_id,
            "public_task_sha256": self.public_task_sha256,
            "session_event_index": session_event_index,
            "joined_robot_id": agent_id,
            "robot_ids_added": [agent_id],
            "sensors": sensors,
            "simulation_budget_s": self._simulation_budget_s,
            "public_profile_sha256": canonical_sha256(profile),
            "already_active": already_active,
            "execution_validated": False,
            "scoreable": False,
        }

    def attach_lifecycle_report(self, report: Mapping[str, Any]) -> None:
        if not isinstance(report, Mapping):
            raise ValueError("lifecycle report must be an object")
        failures = list(report.get("failures", []))
        self._lifecycle_report = {
            "render_product_active_s": float(report.get("render_product_active_s", 0.0)),
            "create_count": int(report.get("create_count", 0)),
            "destroy_count": int(report.get("destroy_count", 0)),
            "warmup_frames": int(report.get("warmup_frames", 0)),
            "first_frame_latency_s": list(report.get("first_frame_latency_s", [])),
            "native_orsus_rtx_verified": bool(report.get("native_orsus_rtx_verified", False)),
            "failures": deepcopy(failures),
            "cleanup_pending": bool(report.get("cleanup_pending", False)),
            "cleanup_complete": bool(report.get("cleanup_complete", True)),
        }

    def _control(self, kind: str, data: Mapping[str, Any], now: float) -> None:
        self._control_events.append({
            "message_id": f"{self.episode_id}:camera-control:{self._event_index}",
            "producer_id": "benchmark_camera_tools",
            "kind": kind,
            "data": deepcopy(dict(data)),
            "valid_until_s": now + self.validity_s,
        })
        payload = self._control_events[-1]
        size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8"))
        self._control_ledger.append({
            "payload": deepcopy(payload),
            "created_at_s": now,
            "size_bytes": size,
            "accounting_scope": "benchmark_to_model_control_not_radio_transport",
        })
        self._event_index += 1

    def _validate_time(self, time_s: float) -> float:
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s) or time_s < self._now):
            raise ValueError("camera tool time must be finite and monotonic")
        return float(time_s)

    def _reject(self, call_id: str, tool: str, code: str, now: float, **extra: Any) -> dict[str, Any]:
        if code not in _ERROR_CODES:
            raise ValueError(f"unknown camera error code: {code}")
        row = self._calls[call_id]
        row.update(status="rejected", error_code=code, **extra)
        self._control("camera_tool_result", {
            "request_id": call_id, "tool": tool, "accepted": False, "error_code": code, **extra,
        }, now)
        return {"accepted": False, "request_id": call_id, "tool": tool,
                "error_code": code, "execution_validated": False}

    def _sensor_key(self, robot_id: str, sensor_id: str) -> tuple[str, str]:
        return (robot_id, sensor_id)

    def _capture_key(self, robot_id: str, sensor_id: str, width: int, height: int,
                     encoding: str, capture_sim_time_s: float) -> str:
        return "|".join([
            robot_id, sensor_id, str(width), str(height), encoding,
            f"{capture_sim_time_s:.9f}",
        ])

    def _is_terminal(self, request_id: str) -> bool:
        row = self._requests.get(request_id)
        return bool(row and row.get("terminal"))

    def _finalize(self, request_id: str, status: str, now: float, *,
                  error_code: str | None = None, reason: str | None = None,
                  message_id: str | None = None) -> bool:
        """Mark exactly one terminal status; return False if already terminal."""
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {status}")
        row = self._requests[request_id]
        if row.get("terminal"):
            return False
        row["terminal"] = True
        row["status"] = status
        row["terminal_at_s"] = now
        if error_code is not None:
            row["error_code"] = error_code
        if reason is not None:
            row["reason"] = reason
        if message_id is not None:
            row["message_id"] = message_id
            if status != "delivered":
                self._suppressed_messages.add(message_id)
        return True

    def _build_estimate(self, *, request_id: str, frame: Mapping[str, Any],
                        pending: Mapping[str, Any], raw: bytes, now: float,
                        cache_hit: bool) -> tuple[dict[str, Any], int, str]:
        raw_len = len(raw)
        if raw_len > pending["max_bytes"]:
            raise ValueError("captured frame exceeds request max_bytes")
        remaining = self.max_payload_bytes_per_episode - self._payload_bytes_spent
        if raw_len > remaining:
            raise ValueError("episode raw payload byte budget exceeded")
        digest = hashlib.sha256(raw).hexdigest()
        payload_b64 = b64encode(raw).decode("ascii")
        estimate = {
            "request_id": request_id,
            "frame_id": frame["frame_id"],
            "capture_sim_time_s": now,
            "physics_step": frame["physics_step"],
            "pose": deepcopy(dict(frame["pose"])),
            "calibration": deepcopy(dict(frame["calibration"])),
            "encoding": pending["encoding"],
            "payload_b64": payload_b64,
            "payload_sha256": digest,
            "byte_length": raw_len,
            "cache_hit": cache_hit,
            "status": frame.get("status", "fresh"),
        }
        # First edition never exposes internal payload_ref paths to the model.
        transport = estimate_camera_transport_size(estimate)
        if transport <= 0:
            raise ValueError("transport size probe failed")
        return estimate, transport, digest

    def submit(self, call: Mapping[str, Any], *, time_s: float,
               current_agents: set[str] | None = None) -> dict[str, Any]:
        # Reject before writing the call ledger once the episode is finalized.
        if self._episode_finalized or self._closed:
            raise RuntimeError("camera tools are finalized; submissions are rejected")
        now = self._validate_time(time_s)
        fields = {"schema", "version", "request_id", "submitted_at_s", "tool", "arguments"}
        if (not isinstance(call, Mapping) or set(call) != fields
                or call["schema"] != CAMERA_TOOL_SCHEMA or type(call["version"]) is not int
                or call["version"] != 1):
            raise ValueError("invalid camera tool call envelope")
        request_id = call["request_id"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("camera tool request_id must be nonempty")
        if request_id in self._calls:
            raise ValueError("camera tool request_id must be new and nonempty")
        if (isinstance(call["submitted_at_s"], bool)
                or not isinstance(call["submitted_at_s"], (int, float))
                or call["submitted_at_s"] != now):
            raise ValueError("camera tool submission must match current episode time")
        name, arguments = call["tool"], call["arguments"]
        if not isinstance(name, str) or name not in _TOOLS:
            raise ValueError("unknown camera tool")
        if not isinstance(arguments, Mapping):
            raise ValueError("camera tool arguments must be an object")
        canonical = json.dumps(dict(call), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        row = {
            "call": deepcopy(dict(call)),
            "received_at_s": now,
            "size_bytes": len(canonical),
            "status": "received",
            "accounting_scope": "model_to_benchmark_ingress_not_radio_transport",
        }
        self._calls[request_id] = row
        self._now = now
        self.telemetry.advance(now)

        if name == "cancel_request":
            if set(arguments) != {"request_id"}:
                return self._reject(request_id, name, "invalid_arguments", now)
            target = arguments["request_id"]
            pending = self._pending.get(target)
            awaiting = self._awaiting_delivery.get(target)
            if pending is None and awaiting is None:
                return self._reject(request_id, name, "not_found", now)
            message_id = None
            if pending is not None:
                key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
                self._outstanding.get(key, set()).discard(target)
                self._pending.pop(target, None)
            if awaiting is not None:
                message_id = awaiting["message_id"]
                self._awaiting_delivery.pop(target, None)
            self._finalize(target, "cancelled", now, error_code="cancelled",
                           reason="cancelled_by_request", message_id=message_id)
            row["status"] = "accepted"
            self._control("camera_tool_result", {
                "request_id": request_id, "tool": name, "accepted": True,
                "cancelled_request_id": target,
            }, now)
            self._control("camera_request_cancelled", {
                "request_id": target, "reason": "cancelled_by_request",
                "time_s": now, "cancelled_by": request_id,
            }, now)
            return {"accepted": True, "request_id": request_id, "tool": name,
                    "execution_validated": False}

        if "requested_sim_time_s" in arguments:
            return self._reject(request_id, name, "unsupported_field", now,
                                field="requested_sim_time_s")
        if set(arguments) != _REQUEST_ARGUMENT_FIELDS:
            return self._reject(request_id, name, "invalid_arguments", now)
        if now >= self._simulation_budget_s:
            return self._reject(request_id, name, "episode_budget_ended", now)

        robot_id = arguments["robot_id"]
        sensor_id = arguments["sensor_id"]
        if not isinstance(robot_id, str) or robot_id not in self._known_robot_ids:
            return self._reject(request_id, name, "unknown_robot", now)
        if current_agents is not None and robot_id not in current_agents:
            return self._reject(request_id, name, "inactive_robot", now)
        profile = self._public_sensors.get(robot_id, {}).get(sensor_id)
        if profile is None:
            return self._reject(request_id, name, "unknown_sensor", now)

        modalities = arguments["modalities"]
        if (not isinstance(modalities, list) or modalities != ["rgb"]):
            return self._reject(request_id, name, "invalid_modality", now)

        resolution = arguments["resolution"]
        if (not isinstance(resolution, Mapping)
                or set(resolution) != {"width", "height"}):
            return self._reject(request_id, name, "invalid_arguments", now)
        try:
            width = _positive_int(resolution["width"], "width")
            height = _positive_int(resolution["height"], "height")
        except ValueError:
            return self._reject(request_id, name, "invalid_arguments", now)
        if width > profile["max_width"] or height > profile["max_height"]:
            return self._reject(request_id, name, "resolution_exceeded", now)

        quality = arguments["quality"]
        if not isinstance(quality, Mapping) or set(quality) != _QUALITY_FIELDS:
            return self._reject(request_id, name, "invalid_arguments", now)
        encoding = quality["encoding"]
        if encoding not in profile["encodings"]:
            return self._reject(request_id, name, "invalid_encoding", now)
        try:
            max_bytes = _positive_int(quality["max_bytes"], "max_bytes")
        except ValueError:
            return self._reject(request_id, name, "invalid_arguments", now)
        if max_bytes > profile["max_bytes"]:
            return self._reject(request_id, name, "budget_exceeded", now)

        freshness = arguments["freshness"]
        if freshness not in _FRESHNESS:
            return self._reject(request_id, name, "invalid_arguments", now)

        try:
            deadline = _finite(arguments["deadline_s"], "deadline_s", nonnegative=True)
        except ValueError:
            return self._reject(request_id, name, "invalid_deadline", now)
        if deadline <= now:
            return self._reject(request_id, name, "invalid_deadline", now)
        # Trusted host simulation budget caps every request deadline.
        if deadline > self._simulation_budget_s:
            return self._reject(request_id, name, "invalid_deadline", now,
                                reason="deadline_exceeds_simulation_budget")

        if self._request_count >= self.max_requests_per_episode:
            return self._reject(request_id, name, "budget_exceeded", now)
        if self._payload_bytes_spent >= self.max_payload_bytes_per_episode:
            return self._reject(request_id, name, "budget_exceeded", now)

        key = self._sensor_key(robot_id, sensor_id)
        outstanding = self._outstanding.setdefault(key, set())
        if len(outstanding) >= self.max_outstanding_per_sensor:
            return self._reject(request_id, name, "busy", now)

        outstanding.add(request_id)
        self._request_count += 1
        pending = {
            "request_id": request_id,
            "robot_id": robot_id,
            "sensor_id": sensor_id,
            "member": profile["member"],
            "modalities": ["rgb"],
            "width": width,
            "height": height,
            "encoding": encoding,
            "max_bytes": max_bytes,
            "freshness": freshness,
            "deadline_s": deadline,
            "submitted_at_s": now,
            "status": "accepted",
            "cancelled": False,
            "terminal": False,
        }
        self._pending[request_id] = pending
        self._requests[request_id] = deepcopy(pending)
        row["status"] = "accepted"
        self._control("camera_tool_result", {
            "request_id": request_id, "tool": name, "accepted": True,
            "response_deadline_s": deadline,
        }, now)
        return {"accepted": True, "request_id": request_id, "tool": name,
                "execution_validated": False}

    def _deliver_frame(self, pending: Mapping[str, Any], frame: Mapping[str, Any],
                       now: float, *, cache_hit: bool, raw: bytes,
                       cache_key: str | None = None,
                       cache_entry: Mapping[str, Any] | None = None) -> None:
        request_id = pending["request_id"]
        estimate, transport, digest = self._build_estimate(
            request_id=request_id, frame=frame, pending=pending, raw=raw,
            now=now, cache_hit=cache_hit)
        # Publish first; only cache after CommunicationRuntime accepts the message.
        record = self.telemetry.publish(
            pending["robot_id"], estimate, valid_until_s=now + self.validity_s)
        if cache_key is not None and cache_entry is not None and not cache_hit:
            self._cache[cache_key] = deepcopy(dict(cache_entry))
        self._payload_bytes_spent += estimate["byte_length"]
        self._transport_bytes_published += record["size_bytes"]
        message_id = record["payload"]["message_id"]
        self._requests[request_id].update({
            "status": "published",
            "message_id": message_id,
            "cache_hit": cache_hit,
            "byte_length": estimate["byte_length"],
            "payload_sha256": digest,
            "transport_size_bytes": record["size_bytes"],
            "estimated_transport_size_bytes": transport,
            "capture_sim_time_s": now,
        })
        self._awaiting_delivery[request_id] = {
            "request_id": request_id,
            "robot_id": pending["robot_id"],
            "message_id": message_id,
            "deadline_s": pending["deadline_s"],
        }

    def _cancel_inactive(self, request_id: str, robot_id: str, now: float,
                         *, message_id: str | None = None) -> None:
        if not self._finalize(request_id, "cancelled", now, error_code="inactive_robot",
                              reason="inactive_robot", message_id=message_id):
            return
        key = self._sensor_key(robot_id, self._requests[request_id]["sensor_id"])
        self._outstanding.get(key, set()).discard(request_id)
        self._control("camera_request_cancelled", {
            "request_id": request_id, "reason": "inactive_robot", "time_s": now,
        }, now)

    def tick(self, time_s: float, *, current_agents: set[str] | None = None) -> list[dict[str, Any]]:
        if self._closed and self._episode_finalized:
            # Allow a final post-finalize drain only if not tools-closed for capture;
            # once closed, tick is a no-op observation drain of leftover control only.
            raise RuntimeError("camera tools are closed")
        if self._episode_finalized:
            raise RuntimeError("camera tools are finalized")
        now = self._validate_time(time_s)
        self._now = now
        ready: list[dict[str, Any]] = []
        ready.extend(self.telemetry.advance(now))

        for request_id, pending in list(self._pending.items()):
            if self._is_terminal(request_id):
                self._pending.pop(request_id, None)
                continue
            if current_agents is not None and pending["robot_id"] not in current_agents:
                self._pending.pop(request_id, None)
                self._cancel_inactive(request_id, pending["robot_id"], now)
                continue
            if now >= pending["deadline_s"]:
                self._pending.pop(request_id, None)
                key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
                self._outstanding.get(key, set()).discard(request_id)
                if self._finalize(request_id, "timeout", now, error_code="timeout",
                                  reason="deadline_exceeded"):
                    self._control("camera_request_timeout", {
                        "request_id": request_id,
                        "response_deadline_s": pending["deadline_s"],
                        "time_s": now,
                    }, now)
                continue

            try:
                cache_key = self._capture_key(
                    pending["robot_id"], pending["sensor_id"],
                    pending["width"], pending["height"], pending["encoding"], now)
                if cache_key in self._cache:
                    cached = self._cache[cache_key]
                    raw = bytes(cached["payload"])
                    frame = {
                        "frame_id": cached["frame_id"],
                        "physics_step": cached["physics_step"],
                        "pose": deepcopy(dict(cached["pose"])),
                        "calibration": deepcopy(dict(cached["calibration"])),
                        "status": cached.get("status", "fresh"),
                    }
                    # Re-validate budgets before remetering a cache hit.
                    self._build_estimate(
                        request_id=request_id, frame=frame, pending=pending,
                        raw=raw, now=now, cache_hit=True)
                    self._deliver_frame(pending, frame, now, cache_hit=True, raw=raw)
                else:
                    request_view = {
                        "request_id": request_id,
                        "robot_id": pending["robot_id"],
                        "sensor_id": pending["sensor_id"],
                        "member": pending["member"],
                        "modalities": list(pending["modalities"]),
                        "width": pending["width"],
                        "height": pending["height"],
                        "encoding": pending["encoding"],
                        "max_bytes": pending["max_bytes"],
                        "freshness": pending["freshness"],
                        "submitted_at_s": pending["submitted_at_s"],
                    }
                    probe = self._capture(request_view, now)
                    if not isinstance(probe, Mapping):
                        raise ValueError("capture must return an object")
                    capture_time = probe.get("capture_sim_time_s", now)
                    if capture_time != now:
                        raise ValueError("trusted capture clock mismatch")
                    required = {
                        "frame_id", "physics_step", "pose", "calibration", "payload",
                    }
                    if not required <= set(probe):
                        raise ValueError("capture missing required frame fields")
                    if any(key.casefold() in {
                        "ground_truth", "gt", "hidden_answer", "oracle", "task_answer",
                    } for key in probe):
                        raise ValueError("capture must not include hidden ground truth")
                    raw = probe["payload"]
                    if not isinstance(raw, (bytes, bytearray)):
                        raise ValueError("capture payload must be bytes")
                    raw = bytes(raw)
                    # Validate raw size before cache allocation / base64.
                    frame = {
                        "frame_id": probe["frame_id"],
                        "physics_step": probe["physics_step"],
                        "pose": deepcopy(dict(probe["pose"])),
                        "calibration": deepcopy(dict(probe["calibration"])),
                        "status": probe.get("status", "fresh"),
                    }
                    self._build_estimate(
                        request_id=request_id, frame=frame, pending=pending,
                        raw=raw, now=now, cache_hit=False)
                    cache_entry = {
                        "frame_id": frame["frame_id"],
                        "physics_step": frame["physics_step"],
                        "pose": deepcopy(dict(frame["pose"])),
                        "calibration": deepcopy(dict(frame["calibration"])),
                        "payload": raw,
                        "status": frame["status"],
                        "capture_sim_time_s": capture_time,
                    }
                    self._deliver_frame(
                        pending, frame, now, cache_hit=False, raw=raw,
                        cache_key=cache_key, cache_entry=cache_entry)
            except Exception as exc:
                key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
                self._outstanding.get(key, set()).discard(request_id)
                reason = f"{type(exc).__name__}: {exc}"
                code = "publish_failed" if (
                    "payload_ref" in reason
                    or "duplicate message" in reason
                    or "must equal the current communication" in reason
                ) else "capture_failed"
                if self._finalize(request_id, "failed", now, error_code=code, reason=reason):
                    self._control("camera_request_failed", {
                        "request_id": request_id, "reason": code, "time_s": now,
                    }, now)
            else:
                key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
                self._outstanding.get(key, set()).discard(request_id)
            self._pending.pop(request_id, None)

        ready.extend(self.telemetry.advance(now))
        delivered_by_message = {
            row["message_id"]: row for row in ready if row.get("kind") == "camera_frame"
        }

        for request_id, waiting in list(self._awaiting_delivery.items()):
            if current_agents is not None and waiting["robot_id"] not in current_agents:
                self._awaiting_delivery.pop(request_id, None)
                self._cancel_inactive(
                    request_id, waiting["robot_id"], now,
                    message_id=waiting["message_id"])
                continue
            if now >= waiting["deadline_s"]:
                self._awaiting_delivery.pop(request_id, None)
                if self._finalize(request_id, "timeout", now, error_code="timeout",
                                  reason="deadline_exceeded",
                                  message_id=waiting["message_id"]):
                    self._control("camera_request_timeout", {
                        "request_id": request_id,
                        "response_deadline_s": waiting["deadline_s"],
                        "time_s": now,
                    }, now)
                continue
            if waiting["message_id"] in delivered_by_message:
                self._awaiting_delivery.pop(request_id, None)
                self._finalize(request_id, "delivered", now,
                               message_id=waiting["message_id"])

        exposed: list[dict[str, Any]] = []
        for row in ready:
            if row.get("kind") != "camera_frame":
                if now < row["valid_until_s"]:
                    exposed.append(row)
                continue
            message_id = row["message_id"]
            request_id = row.get("data", {}).get("request_id")
            if message_id in self._suppressed_messages:
                continue
            req = self._requests.get(request_id, {})
            if req.get("status") != "delivered" or req.get("message_id") != message_id:
                continue
            if now < row["valid_until_s"]:
                exposed.append(row)

        # Housekeeping after public observations are formed so destroy failures
        # cannot erase an already delivered frame from this tick's return.
        if self._backend is not None:
            try:
                self._backend.tick(now)
                self.attach_lifecycle_report(self._backend.lifecycle_report())
            except Exception as exc:
                error = {
                    "phase": "tick_idle_release",
                    "error": f"{type(exc).__name__}: {exc}",
                    "time_s": now,
                }
                self._lifecycle_errors.append(error)
                try:
                    self.attach_lifecycle_report(self._backend.lifecycle_report())
                except Exception:
                    pass
                self._control("camera_lifecycle_error", error, now)

        control, self._control_events = self._control_events, []
        return [*control, *exposed]

    def next_wakeup_s(self) -> float | None:
        """Earliest deadline or transport service/delivery/expiry wake time.

        Never busy-polls with a fixed 50 ms step. Queued bandwidth-limited
        messages jump to CommunicationRuntime's next scheduled event.
        """
        candidates: list[float] = []
        for pending in self._pending.values():
            if not self._is_terminal(pending["request_id"]):
                candidates.append(float(pending["deadline_s"]))
        for waiting in self._awaiting_delivery.values():
            candidates.append(float(waiting["deadline_s"]))
            transport = self.telemetry.next_transport_event_s()
            if transport is not None and transport > self._now:
                candidates.append(float(transport))
            elif transport is not None and transport == self._now:
                # Event due now still needs a forward tick from the runner.
                candidates.append(float(transport) + 1e-9)
        # Also wake for any owned in-transit delivery time from the report.
        report = self.telemetry.communication_report()
        active_messages = {
            waiting["message_id"] for waiting in self._awaiting_delivery.values()
        }
        for message_id, message in report["messages"].items():
            if message_id not in active_messages:
                continue
            for delivery in message["recipients"].values():
                scheduled = delivery.get("scheduled_delivery_at_s")
                if delivery.get("status") == "in_transit" and scheduled is not None:
                    candidates.append(float(scheduled))
        if not candidates:
            return None
        return float(min(candidates))

    def clear_episode_cache(self) -> None:
        self._cache.clear()

    def finalize_episode(self, time_s: float | None = None) -> list[dict[str, Any]]:
        """Terminalize every accepted/published request at episode end.

        Safe to call multiple times. Preserves the request ledger for archive.
        After this returns, submit/cancel/profile activation are rejected.
        """
        now = self._now if time_s is None else float(time_s)
        if time_s is not None:
            if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                    or not math.isfinite(time_s) or time_s < self._now):
                raise ValueError("camera tool time must be finite and monotonic")
            now = float(time_s)
        self._now = now
        self.telemetry.advance(now)
        if self._episode_finalized:
            self._terminalize_orphaned_requests(now)
            events, self._control_events = self._control_events, []
            return events
        for request_id, pending in list(self._pending.items()):
            self._pending.pop(request_id, None)
            key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
            self._outstanding.get(key, set()).discard(request_id)
            if self._finalize(request_id, "episode_ended", now,
                              error_code="episode_ended", reason="episode_ended"):
                self._control("camera_request_cancelled", {
                    "request_id": request_id, "reason": "episode_ended", "time_s": now,
                }, now)
        for request_id, waiting in list(self._awaiting_delivery.items()):
            self._awaiting_delivery.pop(request_id, None)
            if self._finalize(request_id, "episode_ended", now,
                              error_code="episode_ended", reason="episode_ended",
                              message_id=waiting["message_id"]):
                self._control("camera_request_cancelled", {
                    "request_id": request_id, "reason": "episode_ended", "time_s": now,
                }, now)
        self._terminalize_orphaned_requests(now)
        self._episode_finalized = True
        events, self._control_events = self._control_events, []
        return events

    def _terminalize_orphaned_requests(self, now: float) -> None:
        """Force every non-terminal request ledger row into episode_ended."""
        for request_id, row in list(self._requests.items()):
            if row.get("terminal"):
                continue
            message_id = row.get("message_id")
            self._pending.pop(request_id, None)
            self._awaiting_delivery.pop(request_id, None)
            if self._finalize(request_id, "episode_ended", now,
                              error_code="episode_ended", reason="episode_ended",
                              message_id=message_id if isinstance(message_id, str) else None):
                self._control("camera_request_cancelled", {
                    "request_id": request_id, "reason": "episode_ended", "time_s": now,
                }, now)

    def report(self) -> dict[str, Any]:
        return {
            "schema": "eai.benchmark.goal_camera_tools_report",
            "version": 1,
            "episode_id": self.episode_id,
            "profile": self.public_profile(),
            "calls": deepcopy(list(self._calls.values())),
            "requests": deepcopy(list(self._requests.values())),
            "control_responses": deepcopy(self._control_ledger),
            "communication": self.telemetry.communication_report(),
            "payload_bytes_spent": self._payload_bytes_spent,
            "transport_bytes_published": self._transport_bytes_published,
            "request_count": self._request_count,
            "cache_entries": len(self._cache),
            "render_lifecycle": deepcopy(self._lifecycle_report),
            "lifecycle_errors": deepcopy(self._lifecycle_errors),
            "episode_finalized": self._episode_finalized,
            "closed": self._closed,
            "execution_validated": False,
            "scoreable": False,
        }

    def has_pending_work(self) -> bool:
        """True while an accepted request still awaits capture or in-deadline delivery."""
        return bool(self._pending or self._awaiting_delivery)

    def close(self) -> None:
        """Idempotent shutdown: terminalize unfinished work, keep ledger, retry cleanup.

        Always scans the request ledger for non-terminal rows even if
        ``finalize_episode`` already ran. Backend cleanup may be retried on
        subsequent ``close`` calls until owned products are released.
        """
        self._terminalize_orphaned_requests(self._now)
        for request_id, pending in list(self._pending.items()):
            self._pending.pop(request_id, None)
            key = self._sensor_key(pending["robot_id"], pending["sensor_id"])
            self._outstanding.get(key, set()).discard(request_id)
            if self._finalize(request_id, "episode_ended", self._now,
                              error_code="episode_ended", reason="episode_ended"):
                self._control("camera_request_cancelled", {
                    "request_id": request_id, "reason": "episode_ended",
                    "time_s": self._now,
                }, self._now)
        for request_id, waiting in list(self._awaiting_delivery.items()):
            self._awaiting_delivery.pop(request_id, None)
            if self._finalize(request_id, "episode_ended", self._now,
                              error_code="episode_ended", reason="episode_ended",
                              message_id=waiting["message_id"]):
                self._control("camera_request_cancelled", {
                    "request_id": request_id, "reason": "episode_ended",
                    "time_s": self._now,
                }, self._now)
        self._episode_finalized = True
        self.clear_episode_cache()
        self._pending.clear()
        self._awaiting_delivery.clear()
        self._outstanding.clear()
        # Drain any control events produced by terminalization into the ledger only.
        if self._control_events:
            for payload in self._control_events:
                size = len(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode("utf-8"))
                self._control_ledger.append({
                    "payload": deepcopy(payload),
                    "created_at_s": self._now,
                    "size_bytes": size,
                    "accounting_scope": "benchmark_to_model_control_not_radio_transport",
                })
            self._control_events = []
        self._closed = True
        if self._backend is not None:
            try:
                self._backend.close(now_s=self._now)
                self.attach_lifecycle_report(self._backend.lifecycle_report())
            except Exception as exc:
                error = {
                    "phase": "close",
                    "error": f"{type(exc).__name__}: {exc}",
                    "time_s": self._now,
                }
                self._lifecycle_errors.append(error)
                try:
                    self.attach_lifecycle_report(self._backend.lifecycle_report())
                except Exception:
                    pass
