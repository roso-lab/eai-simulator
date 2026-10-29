from __future__ import annotations

import importlib.util
import inspect
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest


def _load_runtime_module():
    package_name = "_eai_cylindrical_manipulation_test"
    controllers_dir = Path(__file__).resolve().parents[1] / "EAI" / "controllers"
    package = ModuleType(package_name)
    package.__path__ = [str(controllers_dir)]
    sys.modules[package_name] = package
    for module_name in ("ik_controller", "idealized_manipulation"):
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified, controllers_dir / f"{module_name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.idealized_manipulation"]


manipulation = _load_runtime_module()


class _Spec:
    model = "fake_arm"
    joint_names = ("joint_a", "joint_b")
    ee_body_names = ("tool",)
    gripper_joint_name = None


class _FakeArm:
    num_instances = 1
    device = "cpu"

    def __init__(self):
        self.data = SimpleNamespace(
            body_pos_w=np.array([[[0.0, 0.0, 0.0], [0.0, 0.0, 0.5]]], dtype=float),
            body_quat_w=np.array([[[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]], dtype=float),
            root_pos_w=np.array([[0.0, 0.0, 0.0]], dtype=float),
            root_quat_w=np.array([[1.0, 0.0, 0.0, 0.0]], dtype=float),
        )
        self.root_physx_view = SimpleNamespace(
            link_paths=[["/World/robot_arm/base", "/World/robot_arm/tool"]]
        )

    def find_joints(self, names, preserve_order=True):
        del preserve_order
        return list(range(len(names))), []

    def find_bodies(self, names, preserve_order=True):
        del preserve_order
        return ([1] if names == ["tool"] else []), []


class _FakeEnv:
    num_envs = 1

    def __init__(self):
        self.arm = _FakeArm()
        self.sim = SimpleNamespace(get_physics_dt=lambda: 0.5)
        self.scene = SimpleNamespace(articulations={"robot_arm": self.arm})
        self.ee_position = (0.0, 0.0, 0.5)
        self.ee_orientation = (1.0, 0.0, 0.0, 0.0)
        self.object_position = (0.8, 0.0, 0.2)
        self.object_orientation = (1.0, 0.0, 0.0, 0.0)
        self.proxy_position = None
        self.proxy_orientation = None
        self.object_isolated = False
        self.object_pose_writes = 0
        self.mass = 2.5
        self.inertia = (1.0, 2.0, 3.0)
        self.material = "steel"
        self.pose_writes = []


class _BatchedFakeArm:
    num_instances = 2
    device = "cpu"

    def __init__(self):
        self.target_rows = np.zeros((2, 2), dtype=float)
        self.data = SimpleNamespace(
            body_pos_w=np.array([
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.5]],
                [[4.0, 0.0, 0.0], [4.0, 0.0, 0.5]],
            ], dtype=float),
            body_quat_w=np.tile(np.array([[[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]]]), (2, 1, 1)),
            root_pos_w=np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]], dtype=float),
            root_quat_w=np.tile(np.array([[1.0, 0.0, 0.0, 0.0]]), (2, 1)),
        )
        self.root_physx_view = SimpleNamespace(link_paths=[
            [f"/World/envs/env_{i}/robot_arm/base", f"/World/envs/env_{i}/robot_arm/tool"]
            for i in range(2)
        ])

    def find_joints(self, names, preserve_order=True):
        del preserve_order
        return list(range(len(names))), []

    def find_bodies(self, names, preserve_order=True):
        del preserve_order
        return ([1] if names == ["tool"] else []), []

    def set_joint_position_target(self, target, *, joint_ids=None, env_ids=None):
        rows = np.asarray(env_ids, dtype=int)
        columns = list(joint_ids)
        self.target_rows[np.ix_(rows, columns)] = np.asarray(target)


class _BatchedFakeEnv:
    num_envs = 2

    def __init__(self):
        self.arm = _BatchedFakeArm()
        self.sim = SimpleNamespace(get_physics_dt=lambda: 0.5)
        self.scene = SimpleNamespace(
            articulations={"robot_arm": self.arm},
            rigid_objects={},
            extras={},
            env_origins=np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]], dtype=float),
        )


class _ValidPrim:
    def IsValid(self):
        return True


class _FakeRuntime(manipulation.IdealizedManipulationRuntime):
    def _end_effector_pose(self, binding):
        del binding
        return self._env.ee_position, self._env.ee_orientation

    def _rigid_body_pose(self, body_path):
        if body_path == "/World/Object":
            return self._env.object_position, self._env.object_orientation
        return self._env.ee_position, self._env.ee_orientation

    def _isolate_object_for_follow(
        self, robot_name, object_body_path, object_position_w, object_orientation_wxyz
    ):
        proxy_path = f"/World/fake/{robot_name}/proxy"
        self._owned_proxy_paths.add(proxy_path)
        self._env.object_isolated = True
        self._env.proxy_position = tuple(object_position_w)
        self._env.proxy_orientation = tuple(object_orientation_wxyz)
        return manipulation._ObjectIsolation(
            object_body_path=object_body_path,
            proxy_path=proxy_path,
            object_position_w=tuple(object_position_w),
            object_orientation_wxyz=tuple(object_orientation_wxyz),
            object_scale_w=(1.0, 1.0, 1.0),
            attributes=(),
        )

    def _set_proxy_object_pose_values(
        self, stage, proxy_path, object_position_w, object_orientation_wxyz, object_scale_w
    ):
        del stage, object_scale_w
        assert proxy_path in self._owned_proxy_paths
        self._env.proxy_position = tuple(object_position_w)
        self._env.proxy_orientation = tuple(object_orientation_wxyz)

    def _sync_follow_proxy_pose(self, held, ee_position_w, ee_orientation_wxyz):
        object_position, object_orientation = manipulation._follow_object_pose(
            ee_position_w,
            ee_orientation_wxyz,
            held.relative_object_position_ee,
            held.relative_object_orientation_wxyz,
        )
        self._env.proxy_position = object_position
        self._env.proxy_orientation = object_orientation
        return object_position, object_orientation

    def _write_rigid_body_pose(self, body_path, position_w, orientation_wxyz):
        assert body_path == "/World/Object"
        self._env.object_position = tuple(position_w)
        self._env.object_orientation = tuple(orientation_wxyz)
        self._env.object_pose_writes += 1

    def _restore_object_isolation(self, isolation, *, remove_proxy):
        assert isolation.object_body_path == "/World/Object"
        self._env.object_isolated = False
        if remove_proxy:
            self._remove_proxy(isolation.proxy_path)

    def _zero_rigid_body_velocity(self, body_path):
        assert body_path == "/World/Object"

    def _remove_proxy(self, proxy_path):
        assert proxy_path in self._owned_proxy_paths
        self._owned_proxy_paths.remove(proxy_path)


