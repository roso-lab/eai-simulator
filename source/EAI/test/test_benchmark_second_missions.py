"""Authoring checks for the second mission beside each frozen GOAL-01 task."""

import json
import math

from demo.benchmark.city_exit_task import EXIT_TASK_ID, build_city_exit_package, dismissal_roster
from demo.benchmark.factory_fire_task import FIRE_TASK_ID, build_factory_fire_package
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_fleet import RESTAURANT_PUBLIC_TASK_SHA256
from demo.benchmark.goal_scene_registry import registered_goal_scenes
from demo.benchmark.house_delivery_task import DELIVERY_TASK_ID, build_house_delivery_package
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.restaurant_service_task import SERVICE_TASK_ID, build_restaurant_service_package
from demo.benchmark.warehouse_inbound_task import INBOUND_TASK_ID, build_warehouse_inbound_package


_FROZEN_INDOOR_SHA256 = {
    ("factory", "public_task"):
        "49c97523de62278b99e2e544e4ca3b911a96a8f4572c6892dcf6bebf81c30350",
    ("factory", "hidden_evaluator"):
        "dc232ef56604a5c0ed7c132e9b2c5325812351d69ace3c2a4a570c115fca33c9",
    ("warehouse_nvd", "public_task"):
        "25052247bf3176f4903b2fb4838226b9d67f5d069ad6ff93e29c97864ecdabf5",
    ("warehouse_nvd", "hidden_evaluator"):
        "0817399a230f8f4995424be24b61ab68e3e3c7be73038b2c10d1e6f5d1b7b927",
    ("modern_house_nvd", "public_task"):
        "2c808a29dee58f61eba575987b4eefda4f839baec5e47585e004d3c971f28857",
    ("modern_house_nvd", "hidden_evaluator"):
        "3d9c3c570cb727aa69fe7040e7c14ff6a00a7c8cda475340d9303fe78b8e96aa",
}
_GOAL_OUTCOMES = {
    "factory": {"anomaly_confirmed", "response_area_served", "maintenance_handoff_recorded"},
    "warehouse_nvd": {"order_inventory_reconciled", "order_consolidated",
                      "dock_handoff_confirmed", "warehouse_closeout"},
    "modern_house_nvd": {"furniture_repositioned", "belonging_staged",
                         "resident_path_restored", "team_withdrawn"},
    "city": {"incident_assessed", "exposed_people_accounted_for",
             "secondary_traffic_protected", "response_access_preserved"},
}
_NEW_TASK_IDS = {FIRE_TASK_ID, INBOUND_TASK_ID, DELIVERY_TASK_ID, EXIT_TASK_ID, SERVICE_TASK_ID}


def _outcomes(public: dict) -> set[str]:
    return {row["id"] for row in public["required_outcomes"]}


def test_frozen_goal_packages_keep_ids_and_hashes() -> None:
    restaurant = build_goal_task_package("restaurant")
    assert restaurant["public_task"]["task_id"] == "RESTAURANT-GOAL-01"
    assert canonical_sha256(restaurant["public_task"]) == RESTAURANT_PUBLIC_TASK_SHA256
    for scene, outcomes in _GOAL_OUTCOMES.items():
        bundle = build_goal_task_package(scene)
        public = bundle["public_task"]
        assert public["task_id"].endswith("-GOAL-01")
        assert _outcomes(public) == outcomes
        if scene in {"factory", "warehouse_nvd", "modern_house_nvd"}:
            assert canonical_sha256(public) == _FROZEN_INDOOR_SHA256[(scene, "public_task")]
            assert (canonical_sha256(bundle["hidden_evaluator"])
                    == _FROZEN_INDOOR_SHA256[(scene, "hidden_evaluator")])
    warehouse = build_goal_task_package("warehouse_nvd")["public_task"]
    initial = {row["id"]: row["physical_properties"]["initial_region_id"]
               for row in warehouse["public_world"]["objects"]}
    assert initial["order_crate_01"] == "rack_aisle_a"
    assert initial["order_carton_01"] == "rack_aisle_b"


def test_second_missions_are_outside_the_default_suite() -> None:
    admitted = [row["scene_key"] for row in registered_goal_scenes()
                if row["package_kind"] == "goal_bundle"]
    inventory = {build_goal_task_package(scene)["public_task"]["task_id"] for scene in admitted}
    assert inventory == {
        "FACTORY-GOAL-01", "WAREHOUSE-GOAL-01", "MODERN_HOUSE-GOAL-01",
        "CITY-GOAL-01", "RESTAURANT-GOAL-01",
    }
    assert not inventory & _NEW_TASK_IDS


def _gate(bundle: dict) -> dict:
    gate = bundle["hidden_evaluator"]["release_gate"]
    assert gate["benchmark_ready"] is False
    assert gate["scoreable"] is False
    assert gate["execution_status"] == "external_pending"
    assert gate["blockers"]
    assert bundle["hidden_evaluator"]["release_gate"]["benchmark_ready"] is False
    return gate


