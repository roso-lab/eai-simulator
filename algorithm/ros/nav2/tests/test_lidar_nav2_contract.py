from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
NAV2_DIR = REPO_ROOT / "algorithm" / "ros" / "nav2"
EAI_SOURCE = REPO_ROOT / "source" / "EAI"
TARGET_ROBOTS = (
    "carter",
    "go2",
    "b2",
    "m20",
    "scout",
    "mushr_v2",
    "coco",
    "lite3",
)
PROFILE_NAMES = {
    "carter": "Carter",
    "go2": "Go2",
    "b2": "B2",
    "m20": "M20",
    "scout": "Scout",
    "mushr_v2": "MuSHR Nano v2",
    "coco": "Coco AIRS",
    "lite3": "Lite3",
}
EXPECTED_LIDAR_MOUNTS = {
    "carter": {"link": "Carter/GS_Hub_chassis_link", "xyz": (0.026, 0.0, 0.444862)},
    "go2": {"link": "base", "xyz": (0.22631, -0.003, 0.136534)},
    "b2": {"link": "base_link", "xyz": (0.36723, 0.0, 0.2902)},
    "m20": {"link": "base_link", "xyz": (0.29718, 0.0, 0.121437)},
    "scout": {"link": "base_link", "xyz": (0.24749, 0.0, 0.160402)},
    "mushr_v2": {"link": "mushr_nano/base_link", "xyz": (-0.035325, 0.0, 0.18495)},
    "coco": {"link": "base_link", "xyz": (0.0, 0.0, 0.478662)},
    "lite3": {"link": "TORSO", "xyz": (0.16669, 0.0, 0.114523)},
}
EXPECTED_GSHUB_HOSTS = ("carter", "go2", "b2", "m20", "scout", "coco", "lite3")