def _runtime(monkeypatch, *, move_ee=True, **kwargs):
    env = _FakeEnv()

    def apply_pose(
        runtime_env,
        arm,
        robot_name,
        model_spec,
        target_position_w,
        target_orientation_wxyz,
        **extra,
    ):
        del arm, robot_name, model_spec, extra
        runtime_env.pose_writes.append(tuple(target_position_w))
        if move_ee:
            runtime_env.ee_position = tuple(target_position_w)
            runtime_env.ee_orientation = tuple(target_orientation_wxyz)
        return True

    monkeypatch.setattr(manipulation, "apply_end_effector_pose", apply_pose)
    monkeypatch.setattr(manipulation, "hold_gripper_position", lambda *_, **__: True)
    return env, _FakeRuntime(env, {"robot": _Spec()}, **kwargs)


def _advance(runtime, phase, limit=200):
    for _ in range(limit):
        runtime.run_post_apply()
        if runtime.state("robot") == phase:
            return
    raise AssertionError(f"runtime did not reach {phase}")


def _advance_pick_to_hold(runtime):
    _advance(runtime, manipulation.ManipulationPhase.ATTACHED_HOLD)


def test_unified_arm_boolean_place_after_external_base_motion(monkeypatch):
    from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
    from task.executors import PickPlaceExecutor
    from task.executors.pick_place_adapters import ArmPickPlaceAdapter
    env, runtime = _runtime(monkeypatch)
    # Real grasp offset is intentionally nonzero. The place input still
    # describes the object origin, not the wrist.
    backend = ArmPickPlaceAdapter("robot", runtime, grasp_offset_object_m=(0., 0., .06))
    clock = [0.]
    def tick():
        runtime.run_post_apply()
        clock[0] += .5
    executor = PickPlaceExecutor(advance=tick, simulation_time=lambda: clock[0],
                                physical_object_experiment_authorization="action_only_pick_place")
    executor.register_adapter(backend)
    def req(key, operation, target):
        del target
        return SkillRequest(key, PICK_PLACE_SKILL_ID, "robot", parameters={
            "operation": operation, "object_ref": "logical_box", "timeout_s": 100.})
    assert executor.execute(req("pick", "pick", env.object_position)) is True
    assert not env.object_isolated and runtime.held_object("robot") is None
    env.arm.data.root_pos_w[0] = (1., .2, 0.)
    env.arm.data.root_quat_w[0] = (math.sqrt(.5), 0., 0., math.sqrt(.5))
    tick()
    assert executor.execute(req("place", "place", (1., 1., .2))) is True
    assert env.object_position == pytest.approx((.8, 0., .2), abs=1e-5)
    assert env.object_pose_writes == 0
    assert not env.object_isolated
    tick()
    assert env.object_pose_writes == 0


def test_opt_in_arm_failure_retains_follow_proxy(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.8, .2, 0.), recovery_pose=(.4, .5, 0.))
    _advance_pick_to_hold(runtime)
    runtime._fail(runtime._states["robot"], "injected holding failure")
    assert runtime.state("robot") is manipulation.ManipulationPhase.ATTACHED_HOLD
    assert runtime.held_object("robot") is not None
    assert env.object_isolated and env.object_pose_writes == 0
    env.arm.data.root_pos_w[0, 0] += .1
    runtime.run_post_apply()
    assert env.proxy_position is not None


@pytest.mark.parametrize("position,orientation", [
    ((.25, -.1, .1), (1., 0., 0., 0.)),
    ((.2, -.2, .1), (.8, .2, .3, .1)),
])
def test_local_backend_uses_full_arm_mount_pose(monkeypatch, position, orientation):
    from task.executors.pick_place_adapters import ArmPickPlaceAdapter
    from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
    from algorithm.skills.pick_place import compile_request
    env, runtime = _runtime(monkeypatch)
    env.arm.data.root_pos_w[0] = position
    env.arm.data.root_quat_w[0] = orientation
    backend = ArmPickPlaceAdapter("robot", runtime)
    command = compile_request(SkillRequest(
        "fixed", PICK_PLACE_SKILL_ID, "robot",
        parameters={"operation": "pick", "object_ref": "logical_box"}))
    contact, _, pregrasp, retreat, approach = backend._cartesian_targets(command)
    assert contact[2] < pregrasp[2] < retreat[2]
    assert approach == pytest.approx((0., 0., -1.))


def test_opt_in_arm_restore_failure_retains_pending_ownership(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.8, .2, 0.), recovery_pose=(.4, .5, 0.))
    def fail(*_, **__):
        raise RuntimeError("restore failed")
    monkeypatch.setattr(runtime, "_restore_object_isolation", fail)
    runtime._fail(runtime._states["robot"], "cancel before grasp")
    assert env.object_isolated
    assert runtime._states["robot"].pending_isolation is not None
    assert runtime._object_owners["/World/Object"] == "robot"


def test_empty_arm_cancel_freezes_actual_joint_targets_and_restores_object(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    env.arm.data.joint_pos = np.array([[.2, -.4]])
    env.arm.data.joint_pos_target = np.array([[1., 1.]])
    def write(values, *, joint_ids):
        env.arm.data.joint_pos_target[:, joint_ids] = values
    env.arm.set_joint_position_target = write
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.8, .2, 0.), recovery_pose=(.4, .5, 0.))
    assert runtime.stop_retaining_object("robot", "cancel before attach") is True
    assert np.allclose(env.arm.data.joint_pos_target, env.arm.data.joint_pos)
    assert runtime.held_object("robot") is None
    assert not env.object_isolated and env.object_pose_writes == 0


def test_opt_in_partial_release_is_not_repeated(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.8, .2, 0.), recovery_pose=(.4, .5, 0.))
    _advance_pick_to_hold(runtime)
    assert runtime.start_place("robot", (.7, .2, 0.))
    def fail(*_, **__):
        raise RuntimeError("release restoration failed")
    monkeypatch.setattr(runtime, "_restore_object_isolation", fail)
    _advance(runtime, manipulation.ManipulationPhase.FAILED)
    assert env.object_pose_writes == 1
    assert runtime._states["robot"].release_started
    for _ in range(5):
        runtime.run_post_apply()
    assert env.object_pose_writes == 1
    assert runtime.stop_retaining_object("robot", "cancel") is False


def test_unified_arm_released_object_drift_returns_false_without_releasing_twice(monkeypatch):
    from algorithm.skills import PICK_PLACE_SKILL_ID, SkillRequest
    from task.executors import PickPlaceExecutor
    from task.executors.pick_place_adapters import ArmPickPlaceAdapter
    env, runtime = _runtime(monkeypatch)
    backend = ArmPickPlaceAdapter("robot", runtime)
    clock = [0.]
    def tick():
        runtime.run_post_apply()
        clock[0] += .5
    executor = PickPlaceExecutor(advance=tick, simulation_time=lambda: clock[0],
                                physical_object_experiment_authorization="action_only_pick_place")
    executor.register_adapter(backend)
    assert backend.feedback().grasp_state == "empty"
    def request(request_id, operation):
        return SkillRequest(request_id, PICK_PLACE_SKILL_ID, "robot",
                            parameters={"operation": operation, "object_ref": "logical_box", "timeout_s": 100.})
    assert executor.execute(request("pick", "pick")) is True
    assert executor.execute(request("place", "place")) is True
    assert runtime.held_object("robot") is None
    assert env.object_pose_writes == 0
    assert not executor._resources.snapshot()["resources"]
    runtime.run_post_apply()
    assert env.object_pose_writes == 0