def test_factory_fire_names_the_rescue_mission() -> None:
    bundle = build_factory_fire_package()
    public = bundle["public_task"]
    assert public["task_id"] == FIRE_TASK_ID
    assert _outcomes(public) == {
        "rescue_channel_opened", "extinguisher_at_fire", "fire_scene_sensed",
        "scout_at_fire_site", "carter_at_fire_site", "extinguisher_carrier_at_fire_site",
    }
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    assert robots["factory_iris_1"]["mobility"] == "aerial"
    assert {"factory_m20_1", "factory_carter_1", "factory_scout_1"} <= set(robots)
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) == 2
    assert all("factory_iris_1" not in witness["plan"]["assigned_team"] for witness in witnesses)
    assert all("factory_scout_1" in witness["plan"]["assigned_team"] for witness in witnesses)
    extinguisher = next(row for row in public["public_world"]["objects"]
                        if row["id"] == "factory_extinguisher_01")
    point = extinguisher["physical_properties"]["grasp_points"][0]
    assert point["interface"] == "support_socket_v1"
    assert "no_finger_grasp" in extinguisher["handling_constraints"]
    prose = json.dumps(public, ensure_ascii=False)
    assert "救援" not in prose and "救出" not in prose
    assert public["mission"]["goal"].startswith("打开通向火场的通道")
    assert any("episode" in item for item in _gate(bundle)["blockers"])


def test_warehouse_inbound_reuses_declared_goods_from_the_buffer() -> None:
    bundle = build_warehouse_inbound_package()
    public = bundle["public_task"]
    assert public["task_id"] == INBOUND_TASK_ID
    assert _outcomes(public) == {
        "crate_arrival_identified", "carton_arrival_identified",
        "crate_shelved_on_aisle_a", "carton_shelved_on_aisle_b",
        "receiving_buffer_closed", "inbound_team_withdrawn",
    }
    team = next(row for row in public["required_outcomes"] if row["id"] == "inbound_team_withdrawn")
    assert team["evidence_expectations"] == [
        "picker_final_position", "partner_final_position", "observer_final_position",
    ]
    assert "weight" not in team
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        assert len(witness["outcome_support"]["inbound_team_withdrawn"]) == 3
    objects = {row["id"]: row for row in public["public_world"]["objects"]}
    assert objects["order_crate_01"]["physical_properties"]["initial_region_id"] == "receiving_buffer"
    assert objects["order_carton_01"]["physical_properties"]["initial_region_id"] == "receiving_buffer"
    crate_point = objects["order_crate_01"]["physical_properties"]["grasp_points"][1]
    assert crate_point["interface"] == "support_socket_v1"
    carton_point = objects["order_carton_01"]["physical_properties"]["grasp_points"][0]
    assert carton_point["id"] == "center_grip"
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    assert robots["warehouse_iris_1"]["mobility"] == "aerial"
    assert "pick" not in robots["warehouse_carter_1"]["public_capabilities"]
    from demo.benchmark.goal_indoor_deployment import build_indoor_deployment_overlay
    from demo.benchmark.second_mission_deployment import compile_second_mission_formal_deployment
    formal = compile_second_mission_formal_deployment(public)
    assert formal["public_task_sha256"] == canonical_sha256(public)
    placed = {row["public_id"]: row["scene_binding"]["initial_region_check"]["admitted"]
              for row in formal["object_instances"]}
    assert placed == {"order_crate_01": True, "order_carton_01": True}
    goal_overlay_positions = {
        row["id"]: row["deployment_directive"]["position_m"][:2]
        for row in build_indoor_deployment_overlay(
            build_goal_task_package("warehouse_nvd")["public_task"], verify_sources=False
        )["object_bindings"]
    }
    assert goal_overlay_positions["order_crate_01"] == [-10.6, -11.75]
    assert goal_overlay_positions["order_carton_01"] == [-7.0, -10.05]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert all("warehouse_iris_1" not in witness["plan"]["assigned_team"] for witness in witnesses)
    assert _gate(bundle)["scoreable"] is False


def test_house_delivery_leaves_furniture_and_uses_a_static_signoff() -> None:
    bundle = build_house_delivery_package()
    public = bundle["public_task"]
    assert public["task_id"] == DELIVERY_TASK_ID
    assert _outcomes(public) == {
        "parcel_released_indoors", "delivery_signoff_recorded",
        "carrier_left_delivery_path", "pepper_left_signoff_path",
    }
    objects = {row["id"]: row for row in public["public_world"]["objects"]}
    assert objects["living_armchair_01"]["physical_properties"]["handling_modes"] == []
    assert objects["resident_bookstack_01"]["physical_properties"]["handling_modes"] == []
    assert objects["living_armchair_01"]["physical_properties"]["initial_region_id"] == "furniture_side"
    assert objects["doorstep_parcel_01"]["physical_properties"]["grasp_points"][0]["interface"] == "support_socket_v1"
    assert objects["delivery_signoff_marker"]["handling_constraints"]
    assert "not_a_human" in objects["delivery_signoff_marker"]["handling_constraints"]
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    assert robots["house_pepper_1"]["type"] == "pepper"
    assert "pick" not in robots["house_pepper_1"]["public_capabilities"]
    assert all("house_iris_1" not in witness["plan"]["assigned_team"]
               for witness in bundle["hidden_evaluator"]["witness_plans"])
    assert _gate(bundle)["benchmark_ready"] is False


