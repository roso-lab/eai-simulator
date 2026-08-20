from __future__ import annotations

import ast
import importlib.util
import math
from queue import Queue
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace

import torch

import simulator

try:
    import pxr  # noqa: F401
except ModuleNotFoundError:
    pxr_stub = ModuleType("pxr")
    pxr_stub.Gf = SimpleNamespace()
    sys.modules["pxr"] = pxr_stub

from demo.fire_rescue.runtime.ur5 import POSE_CARRY, UR5Manager, resolve_ur5_articulation
from demo.fire_rescue.runtime.navigation import (
    InterRobotCollisionRisk,
    RobotNavController,
    RobotTask,
    TaskQueue,
    USE_CONFLICT_AWARE_PLANNING,
    collision_guard_stop_agents,
    detect_inter_robot_collision_risks,
)
from demo.fire_rescue.runtime.settings import (
    FIRE_EXTINGUISHER_EGRESS_TARGET,
    FIRE_EXTINGUISHER_NAV_TARGET,
    FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT,
    INTER_ROBOT_RADII,
    INTER_ROBOT_RELEASE_HYSTERESIS,
    INTER_ROBOT_SAFETY_MARGIN,
)
from demo.fire_rescue.algorithm_paths import default_factory_map_yaml
from algorithm.global_planner.core import FactoryMapPlanner

_MANAGER_PATH = (
    Path(__file__).resolve().parents[3]
    / "source/EAI/EAI/hmrs_ros/manipulator_omnigraph.py"
)
_MANAGER_SPEC = importlib.util.spec_from_file_location(
    "_fire_rescue_manipulator_omnigraph",
    _MANAGER_PATH,
)
assert _MANAGER_SPEC is not None and _MANAGER_SPEC.loader is not None
_MANAGER_MODULE = importlib.util.module_from_spec(_MANAGER_SPEC)
sys.modules[_MANAGER_SPEC.name] = _MANAGER_MODULE
_MANAGER_SPEC.loader.exec_module(_MANAGER_MODULE)
ManipulatorModelSpec = _MANAGER_MODULE.ManipulatorModelSpec
ManipulatorOmniGraphManager = _MANAGER_MODULE.ManipulatorOmniGraphManager


class _FakeArm:
    def __init__(self) -> None:
        self.device = "cpu"
        self.target = None
        self.joint_ids = None
        self.write_count = 0

    def find_joints(self, names, preserve_order=True):
        return list(range(len(names))), list(names)

    def set_joint_position_target(self, target, *, joint_ids):
        self.target = target
        self.joint_ids = joint_ids

    def write_data_to_sim(self):
        self.write_count += 1


class _FailingRuntime:
    def __init__(self) -> None:
        self.create_count = 0
        self.closed = False

    def create_or_reuse_graph(self, key, spec):
        self.create_count += 1
        raise RuntimeError("ROS2 node type unavailable")

    def close(self):
        self.closed = True


class Ur5ArticulationChecks(unittest.TestCase):
    def test_separate_arm_takes_precedence_over_host(self):
        host = object()
        arm = object()
        articulations = {"m20_2": host, "m20_2_arm": arm}

        self.assertIs(resolve_ur5_articulation(articulations, "m20_2"), arm)

    def test_legacy_merged_articulation_remains_supported(self):
        host = object()

        self.assertIs(resolve_ur5_articulation({"m20_2": host}, "m20_2"), host)

    def test_joint_pose_is_written_to_separate_arm(self):
        host = object()
        arm = _FakeArm()
        env = SimpleNamespace(
            scene=SimpleNamespace(articulations={"m20_2": host, "m20_2_arm": arm})
        )
        manager = UR5Manager(env)

        self.assertTrue(manager.apply_joint_pose("m20_2", [0.0] * 6))
        self.assertEqual(arm.joint_ids, list(range(6)))
        self.assertEqual(arm.write_count, 1)
        self.assertTrue(torch.equal(arm.target, torch.zeros((1, 6))))


