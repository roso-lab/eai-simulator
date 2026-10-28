"""Typed public boundary; execution and evaluator accounting live in benchmark."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping
from ..contracts import SkillRequest, SkillContractError
from .tools import CAMERA_TOOL_SCHEMA

SKILL_ID = "eai.observation.camera"
CAPTURE_TICKS = 5


def camera_call(request: SkillRequest, *, time_s: float) -> dict[str, Any]:
    """Translate one typed request without discarding unknown fields.

    Validation of rights, time, encoding and byte budgets stays in the ported
    tool state machine. Unsupported historical-time requests retain its
    ``unsupported_field`` result, instead of being silently ignored.
    """
    if not isinstance(request, SkillRequest) or request.skill_id != SKILL_ID:
        raise SkillContractError("expected eai.observation.camera SkillRequest")
    if request.skill_version != "simulator.skill.v1":
        raise SkillContractError("unsupported observation schema version")
    if request.target_ref is not None or request.target_pose is not None:
        raise SkillContractError("camera identity belongs in camera_ref; pose override is unsupported")
    params = deepcopy(dict(request.parameters))
    allowed = {"camera_ref", "mode", "deadline", "resolution", "quality", "requested_sim_time_s"}
    if set(params) - allowed:
        raise SkillContractError("undeclared observation parameters")
    if params.get("mode", "capture") != "capture":
        raise SkillContractError("only capture mode is supported")
    if not {"camera_ref", "deadline", "resolution", "quality"} <= set(params):
        raise SkillContractError("camera_ref, deadline, resolution and quality are required")
    if not isinstance(params["camera_ref"], str) or not params["camera_ref"].strip():
        raise SkillContractError("camera_ref must be a non-empty public sensor ID")
    arguments = {"robot_id": request.robot_id, "sensor_id": params["camera_ref"],
                 "modalities": ["rgb"], "deadline_s": params["deadline"],
                 "resolution": params["resolution"], "quality": params["quality"],
                 "freshness": "capture_at_or_after_submit"}
    if "requested_sim_time_s" in params:
        arguments["requested_sim_time_s"] = params["requested_sim_time_s"]
    return {"schema": CAMERA_TOOL_SCHEMA, "version": 1,
            "request_id": request.request_id, "submitted_at_s": time_s,
            "tool": "request_observation", "arguments": arguments}


class ObservationReceipt:
    def __init__(self, **values: Any):
        self.__dict__.update(values)

    def to_mapping(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


class CameraObservationRuntime:
    skill_id = SKILL_ID
    mode = "capture"

    def __init__(self, tools: Any, *, ledger: Any = None, artifact_store: Any = None):
        from .tools import GoalCameraTools
        if not isinstance(tools, GoalCameraTools):
            raise TypeError("tools must be GoalCameraTools")
        self.tools, self.ledger, self.artifact_store = tools, ledger, artifact_store
        self._last_time = 0.0

    def request(self, request: Mapping[str, Any], *, time_s: float, current_agents: set[str] | None = None) -> dict[str, Any]:
        if not isinstance(request, (SkillRequest, Mapping)):
            raise ValueError("observation request must be an object")
        typed = request if isinstance(request, SkillRequest) else SkillRequest.from_mapping(request)
        self._last_time = float(time_s)
        result = self.tools.submit(camera_call(typed, time_s=time_s), time_s=time_s, current_agents=current_agents)
        if result.get("accepted"):
            # Rejected requests leave no ledger row: nothing was metered.
            self._append("accepted", typed.request_id, result)
        return result

    def tick(self, time_s: float, *, current_agents: set[str] | None = None) -> list[dict[str, Any]]:
        self._last_time = float(time_s)
        events = self.tools.tick(time_s, current_agents=current_agents)
        for event in events:
            kind = event.get("kind")
            if kind in {"camera_request_timeout", "camera_request_cancelled", "camera_request_failed", "camera_frame"}:
                data = event.get("data", {})
                if kind == "camera_frame" and self.artifact_store is not None and isinstance(data.get("payload_b64"), str):
                    import base64
                    digest = self.artifact_store.put(base64.b64decode(data["payload_b64"].encode("ascii")))
                    if digest != data.get("payload_sha256"):
                        raise ValueError("camera payload digest does not match artifact bytes")
                self._append("delivered" if kind == "camera_frame" else kind, data.get("request_id"), event)
        return events

    def receipt(self, request_id: str) -> ObservationReceipt:
        rows = [row for row in self.tools.report()["requests"] if row["request_id"] == request_id]
        if not rows:
            raise KeyError(request_id)
        row = rows[0]
        digest = row.get("payload_sha256")
        # Only a delivered frame claims artifact-backed evidence; timed-out
        # rows may still carry the digest of bytes that were metered and
        # then suppressed, which must verify against nothing.
        if (self.artifact_store is not None and digest is not None
                and row.get("status") == "delivered"):
            try:
                stored = self.artifact_store.get(digest)
            except (KeyError, TypeError) as exc:
                # A receipt claiming delivered bytes must be backed by the
                # evaluator-side store; missing evidence fails closed.
                raise ValueError("observation artifact bytes missing for receipt") from exc
            if __import__("hashlib").sha256(stored).hexdigest() != digest:
                raise ValueError("observation artifact digest mismatch")
        return ObservationReceipt(request_id=request_id, status=row["status"], robot_id=row["robot_id"], sensor_id=row["sensor_id"], terminal=bool(row.get("terminal")), capture_sim_time_s=row.get("capture_sim_time_s"), frame_id=row.get("frame_id"), payload_sha256=row.get("payload_sha256"), byte_length=int(row.get("byte_length", 0)), transport_size_bytes=int(row.get("transport_size_bytes", 0)), message_id=row.get("message_id"), artifact_sha256=row.get("payload_sha256"))

    def _append(self, kind: str, request_id: str | None, payload: Mapping[str, Any]) -> None:
        if self.ledger is not None and request_id and callable(getattr(self.ledger, "append", None)):
            self.ledger.append(kind, tick=int(round(self._last_time)), source="observation.camera", payload=dict(payload))


__all__ = ["CAPTURE_TICKS", "SKILL_ID", "CameraObservationRuntime", "ObservationReceipt", "camera_call"]