def test_city_exit_guides_the_known_roster_without_a_manipulator() -> None:
    bundle = build_city_exit_package()
    public = bundle["public_task"]
    assert public["task_id"] == EXIT_TASK_ID
    assert _outcomes(public) == {
        "dismissal_roster_observed", "dismissal_headcount_reconciled",
        "station_corridor_clear",
        *(f"{person_id}_at_dismissal_assembly" for person_id in dismissal_roster()),
    }
    headcount = next(row for row in public["required_outcomes"]
                     if row["id"] == "dismissal_headcount_reconciled")
    assert headcount["evidence_expectations"] == ["headcount_record"]
    assert "weight" not in headcount
    for witness in bundle["hidden_evaluator"]["witness_plans"]:
        support = witness["outcome_support"]
        assert support["dismissal_headcount_reconciled"] != support["affected_person_00_at_dismissal_assembly"]
    assert set(dismissal_roster()) == {
        "affected_person_00", "affected_person_01", "affected_person_02", "affected_person_03",
    }
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    assert "manipulator" not in robots["go2_1"]["interfaces"]
    assert "manipulator" not in robots["carter_1"]["interfaces"]
    assert "guide_people" in robots["go2_1"]["public_capabilities"]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert all("iris_1" not in witness["plan"]["assigned_team"] for witness in witnesses)
    assert all(any(task["skill"] == "guide_people" for task in witness["plan"]["tasks"])
               for witness in witnesses)
    assert _gate(bundle)["scoreable"] is False


def test_city_exit_go2_stands_on_the_deck_and_uses_body_velocity() -> None:
    """A z=0.4 root leaves the feet in the 0.30 m sidewalk. That gait does not track vx."""
    from demo.benchmark.second_mission_deployment import (
        build_second_mission_deployment, _city_ground_z,
    )

    bundle = build_city_exit_package()
    plan = bundle["hidden_evaluator"]["witness_plans"][0]["plan"]
    deployment = build_second_mission_deployment(bundle["public_task"], plan)
    go2 = next(row for row in deployment["robot_instances"] if row["runtime_id"] == "go2_1")
    position = go2["spawn_binding"]["deployment_directive"]["position_m"]
    # Sidewalk deck is 0.30 m and the Go2 standing root is 0.40 m above its feet.
    assert go2["command_interface"] == "base_velocity"
    assert go2["type"] == "go2"
    assert abs(position[2] - 0.70) < 1e-6
    assert abs(_city_ground_z("go2", (position[0], position[1])) - 0.70) < 1e-6
    assert position[2] > 0.40
    assert deployment["scoreable"] is False


def test_gpu_readback_fails_closed_unless_the_current_step_is_synchronized() -> None:
    from demo.benchmark.gpu_evidence_contract import admit_synchronized_gpu_readback

    admitted, context = admit_synchronized_gpu_readback(
        device="cuda:0", scene_queries_enabled=True, fabric_enabled=True,
        physics_step=6, bound_physics_step=6, readback_suppressed=False)
    assert admitted is True
    assert context["formal_evidence"] is True
    assert context["clearance_status"] == "unknown"
    for kwargs, reason in (
            ({"device": "cpu"}, "cpu_readback_is_not_formal_evidence"),
            ({"scene_queries_enabled": False}, "scene_query_support_disabled"),
            ({"fabric_enabled": False}, "fabric_disabled"),
            ({"physics_step": 0}, "physics_step_not_advanced"),
            ({"bound_physics_step": 5}, "readback_not_bound_to_current_step"),
            ({"readback_suppressed": True}, "readback_suppressed"),
            ({"readback_suppressed": None}, "readback_suppression_unknown"),
    ):
        base = dict(device="cuda:0", scene_queries_enabled=True, fabric_enabled=True,
                    physics_step=6, bound_physics_step=6, readback_suppressed=False)
        base.update(kwargs)
        failed, failed_context = admit_synchronized_gpu_readback(**base)
        assert failed is False
        assert failed_context["reason"] == reason
        assert failed_context["formal_evidence"] is False
        assert failed_context["clearance_status"] == "unknown"


def test_a_live_session_binds_the_first_skill_to_the_house_adapter(tmp_path) -> None:
    """The real ground follower runs. A pose outside the map fails closed there."""
    from demo.benchmark.second_mission_episode import run_second_mission_episode, second_mission_builders

    class Pose:
        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return [1.0e6, 1.0e6, 0.0, 1.0, 0.0, 0.0, 0.0]

    class Articulations(dict):
        def __missing__(self, key):
            row = type("Articulation", (), {"data": type("Data", (), {"root_state_w": [Pose()]})()})()
            self[key] = row
            return row

    class Env:
        def step(self, actions):
            del actions

    class Base:
        step_dt = 0.05
        scene = type("Scene", (), {"articulations": Articulations()})()

        def set_command(self, *_args, **_kwargs):
            raise AssertionError("a blocked path must fail before a velocity command")

    class Session:
        device = "cpu"
        possible_agents = []
        env = Env()
        base_env = Base()

    for task_id in second_mission_builders():
        summary = run_second_mission_episode(task_id, tmp_path / task_id, session=Session())
        assert summary["physical_episode_bound"] is True
        assert summary["formal_backend"] == "house_native_goal_backend"
        assert summary["scoreable"] is False
        assert summary["formal_geometry_admitted"] is False
        assert summary["clearance_status"] == "unknown"
        first = summary["skill_results"][0]
        assert first["skill"] == "ground_navigation"
        assert first["status"] == "failed"
        assert first["reason"].startswith("navigation_path_blocked:")
        assert first["reason"] != "physical_episode_unbound"