class ExtinguisherPickupChecks(unittest.TestCase):
    def _manager(self):
        arm = _FakeArm()
        env = SimpleNamespace(
            scene=SimpleNamespace(articulations={"m20_2_arm": arm})
        )
        manager = UR5Manager(env, enable_extinguisher_stage_attach=False)
        manager._ext_active = True
        manager._ext_robot_name = "m20_2"
        manager._ext_target_prim = "/World/Extinguisher"
        manager._ext_target_pos = (0.0, 0.0, 0.0)
        manager._get_ee_pos_cached = lambda *_args: (0.0, 0.0, 0.0)
        manager._get_prim_pos = lambda *_args: (0.0, 0.0, 0.0)
        return manager, arm

    def test_extinguisher_cannot_attach_before_grasp_phase(self):
        manager, arm = self._manager()
        for phase in (0, 1):
            manager._ext_grabbed = False
            manager._ext_pick = SimpleNamespace(active=True, phase=phase, grabbed=False)

            manager._check_extinguisher_contact(arm, "m20_2", stage=None)

            self.assertFalse(manager.is_extinguisher_grabbed)

    def test_extinguisher_can_attach_during_grasp_phase(self):
        manager, arm = self._manager()
        manager._ext_pick = SimpleNamespace(active=True, phase=2, grabbed=False)

        manager._check_extinguisher_contact(arm, "m20_2", stage=None)

        self.assertTrue(manager.is_extinguisher_grabbed)
        self.assertTrue(manager._ext_pick.grabbed)
        self.assertFalse(manager.is_extinguisher_pick_complete)

    def test_grabbed_extinguisher_still_completes_lift(self):
        manager, arm = self._manager()

        class LiftPick:
            active = True
            phase = 3
            step_count = 0

            def step(self, _env, _stage):
                self.step_count += 1
                self.active = False
                self.phase = 4
                return torch.ones((1, 6)), False

        manager._ext_pick = LiftPick()
        manager._ext_grabbed = True

        manager.update_extinguisher_grab(stage=None)

        self.assertEqual(manager._ext_pick.step_count, 1)
        self.assertEqual(arm.write_count, 1)
        self.assertTrue(manager.is_extinguisher_pick_complete)

    def test_completed_pick_holds_high_carry_pose(self):
        manager, arm = self._manager()
        carry_pose = [-2.5] + list(POSE_CARRY[1:])
        manager._ext_pick = SimpleNamespace(
            active=False,
            phase=4,
            carry_pose=carry_pose,
        )
        manager._ext_grabbed = True

        manager.update_extinguisher_grab(stage=None)

        self.assertEqual(arm.write_count, 1)
        self.assertEqual(arm.joint_ids, list(range(6)))
        self.assertTrue(
            torch.equal(arm.target, torch.tensor([carry_pose], dtype=torch.float32))
        )
        self.assertTrue(manager._ext_carry_hold_logged)

    def test_carry_pose_raises_and_folds_the_arm(self):
        self.assertEqual(POSE_CARRY[1:4], [-1.57, 0.0, -1.57])
        self.assertEqual(POSE_CARRY[4:], [-1.57, 0.0])

    def test_experiment_waits_for_lift_before_navigation(self):
        source_path = Path(__file__).resolve().parents[1] / "runtime/experiment_loop.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("ur5.is_extinguisher_pick_complete"), 3)
        self.assertNotIn("ur5.update_extinguisher_grab(stage)\n            except", source)
        self.assertIn("ur5.update_extinguisher_visual_follow(stage)", source)


class ManipulatorGraphChecks(unittest.TestCase):
    def test_disabled_ros_bridge_removes_only_manipulator_auxiliary(self):
        primary = object()
        retained_auxiliary = lambda env: None

        class ManipulatorAuxiliary:
            model_spec = SimpleNamespace(
                model="ur5",
                joint_names=("joint",),
                ee_body_names=("tool",),
            )

            def __call__(self, env):
                return None

        config = SimpleNamespace(
            controllers={
                "m20_2": (primary, ManipulatorAuxiliary(), retained_auxiliary),
                "scout_1": primary,
            }
        )

        removed = simulator._disable_manipulator_ros2_auxiliaries(config)

        self.assertEqual(removed, 1)
        self.assertEqual(config.controllers["m20_2"], (primary, retained_auxiliary))
        self.assertIs(config.controllers["scout_1"], primary)

    def test_failed_graph_setup_is_not_retried(self):
        runtime = _FailingRuntime()
        manager = ManipulatorOmniGraphManager(runtime=runtime)
        model = ManipulatorModelSpec(
            model="ur5",
            joint_names=("joint",),
            ee_body_names=("tool",),
        )

        self.assertFalse(manager.setup_robot("scout_1", model))
        self.assertFalse(manager.setup_robot("scout_1", model))
        self.assertEqual(runtime.create_count, 1)


