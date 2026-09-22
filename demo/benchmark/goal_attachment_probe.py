"""Warehouse ideal FixedJoint microdiagnostic; no business completion claims.

Prepare without Isaac; execute starts one lock-owning worker through the shared
session API. Only M20_2's experimental spawn is changed. The runner samples raw
PhysX tensors; public payload/reach and native object configuration stay frozen.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import traceback
import uuid

from . import goal_physical_probe as physical
from .provenance import canonical_sha256, file_sha256

ROOT = Path(__file__).resolve().parents[2]
MEMBER = "warehouse_ground_3"
OBJECT = "order_carton_01"
PHASES = (("settle", 240, [0., 0., 0.]),
          ("attached_reverse", 100, [-.1, 0., 0.]),
          ("detached_reverse", 100, [-.1, 0., 0.]))


def build_probe_selection(deployment):
    selection = physical.build_physical_selection(deployment)
    if deployment["scene_key"] != "warehouse_nvd":
        raise ValueError("attachment probe only supports warehouse_nvd")
    rows = deployment["robot_instances"]
    index = next(i for i, row in enumerate(rows) if row["public_id"] == MEMBER)
    if rows[index]["runtime_id"] != "m20_2" or rows[index]["type"] != "m20":
        raise ValueError("reviewed M20_2 identity changed")
    old = deepcopy(selection["robots"][index]["spawn_pose"])
    selection["robots"][index]["spawn_pose"]["position"] = [-7.50, -10.05, .53]
    carton = next(r for r in deployment["object_instances"] if r["public_id"] == OBJECT)
    if (carton["physical_properties"]["mass_kg"] != 3
            or carton["scene_binding"]["deployment_directive"]["position_m"] != [-7., -10.05, .15]):
        raise ValueError("reviewed frozen carton mass/spawn changed")
    adjustment = {"public_id": MEMBER, "runtime_id": "m20_2", "formal_task_spawn": False,
                  "reason": "author_only_ideal_attachment_microdiagnostic",
                  "original_spawn_pose": old,
                  "experimental_spawn_pose": deepcopy(selection["robots"][index]["spawn_pose"])}
    return selection, adjustment


def _sources(deployment):
    result = physical._probe_sources(deployment)
    for name in ("goal_attachment_probe.py", "goal_native_attachment.py",
                 "goal_driven_contract.py", "goal_driven_tasks.py"):
        path = Path(__file__).with_name(name)
        result[str(path)] = file_sha256(path)
    result.update(deployment.get("compiler_sources", {}))
    capture_source = ROOT / "demo/city_benchmark/scene/presentation.py"
    result[str(capture_source)] = file_sha256(capture_source)
    return result


def _request(deployment, selection, adjustment, *, run_id, gui, device, formal_binding=None):
    return {"schema": "eai.benchmark.goal_attachment_request", "version": 2,
            "run_id": run_id, "scene_key": "warehouse_nvd", "gui": gui, "device": device,
            "deployment_sha256": deployment["sha256"],
            "selection_sha256": canonical_sha256(selection), "source_files": _sources(deployment),
            "spawn_adjustment": adjustment, "phases": deepcopy(PHASES),
            "member_id": MEMBER, "object_id": OBJECT, "mode_id": "single_grip",
            "point_id": "center_grip", "negative_cases": ["overload_crate", "inactive", "out_of_reach"],
            "scope": "ideal_fixed_point_microdiagnostic", "scoreable": False,
            "formal_binding": deepcopy(formal_binding)}


def _validate_formal_binding(binding, public):
    if not isinstance(binding, dict) or set(binding) != {"episode_id", "plan", "dispatch_action_id"}:
        raise ValueError("invalid formal attachment binding")
    if any(not isinstance(binding[key], str) or not binding[key].strip()
           for key in ("episode_id", "dispatch_action_id")):
        raise ValueError("invalid formal attachment identity")
    if binding["dispatch_action_id"] in {"formal_select", "formal_plan"}:
        raise ValueError("formal attachment dispatch identity collides with host actions")
    plan = binding["plan"]
    if not isinstance(plan, dict) or plan.get("assigned_team") != [MEMBER]:
        raise ValueError("formal attachment requires the reviewed single carrier")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise ValueError("formal attachment requires exactly one task")
    task = tasks[0]
    if (task.get("skill") != "pick" or task.get("assigned_agents") != [MEMBER]
            or task.get("arguments") != {
                "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                "handling_mode_id": "single_grip",
                "carrier_bindings": [{"agent_id": MEMBER, "point_id": "center_grip"}],
            }):
        raise ValueError("formal attachment task differs from native failure case")
    if not isinstance(task.get("task_id"), str) or not task["task_id"].strip():
        raise ValueError("formal attachment task identity is missing")
    if not any(row["id"] == MEMBER for row in public["candidate_robot_pool"]):
        raise ValueError("formal attachment carrier is absent from the deployment")


def prepare_probe(output_root, *, gui=False, device="cuda:0", formal_binding=None):
    """Freeze source-bound input files; no simulator or GPU launch."""
    if type(gui) is not bool or device not in {"cpu", "cuda:0"}:
        raise ValueError("invalid GUI/device selection")
    deployment = physical.build_scene_deployment("warehouse_nvd")
    selection, adjustment = build_probe_selection(deployment)
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
    interactive_selection_from_dict(selection)
    if formal_binding is not None:
        _validate_formal_binding(formal_binding, deployment["public_task"])
    request = _request(deployment, selection, adjustment, run_id=uuid.uuid4().hex,
                       gui=gui, device=device, formal_binding=formal_binding)
    root = Path(output_root).absolute()
    root.mkdir(parents=True, exist_ok=False)
    for name, value in (("deployment.json", deployment), ("selection.json", selection), ("request.json", request)):
        physical.write_json(root / name, value)
    return request


def load_probe(output_root, *, rebuild=True):
    root = Path(output_root)
    request, deployment, selection = (json.loads((root / name).read_text()) for name in
                                      ("request.json", "deployment.json", "selection.json"))
    if (request.get("schema") != "eai.benchmark.goal_attachment_request"
            or request.get("version") != 2
            or set(request) != {"schema", "version", "run_id", "scene_key", "gui", "device",
                                "deployment_sha256", "selection_sha256", "source_files",
                                "spawn_adjustment", "phases", "member_id", "object_id",
                                "mode_id", "point_id", "negative_cases", "scope", "scoreable",
                                "formal_binding"}
            or type(request.get("gui")) is not bool or request.get("device") not in {"cpu", "cuda:0"}
            or not isinstance(request.get("run_id"), str)
            or not re.fullmatch("[a-f0-9]{32}", request["run_id"])):
        raise ValueError("invalid attachment request")
    if canonical_sha256({k: v for k, v in deployment.items() if k != "sha256"}) != deployment["sha256"]:
        raise ValueError("deployment hash mismatch")
    expected_selection, adjustment = build_probe_selection(deployment)
    if request.get("formal_binding") is not None:
        _validate_formal_binding(request["formal_binding"], deployment["public_task"])
    expected = _request(deployment, expected_selection, adjustment, run_id=request["run_id"],
                        gui=request["gui"], device=request["device"],
                        formal_binding=request.get("formal_binding"))
    if canonical_sha256(request) != canonical_sha256(expected) or selection != expected_selection:
        raise ValueError("attachment request/source/selection changed")
    for path, digest in request["source_files"].items():
        if file_sha256(Path(path)) != digest:
            raise ValueError(f"attachment source changed: {path}")
    if rebuild:
        physical.validate_goal_deployment(deployment)
    return request, deployment, selection


def _list(tensor):
    result = tensor.detach().cpu().tolist()
    if not physical._finite(result):
        raise ValueError("nonfinite native PhysX tensor")
    return result


def _pose(transform, *, path, step, mass=None):
    if len(transform) != 7 or not physical._finite(transform):
        raise ValueError("unexpected PhysX transform")
    result = {"source": "physx", "body_path": path, "active": True, "physics_step": step,
              "position_m": transform[:3], "rotation_wxyz": [transform[6], *transform[3:6]]}
    if mass is not None:
        result["mass_kg"] = mass
    return result


class LiveBodies:
    """Reviewed public IDs -> actual tensor link paths; never match by leaf name."""

    def __init__(self, scene, deployment):
        self.scene = scene
        self.step = 0
        self.bindings = {"objects": {}, "members": {}}
        self.readers = {}
        self.robots = {}
        for row in deployment["robot_instances"]:
            view = scene.articulations[row["runtime_id"]].root_physx_view
            paths = list(view.link_paths[0])
            if not paths or len(view.link_paths) != 1 or len(set(paths)) != len(paths):
                raise ValueError("ambiguous native articulation links")
            # PhysX articulation link zero is its root rigid body. Preserve all
            # paths plus root transform separately as diagnostic evidence.
            path = paths[0]
            if not path.startswith(row["prim_path"] + "/") and path != row["prim_path"]:
                raise ValueError("native root link outside reviewed robot root")
            self.bindings["members"][row["public_id"]] = {"root_path": row["prim_path"], "body_path": path}
            self.readers[path] = ("link", view, 0)
            self.robots[row["public_id"]] = (row, view, paths)
        for row in deployment["object_instances"]:
            view = scene[row["runtime_id"]].root_physx_view
            paths = list(view.prim_paths)
            if paths != [row["prim_path"]]:
                raise ValueError("native object PhysX path mismatch")
            path = paths[0]
            self.bindings["objects"][row["public_id"]] = {"root_path": path, "body_path": path}
            self.readers[path] = ("rigid", view, 0)

    def __call__(self, path):
        kind, view, index = self.readers[path]
        if kind == "link":
            transform = _list(view.get_link_transforms()[0, index])
            mass = _list(view.get_masses()[0])[index]
        else:
            transform = _list(view.get_transforms()[index])
            masses = _list(view.get_masses()[index])
            mass = masses[0] if isinstance(masses, list) else masses
        return _pose(transform, path=path, step=self.step, mass=mass)

    def snapshot(self):
        return {"bodies": {path: self(path) for path in self.readers},
                "robot_roots": {key: {"runtime_id": row["runtime_id"], "prim_path": row["prim_path"],
                    "actual_link_paths": paths, "root_transform_xyzw": _list(view.get_root_transforms()[0])}
                    for key, (row, view, paths) in self.robots.items()}}


def anchor_quality(receipt, poses):
    """Live frame separation, including after detach; not a solver pass verdict."""
    from .goal_native_attachment import _rotate, _mul
    rows = []
    for joint in receipt["joint_readback"]:
        p0, p1 = poses[joint["body0"][0]], poses[joint["body1"][0]]
        def frame(pose, side):
            delta = _rotate(pose["rotation_wxyz"], joint[f"local_pos{side}"])
            return ([a+b for a, b in zip(pose["position_m"], delta)],
                    _mul(pose["rotation_wxyz"], joint[f"local_rot{side}_wxyz"]))
        x0, q0 = frame(p0, 0)
        x1, q1 = frame(p1, 1)
        cosine = abs(sum(a*b for a, b in zip(q0, q1))) / math.sqrt(sum(a*a for a in q0)*sum(b*b for b in q1))
        rows.append({"joint_path": joint["prim_path"], "anchor0_world_m": x0, "anchor1_world_m": x1,
                     "anchor_error_m": math.dist(x0, x1),
                     "orientation_error_rad": 2*math.acos(min(1., cosine))})
    return rows


def negative_checks(stage, public, live, active):
    from .goal_native_attachment import prepare_attachment
    cases = (("overload_crate", "order_crate_01", MEMBER, active, "payload"),
             ("inactive", OBJECT, MEMBER, set(active)-{MEMBER}, "currently active"),
             ("out_of_reach", OBJECT, "warehouse_ground_1", active, "distance exceeds reach"))
    results = []
    for name, obj, member, current, reason in cases:
        before = stage.GetEditTarget().GetLayer().ExportToString()
        error = None
        try:
            prepare_attachment(stage, public, object_id=obj, mode_id="single_grip",
                carriers={member: "center_grip"}, active_member_ids=current,
                body_bindings=live.bindings, pose_provider=live)
        except ValueError as exc:
            error = str(exc)
        unchanged = before == stage.GetEditTarget().GetLayer().ExportToString()
        results.append({"case": name, "error": error, "expected_error": reason,
                        "expected_rejection": error is not None and reason in error,
                        "zero_stage_effect": unchanged, "live_snapshot": live.snapshot()})
    return results


def require_native_objects(readback):
    rows = {row["public_id"]: row for row in readback["objects"]}
    for public_id in (OBJECT, "order_crate_01"):
        if rows.get(public_id, {}).get("status") != "measured":
            raise ValueError(f"native object configuration readback unresolved: {public_id}")


def _worker_lock():
    descriptor = int(os.environ["EAI_GOAL_PHYSICAL_WORKER_LOCK_FD"])
    actual, expected = os.fstat(descriptor), (ROOT / "tmp/goal_physical_probe.lock").stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise RuntimeError("worker requires canonical serial lock")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _formal_goal_overload_session(public, *, run_id, deployment, binding=None):
    """Build the participant-owned single-carrier overload attempt."""
    from .goal_driven_contract import GOAL_PLAN_VERSION, build_goal_participant_api
    from .goal_session import GoalSession

    api = build_goal_participant_api(public)
    episode_id = binding["episode_id"] if binding else f"warehouse-formal-overload:{run_id}"
    session = GoalSession(public, api, episode_id=episode_id)
    regions = {
        row["id"]: ("rack_aisle_a" if row["mobility"] == "ground" else "rack_overview")
        for row in public["candidate_robot_pool"]
    }
    objects = {
        row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    session.update_runtime_state(0.0, regions, objects)
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
        "plan_id": "formal_overload_plan", "assigned_team": [MEMBER],
        "tasks": [{
            "task_id": "formal_overload_crate", "skill": "pick",
            "arguments": {
                "object_id": "order_crate_01", "pickup_region_id": "rack_aisle_a",
                "handling_mode_id": "single_grip",
                "carrier_bindings": [{"agent_id": MEMBER, "point_id": "center_grip"}],
            },
            "required_capabilities": ["pick"], "assigned_agents": [MEMBER],
            "prerequisites": [], "evidence_refs": [],
            "execution": {"start_s": 0.0, "duration_s": 4.0},
        }],
    }
    def action(action_id, type_, payload):
        return {"schema": "eai.benchmark.goal_action", "version": 1,
                "action_id": action_id, "submitted_at_s": 0.0,
                "type": type_, "payload": payload}
    if binding:
        plan = deepcopy(binding["plan"])
    dispatch_action_id = binding["dispatch_action_id"] if binding else "formal_dispatch"
    accepted = [
        session.submit_action(action("formal_select", "select_team", {"agent_ids": [MEMBER]})),
        session.submit_action(action("formal_plan", "submit_plan", {"plan": plan})),
        session.submit_action(action(dispatch_action_id, "dispatch_skill",
                                     {"task_id": plan["tasks"][0]["task_id"], "evidence_refs": []})),
    ]
    return {"episode_id": episode_id, "session": session, "plan": plan,
            "task_id": plan["tasks"][0]["task_id"], "dispatch_action_id": dispatch_action_id,
            "regions": regions, "objects": objects, "accepted": accepted,
            "deployment_sha256": deployment["sha256"]}


def _execute_formal_overload_attempt(stage, deployment, live, active, formal_goal,
                                     attachment, root, *, completed_at_s=4.0):
    """Execute and archive the native failure for the admitted overload task."""
    crate_path = live.bindings["objects"]["order_crate_01"]["body_path"]
    layer_before = stage.GetEditTarget().GetLayer().ExportToString()
    crate_before = live(crate_path)
    formal_error = None
    try:
        attachment.prepare_attachment(
            stage, deployment["public_task"], object_id="order_crate_01",
            mode_id="single_grip", carriers={MEMBER: "center_grip"},
            active_member_ids=active, body_bindings=live.bindings,
            pose_provider=live)
    except ValueError as exc:
        formal_error = str(exc)
    layer_after = stage.GetEditTarget().GetLayer().ExportToString()
    crate_after = live(crate_path)
    if not formal_error:
        raise ValueError("formal overload attempt unexpectedly succeeded")
    zero_stage_effect = layer_before == layer_after
    zero_pose_effect = (
        crate_before["position_m"] == crate_after["position_m"]
        and crate_before["rotation_wxyz"] == crate_after["rotation_wxyz"])
    formal_evidence = {
        "schema": "eai.benchmark.formal_goal_skill_evidence", "version": 1,
        "episode_id": formal_goal["episode_id"],
        "task_id": formal_goal["task_id"], "status": "failed",
        "failure_reason": formal_error,
        "object_id": "order_crate_01", "object_mass_kg": 12.0,
        "object_moved": False, "task_advanced": False,
        "zero_stage_effect": zero_stage_effect,
        "zero_pose_effect": zero_pose_effect,
        "before": crate_before, "after": crate_after,
        "source": "native_physx_attachment_admission",
    }
    evidence_ref = "sha256:" + canonical_sha256(formal_evidence)
    formal_receipt = {
        "backend_id": "warehouse_native_formal_attachment_v1",
        "receipt_ref": evidence_ref,
        "task_id": formal_goal["task_id"], "action_id": formal_goal["dispatch_action_id"],
        "status": "failed", "completed_at_s": completed_at_s,
        "episode_id": formal_goal["episode_id"],
        "evidence_refs": [evidence_ref],
    }
    formal_completion = formal_goal["session"].complete_skill(
        formal_goal["task_id"], time_s=completed_at_s, status="failed", receipt=formal_receipt)
    formal_goal["session"].update_runtime_state(
        completed_at_s, formal_goal["regions"], formal_goal["objects"])
    formal_goal_report = {
        "episode_id": formal_goal["episode_id"],
        "accepted_actions": formal_goal["accepted"],
        "completion": formal_completion,
        "evidence": formal_evidence,
        "receipt": formal_receipt,
        "ledger": formal_goal["session"].report(),
        "execution_validated": False, "scoreable": False,
    }
    physical.write_json(root / "formal_goal_ledger_after.json", formal_goal_report)
    return {
        "status": "failed_as_expected", "failure_reason": formal_error,
        "object_moved": False, "task_advanced": False,
        "zero_stage_effect": zero_stage_effect, "zero_pose_effect": zero_pose_effect,
        "receipt_ref": evidence_ref, "execution_validated": False, "scoreable": False,
    }


def _command(root):
    return [sys.executable, "-m", "demo.benchmark.goal_attachment_probe", "--worker", "--output-root", str(root)]


def run_worker(output_root):
    _worker_lock()
    root = Path(output_root).absolute()
    request, deployment, selection = load_probe(root, rebuild=False)
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must not be imported before AppLauncher")
    launch = json.loads((root / "launch.json").read_text())
    if (launch.get("command") != _command(root) or launch.get("run_id") != request["run_id"]
            or launch.get("request_sha256") != canonical_sha256(request)):
        raise ValueError("worker launch/request mismatch")
    formal_goal = _formal_goal_overload_session(
        deployment["public_task"], run_id=request["run_id"], deployment=deployment,
        binding=request.get("formal_binding"))
    physical.write_json(root / "formal_goal_ledger_before.json", {
        "episode_id": formal_goal["episode_id"],
        "accepted_actions": formal_goal["accepted"],
        "ledger": formal_goal["session"].report(),
    })
    from simulator import SimulatorLaunchConfig, open_simulator_session
    report = {"schema": "eai.benchmark.goal_attachment_probe", "version": 1, "run_id": request["run_id"],
              "request_sha256": canonical_sha256(request), "status": "starting", "samples": [],
              "spawn_adjustment": request["spawn_adjustment"], "captures": [],
              "business_outcomes_validated": False, "solver_response_validated": False,
              "coordinated_motion_validated": False, "scoreable": False,
              "formal_goal_episode": {"episode_id": formal_goal["episode_id"],
                                      "task_id": formal_goal["task_id"],
                                      "execution_validated": False, "scoreable": False}}
    def save():
        physical.write_json(root / "attachment_report.json", report)
    @contextmanager
    def archive_session_failures():
        # Exit before the session: Kit close may terminate the process before
        # an outer exception handler can run. Preserve the original failure.
        try:
            yield
        except BaseException as exc:
            report.update(status="failed", error=repr(exc), traceback=traceback.format_exc(),
                          native_step_count=len(report["samples"]))
            save()
            raise
    def configure(cfg):
        try:
            from .goal_native_objects import configure_goal_objects
            cfg.episode_length_s = 3600.
            cfg.sim.render_interval = max(1, round(.1 / cfg.sim.dt))
            report["object_application"] = configure_goal_objects(cfg, deployment)
            save()
        except BaseException as exc:
            report.update(status="failed", error=repr(exc), traceback=traceback.format_exc())
            save()
            raise
    config = SimulatorLaunchConfig(env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=7, headless=not request["gui"],
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True, env_cfg_hook=configure)
    save()
    try:
        with open_simulator_session(config) as session, archive_session_failures():
            import torch
            import omni.usd
            from . import goal_native_attachment as attachment
            from .goal_native_objects import read_goal_objects
            if request["gui"]:
                # Freeze the repository capture module's transitive imports
                # before control, rather than first observing them at the end.
                from demo.city_benchmark.scene import presentation as _capture_module
            stage = omni.usd.get_context().get_stage()
            live = LiveBodies(session.base_env.scene, deployment)
            live.step = int(session.base_env.sim.current_time_step_index)
            report["body_bindings"] = live.bindings
            report["actual_device"] = session.device
            report["timing"] = {"physics_dt_s": float(session.base_env.sim.get_physics_dt()),
                                "control_step_dt_s": float(session.base_env.step_dt),
                                "decimation": int(session.env_cfg.decimation)}
            report["initial_native_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            report["initial_native_snapshot"] = live.snapshot()
            report["runtime_source_files"] = physical.runtime_sources(stage)
            active = set(live.bindings["members"])
            aerial = {row["runtime_id"]: session.base_env.scene.articulations[row["runtime_id"]].data.root_pos_w.clone()
                      for row in deployment["robot_instances"] if row["public_declaration"]["mobility"] == "aerial"}
            receipt = None
            for phase, steps, velocity in PHASES:
                if phase == "attached_reverse":
                    report["settled_native_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
                    save()
                    require_native_objects(report["settled_native_objects"])
                    # Formal GoalSession slice is available whenever the live
                    # deployment exposes the reviewed crate binding. Older
                    # unit doubles exercise the original carton-only path.
                    if "order_crate_01" in live.bindings["objects"]:
                        report["formal_goal_episode"].update(_execute_formal_overload_attempt(
                            stage, deployment, live, active, formal_goal, attachment, root,
                            completed_at_s=(report["samples"][-1]["time_s"]
                                            if request.get("formal_binding") else 4.0)))
                    report["negative_checks"] = negative_checks(stage, deployment["public_task"], live, active)
                    save()
                    if not all(r["expected_rejection"] and r["zero_stage_effect"] for r in report["negative_checks"]):
                        raise ValueError("negative diagnostic did not reject for its expected reason")
                    prepared = attachment.prepare_attachment(stage, deployment["public_task"], object_id=OBJECT,
                        mode_id="single_grip", carriers={MEMBER: "center_grip"}, active_member_ids=active,
                        body_bindings=live.bindings, pose_provider=live)
                    receipt = attachment.commit_attachment(stage, prepared, active_member_ids=active, pose_provider=live)
                    report["attachment_receipt"] = receipt
                    report["attach_snapshot"] = live.snapshot()
                    report["attach_anchor_quality"] = anchor_quality(receipt, report["attach_snapshot"]["bodies"])
                    save()
                if phase == "detached_reverse":
                    report["detach_receipt"] = attachment.detach_attachment(stage, receipt)
                    report["detach_snapshot"] = live.snapshot()
                    report["detach_repeat"] = attachment.detach_attachment(stage, receipt)
                    save()
                for _ in range(steps):
                    if not session.simulation_app.is_running():
                        raise RuntimeError("Isaac application closed before diagnostic completion")
                    actions = {agent: torch.zeros((1, 3), device=session.device) for agent in session.possible_agents}
                    actions["m20_2"][0] = torch.tensor(velocity, device=session.device)
                    for agent, goal in aerial.items():
                        session.base_env.set_command(agent, "goal_position", goal)
                        actions[agent] = goal.clone()
                    session.env.step(actions)
                    live.step = int(session.base_env.sim.current_time_step_index)
                    count = len(report["samples"]) + 1
                    sample = {"step": count, "physics_step": live.step, "phase": phase, "time_s": float(session.base_env.sim.current_time),
                              "commands": {key: _list(value[0]) for key, value in actions.items()}, **live.snapshot()}
                    if receipt:
                        sample["anchor_quality"] = anchor_quality(receipt, sample["bodies"])
                        sample["joint_present"] = [bool(stage.GetPrimAtPath(j["prim_path"])) for j in receipt["joint_readback"]]
                    report["samples"].append(sample)
                    if count % 20 == 0:
                        save()
                if phase == "attached_reverse" and request["gui"]:
                    current = live.snapshot()
                    paths = [live.bindings["members"][key]["body_path"] for key in (MEMBER, "warehouse_aerial_1")]
                    paths.append(live.bindings["objects"][OBJECT]["body_path"])
                    capture = physical._camera_capture(session, root,
                        [current["bodies"][path]["position_m"] for path in paths],
                        "close_iris_m20_carton_attached", scene_key="warehouse_nvd")
                    capture.update(body_paths=paths, joint_paths=[j["prim_path"] for j in receipt["joint_readback"]],
                        scope="single_frame_native_entities_joint_is_nonrendering",
                        framing_limit="Iris frozen spawn is distant; framing includes all three, not guaranteed close-up",
                        native_snapshot=current)
                    report["captures"].append(capture)
                    save()
            report["final_native_objects"] = read_goal_objects(stage, session.base_env.scene, deployment)
            report["final_native_snapshot"] = live.snapshot()
            report["native_step_count"] = len(report["samples"])
            report["quality_summary"] = {phase: {
                "max_anchor_error_m": max(q["anchor_error_m"] for s in report["samples"] if s["phase"] == phase for q in s["anchor_quality"]),
                "max_orientation_error_rad": max(q["orientation_error_rad"] for s in report["samples"] if s["phase"] == phase for q in s["anchor_quality"])}
                for phase in ("attached_reverse", "detached_reverse")}
            final_sources = physical.runtime_sources(stage)
            for inventory in (request["source_files"], report["runtime_source_files"], final_sources):
                for path, digest in inventory.items():
                    if file_sha256(Path(path)) != digest:
                        raise ValueError(f"source changed during diagnostic: {path}")
            if not report["runtime_source_files"].keys() <= final_sources.keys():
                raise ValueError("initial native source inventory was lost during diagnostic")
            report["runtime_source_files_final"] = final_sources
            report["provenance_scope"] = "loaded_repository_python_and_used_USD_layers_not_binary_or_texture_closure"
            report["late_loaded_source_paths"] = sorted(set(final_sources)-set(report["runtime_source_files"]))
            report["status"] = "diagnostic_completed"
            save()
    except BaseException as exc:
        if report["status"] != "failed":
            report.update(status="failed", error=repr(exc), traceback=traceback.format_exc(),
                          native_step_count=len(report["samples"]))
            save()
        raise
    return report


def execute_probe(output_root, *, timeout_s=600.):
    """One serial worker; terminate/reap its process group before releasing lock."""
    root = Path(output_root).absolute()
    request, _, _ = load_probe(root)
    if (root / "launch.json").exists():
        raise ValueError("probe already launched; prepare a fresh directory")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be positive")
    lock_path = ROOT / "tmp/goal_physical_probe.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another goal physical scene is running") from exc
        command = _command(root)
        physical.write_json(root / "launch.json", {"command": command, "run_id": request["run_id"],
            "request_sha256": canonical_sha256(request), "timeout_s": timeout_s})
        started = time.monotonic()
        interrupted, timed_out = None, False
        with (root / "isaac.log").open("x") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock.fileno(),),
                env={**os.environ, "EAI_GOAL_PHYSICAL_WORKER_LOCK_FD": str(lock.fileno())})
            previous = signal.getsignal(signal.SIGTERM)
            def terminate_parent(signum, frame):
                raise KeyboardInterrupt("attachment probe parent terminated")
            def stop():
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    return process.wait(timeout=10.)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    return process.wait(timeout=10.)
            signal.signal(signal.SIGTERM, terminate_parent)
            try:
                code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = stop()
            except BaseException as exc:
                interrupted = exc
                code = stop()
            finally:
                signal.signal(signal.SIGTERM, previous)
        result = {"run_id": request["run_id"], "request_sha256": canonical_sha256(request),
                  "returncode": code, "timed_out": timed_out, "wall_time_s": time.monotonic()-started,
                  "diagnostic_completed": False, "scoreable": False}
        report_path = root / "attachment_report.json"
        result["attachment_report_sha256"] = file_sha256(report_path) if report_path.is_file() else None
        result["launch_sha256"] = file_sha256(root / "launch.json")
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text())
                result["diagnostic_completed"] = (code == 0 and not timed_out
                    and report.get("run_id") == request["run_id"]
                    and report.get("request_sha256") == canonical_sha256(request)
                    and report.get("status") == "diagnostic_completed" and report.get("native_step_count") == 440)
            except (ValueError, OSError):
                pass
        physical.write_json(root / "process_result.json", result)
        if interrupted:
            raise interrupted
        load_probe(root, rebuild=False)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cuda:0")
    parser.add_argument("--timeout-s", type=float, default=600.)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        run_worker(args.output_root)
    elif args.execute:
        result = execute_probe(args.output_root, timeout_s=args.timeout_s)
        print(json.dumps(result))
        return 0 if result["diagnostic_completed"] else 1
    else:
        print(json.dumps(prepare_probe(args.output_root, gui=args.gui, device=args.device)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