def test_second_missions_enter_the_formal_episode_runner_without_scoring() -> None:
    import tempfile

    from demo.benchmark.restaurant_service_task import SERVICE_TASK_ID, build_restaurant_service_package
    from demo.benchmark.second_mission_episode import (
        PENDING_REASON,
        SCENE_FORMAL_EPISODES,
        run_second_mission_episode,
        second_mission_builders,
    )

    assert {row["second_task_id"] for row in SCENE_FORMAL_EPISODES} == set(second_mission_builders())
    assert {row["scene_key"] for row in SCENE_FORMAL_EPISODES} == {
        "factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant",
    }
    with tempfile.TemporaryDirectory() as tmp:
        for task_id in second_mission_builders():
            summary = run_second_mission_episode(task_id, f"{tmp}/{task_id}")
            assert summary["task_id"] == task_id
            assert summary["error"] is None
            assert summary["skill_receipt_count"] == len(summary["skill_results"])
            assert summary["skill_receipt_count"] > 0
            assert {row["status"] for row in summary["skill_results"]} == {"failed"}
            assert {row["reason"] for row in summary["skill_results"]} == {PENDING_REASON}
            assert summary["scoreable"] is False
            assert summary["execution_validated"] is False
            assert summary["clearance_status"] == "unknown"
            assert summary["formal_geometry_admitted"] is False
            assert summary["cpu_is_formal_evidence"] is False
            assert summary["formal_backend"] == "house_native_goal_backend"
            assert set(summary["region_ids"])
    restaurant = build_restaurant_service_package()["public_task"]
    assert canonical_sha256(build_goal_task_package("restaurant")["public_task"]) == RESTAURANT_PUBLIC_TASK_SHA256
    assert restaurant["task_id"] == SERVICE_TASK_ID


