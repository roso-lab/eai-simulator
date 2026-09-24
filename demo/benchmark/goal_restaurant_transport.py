"""Trusted, incremental readiness-v2 transport for a native Restaurant worker.

The worker supplies measured physics stamps and observations; participants do
not call this object. It uses the SAME CommunicationRuntime as telemetry and
commands, so a receipt cannot bypass contention, delay, loss or TTL. This is
an opt-in execution primitive, not a native entrypoint or a physical verifier.
The independent business-v2 verifier still checks physical/session evidence.
No task decomposition, producer choice or ordering is supplied here.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from typing import Any

from .communication import CommunicationRuntime
from .goal_restaurant_business_v2 import RELAY_CHANNEL, _relay_wire
from .provenance import canonical_sha256
from demo.restaurant_benchmark.service_backend_v2 import RestaurantReadinessBackendV2


SERVICE_RECIPIENT = "restaurant_opening_service"


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _number(value: Any, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _sealed(row: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(row))
    result["evidence_ref"] = "sha256:" + canonical_sha256(result)
    return result


class RestaurantReadinessTransport:
    """Native observation → metered relay → delivered terminal receipt.

    ``poll_relay`` never steps the simulator or waits for the link. The caller
    pumps native steps/events and retries with a fresh measured stamp. A
    dropped/expired publication remains archived and cannot yield a receipt;
    another participant dispatch may publish a new ID. Only one terminal
    readiness receipt may be committed per episode.
    """

    def __init__(self, *, episode_id: str, runtime: CommunicationRuntime,
                 robot_bindings: Mapping[str, Mapping[str, Any]],
                 episode_step_origin: int, physics_dt_s: float,
                 service_recipient: str = SERVICE_RECIPIENT):
        self.episode_id = _text(episode_id, "episode_id")
        if not isinstance(runtime, CommunicationRuntime):
            raise ValueError("a shared CommunicationRuntime is required")
        if runtime.time_s != 0.0:
            raise ValueError("shared communication runtime must start at the native episode epoch")
        contract = runtime.report()["contract"]
        if ((contract.get("pools") or contract.get("access"))
                and not {"backhaul_uplink", "backhaul"}.intersection(contract.get("pools", {}))):
            raise ValueError("readiness service requires a configured backhaul uplink pool")
        if (type(episode_step_origin) is not int or episode_step_origin < 0
                or _number(physics_dt_s, "physics_dt_s") <= 0):
            raise ValueError("invalid native physics epoch")
        if not isinstance(robot_bindings, Mapping) or not robot_bindings:
            raise ValueError("trusted robot bindings are required")
        self._bindings = deepcopy(dict(robot_bindings))
        native_ids = []
        for producer, binding in self._bindings.items():
            _text(producer, "producer_id")
            if not isinstance(binding, Mapping):
                raise ValueError("robot binding must be an object")
            native_ids.append(_text(binding.get("native_agent_id"), "native_agent_id"))
        if len(native_ids) != len(set(native_ids)):
            raise ValueError("native bindings must be one-to-one")
        self._runtime = runtime
        self.service_recipient = _text(service_recipient, "service_recipient")
        if self.service_recipient in self._bindings:
            raise ValueError("readiness recipient must be a service, not a robot")
        self._origin = episode_step_origin
        self._physics_dt = float(physics_dt_s)
        self._last_step = episode_step_origin
        self._service = RestaurantReadinessBackendV2(episode_id=episode_id)
        self._evidence: list[dict[str, Any]] = []
        self._relays: dict[str, dict[str, Any]] = {}

    def _stamp(self, stamp: Mapping[str, Any], producer_id: str) -> dict[str, Any]:
        if not isinstance(stamp, Mapping):
            raise ValueError("native stamp must be an object")
        if (stamp.get("producer_id") != producer_id or stamp.get("native_agent_id")
                != self._bindings[producer_id]["native_agent_id"]):
            raise ValueError("native sample producer/articulation differs from its binding")
        step = stamp.get("simulation_step")
        if type(step) is not int or step < self._last_step:
            raise ValueError("native physics step regressed or is invalid")
        time = _number(stamp.get("time_s"), "native time")
        expected = (step - self._origin) * self._physics_dt
        if time < 0 or not math.isclose(time, expected, rel_tol=0, abs_tol=1e-9):
            raise ValueError("native stamp differs from the shared physics epoch")
        if time < self._runtime.time_s:
            raise ValueError("native stamp precedes shared communication clock")
        root = stamp.get("root_state_w")
        if not isinstance(root, (list, tuple)) or len(root) != 13:
            raise ValueError("native stamp requires a complete measured root state")
        for value in root:
            _number(value, "root state")
        if not math.isclose(sum(float(v) ** 2 for v in root[3:7]), 1.0,
                            rel_tol=0, abs_tol=1e-3):
            raise ValueError("native root orientation is not a unit quaternion")
        # Identity is retained on the enclosing native evidence record. Keep
        # the established v2 stamp schema for independent archive replay.
        return deepcopy({key: value for key, value in stamp.items()
                         if key not in {"producer_id", "native_agent_id"}})

    def _identity(self, task_id: str, producer_id: str) -> dict[str, str]:
        _text(task_id, "task_id")
        _text(producer_id, "producer_id")
        if producer_id not in self._bindings:
            raise ValueError("producer has no trusted native binding")
        return {"episode_id": self.episode_id, "task_id": task_id,
                "producer_id": producer_id,
                "native_agent_id": self._bindings[producer_id]["native_agent_id"]}

    def record_observation(self, *, task_id: str, producer_id: str,
                           observation_id: str,
                           scene_observation: Mapping[str, Any]) -> dict[str, Any]:
        """Archive an already measured scene query, without claiming its truth."""
        identity = self._identity(task_id, producer_id)
        scene = deepcopy(dict(scene_observation))
        if scene.get("producer_id") != producer_id:
            raise ValueError("scene observation producer differs from native binding")
        stamp = self._stamp(scene.get("stamp"), producer_id)
        scene["stamp"] = stamp
        region = _text(scene.get("region_id"), "observation region")
        digest = "sha256:" + canonical_sha256({
            "episode_id": self.episode_id, "region_id": region,
            "producer_id": producer_id, "scene_observation": scene})
        service_row = self._service.record_observation(
            region_id=region, observation_id=observation_id,
            simulation_step=stamp["simulation_step"], time_s=stamp["time_s"],
            producer_id=producer_id, evidence_digest=digest)
        native = _sealed({"kind": "observation", **identity, "region_id": region,
                          "stamp": stamp, "scene_observation": scene,
                          "service_record": service_row})
        self._evidence.append(native)
        self._last_step = stamp["simulation_step"]
        return deepcopy(native)

    def publish_relay(self, *, task_id: str, producer_id: str, message_id: str,
                      observation_refs: list[str], stamp: Mapping[str, Any],
                      valid_until_s: float) -> dict[str, Any]:
        identity = self._identity(task_id, producer_id)
        stamp = self._stamp(stamp, producer_id)
        _text(message_id, "message_id")
        expiry = _number(valid_until_s, "valid_until_s")
        if expiry <= stamp["time_s"]:
            raise ValueError("relay TTL must extend beyond publication")
        # Stage the service mutation; an invalid/duplicate shared-link ID must
        # not leave an orphan publication in the readiness service.
        staged = deepcopy(self._service)
        publication = staged.publish_relay(
            message_id=message_id, observation_refs=observation_refs,
            simulation_step=stamp["simulation_step"], time_s=stamp["time_s"])
        native = _sealed({"kind": "relay_publication", **identity,
                          "publication_stamp": stamp, "relay_publication": publication})
        wire = _relay_wire(episode_id=self.episode_id, producer=producer_id,
                           publication=publication,
                           source_evidence_ref=native["evidence_ref"])
        self._runtime.publish(
            message_id=message_id, producer_id=producer_id,
            recipients=[self.service_recipient], channel=RELAY_CHANNEL,
            estimate=wire, published_at_s=stamp["time_s"], valid_until_s=expiry,
            pool="backhaul_uplink", direction="uplink", access_robot_id=producer_id)
        self._service = staged
        self._relays[message_id] = native
        self._evidence.append(native)
        self._last_step = stamp["simulation_step"]
        return deepcopy(native)

    def poll_relay(self, *, message_id: str, stamp: Mapping[str, Any],
                   receipt_id: str) -> dict[str, Any]:
        """Read actual delivery; commit once at a strictly subsequent native tick."""
        _text(receipt_id, "receipt_id")
        if message_id not in self._relays:
            raise ValueError("unknown native relay")
        if self._service.snapshot()["receipt"] is not None:
            raise ValueError("readiness service already terminated")
        native = self._relays[message_id]
        stamp = self._stamp(stamp, native["producer_id"])
        self._runtime.advance(stamp["time_s"])
        self._last_step = stamp["simulation_step"]
        message = self._runtime.report()["messages"][message_id]
        delivery = message["recipients"][self.service_recipient]
        if delivery["status"] in {"dropped", "expired"}:
            return {"status": "failed", "reason": "relay_transport_" + (
                delivery.get("drop_reason") or delivery["status"]),
                    "message_id": message_id, "evidence_ref": native["evidence_ref"]}
        if (delivery["status"] != "delivered"
                or stamp["simulation_step"] <= native["publication_stamp"]["simulation_step"]):
            return {"status": "pending", "message_id": message_id}
        if delivery["consumption"] is not None:
            raise ValueError("readiness relay was already consumed outside its service")
        staged = deepcopy(self._service)
        receipt = staged.commit_readiness_receipt(
            receipt_id=receipt_id, simulation_step=stamp["simulation_step"],
            time_s=stamp["time_s"],
            observation_refs=native["relay_publication"]["observation_refs"],
            relay_message_id=message_id)
        self._runtime.consume(message_id, recipient=self.service_recipient,
                              consumer_id=receipt_id, time_s=stamp["time_s"])
        terminal = _sealed({
            "kind": "readiness_receipt", "episode_id": self.episode_id,
            "task_id": native["task_id"], "producer_id": native["producer_id"],
            "native_agent_id": native["native_agent_id"],
            "relay_evidence_ref": native["evidence_ref"], "receipt_stamp": stamp,
            "business_receipt": receipt})
        self._service = staged
        self._evidence.append(terminal)
        return {"status": "succeeded", "message_id": message_id,
                "native_evidence": deepcopy(terminal)}

    def snapshot(self) -> dict[str, Any]:
        """Evaluator archive only. Never expose transport state to participants."""
        return {"readiness_service": self._service.snapshot(),
                "native_evidence": deepcopy(self._evidence),
                "transport_report": self._runtime.report(),
                "execution_validated": False, "business_outcomes_validated": False,
                "scoreable": False}