def _load_nav2_setup():
    path = NAV2_DIR / "nav2_setup.py"
    spec = importlib.util.spec_from_file_location("eai_nav2_setup_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_simulator():
    path = REPO_ROOT / "simulator.py"
    spec = importlib.util.spec_from_file_location("eai_simulator_nav2_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_tf_bridge():
    path = NAV2_DIR / "tf_bridge.py"
    spec = importlib.util.spec_from_file_location("eai_tf_bridge_nav2_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _builder_lidar_mounts():
    path = REPO_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ROBOT_OPTIONS" for target in node.targets)
    )
    mounts = {}
    for call in assignment.value.elts:
        if not isinstance(call, ast.Call) or not call.args:
            continue
        robot = ast.literal_eval(call.args[0])
        keywords = {item.arg: item.value for item in call.keywords}
        link_node = keywords.get("lidar_mount_link")
        if link_node is None:
            continue
        mounts[robot] = {
            "link": ast.literal_eval(link_node),
            "xyz": tuple(ast.literal_eval(keywords.get("lidar_offset", ast.Tuple(elts=[])))),
        }
    return mounts


def _builder_gshub_mounts():
    path = REPO_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ROBOT_OPTIONS" for target in node.targets)
    )
    mounts = {}
    for call in assignment.value.elts:
        if not isinstance(call, ast.Call) or not call.args:
            continue
        robot = ast.literal_eval(call.args[0])
        keywords = {item.arg: item.value for item in call.keywords}
        link_node = keywords.get("gshub_mount_link")
        if link_node is None and len(call.args) > 5:
            link_node = call.args[5]
        if link_node is None:
            continue
        offset_node = keywords.get("gshub_offset")
        mounts[robot] = {
            "link": ast.literal_eval(link_node),
            "xyz": tuple(ast.literal_eval(offset_node)) if offset_node is not None else (0.026, 0.0, 0.0),
            "disable_physics": ast.literal_eval(
                keywords.get("gshub_disable_physics", ast.Constant(value=False))
            ),
        }
    return mounts


class LidarNav2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nav2_setup = _load_nav2_setup()
        cls.simulator = _load_simulator()
        cls.tf_bridge = _load_tf_bridge()
        cls.profiles = cls.nav2_setup.load_profiles()

    def test_catalog_supports_exact_navigation_robot_set(self):
        sys.path.insert(0, str(EAI_SOURCE))
        try:
            from EAI.hmrs_env.env_diy.catalog import attachment_entry

            supported = attachment_entry("lidar").supported_robots
        finally:
            sys.path.pop(0)
        self.assertEqual(supported, TARGET_ROBOTS)
        self.assertTrue({"cf2x", "pepper", "g1", "human"}.isdisjoint(supported))

    def test_catalog_supports_coco_gshub(self):
        sys.path.insert(0, str(EAI_SOURCE))
        try:
            from EAI.hmrs_env.env_diy.catalog import attachment_entry

            supported = attachment_entry("gshub").supported_robots
        finally:
            sys.path.pop(0)
        self.assertEqual(supported, EXPECTED_GSHUB_HOSTS)

    def test_env_diy_web_catalog_supports_coco_gshub(self):
        html_path = EAI_SOURCE / "EAI" / "hmrs_env" / "env_diy" / "env_diy_app.html"
        html = html_path.read_text(encoding="utf-8")
        match = re.search(r'gshub: \{[^\n]*supported: (\[[^\]]*\])', html)
        self.assertIsNotNone(match)
        self.assertEqual(tuple(json.loads(match.group(1))), EXPECTED_GSHUB_HOSTS)

    def test_builder_has_a_lidar_mount_for_every_target(self):
        mounts = _builder_lidar_mounts()
        self.assertEqual(mounts, EXPECTED_LIDAR_MOUNTS)

    def test_lidar_profile_mount_matches_builder_for_every_target(self):
        mounts = _builder_lidar_mounts()
        for robot, profile_name in PROFILE_NAMES.items():
            with self.subTest(robot=robot):
                profile = self.profiles["robot_profiles"][profile_name]
                xyz, rpy = self.nav2_setup.resolve_sensor_mount(profile, "lidar")
                self.assertEqual(tuple(xyz), mounts[robot]["xyz"])
                self.assertEqual(rpy, [0.0, 0.0, 0.0])

    def test_gshub_profile_mount_matches_builder_for_every_supported_host(self):
        mounts = _builder_gshub_mounts()
        for robot in EXPECTED_GSHUB_HOSTS:
            with self.subTest(robot=robot):
                profile = self.profiles["robot_profiles"][PROFILE_NAMES[robot]]
                xyz, rpy = self.nav2_setup.resolve_sensor_mount(profile, "gshub")
                self.assertEqual(tuple(xyz), mounts[robot]["xyz"])
                self.assertEqual(rpy, [0.0, 0.339, 0.0])

    def test_lidar_interface_topics_follow_robot_namespace(self):
        sys.path.insert(0, str(EAI_SOURCE))
        try:
            from EAI.interface_catalog.loader import load_catalog
            from EAI.interface_catalog.query import resolve_scene_interfaces

            resolved = resolve_scene_interfaces(
                load_catalog(),
                {
                    "robots": [
                        {
                            "type": "mushr_v2",
                            "instance_name": "mushr_v2_1",
                            "attachments": [{"type": "lidar"}, {"type": "ros"}],
                        }
                    ]
                },
                env_name="lidar_contract",
            )
        finally:
            sys.path.pop(0)
        endpoints = {entry.interface_id: entry.endpoint for entry in resolved}
        self.assertEqual(endpoints["ros.lidar.point_cloud"], "/mushr_v2_1/cloud")
        self.assertEqual(endpoints["ros.lidar.odometry"], "/mushr_v2_1/odometry")
        self.assertEqual(endpoints["ros.cmd_vel"], "/mushr_v2_1/cmd_vel")

    def test_auto_sensor_uses_runtime_attachment(self):
        snapshot = {
            "version": 1,
            "heartbeat_at": 100.0,
            "pid": 123,
            "scene_key": "plane",
            "robots": [
                {
                    "instance_name": "coco_1",
                    "attachments": ["lidar", "ros"],
                }
            ],
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as stream:
            import json

            json.dump(snapshot, stream)
            stream.flush()
            sensor, source = self.nav2_setup.resolve_sensor(
                "auto",
                stream.name,
                "coco_1",
                "plane",
                now=100.0,
                pid_checker=lambda _pid: True,
            )
        self.assertEqual((sensor, source), ("lidar", "runtime_snapshot"))

    def test_auto_sensor_rejects_duplicate_topic_publishers(self):
        snapshot = {
            "version": 1,
            "heartbeat_at": 100.0,
            "pid": 123,
            "scene_key": "plane",
            "robots": [
                {
                    "instance_name": "carter_1",
                    "attachments": ["gshub", "lidar", "ros"],
                }
            ],
        }
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as stream:
            import json

            json.dump(snapshot, stream)
            stream.flush()
            with self.assertRaisesRegex(RuntimeError, "both GS-Hub and LiDAR"):
                self.nav2_setup.resolve_sensor(
                    "auto",
                    stream.name,
                    "carter_1",
                    "plane",
                    now=100.0,
                    pid_checker=lambda _pid: True,
                )

    def test_profile_aliases_cover_mushr_and_coco_instance_names(self):
        for robot_name, expected in (("mushr_v2_1", "MuSHR Nano v2"), ("coco_1", "Coco AIRS")):
            with self.subTest(robot_name=robot_name):
                resolved, _profile = self.nav2_setup.resolve_profile(
                    self.profiles,
                    None,
                    robot_name,
                )
                self.assertEqual(resolved, expected)

    def test_b2_profile_does_not_request_unsupported_reverse_gait(self):
        profile = self.profiles["robot_profiles"]["B2"]
        self.assertEqual(profile["min_vel_x"], 0.0)

    def test_scout_profile_allows_time_for_in_place_heading_alignment(self):
        profile = self.profiles["robot_profiles"]["Scout"]
        self.assertEqual(profile["progress_required_movement_radius"], 0.05)
        self.assertEqual(profile["progress_movement_time_allowance"], 30.0)
        params = {
            "controller_server": {
                "ros__parameters": {"FollowPath": {"plugin": "dwb_core::DWBLocalPlanner"}}
            },
            "planner_server": {"ros__parameters": {"GridBased": {}}},
        }
        configured = self.nav2_setup.apply_navigation_plugin_profile(params, profile)
        controller = configured["controller_server"]["ros__parameters"]["FollowPath"]
        self.assertEqual(
            controller["plugin"],
            "nav2_rotation_shim_controller::RotationShimController",
        )
        self.assertEqual(controller["primary_controller"], "dwb_core::DWBLocalPlanner")
        self.assertTrue(controller["closed_loop"])

    def test_scout_cmd_vel_applies_the_skid_steer_yaw_calibration(self):
        vx, vy, wz = self.simulator._transform_cmd_vel_for_robot(
            "scout", 0.4, 0.0, 0.6
        )
        self.assertEqual((vx, vy), (0.4, 0.0))
        self.assertAlmostEqual(wz, 0.6 * 2.9)
        self.assertEqual(
            self.simulator._transform_cmd_vel_for_robot("Coco AIRS", 0.4, 0.0, 0.6),
            (0.4, 0.0, 0.6),
        )

    def test_sensor_attachments_require_omnigraph(self):
        for attachment_type in ("gshub", "lidar"):
            with self.subTest(attachment_type=attachment_type):
                selection = {
                    "robots": [
                        {"attachments": [{"type": attachment_type}]}
                    ]
                }
                self.assertTrue(
                    self.simulator._selection_requires_omnigraph(selection)
                )

    def test_diy_startup_menu_exposes_the_3d_editor(self):
        prompt = self.simulator._diy_method_prompt_text()
        self.assertIn("3. Isaac Sim 3D 编辑器", prompt)

    def test_preflight_payload_can_request_the_3d_editor(self):
        with self.assertRaises(self.simulator.Diy3dRequested):
            self.simulator._handle_preflight_payload(
                {"startup_mode": "diy-3d"}
            )

    def test_coco_uses_ackermann_compatible_nav2_plugins(self):
        profile = self.profiles["robot_profiles"]["Coco AIRS"]
        params = {
            "controller_server": {"ros__parameters": {"FollowPath": {}}},
            "planner_server": {"ros__parameters": {"GridBased": {}}},
        }
        configured = self.nav2_setup.apply_navigation_plugin_profile(params, profile)
        controller = configured["controller_server"]["ros__parameters"]["FollowPath"]
        planner = configured["planner_server"]["ros__parameters"]["GridBased"]

        self.assertLess(profile["min_vel_x"], 0.0)
        self.assertEqual(
            controller["plugin"],
            "nav2_regulated_pure_pursuit_controller::RegulatedPurePursuitController",
        )
        self.assertFalse(controller["use_rotate_to_heading"])
        self.assertTrue(controller["allow_reversing"])
        self.assertEqual(planner["plugin"], "nav2_smac_planner/SmacPlannerHybrid")
        self.assertEqual(planner["motion_model_for_search"], "REEDS_SHEPP")
        self.assertEqual(planner["minimum_turning_radius"], 1.25)

    def test_coco_nav2_geometry_is_referenced_to_rear_axle(self):
        profile = self.profiles["robot_profiles"]["Coco AIRS"]
        offset = self.nav2_setup.resolve_nav_base_offset(profile)
        self.assertEqual(offset, [-0.235, 0.0, 0.0])

        physical_xyz, _ = self.nav2_setup.resolve_sensor_mount(profile, "lidar")
        nav_xyz, _ = self.nav2_setup.resolve_navigation_sensor_mount(
            profile, "lidar", offset
        )
        self.assertEqual(physical_xyz, [0.0, 0.0, 0.478662])
        self.assertEqual(nav_xyz, [0.235, 0.0, 0.478662])

        pose = self.nav2_setup.resolve_navigation_pose(
            {"x": -3.0, "y": 0.0, "yaw": 0.0}, offset
        )
        self.assertEqual(pose, {"x": -3.235, "y": 0.0, "yaw": 0.0})

    def test_coco_gshub_mount_and_nav_tf_are_referenced_to_rear_axle(self):
        builder_mount = _builder_gshub_mounts()["coco"]
        self.assertEqual(builder_mount["link"], "base_link")
        self.assertEqual(builder_mount["xyz"], (0.0, 0.0, 0.430962))
        self.assertTrue(builder_mount["disable_physics"])

        profile = self.profiles["robot_profiles"]["Coco AIRS"]
        offset = self.nav2_setup.resolve_nav_base_offset(profile)
        physical_xyz, physical_rpy = self.nav2_setup.resolve_sensor_mount(
            profile, "gshub"
        )
        nav_xyz, nav_rpy = self.nav2_setup.resolve_navigation_sensor_mount(
            profile, "gshub", offset
        )
        self.assertEqual(tuple(physical_xyz), builder_mount["xyz"])
        self.assertEqual(physical_rpy, [0.0, 0.339, 0.0])
        self.assertEqual(nav_xyz, [0.235, 0.0, 0.430962])
        self.assertEqual(nav_rpy, physical_rpy)

    def test_coco_costmaps_use_rear_axle_polygon(self):
        profile = self.profiles["robot_profiles"]["Coco AIRS"]
        params = {
            name: {name: {"ros__parameters": {"robot_radius": 0.5}}}
            for name in ("local_costmap", "global_costmap")
        }
        configured = self.nav2_setup.apply_costmap_geometry_profile(params, profile)
        for name in ("local_costmap", "global_costmap"):
            costmap = configured[name][name]["ros__parameters"]
            self.assertNotIn("robot_radius", costmap)
            self.assertEqual(ast.literal_eval(costmap["footprint"]), profile["footprint"])

    def test_tf_bridge_rotates_base_offset_with_odometry_orientation(self):
        half_sqrt_two = 2.0 ** -0.5
        position = self.tf_bridge.offset_position_by_local_vector(
            (10.0, 20.0, 0.5),
            (0.0, 0.0, half_sqrt_two, half_sqrt_two),
            (-0.235, 0.0, 0.0),
        )
        self.assertAlmostEqual(position[0], 10.0)
        self.assertAlmostEqual(position[1], 19.765)
        self.assertAlmostEqual(position[2], 0.5)

    def test_every_target_has_a_factory_lidar_navigation_fixture(self):
        for fixture_name, robot, controller, height in (
            ("carter", "carter", "CARTER_DIFF_CFG", 0.2),
            ("go2", "go2", "GO2_VELOCITY_RSL_CFG", 0.4),
            ("b2", "b2", "B2_VELOCITY_RSL_CFG", 0.58),
            ("m20", "m20", "M20_ROUGH_RSL_CFG", 0.52),
            ("scout", "scout", "SCOUT_DIFF_CFG", 0.2),
            ("mushr", "mushr_v2", "MUSHR_ACKERMANN_CFG", 0.0),
            ("coco", "coco", "COCO_ACKERMANN_CFG", 0.3),
            ("lite3", "lite3", "LITE3_VELOCITY_RSL_CFG", 0.35),
        ):
            with self.subTest(robot=robot):
                fixture_path = (
                    REPO_ROOT
                    / "source"
                    / "EAI_hmrs"
                    / "EAI_hmrs"
                    / "envs"
                    / f"navtest_{fixture_name}_lidar.json"
                )
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                entry = fixture["robots"][0]
                self.assertEqual(fixture["scene_key"], "factory")
                self.assertEqual(entry["type"], robot)
                self.assertEqual(entry["controller"]["cfg"], controller)
                self.assertEqual(entry["spawn_pose"]["position"], [-3.0, 0.0, height])
                self.assertEqual(
                    [item["type"] for item in entry["attachments"]],
                    ["lidar", "ros"],
                )

    def test_scout_and_coco_gshub_navigation_fixtures_use_factory(self):
        for robot, controller, height in (
            ("scout", "SCOUT_DIFF_CFG", 0.2),
            ("coco", "COCO_ACKERMANN_CFG", 0.3),
        ):
            with self.subTest(robot=robot):
                fixture_path = (
                    REPO_ROOT
                    / "source"
                    / "EAI_hmrs"
                    / "EAI_hmrs"
                    / "envs"
                    / f"navtest_{robot}_gshub.json"
                )
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                entry = fixture["robots"][0]
                self.assertEqual(fixture["scene_key"], "factory")
                self.assertEqual(entry["type"], robot)
                self.assertEqual(entry["controller"]["cfg"], controller)
                self.assertEqual(entry["spawn_pose"]["position"], [-3.0, 0.0, height])
                self.assertEqual(
                    [item["type"] for item in entry["attachments"]],
                    ["gshub", "ros"],
                )

    def test_run_script_starts_nav2_in_clean_system_ros_environment(self):
        script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
        nav2_block = script.split("# 3. 启动 Nav2", 1)[1]
        self.assertIn("env -i", nav2_block)
        self.assertIn("PATH=/usr/bin:/bin:/opt/ros/humble/bin", nav2_block)
        self.assertIn("RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", nav2_block)
        self.assertIn("source /opt/ros/humble/setup.bash", nav2_block)
        self.assertNotIn("conda activate", nav2_block)

    def test_run_script_waits_for_current_simulator_readiness_log(self):
        script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
        self.assertIn(
            "[EAI Simulator] cmd_vel enabled: /carter_1/cmd_vel",
            script,
        )
        self.assertNotIn("Nav2 控制已启用", script)

    def test_run_script_only_cleans_up_its_own_processes(self):
        script = (NAV2_DIR / "run_nav2.sh").read_text(encoding="utf-8")
        cleanup_block = script.split("cleanup() {", 1)[1].split("}\n", 1)[0]
        self.assertNotIn("pkill", cleanup_block)
        self.assertIn('kill -TERM -- "-$pid"', cleanup_block)
        self.assertEqual(script.count("setsid"), 2)

    def test_launch_preserves_negative_explicit_pose_as_one_argument(self):
        launch_source = (NAV2_DIR / "nav2.launch.py").read_text(encoding="utf-8")
        self.assertIn('cmd += [f"--pose={pose_arg}"]', launch_source)


if __name__ == "__main__":
    unittest.main()