def test_second_missions_stay_outside_the_405_grid_and_goal01_hashes() -> None:
    from demo.benchmark.goal_full_matrix import EXPECTED_PACKAGES

    assert EXPECTED_PACKAGES == 405
    goal_hashes = set(_FROZEN_INDOOR_SHA256.values())
    goal_hashes.add(RESTAURANT_PUBLIC_TASK_SHA256)
    for scene in ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant"):
        package = build_goal_task_package(scene)
        goal_hashes.add(canonical_sha256(package["public_task"]))
        goal_hashes.add(canonical_sha256(package["hidden_evaluator"]))
    inventory = {build_goal_task_package(scene)["public_task"]["task_id"]
                 for scene in ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")}
    assert not inventory & _NEW_TASK_IDS
    bundles = [
        build_factory_fire_package(),
        build_warehouse_inbound_package(),
        build_house_delivery_package(),
        build_city_exit_package(),
        build_restaurant_service_package(),
    ]
    for bundle in bundles:
        public = bundle["public_task"]
        blob = json.dumps(public, sort_keys=True, ensure_ascii=False)
        assert public["task_id"] in _NEW_TASK_IDS
        assert not any(digest in blob for digest in goal_hashes)
        own_hash = canonical_sha256(public)
        assert bundle["hidden_evaluator"]["public_task_sha256"] == own_hash
        assert own_hash not in goal_hashes
        assert bundle["hidden_evaluator"]["release_gate"]["scoreable"] is False
        assert bundle["hidden_evaluator"]["release_gate"]["benchmark_ready"] is False


def test_inbound_approach_stops_at_the_face_normal_rally(monkeypatch) -> None:
    import math

    from demo.benchmark.goal_house_episode import HouseNativeGoalBackend, _RALLY_STANDOFF_M

    public = build_warehouse_inbound_package()["public_task"]
    backend = HouseNativeGoalBackend(public)
    backend.session = object()
    backend._runtime_id = lambda agent_id: {
        "warehouse_m20_1": "m20_1",
        "warehouse_m20_2": "m20_2",
    }[agent_id]
    monkeypatch.setattr(
        "demo.benchmark.goal_house_episode._root_state",
        lambda _session, runtime_id: {
            "m20_1": [-1.3, -0.3, 0.52, 1.0, 0.0, 0.0, 0.0],
            "m20_2": [-1.3, 0.3, 0.52, 1.0, 0.0, 0.0, 0.0],
        }[runtime_id],
    )

    class Bodies:
        bindings = {"objects": {"order_crate_01": {"body_path": "/crate"}}}

        def __call__(self, _path):
            return {"position_m": [0.0, 0.0, 0.3], "rotation_wxyz": [1.0, 0.0, 0.0, 0.0]}

    seen = {}

    def fake_follow(runtime_ids, goals, *, arrived, label, allow_reverse, final_stop_m=0.35,
                    refresh_goals=None, preset_paths=None, final_gait=None):
        del runtime_ids, goals, label
        assert allow_reverse is False
        assert final_stop_m == 0.35
        assert final_gait is None
        assert refresh_goals is None
        assert preset_paths is not None
        seen["paths"] = preset_paths
        seen["arrived_far"] = arrived({
            "m20_1": [-1.3, -0.3, 0.52],
            "m20_2": [-1.3, 0.3, 0.52],
        })
        seen["arrived_at_rally"] = arrived({
            "m20_1": [*preset_paths["m20_1"][-1], 0.52],
            "m20_2": [*preset_paths["m20_2"][-1], 0.52],
        })
        backend._last_navigation_diagnostics = {"agents": {}, "status": "failed"}
        return "failed", "navigation_target_not_reached:order_crate_01:1.00m"

    monkeypatch.setattr(backend, "_follow_ground", fake_follow)
    status, reason = backend._approach_object(
        "order_crate_01",
        ["warehouse_m20_1", "warehouse_m20_2"],
        Bodies(),
        point_ids={"warehouse_m20_1": "support_left", "warehouse_m20_2": "support_right"},
    )
    assert seen["arrived_far"] is False
    assert seen["arrived_at_rally"] is True
    assert status == "failed"
    assert reason.startswith("approach_object_not_reached:order_crate_01:")
    # South support stays south of the crate; north support stays north.
    # The stand is the coarse 1.2–1.5 m rally, not a 0.8 m connector pose.
    sockets = {"m20_1": (0.0, -0.3), "m20_2": (0.0, 0.3)}
    for runtime_id, socket in sockets.items():
        end = seen["paths"][runtime_id][-1]
        assert 1.2 <= math.dist(end, socket) <= 1.5
        assert abs(math.dist(end, socket) - _RALLY_STANDOFF_M) < 1e-6
    assert seen["paths"]["m20_1"][-1][1] < -1.2
    assert seen["paths"]["m20_2"][-1][1] > 1.2


def test_physx_mirror_writes_the_live_world_pose() -> None:
    import pytest
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom

    from demo.benchmark.goal_house_episode import _mirror_physx_bodies_to_usd

    stage = Usd.Stage.CreateInMemory()
    parent = UsdGeom.Xform.Define(stage, "/World")
    parent.AddTranslateOp().Set(Gf.Vec3d(10.0, 0.0, 0.0))
    body = UsdGeom.Xform.Define(stage, "/World/body")
    body.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
    body.AddOrientOp().Set(Gf.Quatf(1.0, 0.0, 0.0, 0.0))
    _mirror_physx_bodies_to_usd(stage, {
        "/World/body": {"position_m": [12.0, 3.0, 0.5], "rotation_wxyz": [1.0, 0.0, 0.0, 0.0]},
    })
    world = UsdGeom.XformCache().GetLocalToWorldTransform(body.GetPrim()).ExtractTranslation()
    assert [world[0], world[1], world[2]] == pytest.approx([12.0, 3.0, 0.5])


def test_support_rally_stays_outside_the_crate() -> None:
    """The rally polyline ends 1.2–1.5 m outside each face and never enters the box."""
    import math

    from demo.benchmark.goal_house_episode import _RALLY_STANDOFF_M, _support_approach_paths

    crate = (-4.0, -9.925)
    half = (0.45, 0.325)
    sockets = {
        "m20_1": [crate[0], crate[1] - 0.3, 0.55],
        "m20_2": [crate[0], crate[1] + 0.3, 0.55],
    }
    start = {"m20_1": (-5.36, -9.73), "m20_2": (-4.57, -10.60)}
    paths = _support_approach_paths(
        start, sockets, list(crate), half, stand_m=_RALLY_STANDOFF_M)
    for runtime_id, socket in sockets.items():
        end = paths[runtime_id][-1]
        assert 1.2 <= math.dist(end, socket[:2]) <= 1.5
        for point in paths[runtime_id]:
            near_x = min(max(point[0], crate[0] - half[0]), crate[0] + half[0])
            near_y = min(max(point[1], crate[1] - half[1]), crate[1] + half[1])
            assert math.hypot(point[0] - near_x, point[1] - near_y) > 0.15


def test_pepper_root_frame_command_reaches_the_waypoint_the_world_command_missed() -> None:
    """XDisp/YDisp stay at the spawn yaw. World velocity has to be rotated back.

    The samples are Pepper's cuda:0 poses from the restaurant service episode
    that finished 42.9 m from dining table A. The plant is that fixed root
    rotation, integrated with the same 0.02 s step the controller uses.
    """
    import math

    from demo.benchmark.goal_house_episode import _pepper_world_command

    # Printed base_link yaw at the first sample. The root frame fitted to the
    # seven pose samples is 1.6 degrees larger; the captured yaw still closes.
    captured_yaw = 2.29
    plant_yaw = math.radians(132.8)
    aim = (18.27, 18.02)
    measured_end = (32.71, 59.52)
    dt = 0.02

    def state(x: float, y: float, yaw: float) -> list[float]:
        half = 0.5 * yaw
        return [x, y, 0.0, math.cos(half), 0.0, 0.0, math.sin(half)]

    def plant(command: list[float], x: float, y: float, yaw: float) -> tuple[float, float, float]:
        cosine, sine = math.cos(plant_yaw), math.sin(plant_yaw)
        world_vx = cosine * command[0] - sine * command[1]
        world_vy = sine * command[0] + cosine * command[1]
        return (x + world_vx * dt, y + world_vy * dt,
                math.atan2(math.sin(yaw + command[2] * dt), math.cos(yaw + command[2] * dt)))

    def rollout(command_frame: float) -> tuple[float, float]:
        x, y, yaw = 18.0, 16.0, captured_yaw
        for _ in range(7000):
            command = _pepper_world_command(
                state(x, y, yaw), aim, 0.45, 0.40, frame_yaw=command_frame)
            x, y, yaw = plant(command, x, y, yaw)
        return x, y

    missed = rollout(0.0)
    assert math.dist(missed, measured_end) < 2.0
    assert math.dist(missed, aim) > 20.0
    reached = rollout(captured_yaw)
    assert math.dist(reached, aim) <= 0.5


def test_pepper_occupancy_radius_clears_its_base() -> None:
    """The gait grid is 0.25 m. Pepper's base is 0.50 m across, so it needs more."""
    from demo.benchmark.goal_house_episode import _PEPPER_OCCUPANCY_RADIUS_M

    assert _PEPPER_OCCUPANCY_RADIUS_M >= 0.45


def _city_exit_regions() -> dict:
    centres = {
        "dismissal_gathering": (-42.0, 50.0),
        "dismissal_assembly": (-44.0, 46.0),
        "station_corridor": (-60.0, 30.0),
        "station_apron": (-69.0, 25.0),
    }
    return {
        "region_bindings": [
            {
                "id": key,
                "candidate_geometry": {
                    "center_m": [value[0], value[1], 0.0],
                    "aabb_xy_m": [value[0] - 0.75, value[1] - 0.75,
                                  value[0] + 0.75, value[1] + 0.75],
                },
            }
            for key, value in centres.items()
        ],
    }


class _MeasuredPeople:
    def __init__(self) -> None:
        self.poses = {
            "affected_person_00": (-42.30, 49.70),
            "affected_person_01": (-42.30, 50.30),
            "affected_person_02": (-41.70, 49.70),
            "affected_person_03": (-41.70, 50.30),
        }
        self.goals = {
            "affected_person_00": (-44.30, 45.70),
            "affected_person_01": (-44.30, 46.30),
            "affected_person_02": (-43.70, 45.70),
            "affected_person_03": (-43.70, 46.30),
        }

    def position(self, actor_id: str) -> tuple[float, float, float]:
        return (*self.poses[actor_id], 0.30)

    def update(self, dt: float, *, actor_ids=None, **kwargs) -> None:
        del kwargs
        for actor_id in actor_ids:
            x, y = self.poses[actor_id]
            tx, ty = self.goals[actor_id]
            dx, dy = tx - x, ty - y
            distance = math.hypot(dx, dy)
            step = 1.2 * dt
            if distance <= step:
                self.poses[actor_id] = (tx, ty)
            else:
                self.poses[actor_id] = (x + dx / distance * step, y + dy / distance * step)


def test_city_exit_deployment_record_omits_the_live_runtime() -> None:
    from demo.benchmark.second_mission_episode import deployment_record_for_disk

    class Runtime:
        pass

    runtime = Runtime()
    deployment = {"city_exit_people": {"runtime": runtime, "person_ids": ["affected_person_00"]}}
    recorded = deployment_record_for_disk(deployment)
    json.dumps(recorded)
    assert recorded["city_exit_people"]["runtime"] == "live_stage_runtime"
    assert deployment["city_exit_people"]["runtime"] is runtime


def test_city_exit_assembly_stays_on_the_reachable_sidewalk() -> None:
    """The assembly has to be a cell Go2 can enter. (-44, 46) is off the deck."""
    from demo.benchmark.goal_house_episode import _scene_occupancy, occupancy_waypoints
    from demo.benchmark.second_mission_deployment import _CITY_EXIT_XYZ

    gathering = _CITY_EXIT_XYZ["dismissal_gathering"][:2]
    assembly = _CITY_EXIT_XYZ["dismissal_assembly"][:2]
    occupancy = _scene_occupancy("city")
    assert occupancy.rectangle_free(assembly[0], assembly[1], 0.25, 0.25)
    path = occupancy_waypoints("city", gathering, assembly, 0.25)
    assert path is not None
    assert math.dist(path[-1], assembly) <= 0.6
    assert math.dist(assembly, gathering) > 2.3
    from demo.benchmark.second_mission_deployment import _city_sidewalk_xy

    spawn = _city_sidewalk_xy((-69.0, 25.0))
    apron = _CITY_EXIT_XYZ["station_apron"][:2]
    corridor = _CITY_EXIT_XYZ["station_corridor"][:2]
    assert occupancy.rectangle_free(apron[0], apron[1], 0.25, 0.25)
    assert occupancy.rectangle_free(corridor[0], corridor[1], 0.25, 0.25)
    carter_path = occupancy_waypoints("city", spawn, apron, 0.25)
    assert carter_path is not None
    assert math.dist(carter_path[-1], apron) <= 0.6
    assert math.dist(apron, corridor) > 2.3


def test_city_exit_person_path_has_a_real_segment() -> None:
    from demo.benchmark.second_mission_episode import city_exit_person_path

    start, goal = city_exit_person_path((-42.0, 50.0), (-44.0, 46.0), (-0.30, -0.30), 0.30)
    assert len((start, goal)) == 2
    assert math.dist(start, goal) > 1.0
    assert start[2] == goal[2] == 0.30


def test_city_exit_roster_requires_measured_people_at_the_gathering() -> None:
    from demo.benchmark.goal_house_episode import _city_exit_observation

    people = _MeasuredPeople()
    deployment = _city_exit_regions()
    deployment["city_exit_people"] = {
        "runtime": people,
        "person_ids": list(people.poses),
    }
    task = {"arguments": {
        "region_id": "dismissal_gathering",
        "subject_ref": "dismissal_gathering",
        "observation_mode": "dismissal_roster",
    }}
    observed, error = _city_exit_observation(task, deployment, (-42.0, 50.0))
    assert error is None
    assert {row["public_id"] for row in observed["people"]} == set(people.poses)
    assert all(row["status"] == "measured" for row in observed["people"])
    _, missing_agent = _city_exit_observation(task, deployment, (-69.0, 25.0))
    assert missing_agent == "observe_agent_not_in_region"
    people.poses["affected_person_00"] = (-44.30, 45.70)
    _, left = _city_exit_observation(task, deployment, (-42.0, 50.0))
    assert left == "observe_subject_not_measured"
    deployment.pop("city_exit_people")
    _, unread = _city_exit_observation(task, deployment, (-42.0, 50.0))
    assert unread == "observe_subject_unmeasured"


def test_city_exit_guidance_arrives_then_headcount_and_corridor_read_the_same_people() -> None:
    from demo.benchmark.goal_house_episode import _city_exit_observation, advance_city_guidance

    people = _MeasuredPeople()
    deployment = _city_exit_regions()
    deployment["city_exit_people"] = {
        "runtime": people,
        "person_ids": list(people.poses),
    }
    arrived, error = advance_city_guidance(
        people, list(people.poses), deployment, "dismissal_assembly", dt=0.1, max_steps=80)
    assert error is None
    assert set(arrived) == set(people.poses)
    headcount = {"arguments": {
        "region_id": "dismissal_assembly",
        "subject_ref": "dismissal_assembly",
        "observation_mode": "dismissal_headcount",
    }}
    observed, headcount_error = _city_exit_observation(headcount, deployment, (-44.0, 46.0))
    assert headcount_error is None
    assert len(observed["people"]) == 4
    corridor = {"arguments": {
        "region_id": "station_apron",
        "subject_ref": "station_corridor",
        "observation_mode": "corridor_clearance",
    }}
    _, clear_error = _city_exit_observation(corridor, deployment, (-69.0, 25.0))
    assert clear_error is None
    people.poses["affected_person_01"] = (-60.0, 30.0)
    _, blocked = _city_exit_observation(corridor, deployment, (-69.0, 25.0))
    assert blocked == "observe_subject_not_measured"
    people.poses["affected_person_00"] = (-42.30, 49.70)
    _, early = advance_city_guidance(
        people, list(people.goals), deployment, "dismissal_assembly", dt=0.1, max_steps=5)
    assert early == "guide_people_not_at_gathering"


def _state_from(xy, yaw):
    import math
    return [xy[0], xy[1], 0.0, math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)]