def test_pose_validation_and_yaw_wrap():
    pose = manipulation.normalize_cylindrical_pose((1, 2, 3 * math.pi))
    assert pose == manipulation.CylindricalPose(1.0, 2.0, -math.pi)
    assert manipulation.normalize_cylindrical_pose(manipulation.CylindricalPose(1, 2, -math.pi)).yaw_rad == -math.pi
    for bad in ((1, 2), (1, 2, 3, 4), (-1, 2, 0), (1, math.inf, 0), (1, 2, math.nan)):
        with pytest.raises(ValueError):
            manipulation.normalize_cylindrical_pose(bad)


def test_cylindrical_conversion_with_translated_rotated_base():
    qz90 = (math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
    pose = manipulation.CylindricalPose(2.0, 3.0, 0.0)
    assert manipulation.cylindrical_to_local_position(pose, 0.0) == pytest.approx((2, 0, 3))
    assert manipulation.cylindrical_to_world_position(pose, (1, 2, 3), qz90, 0.0) == pytest.approx((1, 4, 6))


@pytest.mark.parametrize("offset", (0.0, 0.10915))
@pytest.mark.parametrize("x,yaw", ((0.0, 1.2), (0.2, -3.0), (0.6, 2.9)))
def test_offset_plane_round_trip(offset, x, yaw):
    pose = manipulation.CylindricalPose(x, 0.4, yaw)
    local = manipulation.cylindrical_to_local_position(pose, offset)
    restored = manipulation.local_position_to_cylindrical(local, offset, fallback_yaw=yaw)
    assert restored.x_m == pytest.approx(x, abs=1.0e-8)
    assert restored.z_m == pose.z_m
    assert restored.yaw_rad == pytest.approx(pose.yaw_rad)
    q = (math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0)
    world = manipulation.cylindrical_to_world_position(pose, (2, 3, 4), q, offset)
    restored_world = manipulation.IdealizedManipulationRuntime._cylindrical_from_world(
        world, (2, 3, 4), q, plane_offset_m=offset, fallback_yaw=yaw,
    )
    assert restored_world.x_m == pytest.approx(x, abs=1.0e-8)
    assert restored_world.yaw_rad == pytest.approx(yaw)


def test_model_specific_gripper_and_approach_configuration():
    ur5 = manipulation.planar_ik_spec_for_model(
        SimpleNamespace(model="ur5", joint_names=(), ee_body_names=())
    )
    z1 = manipulation.planar_ik_spec_for_model(
        SimpleNamespace(model="z1", joint_names=(), ee_body_names=(), gripper_joint_name="jointGripper")
    )
    assert ur5.gripper_open_position_rad == pytest.approx(0.0)
    assert ur5.gripper_collision_body_names == ()
    assert z1.gripper_open_position_rad == pytest.approx(-0.8)
    assert z1.gripper_closed_position_rad == pytest.approx(0.0)
    assert z1.gripper_collision_body_names == ("gripperStator", "gripperMover")


def test_offset_exclusion_and_bounded_yaw_paths():
    with pytest.raises(ValueError, match="exclusion"):
        manipulation.local_position_to_cylindrical((0.01, 0.0, 0.4), 0.10915)
    choose = manipulation.feasible_yaw_joint_target
    limit = math.radians(150)
    # Z1 must take the long route through zero, inside its interval.
    assert choose(-2.5, 2.5, -limit, limit) == pytest.approx(-2.5)
    with pytest.raises(ValueError, match="limits"):
        choose(math.pi, 0.0, -limit, limit)
    # UR5 chooses an equivalent angle without rewriting actual joint state.
    assert choose(-3.0, 3.0, -2 * math.pi, 2 * math.pi) == pytest.approx(2 * math.pi - 3.0)


def _planar_runtime(monkeypatch, model):
    env, runtime = _runtime(monkeypatch)
    names = (("joint1", "joint2", "joint3", "joint4", "joint5", "joint6") if model == "z1"
             else ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"))
    spec = SimpleNamespace(model=model, joint_names=names, ee_body_names=("tool",), gripper_joint_name=None)
    runtime._model_specs["robot"] = (spec,)
    plane = manipulation.planar_ik_spec_for_model(spec)
    env.arm.find_joints = lambda requested, **_: ([names.index(n) for n in requested], requested)
    env.arm.data.joint_pos = np.zeros((1, 6))
    env.arm.data.soft_joint_pos_limits = np.array([[[-2.6, 2.6]] * 6])
    env.arm.data.soft_joint_vel_limits = np.full((1, 6), 2.0)
    env.ee_position = manipulation.cylindrical_to_local_position((0.2, 0.5, 0), plane.plane_offset_m)
    env.ee_orientation = plane.tool_orientation_plane_wxyz
    calls = []

    def apply_plane(runtime_env, arm, robot, model_spec, pos, quat, **kwargs):
        calls.append(kwargs)
        arm.data.joint_pos[0, kwargs["yaw_joint_id"]] = kwargs["yaw_joint_target_rad"]
        runtime_env.ee_position, runtime_env.ee_orientation = tuple(pos), tuple(quat)
        return True

    monkeypatch.setattr(manipulation, "apply_planar_end_effector_pose", apply_plane)
    return env, runtime, calls


@pytest.mark.parametrize("model", ("z1", "ur5"))
def test_local_pick_unfolds_and_establishes_tool_reference_before_rotate(monkeypatch, model):
    env, runtime, calls = _planar_runtime(monkeypatch, model)
    binding = runtime._binding("robot")
    env.ee_position = (-.2, binding.planar_spec.plane_offset_m, .5)
    env.ee_orientation = (1., 0., 0., 0.)
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.5, .2, .6), recovery_pose=(.2, .6, 0.))
    assert runtime.state("robot") == manipulation.ManipulationPhase.PREPARE
    _advance(runtime, manipulation.ManipulationPhase.ROTATE)
    assert env.ee_position[0] > 0.
    assert env.ee_orientation == pytest.approx(binding.planar_spec.tool_orientation_plane_wxyz)
    assert calls and all(not call["rotate_only"] for call in calls)
    _advance_pick_to_hold(runtime)
    assert runtime.held_object("robot") is not None


