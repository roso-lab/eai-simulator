"""Validation and I/O mapping for caller-attested HAPPO environment contracts.

No features are invented or padded. The host supplies a fresh, precisely shaped
observation/action-mask snapshot bound to the declared training contract.
"""
from __future__ import annotations

import json
import math

from algorithm.common_coordination.contract import Assignment
from .native_adapter import NativeHAPPOError, NativeHAPPOScopeError, SOURCE_COMMIT, digest


def contract_digest(contract):
    return digest(json.dumps(contract, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _strings(values, *, nonempty=False):
    return (isinstance(values, list) and (not nonempty or bool(values))
            and all(_text(v) for v in values) and len(values) == len(set(values)))


class BoundContract:
    """V2 manifest; arbitrary actor count, vector sizes and discrete bindings.

    External origin is an attestation, never evidence that an official model
    has been reproduced or that an environment transfer is compatible.
    """

    def __init__(self, manifest, read_verified):
        try:
            self._validate(manifest, read_verified)
        except (KeyError, TypeError, IndexError) as exc:
            raise NativeHAPPOError(f"malformed v2 manifest: {exc}") from exc

    def _validate(self, m, read_verified):
        if m.get("algorithm") != "HAPPO" or m.get("source_commit") != SOURCE_COMMIT or not _text(m.get("condition")):
            raise NativeHAPPOError("v2 algorithm, revision or condition missing/mismatched")
        self.contract = c = m["contract"]
        if (c["schema"] != "heterocoord.happo.bound_contract.v1"
                or not _text(c["environment_id"]) or not _text(c["observation_schema"])
                or not _strings(c["scene_ids"], nonempty=True)
                or type(c["horizon"]) is not int or c["horizon"] <= 0):
            raise NativeHAPPOError("invalid bound environment contract")
        feedback = c["feedback"]
        if (not _text(feedback["completed"]) or not _strings(feedback["failed"], nonempty=True)
                or feedback["completed"] in feedback["failed"]):
            raise NativeHAPPOError("invalid feedback outcome schema")
        if not isinstance(c["tasks"], list) or not c["tasks"]:
            raise NativeHAPPOError("explicit task bindings required")
        self.tasks = {}
        for task in c["tasks"]:
            if (not _text(task["task_id"]) or task["task_id"] in self.tasks
                    or not _strings(task["required_capabilities"]) or not _strings(task["prerequisites"])
                    or (task["target_ref"] is not None and not _text(task["target_ref"]))):
                raise NativeHAPPOError("invalid/duplicate task binding")
            self.tasks[task["task_id"]] = task
        if any(set(t["prerequisites"]) - set(self.tasks) or t["task_id"] in t["prerequisites"]
               for t in self.tasks.values()):
            raise NativeHAPPOError("invalid task prerequisites")
        # Unknown/self prerequisites are insufficient: A -> B -> A also
        # produces a permanently blocked graph that must not be accepted.
        pending = {key: set(t["prerequisites"]) for key, t in self.tasks.items()}
        while pending:
            ready = {key for key, prerequisites in pending.items() if not prerequisites}
            if not ready:
                raise NativeHAPPOError("task prerequisite cycle is not a valid bound contract")
            pending = {key: prerequisites - ready for key, prerequisites in pending.items() if key not in ready}
        self.specs = c["actors"]
        if not isinstance(self.specs, list) or not self.specs or len(self.specs) != len(m["actors"]):
            raise NativeHAPPOError("one full checkpoint required for every declared actor")
        ids = []
        for i, (spec, entry) in enumerate(zip(self.specs, m["actors"])):
            if (type(spec["actor_index"]) is not int or spec["actor_index"] != i
                    or not _text(spec["robot_id"]) or spec["robot_id"] in ids
                    or not _strings(spec["capabilities"])):
                raise NativeHAPPOError("invalid/duplicate actor-to-robot map")
            ids.append(spec["robot_id"])
            observation = spec["observation"]
            features = observation["features"]
            if (observation["dtype"] != "float32" or not isinstance(features, list) or not features
                    or observation["shape"] != [len(features)]
                    or not _text(observation["preprocessing"])):
                raise NativeHAPPOError("only explicitly described float32 vector observations supported")
            names = []
            for feature in features:
                if (not _text(feature["name"]) or feature["name"] in names
                        or not _text(feature["meaning"]) or not _text(feature["unit"])):
                    raise NativeHAPPOError("feature names/semantics/units required")
                names.append(feature["name"])
                for key in ("low", "high"):
                    value = feature[key]
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                        raise NativeHAPPOError("feature bound must be finite or null")
                if feature["low"] is not None and feature["high"] is not None and feature["low"] > feature["high"]:
                    raise NativeHAPPOError("reversed observation bounds")
            if not isinstance(spec["actions"], list) or not spec["actions"]:
                raise NativeHAPPOError("explicit discrete action bindings required")
            for action in spec["actions"]:
                if action == {"kind": "wait"}:
                    continue
                if (set(action) != {"kind", "task_id"} or action["kind"] != "assign"
                        or action["task_id"] not in self.tasks):
                    raise NativeHAPPOError("only discrete wait/single-robot task assignment mappings supported")
                if set(self.tasks[action["task_id"]]["required_capabilities"]) - set(spec["capabilities"]):
                    raise NativeHAPPOError("action binding lacks required capabilities")
            if (entry["actor_index"] != i or entry["robot_id"] != spec["robot_id"]
                    or not isinstance(entry["actor_args"], dict)):
                raise NativeHAPPOError("checkpoint actor identity/configuration mismatch")
            args = entry["actor_args"]
            if (type(args.get("recurrent_n")) is not int or args["recurrent_n"] < 1
                    or not isinstance(args.get("hidden_sizes"), list) or not args["hidden_sizes"]
                    or any(type(n) is not int or n < 1 for n in args["hidden_sizes"])
                    or type(args.get("use_recurrent_policy")) is not bool
                    or type(args.get("use_naive_recurrent_policy")) is not bool):
                raise NativeHAPPOError("explicit native actor architecture required")
        self.contract_hash = contract_digest(c)
        # This caller-supplied document binds checkpoint provenance to exact
        # observation/action semantics and architectures. Hashes do not certify
        # the truth of an external training/origin assertion.
        attestation = json.loads(read_verified(m["checkpoint_provenance"]))
        if (attestation.get("schema") != "heterocoord.happo.checkpoint_provenance.v1"
                or attestation.get("algorithm") != "HAPPO" or attestation.get("source_commit") != SOURCE_COMMIT
                or attestation.get("contract_sha256") != self.contract_hash
                or attestation.get("condition") != m["condition"]
                or attestation.get("actors") != [{k: a[k] for k in ("actor_index", "robot_id", "sha256", "actor_args")}
                                                 for a in m["actors"]]):
            raise NativeHAPPOError("checkpoint provenance does not bind this exact environment/model")
        self.origin = attestation["origin"]
        if (self.origin.get("kind") not in ("locally_trained", "external_checkpoint")
                or not _text(self.origin.get("reference")) or not _text(self.origin.get("training_environment"))
                or not _text(attestation.get("compatibility_statement"))):
            raise NativeHAPPOError("checkpoint origin and explicit compatibility statement required")
        self.trained_tensor_hashes = {}
        if self.origin["kind"] == "locally_trained":
            # A matching file hash alone does not establish local training:
            # require the source, positive updates, each actor's configuration,
            # changed tensors and checkpoint bytes to agree with the receipt.
            receipt = json.loads(read_verified(attestation["training_evidence"]))
            if (receipt.get("source_commit") != SOURCE_COMMIT
                    or type(receipt.get("updates")) is not int or receipt["updates"] < 1
                    or receipt.get("contract", {}).get("schema") != self.origin["training_environment"]
                    or len(receipt.get("actors", [])) != len(m["actors"])):
                raise NativeHAPPOError("local training receipt source/environment/update count mismatch")
            cfg = receipt.get("algorithm_config", {})
            for entry, proof in zip(m["actors"], receipt["actors"]):
                if (proof.get("actor_index") != entry["actor_index"]
                        or type(proof.get("optimizer_steps")) is not int or proof["optimizer_steps"] < 1
                        or not _text(proof.get("initial_hash")) or not _text(proof.get("final_hash"))
                        or proof["initial_hash"] == proof["final_hash"]
                        or proof.get("checkpoint_sha256") != entry["sha256"]
                        or entry["actor_args"] != {**cfg.get("model", {}), **cfg.get("algo", {})}):
                    raise NativeHAPPOError("local training receipt actor/checkpoint/configuration mismatch")
                self.trained_tensor_hashes[entry["actor_index"]] = proof["final_hash"]

    def encode(self, task, robots, *, completed=(), last_completed=(), step=0):
        import numpy as np

        c = self.contract
        if (task.observation_schema != c["observation_schema"] or task.scene_id not in c["scene_ids"]
                or task.metadata.get("happo_contract_sha256") != self.contract_hash):
            raise NativeHAPPOError("task does not match bound environment contract")
        if not 0 <= step < c["horizon"]:
            raise NativeHAPPOError("episode horizon reached; reset required")
        ids = {s["robot_id"] for s in self.specs}
        roster = {r.capability.robot_id: r for r in robots}
        if len(robots) != len(ids) or set(roster) != ids or {r.capability.robot_id for r in task.robots} != ids:
            raise NativeHAPPOError("actor-to-robot roster mismatch")
        package_roster = {r.capability.robot_id: r for r in task.robots}
        if any(roster[key].capability != package_roster[key].capability for key in ids):
            raise NativeHAPPOError("live/package robot capabilities or activity disagree")
        tasks = {t.task_id: t for t in task.graph.tasks}
        if not task.graph.valid or len(task.graph.tasks) != len(self.tasks) or set(tasks) != set(self.tasks):
            raise NativeHAPPOError("task graph differs from checkpoint binding")
        for task_id, expected in self.tasks.items():
            actual = tasks[task_id]
            if (set(actual.required_capabilities) != set(expected["required_capabilities"])
                    or list(actual.prerequisites) != expected["prerequisites"] or actual.target_ref != expected["target_ref"]):
                raise NativeHAPPOError("task capabilities/prerequisites/target differ from checkpoint binding")
        rows, masks = [], []
        for spec in self.specs:
            robot = roster[spec["robot_id"]]
            if set(robot.capability.capabilities) != set(spec["capabilities"]):
                raise NativeHAPPOError("robot capabilities differ from actor binding")
            snapshot = robot.metadata.get("happo_observation", {})
            if (snapshot.get("contract_sha256") != self.contract_hash
                    or snapshot.get("episode_id") != task.metadata.get("episode_id")
                    or type(snapshot.get("step")) is not int or snapshot["step"] != step
                    or snapshot.get("completed_task_ids") != sorted(completed)
                    or snapshot.get("last_completed_task_ids") != sorted(last_completed)):
                raise NativeHAPPOError("missing/stale/mismatched bound observation snapshot")
            values = snapshot.get("values")
            available = snapshot.get("available_actions")
            if (not isinstance(values, list) or any(type(v) not in (int, float) for v in values)
                    or not isinstance(available, list) or any(type(v) not in (int, float) for v in available)):
                raise NativeHAPPOError("observation and action mask must be numeric vectors")
            # Validate caller values BEFORE the float32 conversion: 1-1e-9
            # must not round into an enabled binary action, nor an out-of-range
            # observation round back into its declared bounds.
            if any(not math.isfinite(v) for v in values) or any(v not in (0, 1) for v in available):
                raise NativeHAPPOError("finite observation values and exact binary action mask required")
            row = np.asarray(values, dtype=np.float32)
            mask = np.asarray(available, dtype=np.float32)
            if row.shape != tuple(spec["observation"]["shape"]) or not np.isfinite(row).all():
                raise NativeHAPPOError("exact observation shape/finite values required; no padding")
            if mask.shape != (len(spec["actions"]),) or not np.isin(mask, [0, 1]).all() or not mask.any():
                raise NativeHAPPOError("exact nonempty binary action mask required")
            for value, feature in zip(values, spec["observation"]["features"]):
                if ((feature["low"] is not None and value < feature["low"])
                        or (feature["high"] is not None and value > feature["high"])):
                    raise NativeHAPPOError("observation outside declared training bounds")
            for i, action in enumerate(spec["actions"]):
                if action["kind"] == "assign" and mask[i]:
                    task_id = action["task_id"]
                    if (not robot.capability.active or task_id in completed
                            or set(self.tasks[task_id]["prerequisites"]) - set(completed)):
                        raise NativeHAPPOError("caller mask permits unavailable task; refusing to rewrite mask")
            rows.append(row)
            masks.append(mask)
        return rows, masks

    def decode(self, traces, algorithm_id):
        assignments, assigned = [], set()
        for spec, trace in zip(self.specs, traces):
            binding = spec["actions"][trace["action"]]
            trace["action_binding"] = dict(binding)
        for spec, trace in zip(self.specs, traces):
            binding = trace["action_binding"]
            if binding["kind"] == "wait":
                continue
            task_id = binding["task_id"]
            if task_id in assigned:
                raise NativeHAPPOScopeError(
                    "native multi-robot same-task output cannot map to TaskRunner; raw outputs attached", traces)
            assigned.add(task_id)
            assignments.append(Assignment(task_id, spec["robot_id"], source=algorithm_id))
        return assignments