def _plant_body(state, command, dt):
    import math
    from demo.benchmark.goal_house_episode import _yaw

    yaw = _yaw(state)
    state[0] += command[0] * math.cos(yaw) * dt
    state[1] += command[0] * math.sin(yaw) * dt
    yaw = math.atan2(math.sin(yaw + command[2] * dt), math.cos(yaw + command[2] * dt))
    state[3:7] = [math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)]


def _plant_pepper(state, command, frame_yaw, dt):
    import math
    from demo.benchmark.goal_house_episode import _yaw

    cosine, sine = math.cos(frame_yaw), math.sin(frame_yaw)
    state[0] += (cosine * command[0] - sine * command[1]) * dt
    state[1] += (sine * command[0] + cosine * command[1]) * dt
    yaw = _yaw(state) + command[2] * dt
    state[3:7] = [math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)]


def _integrate_path(scene, start, yaw, goal, law, *, frame_yaw=0.0, radius=0.25):
    import math
    from demo.benchmark.goal_house_episode import (
        _CITY_VELOCITY_TO_TARGET, _GROUND_SPEED_MPS, _pepper_world_command,
        _rally_walk_command, _seek_command, _tracked_gait_command, _yaw,
        occupancy_waypoints,
    )

    path = occupancy_waypoints(scene, start, goal, radius)
    if not path:
        return "blocked", None
    state = _state_from(start, yaw)
    index = 0
    dt = 0.05
    for step in range(6000):
        while index < len(path) - 1 and math.hypot(
                state[0] - path[index][0], state[1] - path[index][1]) <= (0.45 if law != "pepper" else 0.45):
            index += 1
        aim = path[index]
        stop = 0.35 if index == len(path) - 1 else 0.4
        if law == "rally":
            command = _rally_walk_command(state, aim, stop)
            _plant_body(state, command, dt)
        elif law == "pepper":
            command = _pepper_world_command(state, aim, 0.45, stop, frame_yaw)
            _plant_pepper(state, command, frame_yaw, dt)
        elif law == "seek":
            command = _seek_command(state, aim, _GROUND_SPEED_MPS, stop)
            _plant_body(state, command, dt)
        else:
            command = _tracked_gait_command(state, aim, stop, _CITY_VELOCITY_TO_TARGET)
            _plant_body(state, command, dt)
        if math.hypot(state[0] - goal[0], state[1] - goal[1]) <= 1.15:
            return "arrived", (step * dt, state[0], state[1], _yaw(state))
    return "short", (state[0], state[1])