class ObstacleRescueChecks(unittest.TestCase):
    def test_obstacle_rescue_uses_package_navigation_module(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "runtime/obstacle_rescue.py"
        )
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn("from robot_nav import", source)
        self.assertEqual(source.count("from .navigation import RobotTask"), 3)


class AlgorithmAdapterChecks(unittest.TestCase):
    def test_adapter_uses_standalone_global_navigation_session(self):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "runtime/algorithm_adapter.py"
        )
        tree = ast.parse(source_path.read_text(encoding="utf-8"))

        imports_global_session = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "algorithm.global_planner.session"
            and any(alias.name == "GlobalNavSession" for alias in node.names)
            for node in tree.body
        )
        bridge = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "EmosFactoryNavBridge"
        )
        constructs_global_session = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "GlobalNavSession"
            for node in ast.walk(bridge)
        )

        self.assertTrue(imports_global_session)
        self.assertEqual(bridge.bases, [])
        self.assertTrue(constructs_global_session)


class PhaseTransitionChecks(unittest.TestCase):
    def test_alarm_retreat_is_not_misclassified_as_fire_navigation(self):
        retreat = RobotTask(
            "emos_green_retreat_scout_1",
            "emos",
            "按钮后退→转向",
            "grey",
            (8.0, 1.0),
        )
        fire_delivery = RobotTask(
            "emos_extinguisher_final_fire_zone",
            "emos",
            "最终任务：前往火源区",
            "grey",
            (-7.15, -3.5),
        )
        post_button_rally = RobotTask(
            "emos_green_done_rally_scout_1",
            "emos",
            "按钮完成→火源集结",
            "grey",
            (-4.0, -5.0),
        )

        self.assertFalse(RobotNavController._is_fire_proximity_task(retreat))
        self.assertTrue(RobotNavController._is_fire_proximity_task(fire_delivery))
        self.assertTrue(RobotNavController._is_fire_proximity_task(post_button_rally))

    def test_button_completion_dispatches_one_continuous_navigation_task(self):
        source_path = Path(__file__).resolve().parents[1] / "runtime/experiment_loop.py"
        source = source_path.read_text(encoding="utf-8")

        self.assertNotIn("emos_green_retreat_", source)
        self.assertNotIn("tq_rc.push_back(_rc_rally_task)", source)
        self.assertIn("按钮完成→火源集结", source)

    def test_extinguisher_egress_segment_is_free_on_factory_map(self):
        planner = FactoryMapPlanner(
            str(default_factory_map_yaml()),
            prefer_astar=True,
            inflation_radius_cells=14,
        )
        start_xy = tuple(float(v) for v in FIRE_EXTINGUISHER_NAV_TARGET[:2])
        egress_xy = tuple(float(v) for v in FIRE_EXTINGUISHER_EGRESS_TARGET[:2])

        self.assertTrue(
            planner._collision_free_segment(start_xy, egress_xy, clearance_cells=0)
        )
        waypoints = planner.plan(start_xy, egress_xy, waypoint_step=1.0)
        self.assertGreaterEqual(len(waypoints), 2)
        self.assertLess(math.dist(waypoints[-1][:2], egress_xy), 0.08)

    def test_visual_proxy_strips_descendant_physics(self):
        source_path = Path(__file__).resolve().parents[1] / "runtime/ur5.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        manager = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "UR5Manager"
        )
        create_proxy = next(
            node
            for node in manager.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_create_visual_proxy"
        )
        calls_recursive_strip = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_strip_physics_recursive"
            for node in ast.walk(create_proxy)
        )

        self.assertTrue(calls_recursive_strip)


