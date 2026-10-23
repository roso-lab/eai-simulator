"""Managed slot ownership and noninterference for a vectorized simulator.

The driver owns native tensors and physics. This guard owns generations, IO
routing and restoration checks. Native snapshots must cover every registered
participant; hashing does not prove unreported hidden state is covered.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import random
from typing import Any, Protocol

from .models import BranchJob, CheckpointArtifact, canonical_digest


SCHEMA = "heterocoord.vectorized_isolation.v1"
PARTICIPANTS = frozenset({
    "world", "clock", "controller", "skill", "task", "algorithm",
    "transport", "locks", "events", "rng", "checkpoint", "evaluator",
})


class IsolationError(RuntimeError):
    """A shared scene is quarantined after uncertain ownership or mutation."""


class VectorizedBackend(Protocol):
    """Native implementation boundary; static configuration is not certification."""
    vectorized_contract: str

    def open_scene(self, scene_id: str, *, num_envs: int, scheduler: Any) -> Any: ...
    def warmup(self, session: Any, *, ticks: int) -> None: ...
    def isolation_config(self, session: Any) -> Mapping[str, Any]: ...
    def open_slot(self, session: Any, binding: SlotBinding, job: BranchJob,
                  checkpoint: CheckpointArtifact, *, channel: SlotChannel) -> Mapping[str, Any]: ...
    def capture_slot(self, session: Any, binding: SlotBinding) -> Mapping[str, Any]: ...
    def restore_slot(self, session: Any, binding: SlotBinding, state: Mapping[str, Any]) -> None: ...
    def mutable_buffers(self, session: Any, binding: SlotBinding) -> Mapping[str, Any]: ...
    def compute_actions(self, session: Any, bindings: tuple[SlotBinding, ...]) -> Mapping[SlotBinding, Any]: ...
    def step_batch(self, session: Any, actions: Mapping[SlotBinding, Any]) -> Mapping[SlotBinding, dict]: ...
    def close_slot(self, session: Any, binding: SlotBinding) -> None: ...
    def close_scene(self, session: Any) -> None: ...

    def collect_shutdown_observation(
        self, session: Any, identity: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SlotBinding:
    session_id: str
    environment_slot: int
    generation: int
    branch_id: str

    @property
    def token(self) -> str:
        return canonical_digest(self.__dict__)

    def row(self) -> dict[str, Any]:
        return {"shared_session_id": self.session_id,
                "environment_slot": self.environment_slot,
                "slot_generation": self.generation, "slot_token": self.token,
                "branch_id": self.branch_id,
                "worker_instance_id": f"{self.session_id}/slot-{self.environment_slot}",
                "worker_session_id": self.token}


class SlotChannel:
    """Copy-on-send local messages and private RNG, fenced by a slot binding.

    Pair branches can use identical random keys in independent generators.
    The physical environment index is deliberately absent from the seed.
    This is a local routing primitive, not a replacement for a bandwidth or
    latency simulator. Such transport state must also be snapshotted by driver.
    """
    def __init__(self, binding: SlotBinding, job: BranchJob):
        self.binding = binding
        self._active = True
        self._messages: dict[str, list[Any]] = {}
        self._event_seed = (job.pair_seed, job.communication_rng_namespace)
        self.random = random.Random(canonical_digest([job.pair_seed, job.rng_namespace]))
        self.communication_random = random.Random(canonical_digest(
            [job.pair_seed, job.communication_rng_namespace]))

    def _check(self, binding: SlotBinding) -> None:
        if not self._active or binding != self.binding:
            raise IsolationError("stale or foreign slot channel binding")

    def send(self, binding: SlotBinding, receiver: str, payload: Any) -> None:
        self._check(binding)
        self._messages.setdefault(receiver, []).append(deepcopy(payload))

    def receive(self, binding: SlotBinding, receiver: str) -> list[Any]:
        self._check(binding)
        return self._messages.pop(receiver, [])

    def event_random(self, binding: SlotBinding, stream: str, event_id: str) -> float:
        """Keyed exogenous draw; absent events cannot shift later draws."""
        self._check(binding)
        if not stream or not event_id:
            raise ValueError("event randomness requires stream and event_id")
        return random.Random(canonical_digest([self._event_seed, stream, event_id])).random()

    def capture(self) -> dict[str, Any]:
        return deepcopy({"messages": self._messages, "random": self.random.getstate(),
                         "communication_random": self.communication_random.getstate()})

    def restore(self, state: Mapping[str, Any]) -> None:
        self._messages = deepcopy(state["messages"])
        self.random.setstate(state["random"])
        self.communication_random.setstate(state["communication_random"])

    def close(self) -> None:
        self._active = False
        self._messages.clear()


@dataclass(frozen=True)
class SlotSnapshot:
    binding: SlotBinding
    state: Mapping[str, Any]
    digest: str
    tick: int


class VectorizedSession:
    """One scene, many independently bound slots, one batched physics call.

    Driver API: open_slot, capture_slot, restore_slot, close_slot,
    compute_actions and step_batch. All use SlotBinding keys, including the
    outputs of compute_actions/step_batch. open_slot receives the managed
    SlotChannel. capture_slot returns all PARTICIPANTS as JSON-compatible
    state. Control and evaluator history are part of that state.

    audit receives ordered lifecycle evidence. Any uncertain operation latches
    quarantine until whole-scene shutdown; no slot may then be reused.
    """
    def __init__(self, driver: Any, session: Any, *, session_id: str, capacity: int, audit=None):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("vector capacity must be a positive integer")
        self.driver, self.session = driver, session
        self.session_id, self.capacity = session_id, capacity
        self.audit = audit or (lambda row: None)
        self.bindings: dict[int, SlotBinding] = {}
        self.channels: dict[SlotBinding, SlotChannel] = {}
        self.ticks: dict[SlotBinding, int] = {}
        self._generations: dict[int, int] = {}
        self.quarantined = False
        self._ownership: dict[str, dict[str, Any]] = {}

    def _check(self, binding: SlotBinding | None = None) -> None:
        if self.quarantined:
            raise IsolationError("vectorized scene is quarantined")
        if binding is not None and self.bindings.get(binding.environment_slot) != binding:
            raise IsolationError("stale or foreign slot binding")

    def _fail(self, exc: BaseException) -> None:
        self.quarantined = True
        self.audit({"kind": "vector_quarantined", "error": f"{type(exc).__name__}: {exc}"})

    def _capture(self, binding: SlotBinding) -> dict[str, Any]:
        state = self.driver.capture_slot(self.session, binding)
        if not isinstance(state, Mapping) or not PARTICIPANTS.issubset(state):
            raise IsolationError("slot snapshot lacks required mutable participants")
        for name, payload in state.items():
            if (not isinstance(payload, Mapping) or not isinstance(payload.get("version"), str)
                    or not payload["version"].strip() or not isinstance(payload.get("state"), Mapping)):
                raise IsolationError(f"participant {name} requires version and state")
        # Deep copies detach returned tensor/array views. Native hidden state
        # coverage remains the driver's declared and tested responsibility.
        value = {"participants": deepcopy(dict(state)),
                 "channel": self.channels[binding].capture()}
        canonical_digest(value)  # reject non-finite / unserialisable state
        return value

    def _others(self, excluded: SlotBinding | None = None) -> dict[str, str]:
        return {binding.token: canonical_digest(self._capture(binding))
                for binding in self.bindings.values() if binding != excluded}

    def bind(self, job: BranchJob, slot: int, checkpoint: CheckpointArtifact) -> tuple[SlotBinding, dict]:
        self._check()
        if (not isinstance(checkpoint.state, Mapping)
                or not isinstance(checkpoint.state.get("participants"), Mapping)
                or not PARTICIPANTS.issubset(checkpoint.state["participants"])
                or canonical_digest(checkpoint.state) != checkpoint.digest):
            raise IsolationError("vector checkpoint requires digest-bound participant state")
        if type(slot) is not int or not 0 <= slot < self.capacity or slot in self.bindings:
            raise IsolationError("environment slot is unavailable")
        if any(item.branch_id == job.branch_id for item in self.bindings.values()):
            raise IsolationError("branch is already bound")
        generation = self._generations.get(slot, 0) + 1
        binding = SlotBinding(self.session_id, slot, generation, job.branch_id)
        try:
            before = self._others()
            self._generations[slot] = generation
            self.bindings[slot] = binding
            self.channels[binding] = SlotChannel(binding, job)
            self.ticks[binding] = -1
            receipt = dict(self.driver.open_slot(self.session, binding, job, deepcopy(checkpoint),
                                                 channel=self.channels[binding]))
            next_step = receipt.get("next_step", 0)
            if type(next_step) is not int or next_step < 0:
                raise IsolationError("restore receipt requires a nonnegative next_step")
            self.ticks[binding] = next_step - 1
            after = self._others(binding)
            if before != after:
                raise IsolationError("opening a slot mutated a peer")
            state = self._capture(binding)
            native_state_digest = receipt.get("state_digest")
            if not isinstance(native_state_digest, str) or not native_state_digest:
                raise IsolationError("open_slot receipt requires native state_digest")
            if native_state_digest != canonical_digest(state["participants"]):
                raise IsolationError("initial restore native state readback mismatch")
            expected_digest = canonical_digest(checkpoint.state["participants"])
            if native_state_digest != expected_digest or next_step != checkpoint.state.get("next_step"):
                raise IsolationError("initial restore differs from frozen checkpoint participants or clock")
            receipt.update(participant_state_digest=native_state_digest,
                           expected_participant_state_digest=expected_digest,
                           participant_versions={name: item["version"] for name, item in state["participants"].items()})
            self._check_buffers()
            self._ownership[job.branch_id] = {
                "binding": binding.row(),
                "state_digest": canonical_digest(state),
                "participant_names": sorted(state["participants"]),
            }
            self.audit({"kind": "vector_slot_bound", **binding.row(),
                        "peer_before": before, "peer_after": after,
                        "participant_names": sorted(state["participants"]),
                        "next_step": next_step,
                        "state_digest": canonical_digest(state),
                        "native_state_digest": native_state_digest,
                        "expected_participant_state_digest": expected_digest})
            return binding, receipt
        except BaseException as exc:
            self._fail(exc)
            raise

    def _check_buffers(self) -> None:
        """Reject exposed mutable aliases, including overlapping array views.

        Different slices of one vectorized storage allocation are permitted.
        Drivers must enumerate live mutable buffers, not copies of them. This
        catches aliasing bugs; arbitrary native writes still require the
        isolated-vs-batched differential qualification described in the guide.
        """
        import numpy as np
        owners = []
        def walk(value, seen):
            if id(value) in seen:
                return
            if isinstance(value, (dict, list, set, np.ndarray)) or hasattr(value, "data_ptr"):
                seen.add(id(value))
                yield value
            if isinstance(value, Mapping):
                for item in value.values():
                    yield from walk(item, seen)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from walk(item, seen)
        for binding in self.bindings.values():
            values = self.driver.mutable_buffers(self.session, binding)
            if not isinstance(values, Mapping) or not PARTICIPANTS.issubset(values):
                raise IsolationError("mutable buffer ownership lacks participants")
            buffers = list(walk(tuple(values.values()), set()))
            for other, old_buffers in owners:
                for left in buffers:
                    for right in old_buffers:
                        overlap = left is right
                        if isinstance(left, np.ndarray) and isinstance(right, np.ndarray):
                            overlap = overlap or np.shares_memory(left, right)
                        elif hasattr(left, "data_ptr") and hasattr(right, "data_ptr"):
                            if not left.is_contiguous() or not right.is_contiguous():
                                raise IsolationError("tensor ownership requires contiguous slot slices")
                            if left.device == right.device:
                                a, b = left.data_ptr(), right.data_ptr()
                                overlap = overlap or (max(a, b) < min(
                                    a + left.numel() * left.element_size(),
                                    b + right.numel() * right.element_size()))
                        if overlap:
                            raise IsolationError(f"mutable buffers overlap between {binding.branch_id} and {other.branch_id}")
            owners.append((binding, buffers))

    def snapshot(self, binding: SlotBinding) -> SlotSnapshot:
        self._check(binding)
        state = self._capture(binding)
        tick = self.ticks[binding]
        return SlotSnapshot(binding, state, canonical_digest({"state": state, "tick": tick}), tick)

    def restore(self, binding: SlotBinding, snapshot: SlotSnapshot) -> None:
        self._check(binding)
        if (snapshot.binding != binding or type(snapshot.tick) is not int or snapshot.tick < -1
                or canonical_digest({"state": snapshot.state, "tick": snapshot.tick}) != snapshot.digest):
            raise IsolationError("checkpoint belongs to another slot generation or was modified")
        try:
            before = self._others(binding)
            current = self._capture(binding)["participants"]
            expected = snapshot.state["participants"]
            if (set(current) != set(expected) or any(
                    current[name]["version"] != expected[name]["version"] for name in current)):
                raise IsolationError("participant version mismatch before restore")
            self.driver.restore_slot(self.session, binding, deepcopy(snapshot.state["participants"]))
            self.channels[binding].restore(snapshot.state["channel"])
            after = self._others(binding)
            actual = canonical_digest({"state": self._capture(binding), "tick": snapshot.tick})
            if before != after or actual != snapshot.digest:
                raise IsolationError("slot restore readback mismatch or peer mutation")
            self.ticks[binding] = snapshot.tick
            self.audit({"kind": "vector_slot_restored", **binding.row(),
                        "peer_before": before, "peer_after": after,
                        "next_step": snapshot.tick + 1,
                        "expected_digest": snapshot.digest, "readback_digest": actual})
        except BaseException as exc:
            self._fail(exc)
            raise

    @staticmethod
    def _keys(value: Any, bindings: tuple[SlotBinding, ...], label: str) -> None:
        if not isinstance(value, Mapping) or set(value) != set(bindings):
            raise IsolationError(f"{label} must cover exactly the active slot bindings")

    def step(self, bindings: tuple[SlotBinding, ...]) -> dict[SlotBinding, dict]:
        """Evaluate controls then advance all selected slots in one batch.

        Missing/finalized slots must be physically frozen by the driver. Their
        registered state is checked before/after each batch. No per-slot step
        fallback is provided by this API.
        """
        self._check()
        if not bindings or len(set(bindings)) != len(bindings):
            raise IsolationError("batch bindings must be nonempty and unique")
        for binding in bindings:
            self._check(binding)
        try:
            self._check_buffers()
            inactive = tuple(item for item in self.bindings.values() if item not in bindings)
            before = {item.token: canonical_digest(self._capture(item)) for item in inactive}
            actions = self.driver.compute_actions(self.session, bindings)
            self._keys(actions, bindings, "actions")
            actions = deepcopy(dict(actions))
            canonical_digest(list(actions.values()))
            result = self.driver.step_batch(self.session, deepcopy(actions))
            self._keys(result, bindings, "batch outputs")
            result = deepcopy(dict(result))
            self._check_buffers()
            for binding, row in result.items():
                if not isinstance(row, dict) or type(row.get("simulator_step")) is not int:
                    raise IsolationError("batch output requires an integer simulator_step")
                if row["simulator_step"] != self.ticks[binding] + 1:
                    raise IsolationError("batch output has stale or skipped tick")
                if "applied_action" not in row or "observation" not in row:
                    raise IsolationError("batch output requires applied_action and observation")
                row["action"] = actions[binding]
                canonical_digest(row)
            after = {item.token: canonical_digest(self._capture(item)) for item in inactive}
            if before != after:
                raise IsolationError("batched stepping mutated an inactive slot")
            for binding, row in result.items():
                self.ticks[binding] = row["simulator_step"]
            self.audit({"kind": "vector_batch", "slot_tokens": [item.token for item in bindings],
                        "peer_before": before, "peer_after": after})
            return result
        except BaseException as exc:
            self._fail(exc)
            raise

    def close(self, binding: SlotBinding) -> None:
        self._check(binding)
        try:
            before = self._others(binding)
            self.driver.close_slot(self.session, binding)
            after = self._others(binding)
            if before != after:
                raise IsolationError("closing a slot mutated a peer")
            self.audit({"kind": "vector_slot_closed", **binding.row(),
                        "peer_before": before, "peer_after": after})
            self.channels.pop(binding).close()
            self.ticks.pop(binding)
            del self.bindings[binding.environment_slot]
        except BaseException as exc:
            self._fail(exc)
            raise

    def isolation_receipt(self) -> dict[str, Any]:
        """Return durable ownership evidence accumulated by this session."""
        return {
            "schema": SCHEMA,
            "mode": "managed_shared_session",
            "session_id": self.session_id,
            "process_failure_scope": "shared_session",
            "slot_ownership": deepcopy(self._ownership),
            "active_slot_count": len(self.bindings),
            "quarantined": self.quarantined,
            "slot_isolation_validated": not self.quarantined and bool(self._ownership),
            "checks": {"generation_fencing": True, "inactive_peer_readback": True,
                       "single_batch_step": True, "private_channels": True,
                       "mutable_buffer_alias_check": True},
        }

    def shutdown_channels(self) -> None:
        """Invalidate handles after confirmed whole-scene shutdown, even in quarantine."""
        for channel in self.channels.values():
            channel.close()
        self.channels.clear()
        self.bindings.clear()
        self.ticks.clear()
        self.quarantined = True