def _integrate_laden(stand, yaw, standoff, goal, bounds):
    import math
    from demo.benchmark.goal_house_episode import (
        _CITY_VELOCITY_TO_TARGET, _inside_region, _tracked_gait_command, _yaw,
    )

    state = _state_from(stand, yaw)
    dt = 0.05
    for step in range(6000):
        heading = _yaw(state)
        cargo = (state[0] + math.cos(heading) * standoff,
                 state[1] + math.sin(heading) * standoff)
        if _inside_region(cargo, goal, bounds, "ground_navigation"):
            return "arrived", (step * dt, cargo[0], cargo[1])
        aim = (goal[0] + state[0] - cargo[0], goal[1] + state[1] - cargo[1])
        command = _tracked_gait_command(state, aim, 0.15, _CITY_VELOCITY_TO_TARGET)
        _plant_body(state, command, dt)
    return "short", (state[0], state[1])


def test_house_and_fire_routes_integrate_before_isaac() -> None:
    from demo.benchmark.factory_fire_task import build_factory_fire_package
    from demo.benchmark.goal_house_episode import _inside_region, _yaw
    from demo.benchmark.house_delivery_task import build_house_delivery_package
    from demo.benchmark.second_mission_deployment import (
        build_second_mission_deployment, resolve_support_deploy,
    )

    packages = (
        build_house_delivery_package(),
        build_factory_fire_package(),
    )
    for package in packages:
        public = package["public_task"]
        plan = package["hidden_evaluator"]["witness_plans"][0]["plan"]
        deployment = build_second_mission_deployment(public, plan)
        deploy = resolve_support_deploy(public)
        assert deploy is not None
        centres = {
            row["id"]: row["candidate_geometry"]["center_m"]
            for row in deployment["region_bindings"]
        }
        bounds = {
            row["id"]: row["candidate_geometry"]["aabb_xy_m"]
            for row in deployment["region_bindings"]
        }
        pickup = centres[deploy["pickup_region_id"]]
        assert abs(deploy["object_xy"][0] - pickup[0]) <= 0.75
        assert abs(deploy["object_xy"][1] - pickup[1]) <= 0.75
        goal = centres[deploy["destination_region_id"]]
        laden = _integrate_laden(
            tuple(deploy["stand_xy"]), deploy["yaw_rad"], deploy["standoff_m"],
            (goal[0], goal[1]), bounds[deploy["destination_region_id"]])
        assert laden[0] == "arrived", (public["task_id"], laden)
        robots = {
            row["public_id"]: row for row in deployment["robot_instances"]
        }
        pose = {}
        held: set[str] = set()
        scene = public["mission"]["scene_key"]
        for task in plan["tasks"]:
            agents = list(task["assigned_agents"])
            if task["skill"] == "pick":
                held.update(agents)
                continue
            if task["skill"] == "place":
                held.difference_update(agents)
                continue
            if task["skill"] != "ground_navigation":
                continue
            region_id = task["arguments"]["region_id"]
            target = (centres[region_id][0], centres[region_id][1])
            for agent in agents:
                row = robots[agent]
                if agent not in pose:
                    origin = row["spawn_binding"]["deployment_directive"]["position_m"]
                    rotation = row["spawn_binding"]["deployment_directive"]["rotation_wxyz"]
                    pose[agent] = (origin[0], origin[1], _yaw([0, 0, 0, *rotation]))
                if agent in held:
                    continue
                start_x, start_y, yaw = pose[agent]
                kind = row["type"]
                if kind == "pepper":
                    law, radius = "pepper", 0.45
                    frame = yaw
                elif kind == "m20":
                    law, radius, frame = "rally", 0.25, 0.0
                else:
                    law, radius, frame = "seek", 0.25, 0.0
                result = _integrate_path(
                    scene, (start_x, start_y), yaw, target, law,
                    frame_yaw=frame, radius=radius)
                assert result[0] == "arrived", (public["task_id"], task["task_id"], agent, result)
                _elapsed, final_x, final_y, final_yaw = result[1]
                assert _inside_region((final_x, final_y), target, bounds[region_id], "ground_navigation")
                pose[agent] = (final_x, final_y, final_yaw)