class CollisionAvoidanceChecks(unittest.TestCase):
    def test_crossing_paths_are_predicted_before_contact(self):
        risks = detect_inter_robot_collision_risks(
            positions={"m20_2": (-2.0, 0.0), "scout_1": (0.0, -2.0)},
            velocities={"m20_2": (1.0, 0.0), "scout_1": (0.0, 1.0)},
            active_agents={"m20_2", "scout_1"},
        )

        self.assertEqual(len(risks), 1)
        self.assertLess(risks[0].predicted_distance, 0.01)
        self.assertFalse(risks[0].hard_stop)

    def test_diverging_paths_do_not_trigger_guard(self):
        risks = detect_inter_robot_collision_risks(
            positions={"m20_2": (-1.0, 0.0), "scout_1": (1.0, 0.0)},
            velocities={"m20_2": (-1.0, 0.0), "scout_1": (1.0, 0.0)},
            active_agents={"m20_2", "scout_1"},
        )

        self.assertEqual(risks, [])

    def test_hard_stop_holds_both_robots(self):
        risk = InterRobotCollisionRisk(
            "m20_2", "scout_1", 1.0, 1.0, 0.0, True
        )

        self.assertEqual(
            collision_guard_stop_agents([risk], {"m20_2": 1, "scout_1": 0}),
            {"m20_2", "scout_1"},
        )

    def test_extinguisher_priority_makes_scout_yield(self):
        risk = InterRobotCollisionRisk(
            "m20_2", "scout_1", 2.0, 0.5, 1.0, False
        )

        self.assertEqual(
            collision_guard_stop_agents([risk], {"m20_2": 1, "scout_1": 0}),
            {"scout_1"},
        )
        self.assertTrue(USE_CONFLICT_AWARE_PLANNING)

    def test_hazard_one_fire_anchors_have_robot_clearance(self):
        anchors = FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT[1]
        names = sorted(anchors)
        for index, first in enumerate(names):
            for second in names[index + 1:]:
                first_radius = next(
                    radius
                    for prefix, radius in INTER_ROBOT_RADII.items()
                    if first.startswith(prefix)
                )
                second_radius = next(
                    radius
                    for prefix, radius in INTER_ROBOT_RADII.items()
                    if second.startswith(prefix)
                )
                required = first_radius + second_radius + INTER_ROBOT_SAFETY_MARGIN
                actual = math.dist(anchors[first], anchors[second])
                self.assertGreaterEqual(actual, required, (first, second))

    def test_runtime_guard_zeroes_the_yielding_robot_command(self):
        class FakeRobot:
            def __init__(self, xy):
                self.data = SimpleNamespace(
                    root_pos_w=torch.tensor([[xy[0], xy[1], 0.5]]),
                    root_quat_w=torch.tensor([[1.0, 0.0, 0.0, 0.0]]),
                )

            def find_bodies(self, *_args, **_kwargs):
                return [], []

        controller = RobotNavController.__new__(RobotNavController)
        controller.possible_agents = ["m20_2", "scout_1"]
        controller.base_env = SimpleNamespace(
            scene=SimpleNamespace(
                articulations={
                    "m20_2": FakeRobot((-1.0, 0.0)),
                    "scout_1": FakeRobot((1.0, 0.0)),
                }
            )
        )
        controller.navigator = SimpleNamespace(is_active=lambda _agent: True)
        controller.is_patrolling = {"m20_2": False, "scout_1": False}
        controller.task_queues = {
            name: TaskQueue() for name in controller.possible_agents
        }
        for name in controller.possible_agents:
            controller.task_queues[name].push_back(
                RobotTask("task", "emos", "move", "grey", (0.0, 0.0))
            )
        controller.last_cmd_vel = {
            "m20_2": torch.tensor([1.0, 0.0, 0.0]),
            "scout_1": torch.tensor([-1.0, 0.0, 0.0]),
        }
        controller.robot_commands = {
            "m20_2": torch.tensor([[1.0, 0.0, 0.0]]),
            "scout_1": torch.tensor([[-1.0, 0.0, 0.0]]),
        }
        controller.goal_controlled_robots = set()
        controller.robot_goal_positions = {}
        controller._collision_guard_agents = set()
        controller._collision_guard_pairs = set()
        controller._collision_guard_replan_ts = {}
        controller._collision_guard_probe_velocities = {}
        controller._queue_collision_replan = lambda _risk, _now: None

        actions = controller.apply_inter_robot_collision_guard(
            {name: command.clone() for name, command in controller.robot_commands.items()}
        )

        self.assertGreater(torch.count_nonzero(actions["m20_2"]).item(), 0)
        self.assertEqual(torch.count_nonzero(actions["scout_1"]).item(), 0)
        self.assertEqual(controller._collision_guard_agents, {"scout_1"})

        # The first stop zeroes command history.  A low-speed command in the
        # same direction must not clear the pair for one frame and oscillate.
        actions = controller.apply_inter_robot_collision_guard(
            {
                "m20_2": torch.tensor([[0.05, 0.0, 0.0]]),
                "scout_1": torch.tensor([[-0.05, 0.0, 0.0]]),
            }
        )
        self.assertEqual(torch.count_nonzero(actions["scout_1"]).item(), 0)
        self.assertEqual(controller._collision_guard_pairs, {("m20_2", "scout_1")})
        self.assertAlmostEqual(
            math.hypot(*controller._collision_guard_probe_velocities["scout_1"]),
            1.0,
        )

    def test_hazard_one_scout_anchor_clears_m20_arrival_envelope(self):
        anchors = FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT[1]
        required = (
            INTER_ROBOT_RADII["m20"]
            + INTER_ROBOT_RADII["scout"]
            + INTER_ROBOT_SAFETY_MARGIN
            + 0.30
        )
        self.assertGreaterEqual(
            math.dist(anchors["m20_1"], anchors["scout_1"]),
            required,
        )

    def test_scout_anchor_does_not_block_extinguisher_delivery_route(self):
        planner = FactoryMapPlanner(
            str(default_factory_map_yaml()),
            prefer_astar=True,
            inflation_radius_cells=10,
        )
        anchors = FIRE_FIXED_PROXIMITY_TARGETS_BY_ROBOT[1]
        route = planner.plan(
            tuple(float(v) for v in FIRE_EXTINGUISHER_EGRESS_TARGET[:2]),
            anchors["m20_2"],
            waypoint_step=1.0,
        )

        def point_segment_distance(point, start, end):
            px, py = point
            ax, ay = start
            bx, by = end
            dx, dy = bx - ax, by - ay
            length_sq = dx * dx + dy * dy
            if length_sq <= 1e-12:
                return math.dist(point, start)
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
            return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

        min_clearance = min(
            point_segment_distance(anchors["scout_1"], first[:2], second[:2])
            for first, second in zip(route, route[1:])
        )
        required = (
            INTER_ROBOT_RADII["m20"]
            + INTER_ROBOT_RADII["scout"]
            + INTER_ROBOT_SAFETY_MARGIN
            + INTER_ROBOT_RELEASE_HYSTERESIS
        )
        self.assertGreaterEqual(min_clearance, required)

    def test_hold_brake_clears_residual_root_velocity(self):
        class FakeRobot:
            def __init__(self):
                self.data = SimpleNamespace(
                    root_vel_w=torch.tensor([[1.0, -2.0, 0.5, 0.2, 0.3, -0.4]])
                )
                self.written_velocity = None

            def write_root_velocity_to_sim(self, velocity):
                self.written_velocity = velocity.clone()

        robot = FakeRobot()
        controller = RobotNavController.__new__(RobotNavController)
        controller.base_env = SimpleNamespace(
            scene=SimpleNamespace(articulations={"m20_1": robot})
        )
        controller.hold_position = set()

        controller._set_hold_position("m20_1", "test")

        self.assertEqual(controller.hold_position, {"m20_1"})
        self.assertEqual(torch.count_nonzero(robot.written_velocity).item(), 0)

    def test_plan_worker_uses_conflict_aware_batch_session(self):
        calls = []

        class FakeSession:
            def plan_batch(self, requests, *, priorities):
                calls.append((requests, priorities))
                return {"m20_2": [(0.0, 0.0, 0.0)]}

        controller = RobotNavController.__new__(RobotNavController)
        controller._nav_session = FakeSession()
        controller._plan_result_queue = Queue()
        requests = [("m20_2", (0.0, 0.0), (1.0, 1.0))]

        controller._plan_worker(requests, {"m20_2": -1}, 17)

        self.assertEqual(calls, [(requests, {"m20_2": -1})])
        self.assertEqual(
            controller._plan_result_queue.get_nowait(),
            (17, "ok", {"m20_2": [(0.0, 0.0, 0.0)]}),
        )


if __name__ == "__main__":
    unittest.main()