@pytest.mark.parametrize("model", ("ur5", "z1"))
def test_planar_dispatch_locks_shoulder_and_uses_stable_tool_reference(monkeypatch, model):
    env, runtime, calls = _planar_runtime(monkeypatch, model)
    assert runtime.start_pick("robot", "/World/Object", (0.5, 0.2, 0.6), recovery_pose=(0.2, 0.6, 0.0))
    _advance_pick_to_hold(runtime)
    rotation = [c for c in calls if c["rotate_only"]]
    planar = [c for c in calls if not c["rotate_only"]]
    assert rotation and planar
    assert all(c["yaw_joint_id"] == 0 and 0 not in c["ik_joint_ids"] for c in calls)
    assert {round(c["yaw_joint_target_rad"], 8) for c in planar} == {0.6}
    if model == "z1":
        assert all(c["ik_joint_ids"] == (1, 2, 3, 4) and c["fixed_joint_ids"] == (5,) for c in calls)
    reference = runtime._binding("robot").planar_spec.tool_orientation_plane_wxyz
    # A measured error must never become the reference of the next task.
    env.ee_orientation = (1, 0, 0, 0)
    assert runtime.start_place("robot", (0.5, 0.2, -0.6))
    assert runtime._states["robot"].ee_orientation_relative_b_wxyz == pytest.approx(reference)
    env.ee_orientation = reference
    _advance(runtime, manipulation.ManipulationPhase.IDLE)
    assert env.object_pose_writes == 1


def test_arrival_requires_lateral_position_and_tool_orientation(monkeypatch):
    env, runtime, _ = _planar_runtime(monkeypatch, "z1")
    assert runtime.start_move("robot", (0.2, 0.5, 0.0))
    runtime.run_post_apply()
    state = runtime._states["robot"]
    binding = runtime._binding("robot")
    state.command_pose = state.phase_goal
    measured = state.command_pose
    env.ee_position = (0.2, 0.08, 0.5)
    assert not runtime._phase_reached(state, measured, binding)
    env.ee_position = (0.2, 0.0, 0.5)
    env.ee_orientation = (1, 0, 0, 0)
    assert not runtime._phase_reached(state, measured, binding)


def test_z1_yaw_crossing_takes_feasible_long_path_at_limited_speed(monkeypatch):
    env, runtime, calls = _planar_runtime(monkeypatch, "z1")
    env.arm.data.joint_pos[0, 0] = 2.5
    env.ee_position = manipulation.cylindrical_to_local_position((0.2, 0.5, 2.5), 0.0)
    qz = (math.cos(1.25), 0.0, 0.0, math.sin(1.25))
    env.ee_orientation = manipulation._quat_multiply(qz, runtime._binding("robot").planar_spec.tool_orientation_plane_wxyz)
    assert runtime.start_move("robot", (0.2, 0.5, -2.5), phase_timeout_steps=100)
    previous = 2.5
    while runtime.state("robot") == manipulation.ManipulationPhase.ROTATE:
        runtime.run_post_apply()
        current = runtime._states["robot"].yaw_joint_target_rad
        assert -2.6 <= current <= 2.6
        assert 0.0 <= previous - current <= env.sim.get_physics_dt() + 1e-12
        previous = current
    assert previous == pytest.approx(-2.5)
    assert len(calls) >= 10


def test_planar_config_and_folded_initial_pose_rejection(monkeypatch):
    with pytest.raises(ValueError, match="unique"):
        manipulation.PlanarIkSpec("z1", "joint1", ("joint1",))
    with pytest.raises(ValueError, match="finite"):
        manipulation.PlanarIkSpec("z1", "joint1", ("joint2",), yaw_zero_rad=math.nan)
    env, runtime, _ = _planar_runtime(monkeypatch, "z1")
    env.ee_position = (-0.2, 0.0, 0.5)
    assert not runtime.start_pick("robot", "/World/Object", (0.4, 0.2, 0), recovery_pose=(0.2, 0.6, 0))
    assert "behind" in runtime.last_error("robot")
    assert not env.object_isolated and not runtime._object_owners


@pytest.mark.parametrize("model", ("ur5", "z1"))
def test_planar_recovery_remains_local_when_base_moves(monkeypatch, model):
    env, runtime, calls = _planar_runtime(monkeypatch, model)
    recovery = manipulation.CylindricalPose(0.2, 0.6, 0.0)
    assert runtime.start_pick("robot", "/World/Object", (0.5, 0.2, 0.6), recovery_pose=recovery)
    _advance_pick_to_hold(runtime)
    original_source = env.object_position
    q = (math.sqrt(0.5), 0, 0, math.sqrt(0.5))
    env.arm.data.root_pos_w[0] = (2.0, -1.0, 0.3)
    env.arm.data.root_quat_w[0] = q
    runtime.run_post_apply()
    binding = runtime._binding("robot")
    expected = manipulation.cylindrical_to_world_position(recovery, (2, -1, .3), q, binding.planar_spec.plane_offset_m)
    assert env.ee_position == pytest.approx(expected)
    assert runtime._states["robot"].command_pose == recovery
    assert calls[-1]["yaw_joint_target_rad"] == pytest.approx(0.0)
    assert env.object_position == original_source


