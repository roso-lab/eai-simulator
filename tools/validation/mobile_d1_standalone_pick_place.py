"""Standalone D1 arm pick->place through the real local pick/place skill stack.

Scene: a fixed-base D1 (``EAI_assets.robots.d1.D1_CFG``, force drives, folded
home pose) on a pedestal at z=0.32 m, a programmatic ground, and an orange
0.05 m / 0.05 kg cube resting on the ground in front of the arm.

Skill call chain exercised (no bespoke motion code in this file):

    PickPlaceExecutor.execute(request)                     (task/executors/pick_place.py)
      -> compile_request -> PickPlaceCommand               (algorithm/skills/pick_place)
      -> ArmPickPlaceAdapter.validate/start                (task/executors/pick_place_adapters/arm.py)
        -> IdealizedManipulationRuntime.start_pick_cartesian / start_place_cartesian
      -> per-step advance(): runtime.run_post_apply + arm.write_data_to_sim + sim.step

The runtime is driven at physics rate exactly like ``MultiRobotDirectEnv``
does (runtime is the last target writer before write_data_to_sim).  Runs with
a live GUI window (headless=False, DISPLAY=:1) so the user watches the pick
and place directly; no Replicator render product is created, evidence is the
printed numbers plus the live viewport.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for _package in ("EAI", "EAI_assets", "EAI_hmrs"):
    _package_path = str(ROOT / "source" / _package)
    if _package_path not in sys.path:
        sys.path.insert(0, _package_path)

ROBOT_NAME = "d1"
ARM_KEY = "d1_arm"
CUBE_PATH = "/World/EaiPickCube"
PICK_PLACE_SKILL_ID = "eai.manipulation.pick_place"

PEDESTAL_Z_M = 0.32
CUBE_SIZE_M = 0.05
CUBE_MASS_KG = 0.05

LIFT_MIN_M = 0.05          # acceptance: cube rises at least 5 cm after pick
PLACE_SHIFT_MIN_M = 0.10   # acceptance: cube moves at least 10 cm horizontally
SETTLE_MAX_DRIFT_M = 0.01  # acceptance: cube is at rest after place

CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}", flush=True)
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true", default=True)
    parser.add_argument("--no-gui", dest="gui", action="store_false")
    parser.add_argument("--forward-m", type=float, default=0.28,
                        help="cube distance in front of the arm base (radius shrink "
                             "keeps the tool-down wrist clear of the Joint4 limit)")
    parser.add_argument("--lateral-m", type=float, default=0.09,
                        help="cube lateral offset (pick +y, place -y)")
    parser.add_argument("--grasp-lift-m", type=float, default=0.01,
                        help="grasp point height above the cube origin")
    parser.add_argument("--place-z-m", type=float, default=0.035,
                        help="place target height for the cube origin")
    parser.add_argument("--pregrasp-m", type=float, default=0.10)
    parser.add_argument("--retreat-m", type=float, default=0.10,
                        help="lift height after grasp; 0.10 keeps the wrist at "
                             "the pregrasp height the arm demonstrably reaches")
    parser.add_argument("--approach-speed-m-s", type=float, default=0.06)
    parser.add_argument("--speed-scale", type=float, default=5.0)
    parser.add_argument("--tolerance-m", type=float, default=0.04)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    args = parser.parse_args()

    from isaaclab.app import AppLauncher

    launcher = AppLauncher(headless=not args.gui, device="cpu", enable_cameras=True)
    failed = False
    try:
        run(args)
    except Exception:
        failed = True
        traceback.print_exc()
        CHECKS.append(("script.exception", False, "see traceback above"))
        _print_summary(False)
    finally:
        launcher.app.close(wait_for_replicator=False, skip_cleanup=True)
    return 1 if failed else 0


def run(args: argparse.Namespace) -> int:
    import torch
    import isaaclab.sim as sim_utils
    from isaaclab.assets import Articulation, RigidObject, RigidObjectCfg
    from pxr import Gf, UsdGeom, Vt

    from EAI_assets.robots.d1 import D1_CFG
    from EAI.controllers.ik_controller import hold_gripper_position
    from task.controllers.pick_place import ActionOnlyManipulationRuntime
    from EAI.hmrs_ros.d1_omnigraph import D1_MODEL_SPEC

    dt = 1.0 / 120.0
    sim = sim_utils.SimulationContext(sim_utils.SimulationCfg(
        dt=dt, device="cpu",
        physx=sim_utils.PhysxCfg(
            solver_type=1, min_position_iteration_count=16,
            min_velocity_iteration_count=4)))

    ground_cfg = sim_utils.CuboidCfg(
        size=(20.0, 20.0, 0.1), collision_props=sim_utils.CollisionPropertiesCfg())
    ground_cfg.func("/World/Ground", ground_cfg, translation=(0.0, 0.0, -0.05))
    pedestal_cfg = sim_utils.CuboidCfg(
        size=(0.18, 0.18, PEDESTAL_Z_M),
        rigid_props=sim_utils.RigidBodyPropertiesCfg(kinematic_enabled=True),
        collision_props=sim_utils.CollisionPropertiesCfg())
    pedestal_cfg.func("/World/EaiPedestal", pedestal_cfg,
                      translation=(0.0, 0.0, PEDESTAL_Z_M / 2))
    light_cfg = sim_utils.DomeLightCfg(intensity=2000)
    light_cfg.func("/World/Light", light_cfg)

    arm_cfg = D1_CFG.copy()
    arm_cfg.prim_path = "/World/D1"
    arm_cfg.init_state.pos = (0.0, 0.0, PEDESTAL_Z_M)
    # The URDF-sourced drives (stiffness 80, force mode) let the ~1 kg arm sag
    # a couple of centimetres at full extension; the cartesian arrival gates
    # (30 mm) then never close.  Stiffen the arm joints in this validation
    # scene copy only, as calibrate_z1.py does for Z1.
    arm_cfg.actuators = arm_cfg.actuators.copy()
    arm_cfg.actuators["arm"] = arm_cfg.actuators["arm"].copy()
    arm_cfg.actuators["arm"].stiffness = 400.0
    arm_cfg.actuators["arm"].damping = 40.0
    arm_cfg.actuators["arm"].effort_limit_sim = 60.0
    arm = Articulation(arm_cfg)

    material_cfg = sim_utils.RigidBodyMaterialCfg(
        static_friction=0.8, dynamic_friction=0.6, restitution=0.0,
        friction_combine_mode="min")
    material_cfg.func("/World/EaiCubeMaterial", material_cfg)
    cube = RigidObject(RigidObjectCfg(
        prim_path=CUBE_PATH,
        spawn=sim_utils.CuboidCfg(
            size=(CUBE_SIZE_M, CUBE_SIZE_M, CUBE_SIZE_M),
            mass_props=sim_utils.MassPropertiesCfg(mass=CUBE_MASS_KG),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                disable_gravity=False, max_depenetration_velocity=1.0),
            collision_props=sim_utils.CollisionPropertiesCfg(
                contact_offset=0.001, rest_offset=0.0),
            physics_material=material_cfg),
        init_state=RigidObjectCfg.InitialStateCfg(
            pos=(args.forward_m, args.lateral_m, CUBE_SIZE_M / 2 + 0.001))))

    sim.reset()
    stage = sim.stage
    try:
        cube_prim = stage.GetPrimAtPath(CUBE_PATH)
        display = cube_prim.GetAttribute("primvars:displayColor")
        if display.IsValid():
            display.Set(Vt.Vec3fArray([(0.95, 0.45, 0.10)]))
        else:
            UsdGeom.Gprim(cube_prim).CreateDisplayColorAttr(
                Vt.Vec3fArray([(0.95, 0.45, 0.10)]))
    except Exception as exc:
        print(f"[info] cube displayColor skipped: {exc}", flush=True)
    if args.gui:
        sim.set_camera_view(
            (args.forward_m + 1.55, args.lateral_m + 1.95, 1.30),
            (args.forward_m, 0.0, 0.30))

    gripper_ids, _ = arm.find_joints(list(D1_MODEL_SPEC.gripper_joint_names), preserve_order=True)
    defaults = arm.data.default_joint_pos.clone()
    arm.write_joint_state_to_sim(defaults, torch.zeros_like(defaults))
    arm.set_joint_position_target(defaults)

    environment = _EnvironmentNamespace(sim, {ARM_KEY: arm}, {CUBE_PATH.rsplit("/", 1)[-1]: cube})
    clock = {"t": 0.0}
    ee_id_int = int(arm.find_bodies(["Link6"], preserve_order=True)[0][0])
    progress = {"n": 0}

    def _log_progress() -> None:
        state = environment.runtime._states.get(ROBOT_NAME) if environment.runtime else None
        if state is None:
            return
        plan = state.cartesian
        goal = state.cartesian_goal_position_w or (plan.reference_position_w if plan else None)
        ee = (float(arm.data.body_pos_w[0, ee_id_int, 0]),
              float(arm.data.body_pos_w[0, ee_id_int, 1]),
              float(arm.data.body_pos_w[0, ee_id_int, 2]))
        fingers = (float(arm.data.joint_pos[0, gripper_ids[0]]),
                   float(arm.data.joint_pos[0, gripper_ids[1]]))
        remaining = (math.dist(plan.reference_position_w, goal)
                     if plan is not None and goal is not None else float("nan"))
        measured_gap = (math.dist(ee, goal) if goal is not None else float("nan"))
        quat = tuple(float(v) for v in arm.data.body_quat_w[0, ee_id_int])
        goal_quat = state.cartesian_goal_orientation_wxyz
        ang_gap = (2.0 * math.acos(min(1.0, abs(sum(a * b for a, b in zip(quat, goal_quat)))))
                   if goal_quat is not None else float("nan"))
        joints = [float(v) for v in arm.data.joint_pos[0, :6]]
        target_gap = float(torch.max(torch.abs(
            arm.data.joint_pos_target[0, :6] - arm.data.joint_pos[0, :6])))
        print(f"[skill] step {progress['n']} t={clock['t']:.2f}s phase={state.phase.value} "
              f"steps={state.phase_steps} ref_left={remaining:.3f} ee_gap={measured_gap:.3f} "
              f"ang_gap={math.degrees(ang_gap):.1f}deg "
              f"ee=({ee[0]:+.3f},{ee[1]:+.3f},{ee[2]:+.3f}) fingers=({fingers[0]:+.3f},{fingers[1]:+.3f}) "
              f"tgt_gap={target_gap:.3f} joints=" + " ".join(f"{v:+.2f}" for v in joints) +
              f" err={state.last_error}", flush=True)

    def physics_step(run_runtime: bool) -> None:
        if run_runtime:
            environment.runtime.run_post_apply(environment)
            progress["n"] += 1
            _mirror_held_cube_usd()
            if progress["n"] % 240 == 0:
                _log_progress()
        arm.write_data_to_sim()
        sim.step(render=args.gui)
        arm.update(dt)
        cube.update(dt)
        clock["t"] += dt

    from isaaclab.utils.math import quat_apply

    cube_translate_op = next(
        op for op in UsdGeom.Xformable(stage.GetPrimAtPath(CUBE_PATH)).GetOrderedXformOps()
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate)

    def _mirror_held_cube_usd() -> None:
        """Mirror the runtime's logical follow pose onto the cube's USD translate.

        The runtime parks the source body (kinematic, no contact) during a
        carry and restores it at release through the tensor API.  Restoring
        the isolation attributes makes PhysX re-import the body from USD, and
        a stale authored translate snaps the released cube back to its spawn
        pose (observed: released cube teleporting 0.18 m back to the pick
        origin).  Keeping the authored translate on the follow pose makes any
        such resync land where the skill is carrying the cube anyway.  This
        mirrors state the runtime already maintains; it does not steer it.
        """
        runtime_ref = environment.runtime
        if runtime_ref is None:
            return
        held = runtime_ref.held_object(ROBOT_NAME)
        if held is None:
            return
        try:
            offset = torch.tensor([held.relative_object_position_ee],
                                  dtype=torch.float32)
            world = quat_apply(arm.data.body_quat_w[0, ee_id_int].reshape(1, 4), offset)
            position = arm.data.body_pos_w[0, ee_id_int] + world[0]
            cube_translate_op.Set(Gf.Vec3f(
                float(position[0]), float(position[1]), float(position[2])))
        except Exception as exc:
            print(f"[info] cube USD mirror skipped: {exc}", flush=True)

    def hold(steps: int) -> None:
        for _ in range(steps):
            arm.set_joint_position_target(defaults)
            hold_gripper_position(arm, D1_MODEL_SPEC)
            physics_step(run_runtime=False)

    def states_finite() -> bool:
        return bool(
            torch.isfinite(arm.data.root_state_w).all()
            and torch.isfinite(arm.data.joint_pos).all()
            and torch.isfinite(cube.data.root_state_w).all())

    print("[phase] settle arm at home + cube drop", flush=True)
    hold(120)
    if not record("settle.finite", states_finite(), "arm/cube states finite after settle"):
        return _finish(False, environment, hold)
    origin = (float(cube.data.root_pos_w[0, 0]), float(cube.data.root_pos_w[0, 1]),
              float(cube.data.root_pos_w[0, 2]))
    resting = abs(origin[2] - CUBE_SIZE_M / 2) < 0.01
    record("cube.resting", resting,
           f"origin=({origin[0]:+.4f}, {origin[1]:+.4f}, {origin[2]:+.4f})")
    pick_origin = origin
    ee_ids, _ = arm.find_bodies(["Link6"], preserve_order=True)
    print(f"[info] ee_home={tuple(round(float(v), 3) for v in arm.data.body_pos_w[0, int(ee_ids[0])])} "
          f"cube={tuple(round(v, 3) for v in origin)}", flush=True)
    if not resting:
        return _finish(False, environment, hold)

    from task.executors.pick_place import PickPlaceExecutor
    from task.executors.pick_place_adapters.arm import ArmPickPlaceAdapter
    from EAI.controllers.ik_controller import apply_end_effector_pose

    runtime = ActionOnlyManipulationRuntime(environment, {ROBOT_NAME: D1_MODEL_SPEC})
    environment.runtime = runtime
    adapter = ArmPickPlaceAdapter(
        ROBOT_NAME, runtime,
        resolve_object=lambda ref: CUBE_PATH,
        position_tolerance_m=args.tolerance_m,
        motion_speed_scale=args.speed_scale,
    )
    executor = PickPlaceExecutor(
        advance=lambda: physics_step(run_runtime=True),
        simulation_time=lambda: clock["t"],
        physical_object_experiment_authorization="action_only_pick_place")
    executor.register_adapter(adapter)
    binding = runtime._binding(ROBOT_NAME, disable_collisions=False)
    print(f"[info] runtime bound: model={binding.model_spec.model} "
          f"joints={list(binding.model_spec.joint_names)} "
          f"ee_body={list(binding.model_spec.ee_body_names)}", flush=True)

    def skill_request(request_id: str, operation: str, target_world=None) -> dict:
        del target_world
        return {
            "request_id": request_id,
            "skill_id": PICK_PLACE_SKILL_ID,
            "robot_id": ROBOT_NAME,
            "parameters": {
                "operation": operation,
                "object_ref": CUBE_PATH,
                "timeout_s": args.timeout_s,
            },
        }

    # ------------------------------------------------------------------ pick
    live_origin = (float(cube.data.root_pos_w[0, 0]), float(cube.data.root_pos_w[0, 1]),
                   float(cube.data.root_pos_w[0, 2]))
    print("[phase] SKILL pick -> executor.execute (runtime.start_pick_cartesian)", flush=True)
    pick_ok = executor.execute(skill_request("d1-skill-pick-1", "pick", live_origin))
    held = runtime.held_object(ROBOT_NAME)
    record("skill.pick_returned", pick_ok,
           f"phase={runtime.state(ROBOT_NAME).value} held={held is not None} "
           f"error={runtime.last_error(ROBOT_NAME)!r}")
    if not pick_ok or held is None:
        return _finish(False, environment, hold)

    cache = UsdGeom.XformCache()

    def cube_proxy_position():
        # The wrapper's world transform composes with the referenced source
        # local pose; the /Visual child is the prim that lands on the world
        # target (unified pick/place validation reads it the same way).
        return _vec3(cache.GetLocalToWorldTransform(
            stage.GetPrimAtPath(f"{held.proxy_path}/Visual")).ExtractTranslation())

    proxy_pos = cube_proxy_position()
    lift = proxy_pos[2] - pick_origin[2]
    record("pick.cube_lifted", lift >= LIFT_MIN_M,
           f"proxy=({proxy_pos[0]:+.3f}, {proxy_pos[1]:+.3f}, {proxy_pos[2]:+.3f}) "
           f"lift={lift:.3f} m (>= {LIFT_MIN_M})")

    hold_samples = []
    for index in range(45):
        physics_step(run_runtime=True)
        if index % 15 == 14:
            hold_samples.append(cube_proxy_position()[2] - pick_origin[2])
    record("pick.holding_stable", bool(hold_samples) and min(hold_samples) >= LIFT_MIN_M,
           f"lift samples during hold: {[round(v, 3) for v in hold_samples]}")
    if not record("pick.finite", states_finite(), "states finite after pick"):
        return _finish(False, environment, hold)

    # ----------------------------------------------------------------- place
    place_target = (args.forward_m, -args.lateral_m, args.place_z_m)
    print("[phase] SKILL place -> executor.execute (runtime.start_place_cartesian)", flush=True)
    place_ok = executor.execute(skill_request("d1-skill-place-1", "place", place_target))
    held_after = runtime.held_object(ROBOT_NAME)
    record("skill.place_returned", place_ok,
           f"phase={runtime.state(ROBOT_NAME).value} held={held_after is not None} "
           f"error={runtime.last_error(ROBOT_NAME)!r}")

    print("[phase] settle cube after release", flush=True)
    cube_prim = stage.GetPrimAtPath(CUBE_PATH)

    def _usd_translate():
        ops = UsdGeom.Xformable(cube_prim).GetOrderedXformOps()
        for op in ops:
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                return tuple(round(float(v), 3) for v in op.Get())
        return None

    def _cube_pos():
        return (float(cube.data.root_pos_w[0, 0]), float(cube.data.root_pos_w[0, 1]),
                float(cube.data.root_pos_w[0, 2]))

    print(f"[settle] post-execute cube={tuple(round(v, 3) for v in _cube_pos())} "
          f"usd={_usd_translate()} target={tuple(round(v, 3) for v in place_target)}", flush=True)
    samples = []
    for index in range(180):
        physics_step(run_runtime=False)
        if index % 12 == 11:
            pos = _cube_pos()
            samples.append(pos)
            print(f"[settle] t={clock['t']:.2f}s cube=({pos[0]:+.3f},{pos[1]:+.3f},{pos[2]:+.3f}) "
                  f"usd={_usd_translate()} dist_to_target={math.dist(pos, place_target):.3f}", flush=True)
    if not samples:
        samples = [(float(cube.data.root_pos_w[0, 0]), float(cube.data.root_pos_w[0, 1]),
                    float(cube.data.root_pos_w[0, 2]))]
    final = samples[-1]
    drift = max(math.dist(samples[i], samples[i + 1])
                for i in range(len(samples) - 4, len(samples) - 1)) \
        if len(samples) >= 5 else math.inf
    horizontal = math.dist((final[0], final[1]), (pick_origin[0], pick_origin[1]))
    record("place.horizontal_shift", horizontal >= PLACE_SHIFT_MIN_M,
           f"moved {horizontal:.3f} m from pick origin (>= {PLACE_SHIFT_MIN_M})")
    record("place.on_ground", abs(final[2] - CUBE_SIZE_M / 2) <= 0.015,
           f"final z={final[2]:+.4f} (cube half-height {CUBE_SIZE_M / 2:.3f})")
    record("place.stable", drift <= SETTLE_MAX_DRIFT_M,
           f"max sample-to-sample drift over the last samples {drift:.4f} m (<= {SETTLE_MAX_DRIFT_M})")
    record("place.finite", states_finite(), "states finite after place")
    record("place.resources_released",
           held_after is None and runtime.state(ROBOT_NAME).value == "idle",
           f"phase={runtime.state(ROBOT_NAME).value}")

    print(
        "KEY_NUMBERS "
        f"pick_origin=({pick_origin[0]:+.3f},{pick_origin[1]:+.3f},{pick_origin[2]:+.3f}) "
        f"lift_m={lift:.3f} hold_min_lift_m={min(hold_samples) if hold_samples else float('nan'):.3f} "
        f"place_target=({place_target[0]:+.3f},{place_target[1]:+.3f},{place_target[2]:+.3f}) "
        f"final=({final[0]:+.3f},{final[1]:+.3f},{final[2]:+.3f}) "
        f"horizontal_shift_m={horizontal:.3f} drift_m={drift:.4f}",
        flush=True,
    )
    return _finish(all(ok for _n, ok, _d in CHECKS), environment, hold)


class _EnvironmentNamespace:
    """Duck-typed env giving IdealizedManipulationRuntime what it reads."""

    def __init__(self, sim, articulations, rigid_objects):
        self.sim = sim
        self.num_envs = 1
        self.device = "cpu"
        self.scene = _SceneNamespace(articulations, rigid_objects)
        self.runtime = None


class _SceneNamespace:
    def __init__(self, articulations, rigid_objects):
        self.articulations = articulations
        self.rigid_objects = rigid_objects


def _vec3(values):
    return (float(values[0]), float(values[1]), float(values[2]))


def _finish(ok: bool, environment, hold) -> int:
    _print_summary(ok)
    print("[skill] phases complete; viewport stays interactive. Ctrl+C to quit.", flush=True)
    app = None
    try:
        import omni.kit.app

        app = omni.kit.app.get_app()
    except Exception:
        pass
    try:
        while True:
            if app is not None and not app.is_running():
                break
            hold(1)
    except KeyboardInterrupt:
        pass
    return 0 if ok else 1


def _print_summary(ok: bool) -> None:
    passed = sum(1 for _name, check_ok, _detail in CHECKS if check_ok)
    print("-" * 72, flush=True)
    for name, check_ok, detail in CHECKS:
        print(f"{'PASS' if check_ok else 'FAIL'}  {name:<28} {detail}", flush=True)
    print(f"SUMMARY {passed}/{len(CHECKS)} checks passed", flush=True)
    print(f"SKILL_RESULT {'PASS' if ok else 'FAIL'} ({passed}/{len(CHECKS)} checks)", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
