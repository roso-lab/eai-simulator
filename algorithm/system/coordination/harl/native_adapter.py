"""Strict native HARL/HAPPO inference for explicit checkpoint-bound contracts.

Only observation encoding, action decoding and recurrent state lifecycle live
here. Policy networks and inference come from the unmodified HARL checkout.
The v1 environment is a local smoke condition. V2 accepts caller-declared
vector observations and discrete assignment bindings without guessing semantics.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from typing import Sequence

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, ExecutionFeedback, FailureCode
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

SOURCE_COMMIT = "b1af98b0dbab72a2eee9d160751cd09aedbb8ce2"
SCHEMA = "heterocoord.happo.two_actor_macro.v1"
CONTRACT = {
    "schema": SCHEMA,
    "scene_id": "happo-symbolic-macro-smoke",
    "actors": [
        {"actor_index": 0, "robot_id": "ground", "task_id": "inspect_ground", "capabilities": ["ground_inspect"]},
        {"actor_index": 1, "robot_id": "air", "task_id": "inspect_air", "capabilities": ["air_inspect"]},
    ],
    "observation_features": ["active", "own_completed", "peer_completed", "robot_x", "robot_y",
                             "target_x", "target_y", "last_feedback_completed", "step_fraction"],
    "coordinate_domain": [0.0, 1.0],
    "action_schema": {"0": "wait", "1": "dispatch_actor_owned_inspection"},
    "horizon": 4,
    "completion_feedback": "macro_completed",
    "reward": "new_completions - 0.05 + 0.5 * all_completed",
    "semantics": "symbolic_instant_macro_completion; no physical simulator",
}


class NativeHAPPOError(ValueError):
    """Missing or incompatible source/checkpoint/environment; no fallback."""


class NativeHAPPOScopeError(NativeHAPPOError):
    """Native outputs cannot map to TaskRunner; raw outputs remain inspectable."""

    def __init__(self, message, actor_outputs):
        super().__init__(message)
        self.actor_outputs = actor_outputs
        # Shared CLI serializes native_result on scope failures, not
        # actor_outputs. Expose the same complete rejected output there.
        self.native_result = {"actor_outputs": actor_outputs}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_root_path(source_root=None) -> Path:
    root = Path(source_root or os.environ.get("HETEROCOORD_HARL_SOURCE_ROOT")
                or "/home/airs/.cache/heterocoord/upstream/harl").resolve()
    if not (root / "harl/algorithms/actors/happo.py").is_file():
        raise NativeHAPPOError(f"HARL source_root unavailable: {root}")
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if revision != SOURCE_COMMIT:
        raise NativeHAPPOError("HARL source revision mismatch")
    return root


def load_native(source_root=None):
    root = source_root_path(source_root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module = importlib.import_module("harl.algorithms.actors.happo")
    # Reject a previously imported HARL from another environment/root.
    for name, imported in tuple(sys.modules.items()):
        if name == "harl" or name.startswith("harl."):
            file = getattr(imported, "__file__", None)
            if file is None or not Path(file).resolve().is_relative_to(root):
                raise NativeHAPPOError(f"native import outside source_root: {name}")
    return root, module.HAPPO


def load_actor(cls, args, observation_size, action_count, data, device, *, tensor_hash=None):
    """Complete native actor load shared by both manifest versions."""
    import numpy as np
    import torch
    from gymnasium.spaces import Box, Discrete

    state = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
    if not isinstance(state, dict) or not state or any(
            not torch.is_tensor(v) or not torch.isfinite(v).all() for v in state.values()):
        raise NativeHAPPOError("checkpoint contains invalid/nonfinite tensors")
    state_hash = digest(b"".join(k.encode() + v.detach().cpu().numpy().tobytes()
                                for k, v in sorted(state.items())))
    if tensor_hash is not None and state_hash != tensor_hash:
        raise NativeHAPPOError("checkpoint tensors differ from training receipt")
    actor = cls(args, Box(-np.inf, np.inf, (observation_size,), dtype=np.float32),
                Discrete(action_count), device=torch.device(device))
    expected = actor.actor.state_dict()
    if set(state) != set(expected) or any(state[k].shape != expected[k].shape
            or state[k].dtype != expected[k].dtype for k in expected):
        raise NativeHAPPOError("checkpoint keys, shapes or dtypes mismatch (strict)")
    actor.actor.load_state_dict(state, strict=True)
    actor.prep_rollout()
    return actor


def encode(task, robots, *, completed=(), last_completed=(), step=0):
    """Exact shared training/inference encoder; no padding or feature guessing."""
    import numpy as np

    if task.observation_schema != SCHEMA or task.scene_id != CONTRACT["scene_id"]:
        raise NativeHAPPOError("task observation schema or scene mismatch")
    if task.metadata.get("happo_contract") != CONTRACT:
        raise NativeHAPPOError("task must declare the exact happo_contract")
    actor_ids = [a["robot_id"] for a in CONTRACT["actors"]]
    roster = {r.capability.robot_id: r for r in robots}
    if len(robots) != 2 or set(roster) != set(actor_ids):
        raise NativeHAPPOError("actor-to-robot roster mismatch")
    if {r.capability.robot_id for r in task.robots} != set(actor_ids):
        raise NativeHAPPOError("package roster mismatch")
    tasks = {t.task_id: t for t in task.graph.tasks}
    if not task.graph.valid or len(task.graph.tasks) != 2 or set(tasks) != {a["task_id"] for a in CONTRACT["actors"]}:
        raise NativeHAPPOError("task graph mismatch")
    if not 0 <= step < CONTRACT["horizon"]:
        raise NativeHAPPOError("episode horizon reached; reset required")
    rows, masks = [], []
    for i, a in enumerate(CONTRACT["actors"]):
        robot, subtask = roster[a["robot_id"]], tasks[a["task_id"]]
        if (set(robot.capability.capabilities) != set(a["capabilities"])
                or set(subtask.required_capabilities) != set(a["capabilities"])
                or subtask.prerequisites):
            raise NativeHAPPOError("capability or prerequisite contract mismatch")
        target = subtask.metadata.get("target_xy")
        if robot.position is None or not isinstance(target, (list, tuple)) or len(target) != 2:
            raise NativeHAPPOError("explicit robot position and target_xy required")
        coords = np.asarray([*robot.position[:2], *target], dtype=np.float32)
        if not np.isfinite(coords).all() or (coords < 0).any() or (coords > 1).any():
            raise NativeHAPPOError("coordinates outside trained domain")
        own = a["task_id"] in completed
        peer = CONTRACT["actors"][1-i]["task_id"] in completed
        rows.append([float(robot.capability.active), float(own), float(peer), *coords,
                     float(a["task_id"] in last_completed), step / CONTRACT["horizon"]])
        masks.append([1.0, float(robot.capability.active and not own)])
    return np.asarray(rows, dtype=np.float32), np.asarray(masks, dtype=np.float32)


class NativeHAPPOAdapter:
    """One adapter instance per live episode stream; explicit reset for replay.

    ``manifest_path`` (or HETEROCOORD_HARL_HAPPO_MANIFEST) is mandatory.
    ``update_feedback`` must acknowledge every emitted assignment before the
    next call. State changes are committed only after every actor succeeds.
    """

    def __init__(self, manifest_path=None, *, source_root=None, device="cpu"):
        path = manifest_path or os.environ.get("HETEROCOORD_HARL_HAPPO_MANIFEST")
        if not path or not Path(path).is_file():
            raise NativeHAPPOError("checkpoint manifest is required")
        self.manifest_path = Path(path).resolve()
        raw = self.manifest_path.read_bytes()
        self.manifest_hash = digest(raw)
        self.manifest = json.loads(raw)
        self._loaded_manifest_digest = digest(json.dumps(self.manifest, sort_keys=True, allow_nan=False).encode())
        m = self.manifest
        self.boundary = None
        if m.get("schema") == "heterocoord.happo.checkpoint.v2":
            self._initialize_bound(m, source_root, device)
            return
        if (m.get("schema") != "heterocoord.happo.checkpoint.v1" or m.get("contract") != CONTRACT
                or m.get("source_commit") != SOURCE_COMMIT or m.get("algorithm") != "HAPPO"
                or m.get("condition") != "locally_trained_macro_smoke"):
            raise NativeHAPPOError("checkpoint manifest contract mismatch")
        self.source_root, cls = load_native(source_root)
        receipt = json.loads(self._verified_bytes(m["training_receipt"]))
        if (receipt.get("contract") != CONTRACT or receipt.get("source_commit") != SOURCE_COMMIT
                or receipt.get("updates", 0) < 1 or receipt.get("condition") != m["condition"]):
            raise NativeHAPPOError("training receipt does not establish this condition")
        self.args = m["actor_args"]
        cfg = receipt.get("algorithm_config", {})
        if self.args != {**cfg.get("model", {}), **cfg.get("algo", {})}:
            raise NativeHAPPOError("inference configuration differs from training receipt")
        if receipt.get("original_runner_methods") != dict.fromkeys(
                ("run", "collect", "insert", "compute", "train", "after_update", "save"), True):
            raise NativeHAPPOError("receipt must identify original runner methods")
        if not self.args.get("use_recurrent_policy") or self.args.get("share_param"):
            raise NativeHAPPOError("v1 requires separate recurrent actors")
        if len(m.get("actors", [])) != 2 or len(receipt.get("actors", [])) != 2:
            raise NativeHAPPOError("exactly two complete actor checkpoints required")
        self.actors = []
        self.checkpoint_hashes = []
        for i, (entry, proof) in enumerate(zip(m["actors"], receipt["actors"])):
            if (entry.get("actor_index") != i or entry.get("robot_id") != CONTRACT["actors"][i]["robot_id"]
                    or proof.get("actor_index") != i or proof.get("optimizer_steps", 0) <= 0
                    or proof.get("initial_hash") == proof.get("final_hash")
                    or proof.get("checkpoint_sha256") != entry.get("sha256")):
                raise NativeHAPPOError("actor mapping or trained checkpoint receipt mismatch")
            actor = load_actor(cls, self.args, 9, 2, self._verified_bytes(entry), device,
                               tensor_hash=proof["final_hash"])
            self.actors.append(actor)
            self.checkpoint_hashes.append(entry["sha256"])
        self.condition = AlgorithmCondition(
            algorithm_id="HARL-HAPPO-native-local-macro-smoke-v1",
            source_reference=f"PKU-MARL/HARL@{SOURCE_COMMIT}",
            implementation="original_harl_HAPPO_act_strict_checkpoint",
            execution_scope="native_inference_declared_symbolic_macro_contract",
            planner="harl.algorithms.actors.happo.HAPPO",
            checkpoint=self.manifest_hash,
            supported_primitives=("two_actor_macro_inspection", "recurrent_feedback"),
            unsupported_dimensions=("Fire_Rescue", "benchmark_performance", "physical_task_success"),
            notes="Locally trained smoke condition; upstream optimizer/trainer unchanged.",
        )
        self.reset()

    def _initialize_bound(self, manifest, source_root, device):
        from .generic_manifest import BoundContract

        self.boundary = BoundContract(manifest, self._verified_bytes)
        self.source_root, cls = load_native(source_root)
        self.actors, self.checkpoint_hashes = [], []
        for spec, entry in zip(self.boundary.specs, manifest["actors"]):
            actor = load_actor(cls, entry["actor_args"], len(spec["observation"]["features"]),
                               len(spec["actions"]), self._verified_bytes(entry), device,
                               tensor_hash=self.boundary.trained_tensor_hashes.get(entry["actor_index"]))
            self.actors.append(actor)
            self.checkpoint_hashes.append(entry["sha256"])
        # Per-actor architectures can differ; reset uses each actor's own args.
        self.args = self.actors[0].args
        self.condition = AlgorithmCondition(
            algorithm_id=manifest["condition"],
            source_reference=f"PKU-MARL/HARL@{SOURCE_COMMIT}",
            implementation="original_harl_HAPPO_act_strict_checkpoint",
            execution_scope="native_inference_caller_bound_contract",
            planner="harl.algorithms.actors.happo.HAPPO", checkpoint=self.manifest_hash,
            supported_primitives=("declared_discrete_assignment", "explicit_observation_snapshot"),
            unsupported_dimensions=("continuous_control", "coalition_dispatch", "unverified_environment_transfer"),
            notes="Caller-attested checkpoint/environment binding; native inference is not benchmark acceptance.",
        )
        self.reset()

    def _verified_bytes(self, entry):
        path = (self.manifest_path.parent / entry["file"]).resolve()
        if not path.is_relative_to(self.manifest_path.parent) or not path.is_file():
            raise NativeHAPPOError("checkpoint/receipt path unavailable or escapes bundle")
        data = path.read_bytes()
        if digest(data) != entry.get("sha256"):
            raise NativeHAPPOError("checkpoint/receipt sha256 mismatch")
        return data

    def reset(self):
        import numpy as np
        self._check_manifest()
        self.rnn_states = [np.zeros((1, actor.args["recurrent_n"], actor.args["hidden_sizes"][-1]),
                                    dtype=np.float32) for actor in self.actors]
        self.completed, self.last_completed, self.pending = set(), set(), {}
        self.step, self.episode = 0, None
        self.terminal = False
        self.awaiting_feedback = False
        self.pending_feedback_token = None

    def _check_manifest(self):
        if digest(json.dumps(self.manifest, sort_keys=True, allow_nan=False).encode()) != self._loaded_manifest_digest:
            raise NativeHAPPOError("loaded manifest mutated; construct a new adapter for a new binding")

    def update_feedback(self, feedback: Sequence[ExecutionFeedback], *, terminal=False, decision_token=None):
        self._check_manifest()
        if not self.awaiting_feedback:
            raise NativeHAPPOError("no decision awaiting feedback")
        if self.boundary and decision_token != self.pending_feedback_token:
            raise NativeHAPPOError("v2 feedback decision_token missing or stale")
        if type(terminal) is not bool:
            raise NativeHAPPOError("terminal must be a boolean")
        completed = set()
        seen = set()
        for item in feedback:
            if not isinstance(item, ExecutionFeedback):
                raise NativeHAPPOError("feedback must be ExecutionFeedback records")
            if item.task_id in seen or self.pending.get(item.task_id) != item.robot_id:
                raise NativeHAPPOError("feedback identity mismatch or duplicate")
            completed_outcome = self.boundary.contract["feedback"]["completed"] if self.boundary else "macro_completed"
            failed_outcomes = self.boundary.contract["feedback"]["failed"] if self.boundary else ["macro_failed"]
            if item.outcome not in [completed_outcome, *failed_outcomes]:
                raise NativeHAPPOError("feedback outcome outside declared macro contract")
            if item.outcome == completed_outcome:
                if item.failure_code != FailureCode.NONE:
                    raise NativeHAPPOError("completed feedback cannot carry a failure")
                completed.add(item.task_id)
            elif item.failure_code == FailureCode.NONE:
                raise NativeHAPPOError("failed feedback requires a non-none failure_code")
            seen.add(item.task_id)
        if seen != set(self.pending):
            raise NativeHAPPOError("feedback required for every pending assignment")
        self.completed.update(completed)
        self.last_completed = completed
        self.pending = {}
        self.terminal = terminal
        self.awaiting_feedback = False
        self.pending_feedback_token = None

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        import numpy as np
        import torch
        self._check_manifest()
        if self.terminal or self.awaiting_feedback:
            raise NativeHAPPOError("terminal episode or pending execution feedback; reset/update required")
        episode = (task.task_id, task.metadata.get("episode_id"), task.scene_id)
        if not isinstance(episode[1], str) or not episode[1]:
            raise NativeHAPPOError("explicit episode_id required")
        if self.episode is not None and self.episode != episode:
            raise NativeHAPPOError("episode identity changed; explicit reset required")
        encoder = self.boundary.encode if self.boundary else encode
        obs, available = encoder(task, robots, completed=self.completed,
                                 last_completed=self.last_completed, step=self.step)
        assignments, traces, next_states = [], [], []
        with torch.no_grad():
            for i, actor in enumerate(self.actors):
                mask = np.asarray([[float(self.step > 0)]], dtype=np.float32)
                row = np.asarray(obs[i], dtype=np.float32)[None, :]
                action_mask = np.asarray(available[i], dtype=np.float32)[None, :]
                action, state = actor.act(row, self.rnn_states[i], mask,
                                          action_mask, deterministic=True)
                if action.numel() != 1 or not torch.isfinite(action).all():
                    raise NativeHAPPOError("invalid native actor output")
                value = int(action.item())
                if value < 0 or value >= len(available[i]) or value != action.item() or not available[i][value]:
                    raise NativeHAPPOError("native actor output violates action mask")
                next_state = state.detach().cpu().numpy().copy()
                if next_state.shape != self.rnn_states[i].shape or not np.isfinite(next_state).all():
                    raise NativeHAPPOError("invalid native recurrent state")
                spec = self.boundary.specs[i] if self.boundary else CONTRACT["actors"][i]
                if not self.boundary and value == 1:
                    assignments.append(Assignment(spec["task_id"], spec["robot_id"],
                                                  source=self.condition.algorithm_id))
                traces.append({"actor_index": i, "robot_id": spec["robot_id"], "action": value,
                               "observation": obs[i].tolist(), "available_actions": available[i].tolist(),
                               "rnn_input_sha256": digest(self.rnn_states[i].tobytes()),
                               "rnn_output_sha256": digest(next_state.tobytes()), "reset_mask": float(mask[0, 0])})
                next_states.append(next_state)
        if self.boundary:
            assignments = self.boundary.decode(traces, self.condition.algorithm_id)
        self.rnn_states, self.episode = next_states, episode
        self.pending = {a.task_id: a.robot_id for a in assignments}
        self.awaiting_feedback = True
        self.pending_feedback_token = uuid4().hex if self.boundary else None
        self.step += 1
        remaining = {t.task_id for t in task.graph.tasks} - self.completed - set(self.pending)
        return AlgorithmDecision(self.condition, tuple(assignments), tuple(sorted(remaining)), {
            "adapter": type(self).__name__, "source_root": str(self.source_root),
            "source_commit": SOURCE_COMMIT, "manifest_sha256": self.manifest_hash,
            "checkpoint_sha256": list(self.checkpoint_hashes), "actor_outputs": traces,
            "evidence": {"contract": "validated", "native": "original_HAPPO.act_executed",
                         "simulation": "not_executed", "physical": "N/A", "benchmark": "not_evaluable"},
            "condition": self.manifest["condition"], "step": self.step,
            **({"contract_sha256": self.boundary.contract_hash,
                "feedback_token": self.pending_feedback_token,
                "checkpoint_origin": deepcopy(self.boundary.origin),
                "checkpoint_origin_verification": "caller_attested_hash_bound_not_independently_verified"}
               if self.boundary else {}),
        })


# Discoverable spelling for the method family without a second implementation.
NativeHARLAdapter = NativeHAPPOAdapter
