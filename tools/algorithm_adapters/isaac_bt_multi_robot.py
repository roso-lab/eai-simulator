"""Two UR5s in one Isaac scene, native BT parallel DAG, measured join observation.

Default is offline preparation. --native-preflight contacts/then closes the BT
mission without execution feedback. Only --execute launches Isaac. All Kit and
original PickPlaceExecutor calls run in one OS thread; greenlets yield only at
the existing host advance boundary. No upstream algorithm is reproduced here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from algorithm.system.coordination.native_runtime import sha256_file
from task.contracts import DependencyOutcome, EaiTaskPackage, ExecutionUpdate, OrchestrationLimits, TaskRunRequest
from task.runner import TaskRunner

ROSTER = ("ur5_1", "ur5_2")
MOVES = ("move_left", "move_right")
SEMANTICS = "fixed_robot_action; no_payload_attachment"
DT = .005
BASES = {"ur5_1": (0., -1.25, .45), "ur5_2": (0., 1.25, .45)}
OBJECTS = {"move_left": "/World/ObjectLeft", "move_right": "/World/ObjectRight"}
STARTS = {task: (.45, y, .475) for task, y in zip(MOVES, (-1.25, 1.25))}
TARGETS = {task: (.425, y + .01, .475) for task, y in zip(MOVES, (-1.25, 1.25))}
SOURCE_FILES = (
    "tools/algorithm_adapters/isaac_bt_multi_robot.py",
    "task/runner.py", "task/executors/pick_place.py", "task/executors/pick_place_adapters/arm.py",
    "task/controllers/pick_place/arm_action_runtime.py",
    "source/EAI/EAI/controllers/idealized_manipulation.py", "source/EAI/EAI/controllers/ik_controller.py",
    "source/EAI/EAI/manipulation_geometry.py", "algorithm/skills/pick_place/provider.py",
    "algorithm/skills/pick_place/contracts.py", "tools/validation/pick_place_clearance.py",
    "algorithm/system/coordination/bt_capabilities/native_adapter.py",
)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def case_document():
    tasks = []
    for task in MOVES:
        tasks.append({"task_id": task, "description": "Move the " + task.removeprefix("move_") + " carton",
            "required_capabilities": ["manipulate"], "prerequisites": [], "target_ref": OBJECTS[task],
            "metadata": {"position": list(STARTS[task]),
                "pick_place": {"object_position_world_m": list(STARTS[task]),
                    "target_position": list(TARGETS[task]), "mode": "cartesian_pose", "approach_degrees": 15.},
                "bt_native": {"bids": {rid: math.dist(BASES[rid][:2], STARTS[task][:2]) for rid in ROSTER}}}})
    tasks.append({"task_id": "inspect_join", "description": "Measure both released cartons for one second",
        "required_capabilities": ["inspect"], "prerequisites": list(MOVES), "target_ref": "/World",
        "metadata": {"inspection": "simulator_ground_truth_position_velocity_and_release; not onboard vision",
            "bt_native": {"bids": {rid: math.dist(BASES[rid][:2], (.425, 0.)) for rid in ROSTER}}}})
    return {"task_id": "bt-two-ur5-dag", "scene_id": "single-isaac-two-ur5-workbenches",
        "robots": [{"robot_id": rid, "capabilities": ["manipulate", "inspect"],
                    "position": list(BASES[rid]), "metadata": {"yaw_rad": 0.}} for rid in ROSTER],
        "tasks": tasks, "metadata": {"attachment_semantics": SEMANTICS,
            "bids_source": "declared_fixture_Euclidean_XY_distance_m; native_auction_selects_robot",
            "inspection_scope": "assigned_robot_executes_host_ground_truth_query; no_vision_or_sensor_motion"}}


def validate_native(decision, package, *, initial=False, pending=None):
    """Reject unsupported native output; never repair/reassign an auction result."""
    raw = decision.provenance.get("native_evidence", {})
    from algorithm.system.coordination.bt_capabilities.native_adapter import encode_task
    contract_hash = hashlib.sha256(json.dumps(encode_task(package, package.robots),
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if raw.get("task_contract_sha256") != contract_hash:
        raise ValueError("native output is not bound to the supplied task and bid contract")
    expected = {node.task_id: list(node.prerequisites) for node in package.graph.tasks}
    if (raw.get("ok") is not True or raw.get("graph_mode") != "dag"
            or raw.get("native_tree") != "ros_bt_py.nodes.parallel.Parallel"
            or raw.get("prerequisites") != expected):
        raise ValueError("native output does not represent the requested Parallel DAG")
    assignments = decision.assignments
    if [(a.task_id, a.robot_id) for a in assignments] != [
            (a["task_id"], a["robot_id"]) for a in raw.get("assignments", [])]:
        raise ValueError("adapted assignment differs from raw native output")
    ids = [a.task_id for a in assignments]
    if len(set(ids)) != len(ids) or any(a.robot_id not in ROSTER for a in assignments):
        raise ValueError("unknown robot or duplicate native task assignment")
    if len({a.robot_id for a in assignments}) != len(assignments):
        raise ValueError("same robot assigned twice; no host robot substitution")
    if set(ids) - set(expected) or set(decision.unassigned_tasks) - set(expected):
        raise ValueError("unknown native task")
    if len(set(decision.unassigned_tasks)) != len(decision.unassigned_tasks) or set(ids) & set(decision.unassigned_tasks):
        raise ValueError("contradictory native assigned/unassigned tasks")
    if assignments and raw.get("tree_state") != "RUNNING":
        raise ValueError("terminal native tree cannot dispatch physical work")
    if pending is not None and (set(ids) | set(decision.unassigned_tasks)) - set(pending):
        raise ValueError("native replan reissued a non-pending task")
    if initial and (set(ids) != set(MOVES) or set(decision.unassigned_tasks) != {"inspect_join"}):
        raise ValueError("initial native output must dispatch both independent moves and gate join")
    # This fixture has two distant fixed arms. An unreachable native decision is
    # rejected, never replaced with the nearby robot or an invented success.
    for a in assignments:
        if a.task_id in MOVES and math.dist(BASES[a.robot_id][:2], STARTS[a.task_id][:2]) > .8:
            raise ValueError("native-selected fixed arm cannot reach this fixture object")


def cooperative_batch(jobs, step, *, max_ticks=60_000):
    """Run blocking original executors on one thread and one shared clock.

    Each job accepts an advance callback. Yield once per live worker, advance
    physics once, then resume the blocked workers. Dead worker values are actual
    function returns, never generated execution statuses.
    """
    from greenlet import getcurrent, greenlet
    parent = getcurrent()
    token = object()
    def advance():
        parent.switch(token)
    workers = {key: greenlet(lambda job=job: job(advance), parent=parent) for key, job in jobs.items()}
    results = {}
    ticks = 0
    while workers:
        waiting = []
        for key, worker in list(workers.items()):
            result = worker.switch()
            if worker.dead:
                results[key] = result
                del workers[key]
            elif result is token:
                waiting.append(key)
            else:
                raise RuntimeError("worker yielded outside the host advance contract")
        if waiting:
            if ticks >= max_ticks:
                raise RuntimeError("cooperative physical tick budget exhausted")
            step(tuple(waiting))
            ticks += 1
    return results


class ConcurrencyEvidence:
    def __init__(self):
        self.previous = None
        self.overlap_ticks = 0
        self.motion_overlap_ticks = 0
        self.first_overlap_tick = None
        self.last_overlap_tick = None
        self.intervals = []

    def observe(self, sample):
        if self.previous is not None and sample["tick"] != self.previous["tick"] + 1:
            raise ValueError("non-contiguous shared physics ticks")
        active = sample["active_tasks"]
        if set(active) == set(MOVES):
            self.overlap_ticks += 1
            self.first_overlap_tick = self.first_overlap_tick or sample["tick"]
            self.last_overlap_tick = sample["tick"]
            if not self.intervals or self.intervals[-1][1] != sample["tick"] - 1:
                self.intervals.append([sample["tick"], sample["tick"]])
            else:
                self.intervals[-1][1] = sample["tick"]
            if self.previous is not None and all(
                max(abs(a - b) for a, b in zip(sample["robots"][rid]["joint_position_rad"],
                    self.previous["robots"][rid]["joint_position_rad"])) > 1e-7 for rid in ROSTER):
                self.motion_overlap_ticks += 1
        self.previous = sample

    def report(self):
        return {"shared_tick_overlap_count": self.overlap_ticks,
            "shared_tick_overlap_simulation_s": self.overlap_ticks * DT,
            "simultaneous_measured_joint_motion_ticks": self.motion_overlap_ticks,
            "simultaneous_measured_joint_motion_s": self.motion_overlap_ticks * DT,
            "joint_motion_threshold_rad_per_tick": 1e-7,
            "first_overlap_tick": self.first_overlap_tick, "last_overlap_tick": self.last_overlap_tick,
            "overlap_tick_intervals_inclusive": self.intervals,
            "passed": self.motion_overlap_ticks >= 20,
            "clock": "one_SimulationContext_one_PhysX_step_per_tick"}


class IsaacTwoUR5Host:
    """Own one scene, two original executors, streamed measured tick evidence."""
    def __init__(self, output, *, gui=False, timeout_s=100.):
        self.output, self.gui, self.timeout_s = Path(output), gui, timeout_s
        self.launcher = self.runtime = self.trace = self.sim = None
        self.tick = 0
        self.receipts = {}
        self.concurrency = ConcurrencyEvidence()
        self.started = False

    def build(self):
        # This is called only after the provider's native output guard passes.
        import greenlet  # fail before Kit startup if the optional dependency is absent
        for package in ("EAI", "EAI_assets", "EAI_hmrs"):
            sys.path.insert(0, str(ROOT / "source" / package))
        os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
        from isaaclab.app import AppLauncher
        self.started = True
        self.launcher = AppLauncher(headless=not self.gui, device="cpu")
        import torch
        import isaaclab.sim as sim_utils
        from isaaclab.assets import Articulation, ArticulationCfg, RigidObject, RigidObjectCfg
        from isaaclab.actuators import ImplicitActuatorCfg
        from pxr import Usd, UsdPhysics, PhysxSchema, UsdGeom
        from task.controllers.pick_place import ActionOnlyManipulationRuntime
        from EAI_assets.robots.ur5_mount import UR5_JOINT_NAMES, UR5_JOINT_EFFORT_LIMITS, UR5_STANDALONE_USD_PATH
        from EAI_assets.robots.ur5_robotiq import attach_robotiq_85, default_robotiq_85_usd
        from tools.validation.pick_place_clearance import ArmTableClearance
        self.UsdPhysics, self.UsdGeom = UsdPhysics, UsdGeom
        self.sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(dt=DT, device="cpu"))
        self.sim.set_camera_view((3., 3., 2.5), (.3, 0., .6))
        material = sim_utils.RigidBodyMaterialCfg(static_friction=.9, dynamic_friction=.7, restitution=0.)
        material.func("/World/ContactMaterial", material)
        ground = sim_utils.CuboidCfg(size=(10., 10., .1), collision_props=sim_utils.CollisionPropertiesCfg(), physics_material=material)
        ground.func("/World/Ground", ground, translation=(0., 0., -.05))
        light = sim_utils.DomeLightCfg(intensity=2000)
        light.func("/World/Light", light)
        self.arms, self.boxes, self.tables, self.payloads = {}, {}, {}, {}
        self.joint_ids, self.hold_counts = {}, {}
        self.scene_audit = {"num_scenes": 1, "num_envs": 1, "robot_prim_paths": {}, "payloads": {},
            "self_collisions_enabled": False, "arm_gravity_enabled": False,
            "cross_arm_mesh_collision_audit": "not_evaluated; bases separated by 2.5m"}
        model = SimpleNamespace(model="ur5", joint_names=UR5_JOINT_NAMES, ee_body_names=("wrist_3_link",), gripper_joint_name="finger_joint")
        for rid, base in BASES.items():
            pedestal = sim_utils.CuboidCfg(size=(.16, .16, .45), collision_props=sim_utils.CollisionPropertiesCfg())
            pedestal.func(f"/World/{rid}_pedestal", pedestal, translation=(base[0], base[1], .225))
            cfg = ArticulationCfg(prim_path=f"/World/{rid}_arm", spawn=sim_utils.UsdFileCfg(
                usd_path=UR5_STANDALONE_USD_PATH, rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=True),
                articulation_props=sim_utils.ArticulationRootPropertiesCfg(fix_root_link=True, enabled_self_collisions=False)),
                init_state=ArticulationCfg.InitialStateCfg(pos=base), actuators={})
            cfg.init_state.joint_pos = dict(zip(UR5_JOINT_NAMES, (0., -1.57, 1.2, -1.2, -1.57, 0.)))
            cfg.init_state.joint_pos["finger_joint"] = 0.
            cfg.actuators = {
                "arm": ImplicitActuatorCfg(joint_names_expr=list(UR5_JOINT_NAMES), effort_limit_sim=dict(UR5_JOINT_EFFORT_LIMITS), velocity_limit_sim=1., stiffness=6000., damping=300.),
                "gripper": ImplicitActuatorCfg(joint_names_expr=["finger_joint"], effort_limit_sim=2., velocity_limit_sim=.8, stiffness=50., damping=1.),
                "passive": ImplicitActuatorCfg(joint_names_expr=["right_outer_knuckle_joint", ".*inner.*joint"], effort_limit_sim=0., stiffness=0., damping=0.)}
            arm = Articulation(cfg)
            attach_robotiq_85(self.sim.stage, arm_path=cfg.prim_path, gripper_usd=default_robotiq_85_usd())
            prim = self.sim.stage.GetPrimAtPath(cfg.prim_path)
            while True:
                instances = [p for p in Usd.PrimRange(prim) if p.IsInstance()]
                if not instances:
                    break
                for p in instances:
                    p.SetInstanceable(False)
            for p in Usd.PrimRange(prim):
                if p.HasAPI(UsdPhysics.RigidBodyAPI):
                    PhysxSchema.PhysxRigidBodyAPI.Apply(p).CreateDisableGravityAttr().Set(True)
                if p.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(p).CreateCollisionEnabledAttr().Set(True)
            PhysxSchema.PhysxArticulationAPI.Apply(prim).CreateSleepThresholdAttr().Set(0.)
            colliders = [p for p in Usd.PrimRange(prim) if p.HasAPI(UsdPhysics.CollisionAPI)]
            if not colliders or not all(UsdPhysics.CollisionAPI(p).GetCollisionEnabledAttr().Get() for p in colliders):
                raise RuntimeError("missing or disabled arm environment collision geometry")
            self.arms[rid] = arm
            self.scene_audit["robot_prim_paths"][rid] = {"path": cfg.prim_path, "enabled_colliders": len(colliders)}
        for task, position in STARTS.items():
            center, size = (.515, position[1], .425), (.57, .7, .05)
            table = RigidObject(RigidObjectCfg(prim_path=f"/World/{task}_table", spawn=sim_utils.CuboidCfg(
                size=size, mass_props=sim_utils.MassPropertiesCfg(mass=10.),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True), collision_props=sim_utils.CollisionPropertiesCfg(), physics_material=material),
                init_state=RigidObjectCfg.InitialStateCfg(pos=center)))
            self.tables[task] = (table, center, size)
            self.boxes[task] = RigidObject(RigidObjectCfg(prim_path=OBJECTS[task], spawn=sim_utils.CuboidCfg(
                size=(.05,) * 3, mass_props=sim_utils.MassPropertiesCfg(mass=.01),
                rigid_props=sim_utils.RigidBodyPropertiesCfg(disable_gravity=False), collision_props=sim_utils.CollisionPropertiesCfg(), physics_material=material),
                init_state=RigidObjectCfg.InitialStateCfg(pos=(position[0], position[1], position[2] + .005))))
            prim = self.sim.stage.GetPrimAtPath(OBJECTS[task])
            api = PhysxSchema.PhysxRigidBodyAPI(prim)
            colliders = [UsdPhysics.CollisionAPI(p) for p in Usd.PrimRange(prim) if p.HasAPI(UsdPhysics.CollisionAPI)]
            if not colliders or not all(c.GetCollisionEnabledAttr().Get() for c in colliders):
                raise RuntimeError("payload support collisions missing")
            self.payloads[task] = (prim, api, colliders, self.filter_snapshot(prim))
        self.sim.reset()
        for rid, arm in self.arms.items():
            arm.write_joint_state_to_sim(arm.data.default_joint_pos, torch.zeros_like(arm.data.default_joint_pos))
            arm.set_joint_position_target(arm.data.default_joint_pos.clone())
            arm.set_joint_velocity_target(torch.zeros_like(arm.data.default_joint_pos))
            arm.write_data_to_sim()
            self.joint_ids[rid] = arm.find_joints(list(UR5_JOINT_NAMES), preserve_order=True)[0]
            self.scene_audit["robot_prim_paths"][rid]["fixed_base"] = bool(arm.is_fixed_base)
            if not arm.is_fixed_base:
                raise RuntimeError("fixture requires fixed UR5 bases")
        for task, box in self.boxes.items():
            box.write_root_pose_to_sim(box.data.default_root_state[:, :7])
            box.write_root_velocity_to_sim(torch.zeros_like(box.data.default_root_state[:, 7:]))
        env = SimpleNamespace(num_envs=1, scene=SimpleNamespace(articulations={f"{rid}_arm": arm for rid, arm in self.arms.items()}), sim=self.sim)
        self.runtime = ActionOnlyManipulationRuntime(env, {rid: model for rid in ROSTER})
        self.clearances = {f"{rid}/{task}": ArmTableClearance(self.sim.stage, arm, center, size)
            for rid, arm in self.arms.items() for task, (_, center, size) in self.tables.items()}
        self.trace = (self.output / "shared_ticks.jsonl").open("w")
        write_json(self.output / "scene_audit.json", self.scene_audit)
        for _ in range(400):
            self.step(())
        for task in MOVES:
            if math.dist(self.runtime._rigid_body_pose(OBJECTS[task])[0], STARTS[task]) > .01:
                raise RuntimeError("payload did not settle at declared native bid position")

    @staticmethod
    def filter_snapshot(prim):
        relation = prim.GetRelationship("physics:filteredPairs")
        return {"api_names": [str(x) for x in prim.GetAppliedSchemas() if "FilteredPairs" in str(x)],
            "authored_targets": bool(relation and relation.HasAuthoredTargets()),
            "targets": [str(x) for x in relation.GetTargets()] if relation else []}

    def step(self, active_tasks):
        self.runtime.run_post_apply()
        for arm in self.arms.values():
            wake = getattr(arm.root_physx_view, "wake_up", None)
            if wake is not None:
                wake()
            arm.write_data_to_sim()
        self.sim.step(render=self.gui)
        for arm in self.arms.values():
            arm.update(DT)
        for box in self.boxes.values():
            box.update(DT)
        self.tick += 1
        sample = {"tick": self.tick, "simulation_time_s": self.sim.current_time,
            "monotonic_wall_s": time.monotonic(), "active_tasks": list(active_tasks), "robots": {}, "objects": {}}
        for rid, arm in self.arms.items():
            binding = self.runtime._binding(rid, disable_collisions=False)
            position, orientation = self.runtime._end_effector_pose(binding)
            sample["robots"][rid] = {"joint_position_rad": arm.data.joint_pos[0, self.joint_ids[rid]].tolist(),
                "ee_position_world_m": list(position), "ee_orientation_wxyz": list(orientation),
                "phase": self.runtime.state(rid).value, "attachment_mode": "none"}
        for task in MOVES:
            sample["objects"][task] = {"source_position_world_m": list(self.runtime._rigid_body_pose(OBJECTS[task])[0]),
                "source_linear_velocity_m_s": self.boxes[task].data.root_lin_vel_w[0].tolist()}
        for clearance in self.clearances.values():
            clearance.sample(self.sim.current_time)
        self.trace.write(json.dumps(sample, allow_nan=False) + "\n")
        if self.tick % 200 == 0:
            self.trace.flush()
        self.concurrency.observe(sample)

    def request(self, assignment, operation):
        from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
        return SkillRequest(assignment.task_id + "/" + operation, PICK_PLACE_SKILL_ID, assignment.robot_id,
            parameters={"operation": operation, "object_ref": OBJECTS[assignment.task_id],
                        "timeout_s": self.timeout_s})

    def move(self, assignment, advance, resources):
        from task.executors import PickPlaceExecutor
        from task.executors.pick_place_adapters.arm import ArmPickPlaceAdapter
        rid, task = assignment.robot_id, assignment.task_id
        executor = PickPlaceExecutor(advance=advance, simulation_time=lambda: self.sim.current_time, resources=resources,
            physical_object_experiment_authorization="algorithm/heterocoord/ADAPTATION_PUBLICATION_20261028.zh-CN.md#bounded-host")
        executor.register_adapter(ArmPickPlaceAdapter(rid, self.runtime, position_tolerance_m=.035))
        start_tick = self.tick
        initial_object = tuple(self.runtime._rigid_body_pose(OBJECTS[task])[0])
        picked = executor.execute(self.request(assignment, "pick"))
        if picked:
            for _ in range(120):
                advance()
        placed = executor.execute(self.request(assignment, "place")) if picked else False
        for _ in range(200):
            advance()
        final = self.runtime._rigid_body_pose(OBJECTS[task])[0]
        state = self.runtime._states.get(rid)
        unchanged = math.dist(final, initial_object) <= .005
        receipt = {"task_id": task, "robot_id": rid, "pick": bool(picked), "place": bool(placed),
            "start_tick": start_tick, "end_tick": self.tick,
            "object_position_before_world_m": list(initial_object),
            "object_position_after_world_m": list(final), "object_pose_unchanged": unchanged,
            "phase_after": self.runtime.state(rid).value, "last_error": self.runtime.last_error(rid),
            "attachment_mode": "none"}
        receipt["passed"] = bool(picked and placed and unchanged and receipt["phase_after"] == "idle" and not receipt["last_error"])
        return receipt

    def inspect(self, assignment):
        if not all(self.receipts.get(task, {}).get("passed") is True for task in MOVES):
            raise RuntimeError("join observation before both measured move receipts passed")
        start = self.tick
        positions = {task: [] for task in MOVES}
        max_speed = {task: 0. for task in MOVES}
        unchanged = True
        for _ in range(200):
            self.step(("inspect_join",))
            for task in MOVES:
                positions[task].append(tuple(self.runtime._rigid_body_pose(OBJECTS[task])[0]))
                max_speed[task] = max(max_speed[task], math.sqrt(sum(float(v)**2 for v in self.boxes[task].data.root_lin_vel_w[0])))
                unchanged = unchanged and self.receipts[task]["object_pose_unchanged"]
        errors = {task: max(math.dist(p, positions[task][0]) for p in positions[task]) for task in MOVES}
        drift = {task: max(math.dist(p, positions[task][0]) for p in positions[task]) for task in MOVES}
        passed = unchanged and all(drift[t] <= .005 and max_speed[t] <= .02 for t in MOVES)
        return {"task_id": "inspect_join", "robot_id": assignment.robot_id, "passed": bool(passed),
            "start_tick": start, "end_tick": self.tick, "measured_ticks": 200, "max_object_drift_m": errors,
            "max_drift_m": drift, "max_linear_speed_m_s": max_speed, "object_pose_unchanged": bool(unchanged),
            "evidence_scope": "simulator_ground_truth_query_executed_for_native_assigned_robot; not onboard vision"}

    def execute_batch(self, assignments, tasks, *, scene):
        from task.resources import ResourceManager
        if len({a.robot_id for a in assignments}) != len(assignments):
            raise ValueError("batch uses one robot twice")
        if self.sim is None:
            if {a.task_id for a in assignments} != set(MOVES):
                raise ValueError("first physical batch must contain both independent moves")
            self.build()
        if {a.task_id for a in assignments} == set(MOVES):
            resources = ResourceManager()
            jobs = {a.task_id: (lambda advance, a=a: self.move(a, advance, resources)) for a in assignments}
            records = cooperative_batch(jobs, self.step, max_ticks=math.ceil((2 * self.timeout_s + 10) / DT))
            clear = not resources.snapshot()["resources"]
            clearance_ok = all(c.report()["passed"] for c in self.clearances.values())
            for record in records.values():
                record["shared_resources_cleared"] = clear
                record["all_robot_table_clearance_passed"] = clearance_ok
                record["passed"] = bool(record["passed"] and clear and clearance_ok)
            self.receipts.update(records)
        elif len(assignments) == 1 and assignments[0].task_id == "inspect_join":
            self.receipts["inspect_join"] = self.inspect(assignments[0])
        else:
            raise ValueError("unsupported native batch; no substitute action")
        updates = []
        for assignment in assignments:
            receipt = self.receipts[assignment.task_id]
            path = self.output / (assignment.task_id + ".json")
            write_json(path, receipt)
            passed = receipt["passed"] is True
            feedback = ExecutionFeedback(assignment.task_id, assignment.robot_id, "success" if passed else "failure",
                FailureCode.NONE if passed else FailureCode.EXECUTION_FAILURE, timestamp_s=self.sim.current_time,
                world_changes={"physical_receipt_sha256": sha256_file(path), "start_tick": receipt["start_tick"], "end_tick": receipt["end_tick"]})
            updates.append(ExecutionUpdate(feedback, DependencyOutcome.COMPLETED if passed else DependencyOutcome.TERMINAL_FAILURE))
        return tuple(updates)

    def evaluate(self, **kwargs):
        passed = (set(self.receipts) == {*MOVES, "inspect_join"} and all(r["passed"] for r in self.receipts.values())
            and self.concurrency.report()["passed"])
        return {"status": "success" if passed else "failure", "attachment_semantics": SEMANTICS,
            "benchmark_scores_available": False, "scope": "bounded_two_UR5_pick_place_and_ground_truth_join",
            "concurrency": self.concurrency.report()}

    def close(self):
        if self.trace:
            self.trace.close()
        if self.runtime:
            self.runtime.close()

    def shutdown_application(self, exit_code):
        # Kit can block or terminate Python here; persist all receipts first.
        # Match the existing single-UR5 host's explicit standalone-app shutdown.
        if self.launcher:
            import omni.kit.app
            omni.kit.app.get_app().post_quit(exit_code)
            self.launcher.app.close(wait_for_replicator=False, skip_cleanup=True)


class RecordedProvider:
    def __init__(self, adapter, output):
        self.adapter, self.output, self.events = adapter, Path(output), []

    def record(self, kind):
        self.events.append({"kind": kind, "native": self.adapter.last_native_state})
        write_json(self.output / "native_events.json", self.events)

    def __call__(self, package, robots):
        try:
            decision = self.adapter(package, robots)
            validate_native(decision, package, initial=True)
            return decision
        finally:
            self.record("plan")

    def replan(self, package, robots, state):
        try:
            decision = self.adapter.replan(package, robots, state)
            validate_native(decision, package, pending=state.pending_task_ids)
            return decision
        finally:
            self.record("replan")

    def feedback_batch(self, feedback, *, succeeded):
        try:
            result = self.adapter.feedback_batch(feedback, succeeded=succeeded)
            by_id = {leaf["task_id"]: leaf for leaf in result.get("leaf_states", [])}
            for item, ok in zip(feedback, succeeded):
                echoed = by_id.get(item.task_id, {}).get("feedback")
                expected = {"task_id": item.task_id, "robot_id": item.robot_id, "succeeded": ok,
                    "outcome": item.outcome, "failure_code": item.failure_code.value}
                if echoed != expected:
                    raise RuntimeError("native BT did not echo the measured terminal feedback")
            return result
        finally:
            self.record("feedback_batch")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("HETEROCOORD_BT_NATIVE_ENDPOINT", "http://127.0.0.1:18767"))
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Explicitly launch Isaac after native decision validation")
    mode.add_argument("--native-preflight", action="store_true", help="Plan/validate/close only; never send success feedback")
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=100.)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_s) or args.timeout_s <= 0:
        parser.error("--timeout-s must be positive and finite")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be new or empty")
    args.output.mkdir(parents=True, exist_ok=True)
    task_path = args.output / "task.json"
    write_json(task_path, case_document())
    task = EaiTaskPackage.from_json(task_path)
    source_hashes = {path: sha256_file(ROOT / path) for path in SOURCE_FILES}
    source_hashes["task.json"] = sha256_file(task_path)
    report = {"schema": "heterocoord.bt_multi_robot.v1", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "execute" if args.execute else "native_preflight" if args.native_preflight else "prepare",
        "passed": False, "physical_success": None, "physical_execution_started": False,
        "native_preflight_passed": None, "physical_concurrency": None, "benchmark_scores_available": False,
        "attachment_semantics": SEMANTICS, "source_sha256": source_hashes,
        "roster": list(ROSTER), "endpoint": args.endpoint}
    host = provider = adapter = None
    try:
        if args.execute or args.native_preflight:
            from benchmark.native_algorithms import build_native_adapter
            from algorithm.system.coordination.bt_capabilities.native_adapter import NativeBTFeedbackExecutor
            adapter = build_native_adapter("bt_capabilities", source_root=args.source_root, endpoint=args.endpoint)
            provider = RecordedProvider(adapter, args.output)
            if args.native_preflight:
                provider(task, task.robots)
                report.update(native_preflight_passed=True, status="native_validated_no_physical_execution")
            else:
                host = IsaacTwoUR5Host(args.output, gui=args.gui, timeout_s=args.timeout_s)
                # TaskRunner creates only a metadata scene before the provider.
                # Isaac is first imported by execute_batch after validated native output.
                result = TaskRunner(orchestration_executor=NativeBTFeedbackExecutor(provider, host), evaluator=host).run_orchestrated(
                    TaskRunRequest(task), provider, replan=provider.replan,
                    limits=OrchestrationLimits(max_rounds=4, max_dispatches=3))
                native_state = adapter.last_native_state or {}
                passed = result.verdict["status"] == "success" and native_state.get("tree_state") == "SUCCEEDED" and set(result.task_states.values()) == {"completed"}
                report.update(passed=passed, physical_success=result.verdict["status"] == "success",
                    status="accepted" if passed else "not_accepted", result=result.to_dict())
        else:
            from algorithm.system.coordination.bt_capabilities.native_adapter import encode_task
            write_json(args.output / "native_payload.json", encode_task(task, task.robots))
            report.update(status="prepared_not_executed", physical_evidence="pending")
    except Exception as exc:
        import traceback
        (args.output / "error.txt").write_text(traceback.format_exc())
        report.update(status="error", error_type=type(exc).__name__, error=str(exc), passed=False)
    finally:
        if adapter:
            report["native_final_state"] = adapter.last_native_state
            try:
                adapter.close()
            except Exception as exc:
                report.update(passed=False, native_close_error=str(exc), status="error")
        if host:
            report.update(physical_execution_started=host.started, physics_ticks=host.tick,
                physical_concurrency=host.concurrency.report())
            if hasattr(host, "clearances"):
                write_json(args.output / "clearance.json", {key: value.report() for key, value in host.clearances.items()})
            try:
                host.close()
            except Exception as exc:
                report.update(passed=False, physical_close_error=str(exc), status="error")
        report["input_files_unchanged"] = all(sha256_file(task_path if path == "task.json" else ROOT / path) == digest for path, digest in source_hashes.items())
        if not report["input_files_unchanged"]:
            report.update(passed=False, status="input_changed")
        for name in ("shared_ticks.jsonl", "native_events.json", "scene_audit.json", "clearance.json"):
            path = args.output / name
            if path.exists():
                report[name + "_sha256"] = sha256_file(path)
        write_json(args.output / "receipt.json", report)
    print(json.dumps({key: report.get(key) for key in ("status", "passed", "physical_success", "physical_execution_started")}, indent=2), flush=True)
    exit_code = 0 if report["status"] in {"prepared_not_executed", "native_validated_no_physical_execution", "accepted"} else 1
    if host:
        host.shutdown_application(exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