def test_reduced_dls_excludes_shoulder_and_fixed_wrist_and_rotation_holds_targets(monkeypatch):
    torch = pytest.importorskip("torch")
    ik = sys.modules[manipulation.__package__ + ".ik_controller"]
    math_module = ModuleType("isaaclab.utils.math")
    math_module.compute_pose_error = lambda p, q, tp, tq, **_: (tp - p, torch.zeros_like(p))
    math_module.matrix_from_quat = lambda q: torch.eye(3).unsqueeze(0).expand(len(q), -1, -1)
    math_module.quat_inv = lambda q: q
    math_module.quat_mul = lambda q, r: q
    monkeypatch.setitem(sys.modules, "isaaclab.utils.math", math_module)
    names = tuple(f"joint{i}" for i in range(1, 7))
    jacobian = torch.zeros(1, 1, 6, 6)
    jacobian[0, 0, 0, 1] = 1.0
    jacobian[0, 0, 2, 2] = 1.0
    jacobian[0, 0, 4, 3] = 1.0
    jacobian[0, 0, 5, 4] = 1.0
    jacobian[..., 0] = 1e6
    jacobian[..., 5] = 1e6
    data = SimpleNamespace(
        joint_pos=torch.zeros(1, 6), joint_pos_target=torch.zeros(1, 6),
        soft_joint_pos_limits=torch.tensor([[[-3., 3.]] * 6]),
        soft_joint_vel_limits=torch.ones(1, 6),
        body_pos_w=torch.zeros(1, 2, 3),
        body_quat_w=torch.tensor([[[1., 0., 0., 0.]] * 2]),
        root_quat_w=torch.tensor([[1., 0., 0., 0.]]),
    )
    writes = []
    arm = SimpleNamespace(
        num_joints=6, num_bodies=2, data=data,
        find_joints=lambda ns, **_: ([names.index(n) for n in ns], ns),
        find_bodies=lambda *_, **__: ([1], []),
        root_physx_view=SimpleNamespace(get_jacobians=lambda: jacobian),
        set_joint_position_target=lambda target, **_: writes.append(target.clone()),
    )
    env = SimpleNamespace(sim=SimpleNamespace(get_physics_dt=lambda: 1 / 120))
    spec = SimpleNamespace(model="z1", joint_names=names, ee_body_names=("tool",))
    kwargs = dict(plane_yaw_rad=0.0, yaw_joint_id=0, yaw_joint_target_rad=0.2,
                  ik_joint_ids=(1, 2, 3, 4), fixed_joint_ids=(5,))
    assert ik.apply_planar_end_effector_pose(env, arm, "robot", spec, (0.2, 0.0, 0.1), (1, 0, 0, 0), **kwargs)
    assert writes[-1][0, 0] == pytest.approx(0.2)
    assert writes[-1][0, 1] > 0 and writes[-1][0, 2] > 0
    assert writes[-1][0, 5] == 0
    previous = writes[-1].clone()
    # A load-induced measured error during rotation cannot recapture targets.
    data.joint_pos[:, 1:5] = 0.05
    kwargs["yaw_joint_target_rad"] = 0.3
    assert ik.apply_planar_end_effector_pose(env, arm, "robot", spec, (99, 99, 99), (1, 0, 0, 0), rotate_only=True, **kwargs)
    assert torch.equal(writes[-1][:, 1:], previous[:, 1:])
    assert writes[-1][0, 0] == pytest.approx(0.3)
    env._eai_planar_ik_states[("other", "z1")] = object()
    runtime = manipulation.IdealizedManipulationRuntime(env, {"robot": spec})
    env.scene = SimpleNamespace(articulations={"robot_arm": arm})
    binding = runtime._binding("robot")
    # Empty-arm ROS targets remain available in IDLE. Only joint6 is written.
    data.joint_pos_target[:, 1:5] = 1.0
    runtime._hold_fixed_joints("robot", binding, manipulation.RobotManipulationState())
    assert writes[-1].shape == (1, 1) and writes[-1].item() == 0.0
    assert torch.equal(data.joint_pos_target[:, 1:5], torch.ones(1, 4))
    runtime.reset()
    assert set(env._eai_planar_ik_states) == {("other", "z1")}


def test_public_interfaces_are_cylindrical_and_keyword_options():
    move = inspect.signature(manipulation.IdealizedManipulationRuntime.start_move)
    pick = inspect.signature(manipulation.IdealizedManipulationRuntime.start_pick)
    place = inspect.signature(manipulation.IdealizedManipulationRuntime.start_place)
    assert "target_pose" in move.parameters
    assert "recovery_pose" in pick.parameters
    assert pick.parameters["recovery_pose"].default is inspect.Parameter.empty
    assert place.parameters["recovery_pose"].default is None
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in list(move.parameters.values())[3:])
    # Pin the documented defaults so code and docs cannot drift silently.
    assert manipulation.DEFAULT_POSITION_TOLERANCE_M == 0.05
    assert manipulation.DEFAULT_YAW_TOLERANCE_RAD == 0.05
    assert manipulation.DEFAULT_PHASE_TIMEOUT_STEPS == 300
    assert manipulation.DEFAULT_ATTACHMENT_SETTLE_DURATION_S == 1.0
    assert manipulation.DEFAULT_RADIAL_SPEED_M_S == 0.35
    assert manipulation.DEFAULT_VERTICAL_SPEED_M_S == 0.10
    assert manipulation.DEFAULT_YAW_SPEED_RAD_S == 1.0
    for signature in (move, pick, place):
        assert signature.parameters["tolerance_m"].default == 0.05
        assert signature.parameters["yaw_tolerance_rad"].default == 0.05
        assert signature.parameters["phase_timeout_steps"].default == 300
    assert pick.parameters["settle_duration_s"].default == 1.0


