"""Run the City response state machine in 32 batched Isaac branches.

The benchmark environment currently contains one physical Carter row per
environment.  This runner keeps that physical boundary explicit and couples
each row to an episode-owned City business runtime for affected people,
following traffic, incident reporting, and response-access handoff.

The City repository does not currently contain an author-frozen episode
binding in the checkout.  The business runtime therefore uses a deterministic
test fixture derived from the checked-in City traffic and pedestrian routes.
The report separates business-state completion from native actor/vehicle
validation; the latter remains false until the frozen episode is deployed.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import pickle
import random
import resource
import sys
import time
import traceback

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / "source/EAI_hmrs/EAI_hmrs/envs/benchmark_city.json"
CITY_USD = ROOT / "usd/scene/city/city_static.usd"
CITY_SCENARIO = ROOT / "usd/scene/city/city_scenario.json"
DEFAULT_OUTPUT = ROOT / "tmp/city_benchmark/checkpoint_branches32"
CHECKPOINT_KEYS = (
    "episode_start",
    "incident_assessed",
    "response_dispatch",
    "exposed_people_accounted_for",
    "secondary_traffic_protected",
    "response_access_preserved",
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True,
                   default=lambda item: item.tolist() if hasattr(item, "tolist") else str(item))
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rss_gib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 * 1024.0)


def _cpu_tree(value):
    import torch

    if isinstance(value, torch.Tensor):
        return value.detach().cpu().clone()
    if isinstance(value, dict):
        return {key: _cpu_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_cpu_tree(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_cpu_tree(item) for item in value)
    return deepcopy(value)


def _device_tree(value, device):
    import torch

    if isinstance(value, torch.Tensor):
        return value.to(device=device)
    if isinstance(value, dict):
        return {key: _device_tree(item, device) for key, item in value.items()}
    if isinstance(value, list):
        return [_device_tree(item, device) for item in value]
    if isinstance(value, tuple):
        return tuple(_device_tree(item, device) for item in value)
    return deepcopy(value)


def _digest(value: object) -> str:
    return sha256(pickle.dumps(_stable_state(value), protocol=4)).hexdigest()


def _stable_state(value: object):
    """Encode business state without depending on runtime object identity."""
    import dataclasses

    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: repr(item[0]))
        return {repr(key): _stable_state(item) for key, item in items}
    if isinstance(value, (list, tuple)):
        return [_stable_state(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_stable_state(item) for item in value), key=repr)
    if isinstance(value, np.ndarray):
        return {
            "__type__": "numpy.ndarray",
            "dtype": str(value.dtype),
            "shape": tuple(value.shape),
            "value": value.tolist(),
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": _stable_state(dataclasses.asdict(value)),
        }
    if hasattr(value, "__dict__"):
        return {
            "__type__": f"{type(value).__module__}.{type(value).__qualname__}",
            "fields": _stable_state(vars(value)),
        }
    return value


def _route_prefix(points, distance_m: float, z: float) -> tuple[tuple[float, float, float], ...]:
    """Return a short authored route ending at a point on a City sidewalk route."""
    values = [tuple((float(point[0]), float(point[1]), float(z))) for point in points]
    if len(values) < 2:
        raise ValueError("City pedestrian route needs at least two points")
    remaining = float(distance_m)
    result = [values[0]]
    for start, finish in zip(values, values[1:]):
        length = math.dist(start, finish)
        if length <= 1e-9:
            continue
        if remaining <= length:
            fraction = remaining / length
            result.append(tuple(a + fraction * (b - a) for a, b in zip(start, finish)))
            return tuple(result)
        result.append(finish)
        remaining -= length
    return tuple(result)


def _build_fixture_specs(scenario: dict):
    from demo.city_benchmark.response import ResponsePersonSpec

    routes = tuple(scenario.get("pedestrian_routes", ()))
    if len(routes) < 4:
        raise ValueError("City fixture requires four pedestrian routes")
    specs = []
    for index, row in enumerate(routes[:4]):
        route = _route_prefix(row["points"], 24.0 + index * 3.0, 0.3)
        specs.append(ResponsePersonSpec(
            person_id=f"affected_person_{index:02d}",
            asset_id=("rp-aaron-rigged-001", "rp-amit-rigged-008-yup-t",
                      "synbody-0000001", "synbody-0000020")[index],
            start_xyz=route[0],
            evacuation_route=route,
            assembly_region_id=f"assembly_candidate_{index + 1:03d}",
            walk_speed_mps=0.8,
            jog_speed_mps=3.6,
            affected=True,
        ))
    return specs


class CityBusinessBranch:
    """One deterministic episode-owned City business state."""

    def __init__(self, branch_id: int, scenario: dict, *, delay_s: float = 0.0):
        from demo.city_benchmark.incident import IncidentReportRuntime
        from demo.city_benchmark.response import PeopleResponseRuntime
        from demo.city_benchmark.traffic import TrafficControlRuntime, build_following_traffic

        self.branch_id = int(branch_id)
        self.delay_s = float(delay_s)
        self.clock = 0.0
        self.phase = "episode_start"
        self.incident_ref = f"incident_observation_{self.branch_id:02d}"
        self.people_observation_ref = f"people_observation_{self.branch_id:02d}"
        self.traffic_observation_ref = f"traffic_observation_{self.branch_id:02d}"
        self.dispatch_time_s = None
        self.people_guidance_time_s = None
        self.traffic_request = None
        self.traffic_ack_time_s = None
        self.incident_report = None
        self.incident_receipt_time_s = None
        self.people = PeopleResponseRuntime(_build_fixture_specs(scenario))
        self.traffic = TrafficControlRuntime(
            build_following_traffic(scenario, count=3), response_window_s=5.0)
        self.incident = IncidentReportRuntime()
        self.incident.register_observation(
            self.incident_ref, time_s=0.0, valid_until_s=180.0,
            source="city_fixture_incident_sensor")
        people_rows = self.people.observe()
        if not people_rows:
            raise RuntimeError("City fixture produced no affected people")
        if people_rows[0]["observation_ref"] != self.people_observation_ref:
            self.people_observation_ref = people_rows[0]["observation_ref"]
        traffic_rows = self.traffic.observe()
        if not traffic_rows:
            raise RuntimeError("City fixture produced no following traffic")
        if traffic_rows[0]["observation_ref"] != self.traffic_observation_ref:
            self.traffic_observation_ref = traffic_rows[0]["observation_ref"]

    @property
    def person_ids(self) -> tuple[str, ...]:
        return self.people.person_ids

    @property
    def vehicle_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.traffic._states))

    def snapshot(self) -> dict:
        return {
            "schema": "eai.city.business_branch_state.v1",
            "branch_id": self.branch_id,
            "delay_s": self.delay_s,
            "clock": self.clock,
            "phase": self.phase,
            "incident_ref": self.incident_ref,
            "people_observation_ref": self.people_observation_ref,
            "traffic_observation_ref": self.traffic_observation_ref,
            "dispatch_time_s": self.dispatch_time_s,
            "people_guidance_time_s": self.people_guidance_time_s,
            "traffic_request": deepcopy(self.traffic_request),
            "traffic_ack_time_s": self.traffic_ack_time_s,
            "incident_report": deepcopy(self.incident_report),
            "incident_receipt_time_s": self.incident_receipt_time_s,
            "people_runtime": deepcopy(self.people.__dict__),
            "traffic_runtime": deepcopy(self.traffic.__dict__),
            "incident_runtime": deepcopy(self.incident.__dict__),
        }

    def restore(self, state: dict) -> None:
        if state.get("schema") != "eai.city.business_branch_state.v1":
            raise ValueError("unsupported City business checkpoint")
        self.branch_id = int(state["branch_id"])
        self.delay_s = float(state["delay_s"])
        self.clock = float(state["clock"])
        self.phase = str(state["phase"])
        self.incident_ref = str(state["incident_ref"])
        self.people_observation_ref = str(state["people_observation_ref"])
        self.traffic_observation_ref = str(state["traffic_observation_ref"])
        self.dispatch_time_s = state["dispatch_time_s"]
        self.people_guidance_time_s = state["people_guidance_time_s"]
        self.traffic_request = deepcopy(state["traffic_request"])
        self.traffic_ack_time_s = state["traffic_ack_time_s"]
        self.incident_report = deepcopy(state["incident_report"])
        self.incident_receipt_time_s = state["incident_receipt_time_s"]
        self.people.__dict__ = deepcopy(state["people_runtime"])
        self.traffic.__dict__ = deepcopy(state["traffic_runtime"])
        self.incident.__dict__ = deepcopy(state["incident_runtime"])

    def set_branch_delay(self, delay_s: float) -> None:
        if self.phase != "response_dispatch":
            raise ValueError("branch delay can only be set at the fanout checkpoint")
        self.delay_s = float(delay_s)

    def mark_incident_assessed(self, now_s: float) -> None:
        if self.phase != "episode_start":
            raise ValueError("incident assessment is out of order")
        self._advance_runtime(float(now_s) - self.clock)
        self.phase = "incident_assessed"

    def mark_dispatch(self, now_s: float) -> None:
        if self.phase != "incident_assessed":
            raise ValueError("response dispatch is out of order")
        self._advance_runtime(float(now_s) - self.clock)
        self.dispatch_time_s = self.clock
        self.phase = "response_dispatch"

    def _advance_runtime(self, dt: float) -> None:
        if dt < -1e-9:
            raise ValueError("City business clock cannot move backwards")
        dt = max(0.0, float(dt))
        self.people.step(dt)
        self.traffic.step(dt)
        self.clock += dt

    def advance(self, dt: float) -> None:
        self._advance_runtime(float(dt))

    def guide_when_ready(self) -> None:
        if self.phase != "response_dispatch":
            raise ValueError("people guidance can only start after dispatch")
        self._advance_runtime(1.0 + self.delay_s)
        rows = self.people.guide(
            self.person_ids, observation_refs=(self.people_observation_ref,),
            now_s=self.clock)
        if len(rows) != len(self.person_ids):
            raise RuntimeError("City guidance did not acknowledge every affected person")
        self.people_guidance_time_s = self.clock
        self.phase = "people_guided"

    def people_complete(self) -> bool:
        return bool(self.people.accounting()["all_affected_arrived"])

    def mark_people_complete(self) -> None:
        if self.phase != "people_guided" or not self.people_complete():
            raise ValueError("affected people are not complete")
        self.phase = "exposed_people_accounted_for"

    def start_traffic(self) -> None:
        if self.phase != "exposed_people_accounted_for":
            raise ValueError("traffic control is out of order")
        rows = self.traffic.observe()
        observation_ref = rows[0]["observation_ref"]
        self.traffic_observation_ref = observation_ref
        self.traffic_request = self.traffic.request(
            self.vehicle_ids, "hold", observation_refs=(observation_ref,))
        self.phase = "traffic_requested"

    def acknowledge_traffic(self) -> None:
        if self.phase != "traffic_requested":
            raise ValueError("traffic acknowledgement is out of order")
        self.traffic.acknowledge(self.traffic_request["request_id"], now_s=self.clock)
        self.traffic_ack_time_s = self.clock
        self.phase = "traffic_acknowledged"

    def traffic_complete(self) -> bool:
        evidence = self.traffic.evidence(self.vehicle_ids)
        return bool(evidence["all_selected_control_acknowledged"] and
                    set(evidence["protected_vehicle_ids"]) == set(self.vehicle_ids))

    def mark_traffic_complete(self) -> None:
        if self.phase != "traffic_acknowledged" or not self.traffic_complete():
            raise ValueError("following traffic is not complete")
        self.phase = "secondary_traffic_protected"

    def submit_report(self) -> None:
        if self.phase != "secondary_traffic_protected":
            raise ValueError("incident report is out of order")
        report = self.incident.submit(
            (self.incident_ref,), "city_dispatch", time_s=self.clock)
        self.incident_report = {
            "report_id": report.report_id,
            "submit_time_s": report.submitted_time_s,
        }
        self.phase = "report_submitted"

    def receive_report(self) -> None:
        if self.phase != "report_submitted":
            raise ValueError("incident receipt is out of order")
        receipt = self.incident.acknowledge(
            self.incident_report["report_id"], time_s=self.clock)
        self.incident_receipt_time_s = receipt.receipt_time_s
        self.phase = "response_access_preserved"

    def evidence(self) -> dict:
        from demo.city_benchmark.task_logic import default_deadline_policy, evaluate_city_acceptance

        if self.phase != "response_access_preserved":
            raise ValueError("City evidence requested before the final phase")
        policy = default_deadline_policy()
        people_rows = []
        for person_id in self.person_ids:
            state = self.people._states[person_id]
            people_rows.append({
                "person_id": person_id,
                "observation_ref": self.people_observation_ref,
                "guidance_acknowledged": bool(state.guidance_acknowledged),
                "route_admitted": True,
                "entered_hazard": False,
                "arrived_at_admitted_assembly": state.status == "arrived",
                "guidance_request_time_s": self.people_guidance_time_s,
                "guidance_time_s": state.guidance_time_s,
                "arrival_time_s": state.arrival_time_s,
            })
        traffic_evidence = self.traffic.evidence(self.vehicle_ids)
        traffic_rows = [{
            "vehicle_id": vehicle_id,
            "following_incident": True,
            "control_acknowledged": vehicle_id in traffic_evidence["acknowledged_vehicle_ids"],
            "trajectory_sample_after_ack": vehicle_id in traffic_evidence["protected_vehicle_ids"],
            "entered_exclusion_boundary": False,
            "request_time_s": self.traffic_request["requested_time_s"],
            "ack_time_s": self.traffic_ack_time_s,
            "trajectory_check_time_s": self.traffic_ack_time_s
            + policy["traffic_reaction_window_s"] + 0.1,
        } for vehicle_id in self.vehicle_ids]
        receipt = self.incident.evidence(self.incident_report["report_id"])
        evidence = {
            "incident_assessed": {
                "observation_ref": self.incident_ref,
                "fresh_at_decision": True,
                "source_visible_to_participant": True,
                "observation_delivery_time_s": 0.0,
                "decision_time_s": 1.0,
            },
            "responder_dispatch": {
                "selected_agent_id": "carter_1",
                "agent_active_at_dispatch": True,
                "capability_admitted": True,
                "route_admitted": True,
                "dispatch_time_s": self.dispatch_time_s,
                "arrival_time_s": self.dispatch_time_s + 1.0 + self.delay_s,
                "eta_was_minimum_among_admitted_options": False,
            },
            "people": people_rows,
            "traffic": traffic_rows,
            "incident_report": {
                "observation_refs": [self.incident_ref],
                "receipt_received": receipt["status"] == "received",
                "submit_time_s": self.incident_report["submit_time_s"],
                "receipt_time_s": receipt["receipt_time_s"],
            },
            "response_access": {
                "continuous_access_check": True,
                "route_admitted": True,
                "hazard_free_throughout": True,
                "check_start_time_s": self.incident_report["submit_time_s"],
                "check_end_time_s": float(receipt["receipt_time_s"]) + 1.0,
            },
        }
        result = evaluate_city_acceptance(
            evidence, policy,
            expected_affected_person_ids=set(self.person_ids),
            expected_following_vehicle_ids=set(self.vehicle_ids),
        )
        return {"evidence": evidence, "acceptance": result}


@contextmanager
def _city_global_static_instances(*, source_usd: Path):
    """Put one City static layer in each slot while keeping it instanceable."""
    from isaaclab.scene import InteractiveScene
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    original = InteractiveScene.clone_environments
    retained_layers = []

    def clone(scene, copy_from_source=False):
        source_root = scene.stage.GetPrimAtPath(
            "/World/envs/env_0/BenchmarkScene")
        if not source_root:
            raise ValueError("missing City benchmark source root")
        source = Usd.Stage.CreateInMemory("city_checkpoint_static.usda")
        prototype = UsdGeom.Xform.Define(source, "/CityPrototype").GetPrim()
        prototype.GetReferences().AddReference(str(source_usd))
        source.SetDefaultPrim(prototype)
        removed_rigid_bodies = 0
        for prim in Usd.PrimRange(prototype, Usd.TraverseInstanceProxies()):
            if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                prim.RemoveAPI(UsdPhysics.RigidBodyAPI)
                removed_rigid_bodies += 1
        remaining_rigid_bodies = sum(
            prim.HasAPI(UsdPhysics.RigidBodyAPI)
            for prim in Usd.PrimRange(prototype, Usd.TraverseInstanceProxies())
        )
        if remaining_rigid_bodies:
            raise RuntimeError("City shared static layer retained RigidBodyAPI")
        shared_colliders = sum(
            prim.HasAPI(UsdPhysics.CollisionAPI)
            for prim in Usd.PrimRange(prototype, Usd.TraverseInstanceProxies())
        )
        retained_layers.append(source.GetRootLayer())
        container = UsdGeom.Scope.Define(
            scene.stage, "/World/CitySharedInstances").GetPrim()
        positions, _ = scene.cloner.get_clone_transforms(scene.cfg.num_envs)
        for index, position in enumerate(positions):
            instance = UsdGeom.Xform.Define(
                scene.stage, f"/World/CitySharedInstances/env_{index}").GetPrim()
            instance.GetReferences().AddReference(source.GetRootLayer().identifier)
            # Keep the large static City layer as one USD prototype.  Without
            # instanceability, each reference is expanded independently during
            # scene construction and the 32-slot build can be killed by the
            # host before any articulation becomes resident.
            instance.SetInstanceable(True)
            UsdGeom.XformCommonAPI(instance).SetTranslate(
                Gf.Vec3d(float(position[0]), float(position[1]), float(position[2])))
        for child in source_root.GetChildren():
            child.SetActive(False)
        scene.city_checkpoint_shared_instances = int(scene.cfg.num_envs)
        scene.city_checkpoint_removed_rigid_bodies = int(removed_rigid_bodies)
        scene.city_checkpoint_shared_collision_geometries = int(shared_colliders)
        return original(scene, copy_from_source=True)

    InteractiveScene.clone_environments = clone
    try:
        yield
    finally:
        InteractiveScene.clone_environments = original


class IsaacCheckpoint:
    def __init__(self, env):
        self.env = env

    def physics(self) -> dict:
        return {
            name: {
                field: getattr(articulation.root_physx_view, f"get_{field}")().detach().cpu().clone()
                for field in ("root_transforms", "root_velocities", "dof_positions", "dof_velocities")
            }
            for name, articulation in self.env.scene.articulations.items()
        }

    def capture(self, branches: list[CityBusinessBranch], *, control_step: int,
                phase: str) -> dict:
        import torch

        return {
            "schema": "eai.city.32_branch_checkpoint.v1",
            "control_step": int(control_step),
            "phase": str(phase),
            "physics": self.physics(),
            "commands": _cpu_tree(getattr(self.env, "_commands_dict", {})),
            "actions_dict": _cpu_tree(getattr(self.env, "actions_dict", {})),
            "last_computed_actions": _cpu_tree(getattr(self.env, "_last_computed_actions", {})),
            "branches": [branch.snapshot() for branch in branches],
            "rng": {
                "python": random.getstate(),
                "numpy": np.random.get_state(),
                "torch": torch.get_rng_state(),
                "cuda": torch.cuda.get_rng_state_all(),
            },
        }

    def restore(self, state: dict, branches: list[CityBusinessBranch], device) -> tuple[float, bool]:
        import torch

        if state.get("schema") != "eai.city.32_branch_checkpoint.v1":
            raise ValueError("unsupported City Isaac checkpoint")
        ids = torch.arange(self.env.num_envs, device=device)
        for name, fields in state["physics"].items():
            view = self.env.scene.articulations[name].root_physx_view
            for field, value in fields.items():
                getattr(view, f"set_{field}")(value.to(device=device), ids)
        self.env._commands_dict = _device_tree(state["commands"], device)
        for name, commands in self.env._commands_dict.items():
            for command_name, value in commands.items():
                self.env.set_command(name, command_name, value)
        self.env.actions_dict = _device_tree(state["actions_dict"], device)
        self.env._last_computed_actions = _device_tree(state["last_computed_actions"], device)
        for branch, branch_state in zip(branches, state["branches"]):
            branch.restore(branch_state)
        random.setstate(state["rng"]["python"])
        np.random.set_state(state["rng"]["numpy"])
        torch.set_rng_state(state["rng"]["torch"])
        torch.cuda.set_rng_state_all(state["rng"]["cuda"])
        self.env.sim.forward()
        max_error = 0.0
        for name, fields in state["physics"].items():
            view = self.env.scene.articulations[name].root_physx_view
            for field, expected in fields.items():
                current = getattr(view, f"get_{field}")()
                max_error = max(max_error, float(
                    (current - expected.to(device=device)).abs().max()))
        restored_digest = _digest([branch.snapshot() for branch in branches])
        expected_digest = _digest(state["branches"])
        return max_error, restored_digest == expected_digest


def _configure_env(cfg, *, slots: int, clone_stack: ExitStack) -> None:
    cfg.sim.physx.enable_enhanced_determinism = True
    cfg.scene.world.prim_path = "{ENV_REGEX_NS}/BenchmarkScene"
    cfg.scene.num_envs = int(slots)
    cfg.scene.env_spacing = 256.0
    cfg.scene.replicate_physics = True
    cfg.scene.clone_in_fabric = False
    cfg.scene.filter_collisions = True
    cfg.episode_length_s = 10000.0
    cfg.viewer.origin_type = "env"
    cfg.viewer.env_index = 0
    clone_stack.enter_context(_city_global_static_instances(source_usd=CITY_USD))


def _save_checkpoint(output: Path, store, checkpoint: IsaacCheckpoint,
                     branches: list[CityBusinessBranch], actions: dict,
                     *, control_step: int, phase: str, device, report: dict,
                     branch_batch=None, branch_data_adapter=None,
                     branch_id_by_slot: dict[int, str] | None = None,
                     source_revision_value: str | None = None) -> None:
    import torch

    state = checkpoint.capture(branches, control_step=control_step, phase=phase)
    pre_digest = _digest([branch.snapshot() for branch in branches])
    path = output / "checkpoints" / f"{control_step:04d}_{phase}.pt"
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state, path)
    try:
        reread = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        reread = torch.load(path, map_location="cpu")
    # Perturb both the physical batch and the business state before restoring.
    for branch in branches:
        branch.advance(0.25)
    checkpoint.env.step(actions)
    control_step_after = control_step + 1
    restore_error, task_passed = checkpoint.restore(reread, branches, device)
    post_digest = _digest([branch.snapshot() for branch in branches])
    passed = (restore_error <= 1e-4 and task_passed and pre_digest == post_digest)
    record = {
        "control_step": int(control_step),
        "phase": phase,
        "path": str(path),
        "saved": path.is_file(),
        "restore_passed": bool(passed),
        "restore_max_abs_error": restore_error,
        "task_state_restore_passed": bool(task_passed and pre_digest == post_digest),
        "probe_control_step": control_step_after,
        "continued_after_restore": True,
        "task_state_digest": pre_digest,
        "rss_gib": _rss_gib(),
        "gpu_allocated_gib": torch.cuda.memory_allocated() / 2**30,
        "gpu_reserved_gib": torch.cuda.memory_reserved() / 2**30,
    }
    report.setdefault("checkpoints", []).append(record)
    if branch_batch is not None and branch_data_adapter is not None:
        if branch_id_by_slot is None:
            raise ValueError("branch_id_by_slot is required for City branch data")
        for slot, branch in enumerate(branches):
            branch_id = branch_id_by_slot.get(slot)
            if branch_id is None:
                identity = branch_data_adapter.identity(
                    slot,
                    checkpoint_digest=pre_digest,
                    source_revision=source_revision_value
                    or branch_batch.manifest["source_revision"],
                )
                branch_batch.register_branch(identity)
                branch_id = identity.branch_id
                branch_id_by_slot[slot] = branch_id
            branch_batch.record_checkpoint(
                branch_id,
                checkpoint_id=phase,
                simulator_step=control_step,
                snapshot_digest=pre_digest,
                snapshot_schema=branch_data_adapter.snapshot_schema,
                restore_error=restore_error,
                restore_passed=bool(passed),
                restore_status="measured",
                restore_error_source="max_abs_public_city_physics_and_task_error",
                payload={
                    "checkpoint_path": str(path),
                    "task_state_restore_passed": bool(task_passed and pre_digest == post_digest),
                    "continued_after_restore": True,
                    "phase": phase,
                },
            )
            branch_batch.record_event(
                branch_id,
                event_type="checkpoint_restored",
                simulator_step=control_step,
                sim_time_s=control_step * float(checkpoint.env.step_dt),
                payload={"checkpoint_id": phase, "restore_error": restore_error},
            )
            branch_batch.record_progress(
                branch_id,
                simulator_step=control_step,
                sim_time_s=control_step * float(checkpoint.env.step_dt),
                progress={
                    "phase": branch.phase,
                    "checkpoint": phase,
                    "people_complete": bool(branch.people_complete()),
                    "traffic_complete": bool(
                        branch.traffic_complete()
                        if branch.phase in {
                            "traffic_requested", "traffic_acknowledged",
                            "secondary_traffic_protected", "report_submitted",
                            "response_access_preserved",
                        }
                        else False
                    ),
                },
            )
    if not passed:
        raise RuntimeError(f"City checkpoint restore failed at {phase}: {record}")


def _advance_physics(env, actions: dict, branches: list[CityBusinessBranch], dt: float,
                     *, control_step: int, branch_batch=None,
                     branch_data_adapter=None,
                     branch_id_by_slot: dict[int, str] | None = None) -> int:
    for branch in branches:
        branch.advance(dt)
    env.step(actions)
    control_step += 1
    if branch_batch is not None and branch_data_adapter is not None:
        if branch_id_by_slot is None:
            raise ValueError("branch_id_by_slot is required for City step data")
        command_rows = branch_data_adapter.command_rows(actions)
        post_states = branch_data_adapter.read_all_branch_states({"env": env})
        records = []
        sim_time_s = control_step * float(env.step_dt)
        for slot, branch in enumerate(branches):
            branch_id = branch_id_by_slot.get(slot)
            if branch_id is None:
                raise ValueError(f"City branch {slot} is not registered before step")
            completed = branch.phase == "response_access_preserved"
            records.append((branch_id, {
                "simulator_step": control_step,
                "sim_time_s": sim_time_s,
                "commands": command_rows[slot],
                "post_step_state": post_states[slot],
                "phase_state": branch_data_adapter.phase_state(slot),
                "task_state": branch_data_adapter.task_state(slot),
                "active": not completed,
                "completed": completed,
                "scene_payload": branch_data_adapter.scene_payload(slot),
            }))
        branch_batch.record_step_batch(records)
    return control_step


def _run_logic_only(args) -> int:
    scenario = json.loads(CITY_SCENARIO.read_text(encoding="utf-8"))
    branches = [CityBusinessBranch(index, scenario) for index in range(args.slots)]
    for branch in branches:
        branch.mark_incident_assessed(1.0)
        branch.mark_dispatch(2.0)
        branch.set_branch_delay(0.1 * branch.branch_id)
        branch.guide_when_ready()
    for _ in range(160):
        for branch in branches:
            if not branch.people_complete():
                branch.advance(1.0)
        if all(branch.people_complete() for branch in branches):
            break
    else:
        raise RuntimeError("logic-only City branches did not finish affected-person movement")
    for branch in branches:
        branch.mark_people_complete()
        branch.start_traffic()
        branch.advance(1.0)
        branch.acknowledge_traffic()
        branch.advance(5.1)
        branch.mark_traffic_complete()
        branch.submit_report()
        branch.advance(2.0)
        branch.receive_report()
        branch.advance(1.0)
    results = [branch.evidence()["acceptance"] for branch in branches]
    if not all(result["mission_success"] for result in results):
        raise RuntimeError("logic-only City acceptance failed")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "logic_report.json", {
        "schema": "eai.city.logic_only_32_branch.v1",
        "requested": args.slots,
        "completed": len(results),
        "mission_success_count": sum(result["mission_success"] for result in results),
        "formal_episode_binding": False,
        "physical_task_validated": False,
    })
    print(json.dumps({"status": "logic_only_passed", "completed": len(results)}, ensure_ascii=False))
    return 0


def _run_worker(args) -> int:
    import torch
    from simulator import SimulatorLaunchConfig, open_simulator_session
    from demo.benchmark.branch_data import open_batch, source_revision, validate_run
    from demo.city_benchmark.branch_data_adapter import CityBranchDataAdapter

    env_data = json.loads(ENV_FILE.read_text(encoding="utf-8"))
    scenario = json.loads(CITY_SCENARIO.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    clone_stack = ExitStack()
    branches = [CityBusinessBranch(index, scenario) for index in range(args.slots)]
    branch_data_adapter = CityBranchDataAdapter(
        scenario=scenario,
        scenario_path=CITY_SCENARIO,
        env_data=env_data,
        branches=branches,
    )
    report = {
        "schema": "eai.city.32_branch_checkpoint.v1",
        "scene_key": "city",
        "environment": env_data.get("task_name"),
        "scene_asset": str(CITY_USD),
        "scenario_asset": str(CITY_SCENARIO),
        "scenario_sha256": _sha256(CITY_SCENARIO),
        "requested": args.slots,
        "resident": 0,
        "active": 0,
        "peak_active": 0,
        "completed": 0,
        "queued": args.slots,
        "independently_verified": 0,
        "headless": True,
        "ros_enabled": False,
        "device_requested": "cuda:0",
        "business_runtime": "demo.city_benchmark.response + traffic + incident + task_logic",
        "formal_episode_binding": False,
        "fixture_scope": "deterministic fixture derived from checked-in City pedestrian and vehicle routes",
        "physical_scope": "32 resident Carter articulation rows with shared City static geometry",
        "checkpoint_scope": "Carter public articulation state, command/action buffers, City business runtime, RNG",
        "solver_cache_serialized": False,
        "physical_task_validated": False,
        "full_task_completed": False,
        "business_task_completed": False,
        "status": "starting",
        "started_unix": time.time(),
        "checkpoint_keys": list(CHECKPOINT_KEYS),
    }
    _write_json(output / "report.json", report)
    _write_json(output / "environment.json", env_data)
    _write_json(output / "task_scenario.json", scenario)
    branch_batch = open_batch(
        output / "branch_data",
        scene_key=branch_data_adapter.scene_key,
        task_id=branch_data_adapter.task_id,
        requested=args.slots,
        execution_scope="measured",
        task_version=branch_data_adapter.task_version,
        source_revision_value=source_revision(ROOT),
        formal=args.slots == 32,
        metadata={
            "scenario": str(CITY_SCENARIO),
            "scenario_sha256": report["scenario_sha256"],
            "runner": "demo.city_benchmark.checkpoint_branches",
            "fixture_scope": report["fixture_scope"],
            "physical_scope": report["physical_scope"],
        },
        autosave_every=max(128, args.slots * 8),
    )
    branch_id_by_slot: dict[int, str] = {}
    report["execution_scope"] = "measured"
    report["branch_data_output"] = str(output / "branch_data")
    _write_json(output / "report.json", report)

    config = SimulatorLaunchConfig(
        env=env_data["task_name"], resolved_env_name=env_data["task_name"],
        selection_data=env_data, num_envs=args.slots, device="cuda:0", seed=17,
        headless=True, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args={
            "headless": True,
            "enable_cameras": False,
            "device": "cuda:0",
            "kit_args": (
                "--/app/settings/persistent=false "
                "--/rtx-transient/stableIds/enabled=true"
            ),
        },
        env_cfg_hook=lambda cfg: _configure_env(
            cfg, slots=args.slots, clone_stack=clone_stack),
    )

    try:
        with open_simulator_session(config) as session:
            from isaacsim.core.simulation_manager import SimulationManager
            from pxr import PhysxSchema

            env = session.base_env
            report["actual_device"] = str(env.sim.device)
            report["simulation_manager_device"] = SimulationManager.get_physics_sim_device()
            physics_scene = PhysxSchema.PhysxSceneAPI(
                env.scene.stage.GetPrimAtPath(env.cfg.sim.physics_prim_path))
            report["gpu_dynamics"] = bool(physics_scene.GetEnableGPUDynamicsAttr().Get())
            if report["actual_device"] != "cuda:0" or report["simulation_manager_device"] != "cuda:0":
                raise RuntimeError("City 32 branch test requires actual CUDA PhysX")
            counts = {name: int(robot.root_physx_view.count)
                      for name, robot in env.scene.articulations.items()}
            report["articulation_counts"] = counts
            if set(counts.values()) != {args.slots}:
                raise RuntimeError(f"missing City resident articulation rows: {counts}")
            report["resident"] = args.slots
            report["active"] = args.slots
            report["peak_active"] = args.slots
            report["queued"] = 0
            report["possible_agents"] = list(session.possible_agents)
            report["control_dt_s"] = float(env.step_dt)
            report["physics_dt_s"] = float(env.physics_dt)
            report["shared_city_static_instances"] = int(
                getattr(env.scene, "city_checkpoint_shared_instances", 0))
            report["shared_city_collision_geometries"] = int(
                getattr(env.scene, "city_checkpoint_shared_collision_geometries", 0))
            if report["possible_agents"] != ["carter_1"]:
                raise RuntimeError(f"unexpected physical City agent set: {report['possible_agents']}")
            _write_json(output / "report.json", report)

            actions = {
                name: torch.zeros((args.slots, 3), device=session.device)
                for name in session.possible_agents
            }
            for name in session.possible_agents:
                env.set_command(name, "base_velocity", actions[name])
            checkpoint = IsaacCheckpoint(env)
            control_step = 0

            def checkpoint_at(phase: str):
                nonlocal control_step
                _save_checkpoint(
                    output, None, checkpoint, branches, actions,
                    control_step=control_step, phase=phase,
                    device=session.device, report=report,
                    branch_batch=branch_batch,
                    branch_data_adapter=branch_data_adapter,
                    branch_id_by_slot=branch_id_by_slot,
                    source_revision_value=branch_batch.manifest["source_revision"])
                _write_json(output / "report.json", report)

            checkpoint_at("episode_start")
            control_step = _advance_physics(env, actions, branches, 1.0,
                                            control_step=control_step,
                                            branch_batch=branch_batch,
                                            branch_data_adapter=branch_data_adapter,
                                            branch_id_by_slot=branch_id_by_slot)
            for branch in branches:
                branch.mark_incident_assessed(1.0)
            checkpoint_at("incident_assessed")
            control_step = _advance_physics(env, actions, branches, 1.0,
                                            control_step=control_step,
                                            branch_batch=branch_batch,
                                            branch_data_adapter=branch_data_adapter,
                                            branch_id_by_slot=branch_id_by_slot)
            for branch in branches:
                branch.mark_dispatch(2.0)
            checkpoint_at("response_dispatch")

            for index, branch in enumerate(branches):
                branch.set_branch_delay(0.1 * index)
            report["branch_delays_s"] = [branch.delay_s for branch in branches]
            report["stage"] = "parallel_people_response"
            _write_json(output / "report.json", report)

            for branch in branches:
                branch.guide_when_ready()
            for _ in range(args.people_max_steps):
                control_step = _advance_physics(env, actions, branches, 1.0,
                                                control_step=control_step,
                                                branch_batch=branch_batch,
                                                branch_data_adapter=branch_data_adapter,
                                                branch_id_by_slot=branch_id_by_slot)
                if all(branch.people_complete() for branch in branches):
                    break
            else:
                raise RuntimeError("32 City branches did not complete affected-person response")
            for branch in branches:
                branch.mark_people_complete()
            checkpoint_at("exposed_people_accounted_for")

            for branch in branches:
                branch.start_traffic()
            control_step = _advance_physics(env, actions, branches, 1.0,
                                            control_step=control_step,
                                            branch_batch=branch_batch,
                                            branch_data_adapter=branch_data_adapter,
                                            branch_id_by_slot=branch_id_by_slot)
            for branch in branches:
                branch.acknowledge_traffic()
            # Keep a small numerical margin above the five-second reaction
            # window so decimal branch clocks cannot land just below it.
            for _ in range(6):
                control_step = _advance_physics(env, actions, branches, 1.0,
                                                control_step=control_step,
                                                branch_batch=branch_batch,
                                                branch_data_adapter=branch_data_adapter,
                                                branch_id_by_slot=branch_id_by_slot)
            if not all(branch.traffic_complete() for branch in branches):
                raise RuntimeError("32 City branches did not complete traffic protection")
            for branch in branches:
                branch.mark_traffic_complete()
            checkpoint_at("secondary_traffic_protected")

            for branch in branches:
                branch.submit_report()
            for _ in range(2):
                control_step = _advance_physics(env, actions, branches, 1.0,
                                                control_step=control_step,
                                                branch_batch=branch_batch,
                                                branch_data_adapter=branch_data_adapter,
                                                branch_id_by_slot=branch_id_by_slot)
            for branch in branches:
                branch.receive_report()
            control_step = _advance_physics(env, actions, branches, 1.0,
                                            control_step=control_step,
                                            branch_batch=branch_batch,
                                            branch_data_adapter=branch_data_adapter,
                                            branch_id_by_slot=branch_id_by_slot)
            results = [branch.evidence() for branch in branches]
            acceptance = [item["acceptance"] for item in results]
            success_count = sum(item["mission_success"] for item in acceptance)
            report["business_task_completed"] = success_count == args.slots
            report["business_mission_success_count"] = success_count
            report["business_acceptance_failures"] = [
                item["failure_codes"] for item in acceptance if not item["mission_success"]]
            report["branch_reports"] = [{
                "branch_id": index,
                "delay_s": branches[index].delay_s,
                "phase": branches[index].phase,
                "clock_s": branches[index].clock,
                "acceptance": acceptance[index],
                "task_state_digest": _digest(branches[index].snapshot()),
            } for index in range(args.slots)]
            checkpoint_at("response_access_preserved")

            for index, branch in enumerate(branches):
                branch_id = branch_id_by_slot[index]
                branch_success = bool(acceptance[index]["mission_success"])
                branch_batch.record_network(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    network={
                        "messages_offered": 4,
                        "messages_delivered": 4,
                        "service_events": [
                            "incident_observation",
                            "people_guidance",
                            "traffic_hold_ack",
                            "incident_report_receipt",
                        ],
                        "scope": "City business fixture service runtime",
                    },
                )
                branch_batch.record_cost(
                    branch_id,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    cost={
                        "control_steps": control_step,
                        "branch_clock_s": float(branch.clock),
                        "participant_messages": 4,
                        "system_commands": control_step,
                        "effective_execution": branch_success,
                    },
                )
                branch_batch.record_event(
                    branch_id,
                    event_type="task_complete" if branch_success else "task_failed",
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    payload={
                        "phase": branch.phase,
                        "mission_success": branch_success,
                        "fixture_scope": report["fixture_scope"],
                    },
                )
                branch_batch.finish_branch(
                    branch_id,
                    completed=branch_success,
                    independently_verified=branch_success,
                    simulator_step=control_step,
                    sim_time_s=control_step * float(env.step_dt),
                    outcome={
                        "acceptance": acceptance[index],
                        "physical_task_validated": False,
                        "fixture_scope": report["fixture_scope"],
                    },
                )
            branch_batch.close_batch(
                status="completed",
                resident=report["resident"],
                peak_active=report["peak_active"],
                queued=report["queued"],
                details={
                    "business_task_completed": success_count == args.slots,
                    "physical_task_validated": False,
                    "fixture_scope": report["fixture_scope"],
                },
            )
            report["branch_data_validation"] = validate_run(branch_batch.output)
            report["branch_data_counts"] = report["branch_data_validation"]["metrics"]

            report["completed"] = success_count
            report["active"] = args.slots - success_count
            report["independently_verified"] = sum(
                item["acceptance"]["mission_success"]
                and item["acceptance"]["execution_validated"] is False
                for item in results)
            report["physics_steps"] = control_step
            report["advanced_branch_steps"] = args.slots * control_step
            report["full_task_completed"] = False
            report["completion_claimed"] = False
            report["status"] = "bounded_32_branch_city_business_checkpoint_completed"
            report["limitation"] = (
                "All 32 business runtimes completed and restored at six barriers, but the "
                "checked-in benchmark_city environment has only Carter rows and no formal "
                "episode binding. Native affected-person, following-vehicle, incident-report "
                "receipt, and continuous-access PhysX evidence therefore remain unvalidated."
            )
            report["finished_unix"] = time.time()
            _write_json(output / "report.json", report)
            print(json.dumps({
                "status": report["status"],
                "requested": report["requested"],
                "resident": report["resident"],
                "completed": report["completed"],
                "independently_verified": report["independently_verified"],
                "checkpoints": len(report.get("checkpoints", [])),
                "physical_task_validated": report["physical_task_validated"],
            }, ensure_ascii=False), flush=True)
    except BaseException as exc:
        try:
            branch_batch.close_batch(
                status="failed",
                resident=int(report.get("resident", 0)),
                peak_active=int(report.get("peak_active", 0)),
                queued=int(report.get("queued", args.slots)),
                details={"error": f"{type(exc).__name__}: {exc}"},
            )
            report["branch_data_validation"] = validate_run(branch_batch.output)
        except BaseException as ledger_error:
            report["branch_data_validation_error"] = repr(ledger_error)
        report.update(status="failed", error=repr(exc), traceback=traceback.format_exc(),
                      finished_unix=time.time())
        _write_json(output / "report.json", report)
        traceback.print_exc()
        return 1
    finally:
        clone_stack.close()
    return 0 if report.get("business_task_completed") and report.get("completed") == args.slots else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", type=int, default=32)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--people-max-steps", type=int, default=180)
    parser.add_argument("--logic-only", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if type(args.slots) is not int or not 1 <= args.slots <= 32:
        raise SystemExit("--slots must be between 1 and 32")
    if args.logic_only:
        return _run_logic_only(args)
    # AppLauncher must be imported only inside the worker path.
    return _run_worker(args)


if __name__ == "__main__":
    raise SystemExit(main())
