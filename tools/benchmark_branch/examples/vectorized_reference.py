"""Executable NumPy batch reference, explicitly not Isaac/robot evidence.

Use the factory through the regular CLI; all mutable buffers are slot slices,
and one NumPy update advances the entire active set. Native backends implement
the same methods with one simulator step and explicit inactive-slot freezing.
"""
from copy import deepcopy

import numpy as np

from tools.benchmark_branch.adapters import DummyAdapter
from tools.benchmark_branch.models import canonical_digest, CheckpointArtifact
from tools.benchmark_branch.runtime import RuntimeComponents
from tools.benchmark_branch.vectorized import PARTICIPANTS, SCHEMA
from tools.physics_replay.execution.scheduler import ReplayCapacity, ReplayScheduler


class NumpyVectorAdapter(DummyAdapter):
    def create_checkpoint(self, plan):
        base = super().create_checkpoint(plan)
        states = {name: {"version": "numpy-reference.v1", "state": {}} for name in PARTICIPANTS}
        states["clock"]["state"] = {"next_step": 0}
        states["controller"]["state"] = {"history": []}
        states["world"]["state"] = {"position_velocity": [0.0, 0.0]}
        return CheckpointArtifact.from_state({"participants": states, "next_step": 0},
                                              metadata=base.metadata)


class NumpyVectorBackend:
    vectorized_contract = SCHEMA

    def __init__(self, *, horizon=4):
        self.horizon = horizon
        self.batch_calls = 0
        self.scene_opens = 0

    def open_scene(self, scene_id, *, num_envs, scheduler):
        self.scene_opens += 1
        return {"world": np.zeros((num_envs, 2), dtype=np.float64), "slots": {},
                "actual_physics_device": "cpu", "controller_device": "cpu"}

    def warmup(self, session, *, ticks):
        pass

    def isolation_config(self, session):
        return {"static_asset_digest": canonical_digest("numpy-reference-v1"),
                "collision_filtering": True, "sensor_filtering": True,
                "independent_reset": True, "inactive_slot_freeze": True,
                "evidence_scope": "protocol_synthetic", "physics_model": "independent_numpy_integrators"}

    def open_slot(self, session, binding, job, checkpoint, *, channel):
        slot = binding.environment_slot
        states = deepcopy(checkpoint.state["participants"])
        session["world"][slot] = states.pop("world")["state"]["position_velocity"]
        session["slots"][slot] = {"states": states, "job": job, "binding": binding, "channel": channel}
        native_state_digest = canonical_digest(self.capture_slot(session, binding))
        return {"restore_passed": True, "readback_digest": checkpoint.digest,
                "state_digest": native_state_digest, "next_step": checkpoint.state["next_step"]}

    def capture_slot(self, session, binding):
        item = session["slots"][binding.environment_slot]
        if item["binding"] != binding:
            raise ValueError("native slot binding mismatch")
        return {**deepcopy(item["states"]),
                "world": {"version": "numpy-reference.v1", "state": {
                    "position_velocity": session["world"][binding.environment_slot].copy()}}}

    def mutable_buffers(self, session, binding):
        return {**session["slots"][binding.environment_slot]["states"],
                "world": session["world"][binding.environment_slot]}

    def restore_slot(self, session, binding, snapshot):
        slot = binding.environment_slot
        session["world"][slot] = snapshot["world"]["state"]["position_velocity"]
        session["slots"][slot]["states"] = deepcopy({key: value for key, value in snapshot.items() if key != "world"})

    def compute_actions(self, session, bindings):
        actions = {}
        for binding in bindings:
            item = session["slots"][binding.environment_slot]
            action = item["channel"].random.uniform(-1, 1)
            if item["job"].branch_type == "intervention":
                action *= 2
            item["states"]["controller"]["state"]["history"].append(action)
            actions[binding] = [action]
        return actions

    def step_batch(self, session, actions):
        self.batch_calls += 1
        bindings = tuple(actions)
        indices = np.asarray([item.environment_slot for item in bindings])
        # Actual batched array integration; never a loop over env.step().
        session["world"][indices, 1] += np.asarray([actions[item][0] for item in bindings])
        session["world"][indices, 0] += session["world"][indices, 1]
        result = {}
        for binding in bindings:
            slot = binding.environment_slot
            clock = session["slots"][slot]["states"]["clock"]["state"]
            tick = clock["next_step"]
            clock["next_step"] += 1
            row = {"simulator_step": tick, "applied_action": actions[binding],
                   "observation": session["world"][slot].tolist(),
                   "post_step_state": {"position": float(session["world"][slot, 0])}}
            if tick + 1 >= self.horizon:
                row["result"] = {"status": "not_evaluable", "completed": True, "physical_success": None}
            result[binding] = row
        return result

    def close_slot(self, session, binding):
        del session["slots"][binding.environment_slot]
        session["world"][binding.environment_slot] = 0

    def close_scene(self, session):
        session["slots"].clear()


def factory(*, plan, run_dir, num_envs=4, horizon=4):
    return RuntimeComponents(
        adapter=NumpyVectorAdapter(), session_backend=NumpyVectorBackend(horizon=horizon),
        scheduler=ReplayScheduler(ReplayCapacity(1, num_envs, num_envs), device_probe=lambda session, request: session),
        plugin_id="numpy-vector-reference", plugin_version="1",
        executor_options={"requested_device": "cpu"})