def test_start_move_strict_rotate_radial_vertical_order(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    assert runtime.start_move("robot", (0.75, 0.8, math.pi / 2), tolerance_m=0.01)
    phases = []
    previous = runtime.state("robot")
    for _ in range(100):
        runtime.run_post_apply()
        current = runtime.state("robot")
        if current != previous:
            phases.append(current)
            previous = current
        if current == manipulation.ManipulationPhase.IDLE:
            break
    assert phases == [
        manipulation.ManipulationPhase.RADIAL_APPROACH,
        manipulation.ManipulationPhase.VERTICAL_APPROACH,
        manipulation.ManipulationPhase.IDLE,
    ]
    assert env.ee_position == pytest.approx((0.0, 0.75, 0.8))


def test_pick_order_and_recovery_hold(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    grasp = manipulation.CylindricalPose(0.8, 0.2, math.pi / 2)
    recovery = manipulation.CylindricalPose(0.2, 0.8, 0.0)
    assert runtime.start_pick("robot", "/World/Object", grasp, recovery_pose=recovery)
    transitions = []
    previous = runtime.state("robot")
    for _ in range(200):
        runtime.run_post_apply()
        current = runtime.state("robot")
        if current != previous:
            transitions.append(current)
            previous = current
        if current == manipulation.ManipulationPhase.ATTACHED_HOLD:
            break
    assert transitions == [
        manipulation.ManipulationPhase.RADIAL_APPROACH,
        manipulation.ManipulationPhase.VERTICAL_APPROACH,
        manipulation.ManipulationPhase.GRASP,
        manipulation.ManipulationPhase.LIFT,
        manipulation.ManipulationPhase.RETRACT,
        manipulation.ManipulationPhase.ROTATE,
        manipulation.ManipulationPhase.ATTACHED_HOLD,
    ]
    assert runtime.held_object("robot") is not None
    assert runtime.start_move("robot", (0.1, 1.0, 0.0)) is False
    assert "ATTACHED_HOLD" in (runtime.last_error("robot") or "")
    assert env.object_isolated is True


def test_z1_gripper_animates_open_close_open_through_pick_place(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    spec = SimpleNamespace(
        model="z1", joint_names=_Spec.joint_names, ee_body_names=_Spec.ee_body_names,
        gripper_joint_name="jointGripper",
        idealized_planar_ik_spec=manipulation.PlanarIkSpec("fake", None, _Spec.joint_names),
    )
    runtime._model_specs["robot"] = (spec,)
    commands = []

    def hold_gripper(arm, model_spec, *, position_rad=None):
        assert arm is env.arm
        assert model_spec is spec
        commands.append((runtime._states["robot"].operation, runtime.state("robot"), position_rad))
        return True

    monkeypatch.setattr(manipulation, "hold_gripper_position", hold_gripper)
    assert runtime.start_pick(
        "robot", "/World/Object", (0.8, 0.2, 0.4), recovery_pose=(0.2, 0.8, 0.0),
    )
    _advance_pick_to_hold(runtime)
    runtime.run_post_apply()
    assert runtime.start_place("robot", (0.7, 0.2, -0.4))
    _advance(runtime, manipulation.ManipulationPhase.IDLE)
    runtime.run_post_apply()
    assert {phase for _, phase, _ in commands} >= {
        manipulation.ManipulationPhase.GRASP,
        manipulation.ManipulationPhase.ATTACHED_HOLD,
        manipulation.ManipulationPhase.RELEASE,
        manipulation.ManipulationPhase.IDLE,
    }
    by_operation_phase = {}
    for operation, phase, position in commands:
        by_operation_phase.setdefault((operation, phase), set()).add(position)
    assert by_operation_phase[("pick", manipulation.ManipulationPhase.GRASP)] == {0.0}
    assert by_operation_phase[("pick", manipulation.ManipulationPhase.LIFT)] == {0.0}
    assert by_operation_phase[("pick", manipulation.ManipulationPhase.RETRACT)] == {0.0}
    assert by_operation_phase[("pick", manipulation.ManipulationPhase.ATTACHED_HOLD)] == {0.0}
    assert by_operation_phase[("place", manipulation.ManipulationPhase.ROTATE)] == {0.0}
    assert by_operation_phase[("place", manipulation.ManipulationPhase.RADIAL_APPROACH)] == {0.0}
    assert by_operation_phase[("place", manipulation.ManipulationPhase.VERTICAL_APPROACH)] == {0.0}
    assert by_operation_phase[("place", manipulation.ManipulationPhase.RELEASE)] == {-0.8}
    assert by_operation_phase[(None, manipulation.ManipulationPhase.IDLE)] == {-0.8}


def test_gripper_explicit_closed_target_does_not_hold_measured_open_angle():
    torch = pytest.importorskip("torch")
    ik = sys.modules[manipulation.__package__ + ".ik_controller"]
    targets = []
    arm = SimpleNamespace(
        data=SimpleNamespace(
            joint_pos=torch.tensor([[-0.35]]),
            soft_joint_pos_limits=torch.tensor([[[-math.pi / 2, 0.0]]]),
        ),
        find_joints=lambda *_, **__: ([0], []),
        set_joint_position_target=lambda target, **_: targets.append(target.clone()),
    )
    spec = SimpleNamespace(gripper_joint_name="jointGripper")
    assert ik.hold_gripper_position(arm, spec)
    assert targets[-1].item() == pytest.approx(-0.35)
    assert ik.hold_gripper_position(arm, spec, position_rad=0.0)
    assert targets[-1].item() == 0.0
    assert arm.data.joint_pos.item() == pytest.approx(-0.35)


def test_z1_gripper_stays_closed_during_recovery_yaw_and_cancelled_holding(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    runtime._model_specs["robot"] = (SimpleNamespace(
        model="z1", joint_names=_Spec.joint_names, ee_body_names=_Spec.ee_body_names,
        gripper_joint_name="jointGripper",
        idealized_planar_ik_spec=manipulation.PlanarIkSpec("fake", None, _Spec.joint_names)),)
    holding_commands = []
    def hold(arm, model, *, position_rad=None):
        if runtime.held_object("robot") is not None:
            holding_commands.append((runtime.state("robot"), position_rad))
        return True
    monkeypatch.setattr(manipulation, "hold_gripper_position", hold)
    runtime.preserve_holding_on_failure("robot")
    assert runtime.start_pick("robot", "/World/Object", (.8, .2, .4), recovery_pose=(.2, .5, 0.))
    _advance_pick_to_hold(runtime)
    assert any(phase == manipulation.ManipulationPhase.ROTATE for phase, _ in holding_commands)
    assert runtime.stop_retaining_object("robot", "cancel") is True
    runtime.run_post_apply()
    assert holding_commands and all(target == 0. for _, target in holding_commands)
    assert env.object_isolated


def test_pick_place_references_keep_speed_limits_across_phase_transitions(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    dt = 1.0 / 120.0
    env.sim.get_physics_dt = lambda: dt
    grasp = manipulation.CylindricalPose(0.47, 0.213, 0.4)
    recovery = manipulation.CylindricalPose(0.25, 0.537, -0.3)
    place = manipulation.CylindricalPose(0.43, 0.137, 1.4)
    assert runtime.start_pick(
        "robot", "/World/Object", grasp, recovery_pose=recovery,
        tolerance_m=0.05, phase_timeout_steps=900,
    )
    for operation, final_phase, target in (
        ("pick", manipulation.ManipulationPhase.ATTACHED_HOLD, grasp),
        ("place", manipulation.ManipulationPhase.IDLE, place),
    ):
        if operation == "place":
            hold_command = runtime._states["robot"].command_pose
            assert runtime.start_place("robot", place, phase_timeout_steps=900)
            place_command = runtime._states["robot"].command_pose
            assert place_command.x_m == pytest.approx(hold_command.x_m)
            assert place_command.z_m == pytest.approx(hold_command.z_m)
        previous = runtime._states["robot"].command_pose
        phases = set()
        for _ in range(4000):
            runtime.run_post_apply()
            state = runtime._states["robot"]
            current = state.command_pose
            delta = (
                abs(current.x_m - previous.x_m),
                abs(current.z_m - previous.z_m),
                abs(manipulation.wrap_angle_rad(current.yaw_rad - previous.yaw_rad)),
            )
            assert delta[0] <= runtime._radial_speed_m_s * dt + 1.0e-12
            assert delta[1] <= runtime._vertical_speed_m_s * dt + 1.0e-12
            assert delta[2] <= runtime._yaw_speed_rad_s * dt + 1.0e-12
            assert sum(value > 1.0e-12 for value in delta) <= 1
            phases.add(state.phase)
            if state.phase in (manipulation.ManipulationPhase.GRASP, manipulation.ManipulationPhase.RELEASE):
                assert (current.x_m, current.z_m, current.yaw_rad) == pytest.approx(
                    (target.x_m, target.z_m, target.yaw_rad), abs=1.0e-12,
                )
            previous = current
            if state.phase == final_phase:
                break
        else:
            raise AssertionError(f"{operation} did not finish: {runtime.last_error('robot')}")
        assert manipulation.ManipulationPhase.VERTICAL_APPROACH in phases
        assert manipulation.ManipulationPhase.LIFT in phases
        expected_contact = manipulation.ManipulationPhase.GRASP if operation == "pick" else manipulation.ManipulationPhase.RELEASE
        assert expected_contact in phases
    assert env.object_pose_writes == 1


def test_recovery_follows_moving_base_in_local_coordinates(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    recovery = manipulation.CylindricalPose(0.2, 0.8, 0.0)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=recovery)
    _advance_pick_to_hold(runtime)
    env.arm.data.root_pos_w[0] = (5.0, -2.0, 1.0)
    env.arm.data.root_quat_w[0] = (math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
    runtime.run_post_apply()
    assert env.pose_writes[-1] == pytest.approx((5.0, -1.8, 1.8))


def test_follow_proxy_tracks_held_end_effector_during_lift(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=(0.2, 0.8, 0.0))
    _advance_pick_to_hold(runtime)
    before = env.proxy_position
    held = runtime.held_object("robot")
    runtime.start_place("robot", (0.6, 0.1, 0.0))
    runtime.run_post_apply()
    runtime.run_post_apply()
    assert env.proxy_position != before
    # The proxy must follow the end effector's *current* pose through the
    # captured relative offset, not a stale pick-time pose.
    expected = manipulation._follow_object_pose(
        env.ee_position,
        env.ee_orientation,
        held.relative_object_position_ee,
        held.relative_object_orientation_wxyz,
    )[0]
    assert env.proxy_position == pytest.approx(expected)


def test_place_order_and_release_once(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    recovery = manipulation.CylindricalPose(0.2, 0.8, 0.0)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=recovery)
    _advance_pick_to_hold(runtime)
    assert runtime.start_place("robot", (0.6, 0.1, math.pi / 2))
    transitions = []
    previous = runtime.state("robot")
    for _ in range(200):
        runtime.run_post_apply()
        current = runtime.state("robot")
        if current != previous:
            transitions.append(current)
            previous = current
        if current == manipulation.ManipulationPhase.IDLE:
            break
    assert transitions == [
        manipulation.ManipulationPhase.RADIAL_APPROACH,
        manipulation.ManipulationPhase.VERTICAL_APPROACH,
        manipulation.ManipulationPhase.RELEASE,
        manipulation.ManipulationPhase.LIFT,
        manipulation.ManipulationPhase.RETRACT,
        manipulation.ManipulationPhase.ROTATE,
        manipulation.ManipulationPhase.IDLE,
    ]
    assert env.object_pose_writes == 1
    assert env.object_isolated is False
    assert runtime.held_object("robot") is None
    assert env.object_position == pytest.approx((0.0, 0.6, 0.1))


def test_place_recovery_override_replaces_pick_recovery(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=(0.2, 0.8, 0.0))
    _advance_pick_to_hold(runtime)
    assert runtime.start_place("robot", (0.6, 0.1, math.pi / 2), recovery_pose=(0.3, 0.7, math.pi / 2))
    _advance(runtime, manipulation.ManipulationPhase.IDLE)
    # The arm must end at the explicit override recovery, not the pose saved
    # at pick time: (0.3, 0.7, pi/2) in the identity base frame.
    assert env.ee_position == pytest.approx((0.0, 0.3, 0.7))


def test_recovery_constraints_are_rejected(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    del env
    with pytest.raises(ValueError, match="greater"):
        runtime.start_pick("robot", "/World/Object", (0.8, 0.5, 0.0), recovery_pose=(0.2, 0.5, 0.0))
    with pytest.raises(ValueError, match="less than or equal"):
        runtime.start_pick("robot", "/World/Object", (0.4, 0.5, 0.0), recovery_pose=(0.5, 0.8, 0.0))


def test_follow_preserves_object_properties_and_proxy_cleanup(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    original = (env.mass, env.inertia, env.material)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=(0.2, 0.8, 0.0))
    _advance_pick_to_hold(runtime)
    assert runtime.held_object("robot").proxy_path in runtime._owned_proxy_paths
    assert runtime.start_place("robot", (0.6, 0.1, 0.0))
    _advance(runtime, manipulation.ManipulationPhase.IDLE)
    assert (env.mass, env.inertia, env.material) == original
    assert runtime._owned_proxy_paths == set()
    assert runtime._object_owners == {}
    assert env.object_pose_writes == 1


def test_timeout_reset_and_close_clean_all_follow_state(monkeypatch):
    env, runtime = _runtime(monkeypatch, move_ee=False)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, math.pi / 2), recovery_pose=(0.2, 0.8, 0.0), phase_timeout_steps=1)
    runtime.run_post_apply()
    assert runtime.state("robot") == manipulation.ManipulationPhase.FAILED
    assert env.object_isolated is False
    assert runtime._owned_proxy_paths == set()
    assert runtime._object_owners == {}
    env2, runtime2 = _runtime(monkeypatch)
    assert runtime2.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=(0.2, 0.8, 0.0))
    _advance_pick_to_hold(runtime2)
    runtime2.reset()
    assert env2.object_isolated is False
    assert runtime2._owned_proxy_paths == set()
    assert env2.object_pose_writes == 0
    runtime2.close()
    assert runtime2.enabled is False


def test_place_timeout_releases_and_clears_held_object(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    assert runtime.start_pick("robot", "/World/Object", (0.8, 0.2, 0.0), recovery_pose=(0.2, 0.8, 0.0))
    _advance_pick_to_hold(runtime)
    assert runtime.start_place("robot", (0.6, 0.1, math.pi / 2), phase_timeout_steps=1)
    runtime.run_post_apply()
    assert runtime.state("robot") == manipulation.ManipulationPhase.FAILED
    assert runtime.held_object("robot") is None
    assert env.object_isolated is False
    assert env.object_pose_writes == 0
    assert runtime._object_owners == {}


def test_error_paths_for_batch_and_busy_and_missing_hold(monkeypatch):
    env, runtime = _runtime(monkeypatch)
    env.num_envs = 2
    assert runtime.start_move("robot", (0.1, 0.2, 0.0)) is False
    assert "num_envs=1" in (runtime.last_error("robot") or "")
    env.num_envs = 1
    assert runtime.start_move("robot", (0.8, 0.2, 0.0))
    assert runtime.start_move("robot", (0.1, 0.2, 0.0)) is False
    assert "busy" in (runtime.last_error("robot") or "")
    assert runtime.start_place("robot", (0.1, 0.2, 0.0)) is False
    assert "hold" in (runtime.last_error("robot") or "")


def test_multi_environment_commands_write_only_selected_row(monkeypatch):
    env = _BatchedFakeEnv()
    runtime = manipulation.IdealizedManipulationRuntime(env, {"robot": _Spec()})
    assert runtime.enabled
    with pytest.raises(ValueError, match="env_id"):
        runtime.start_move("robot", (0.8, 0.7, 0.0), env_id=2)
    assert runtime.start_move("robot", (0.8, 0.7, 0.0), env_id=0)
    assert runtime.start_move("robot", (0.6, 0.6, 0.0), env_id=1)
    assert runtime.state("robot", env_id=0) == manipulation.ManipulationPhase.ROTATE
    assert runtime.state("robot", env_id=1) == manipulation.ManipulationPhase.ROTATE

    def write_env_target(self, robot_name, binding, state):
        del robot_name
        binding.arm.set_joint_position_target(
            np.array([[state.target_pose.x_m, state.target_pose.z_m]]),
            joint_ids=[0, 1], env_ids=[0],
        )

    monkeypatch.setattr(manipulation.IdealizedManipulationRuntime, "_drive_state", write_env_target)
    runtime.run_post_apply()
    assert env.arm.target_rows[0] == pytest.approx([0.8, 0.7])
    assert env.arm.target_rows[1] == pytest.approx([0.6, 0.6])


def test_multi_environment_pick_ownership_and_selective_reset(monkeypatch):
    env = _BatchedFakeEnv()
    runtime = manipulation.IdealizedManipulationRuntime(env, {"robot": _Spec()})
    stage = SimpleNamespace(GetPrimAtPath=lambda _path: _ValidPrim())
    monkeypatch.setattr(manipulation.IdealizedManipulationRuntime, "_stage", lambda self: stage)
    monkeypatch.setattr(
        manipulation.IdealizedManipulationRuntime,
        "_rigid_body_pose",
        lambda self, _path: ((0.8, 0.0, 0.2), (1.0, 0.0, 0.0, 0.0)),
    )

    def isolate(self, robot_name, object_body_path, position, orientation):
        proxy_path = f"{object_body_path}/VisualFollow/{robot_name}_proxy"
        self._owned_proxy_paths.add(proxy_path)
        return manipulation._ObjectIsolation(
            object_body_path, proxy_path, tuple(position), tuple(orientation),
            (1.0, 1.0, 1.0), (),
        )

    monkeypatch.setattr(manipulation.IdealizedManipulationRuntime, "_isolate_object_for_follow", isolate)
    monkeypatch.setattr(
        manipulation.IdealizedManipulationRuntime,
        "_restore_object_isolation",
        lambda self, isolation, *, remove_proxy: self._owned_proxy_paths.discard(isolation.proxy_path),
    )
    pick_args = ("robot", "/World/envs/env_0/Object", (0.8, 0.2, 0.0))
    assert runtime.start_pick(*pick_args, recovery_pose=(0.2, 0.8, 0.0), env_id=0)
    assert runtime.start_pick(*pick_args, recovery_pose=(0.2, 0.8, 0.0), env_id=1)
    row0, row1 = runtime._environment_runtimes
    assert set(row0._object_owners) == {"/World/envs/env_0/Object"}
    assert set(row1._object_owners) == {"/World/envs/env_1/Object"}
    assert runtime.held_object("robot", env_id=0) is None
    assert runtime.held_object("robot", env_id=1) is None

    runtime.reset([1])
    assert set(row0._object_owners) == {"/World/envs/env_0/Object"}
    assert row1._object_owners == {}
    assert runtime.state("robot", env_id=0) != manipulation.ManipulationPhase.IDLE
    assert runtime.state("robot", env_id=1) == manipulation.ManipulationPhase.IDLE
    with pytest.raises(ValueError, match="env_id"):
        runtime.reset([2])


def _make_usd_follow_fixture():
    pytest.importorskip("pxr.Usd")
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    object_xform = UsdGeom.Xform.Define(stage, "/World/Object")
    object_xform.AddTranslateOp().Set(Gf.Vec3d(1.0, 2.0, 3.0))
    object_xform.AddOrientOp().Set(
        Gf.Quatf(2**0.5 / 2, Gf.Vec3f(0.0, 0.0, 2**0.5 / 2))
    )
    object_xform.AddScaleOp().Set(Gf.Vec3f(1.0, 2.0, 0.5))
    object_prim = object_xform.GetPrim()
    rigid_body = UsdPhysics.RigidBodyAPI.Apply(object_prim)
    rigid_body.CreateRigidBodyEnabledAttr().Set(True)
    mass = UsdPhysics.MassAPI.Apply(object_prim)
    mass.CreateMassAttr().Set(2.5)
    mass.CreateDiagonalInertiaAttr().Set(Gf.Vec3f(1.0, 2.0, 3.0))
    mass.CreateCenterOfMassAttr().Set(Gf.Vec3f(0.1, 0.2, 0.3))
    cube = UsdGeom.Cube.Define(stage, "/World/Object/Visual")
    collision = UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    collision.CreateCollisionEnabledAttr().Set(True)
    return stage, object_prim, rigid_body, mass, collision


def test_follow_proxy_is_nonphysical_and_restores_source_attributes():
    stage, object_prim, rigid_body, mass, collision = _make_usd_follow_fixture()
    from pxr import Usd, UsdGeom, UsdPhysics

    transform = UsdGeom.XformCache().GetLocalToWorldTransform(object_prim)
    position = transform.ExtractTranslation()
    rotation = transform.ExtractRotationQuat()
    imaginary = rotation.GetImaginary()
    env = _FakeEnv()
    runtime = manipulation.IdealizedManipulationRuntime(env, {"robot": _Spec()})
    runtime._stage = lambda: stage
    runtime._flush_physx_transforms = lambda: None
    runtime._zero_rigid_body_velocity = lambda _path: None

    original_mass = mass.GetMassAttr().Get()
    original_inertia = mass.GetDiagonalInertiaAttr().Get()
    original_com = mass.GetCenterOfMassAttr().Get()
    isolation = runtime._isolate_object_for_follow(
        "robot",
        "/World/Object",
        tuple(position),
        (
            float(rotation.GetReal()),
            float(imaginary[0]),
            float(imaginary[1]),
            float(imaginary[2]),
        ),
    )

    assert rigid_body.GetRigidBodyEnabledAttr().Get() is True
    assert rigid_body.GetKinematicEnabledAttr().Get() is True
    assert collision.GetCollisionEnabledAttr().Get() is False
    assert UsdGeom.Imageable(object_prim).GetVisibilityAttr().Get() == "invisible"
    assert mass.GetMassAttr().Get() == original_mass
    assert mass.GetDiagonalInertiaAttr().Get() == original_inertia
    assert mass.GetCenterOfMassAttr().Get() == original_com

    proxy_visual = stage.GetPrimAtPath(f"{isolation.proxy_path}/Visual")
    assert proxy_visual and proxy_visual.IsValid()
    assert any(prim.IsA(UsdGeom.Cube) for prim in Usd.PrimRange(proxy_visual))
    for prim in Usd.PrimRange(proxy_visual):
        assert not prim.IsA(UsdPhysics.Joint)
        assert not prim.HasAPI(UsdPhysics.RigidBodyAPI)
        assert not prim.HasAPI(UsdPhysics.CollisionAPI)

    runtime._restore_object_isolation(isolation, remove_proxy=True)
    assert rigid_body.GetRigidBodyEnabledAttr().Get() is True
    assert rigid_body.GetKinematicEnabledAttr().Get() is False
    assert collision.GetCollisionEnabledAttr().Get() is True
    assert UsdGeom.Imageable(object_prim).GetVisibilityAttr().Get() == "inherited"
    assert mass.GetMassAttr().Get() == original_mass
    assert mass.GetDiagonalInertiaAttr().Get() == original_inertia
    assert mass.GetCenterOfMassAttr().Get() == original_com
    assert not stage.GetPrimAtPath(isolation.proxy_path).IsValid()