def test_house_signoff_and_fire_sense_read_measured_subjects() -> None:
    from demo.benchmark.factory_fire_task import build_factory_fire_package
    from demo.benchmark.goal_house_episode import (
        _fire_scene_observation, _payload_presence_observation,
        _second_mission_subject_measured,
    )
    from demo.benchmark.house_delivery_task import build_house_delivery_package
    from demo.benchmark.second_mission_deployment import build_second_mission_deployment

    house = build_house_delivery_package()
    house_public = house["public_task"]
    house_deployment = build_second_mission_deployment(
        house_public, house["hidden_evaluator"]["witness_plans"][0]["plan"])
    signoff = {"arguments": {
        "region_id": "delivery_signoff",
        "subject_ref": "delivery_signoff_marker",
        "observation_mode": "static_signoff",
    }}
    marker = (4.6, 4.2)
    observed, error = _payload_presence_observation(
        signoff, house_deployment, marker, marker)
    assert error is None
    assert _second_mission_subject_measured(house_public, signoff, observed, house_deployment)
    _, missing = _payload_presence_observation(signoff, house_deployment, (8.4, 10.0), marker)
    assert missing == "observe_agent_not_in_region"
    _, unread = _payload_presence_observation(signoff, house_deployment, marker, None)
    assert unread == "observe_subject_unmeasured"

    fire = build_factory_fire_package()
    fire_public = fire["public_task"]
    fire_deployment = build_second_mission_deployment(
        fire_public, fire["hidden_evaluator"]["witness_plans"][0]["plan"])
    button = {"arguments": {
        "region_id": "rescue_button",
        "subject_ref": "rescue_channel_button",
        "observation_mode": "fixed_joint_button_strike",
    }}
    button_xy = (4.2, 3.4)
    struck, button_error = _payload_presence_observation(
        button, fire_deployment, button_xy, button_xy)
    assert button_error is None
    assert _second_mission_subject_measured(fire_public, button, struck, fire_deployment)
    sense = {"arguments": {
        "region_id": "fire_site",
        "subject_ref": "fire_site",
        "observation_mode": "fire_scene_sense",
    }}
    sensed, sense_error = _fire_scene_observation(sense, fire_deployment, (3.20, 3.40))
    assert sense_error is None
    assert _second_mission_subject_measured(fire_public, sense, sensed, fire_deployment)
    _, away = _fire_scene_observation(sense, fire_deployment, (-1.95, 3.4))
    assert away == "observe_agent_not_in_region"
    _, past_the_wall = _fire_scene_observation(sense, fire_deployment, (5.55, 3.4))
    assert past_the_wall == "observe_agent_not_in_region"
    clearance = {"objects": [
        {"public_id": "factory_extinguisher_01", "status": "measured",
         "physx": {"status": "measured", "position_m": [-1.95, 2.0]}},
        {"public_id": "rescue_channel_button", "status": "measured",
         "physx": {"status": "measured", "position_m": [4.2, 3.4]}},
    ]}
    assert _second_mission_subject_measured(fire_public, sense, clearance, fire_deployment) is False
