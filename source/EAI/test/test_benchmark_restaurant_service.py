"""Authoring checks for the restaurant order-and-delivery task."""

from pathlib import Path

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_restaurant_fleet import RESTAURANT_PUBLIC_TASK_SHA256
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.restaurant_service_task import (
    SERVICE_TASK_ID,
    build_restaurant_service_package,
    service_prop_paths,
)
from demo.restaurant_benchmark.service_layout import validate_service_layout


def test_opening_readiness_task_stays_frozen() -> None:
    bundle = build_goal_task_package("restaurant")
    public = bundle["public_task"]
    assert public["task_id"] == "RESTAURANT-GOAL-01"
    assert canonical_sha256(public) == RESTAURANT_PUBLIC_TASK_SHA256
    assert "restaurant_pepper_1" not in {row["id"] for row in public["candidate_robot_pool"]}
    assert bundle["hidden_evaluator"]["release_gate"]["scoreable"] is False


def test_service_task_orders_then_delivers_matching_dishes() -> None:
    bundle = build_restaurant_service_package()
    public = bundle["public_task"]
    gate = bundle["hidden_evaluator"]["release_gate"]
    assert public["task_id"] == SERVICE_TASK_ID
    robots = {row["id"]: row for row in public["candidate_robot_pool"]}
    assert robots["restaurant_pepper_1"]["type"] == "pepper"
    assert "pick" not in robots["restaurant_pepper_1"]["public_capabilities"]
    assert "pick" not in robots["restaurant_iris_1"]["public_capabilities"]
    witnesses = bundle["hidden_evaluator"]["witness_plans"]
    assert all("restaurant_iris_1" not in witness["plan"]["assigned_team"] for witness in witnesses)
    assert {"pick", "place"} <= set(robots["restaurant_carter_1"]["public_capabilities"])
    assert {"pick", "place"} <= set(robots["restaurant_m20_1"]["public_capabilities"])
    objects = {row["id"]: row for row in public["public_world"]["objects"]}
    for dish_id in ("service_plate_apple_a", "service_plate_apple_b"):
        point = objects[dish_id]["physical_properties"]["grasp_points"][0]
        assert point["interface"] == "support_socket_v1"
        assert objects[dish_id]["physical_properties"]["handling_modes"][0]["id"] == "support_attach"
    assert objects["static_customer_a"]["physical_properties"]["handling_modes"] == []
    assert gate["benchmark_ready"] is False
    assert gate["scoreable"] is False
    assert gate["execution_status"] == "external_pending"
    missing = [path for path in service_prop_paths() if not path.is_file()]
    assert missing == []
    manifest = Path("usd/scene/restaurant/Assets/ServiceProps/service_props_manifest.json")
    assert "plate_large.usd" in manifest.read_text(encoding="utf-8")


def test_service_layout_places_dishes_and_static_customers() -> None:
    report = validate_service_layout()
    layout = report["layout"]
    assert layout["height_status"] == "pass_shelf_contact_recorded"
    assert layout["customer_stage_status"] == "phone_call_frame_frozen_feet_on_ground"
    assert layout["customers"]["static_customer_a"]["pose_motion_id"] == "phone_call"
    assert set(layout["pass_shelves"]) == {"pass_shelf_a", "pass_shelf_b"}
    assert set(layout["drop_shelves"]) == {"drop_shelf_a", "drop_shelf_b"}
    assert set(layout["dishes"]) == {"service_plate_apple_a", "service_plate_apple_b"}
    assert report["dish_size_m"][0] < 0.5
    assert layout["customers"]["static_customer_a"]["dialogue"] is False
    assert layout["pepper_interaction"]["dining_table_a"]["distance_m"] == 0.8


def test_service_episode_receipt_requires_both_orders_and_placements() -> None:
    from demo.benchmark.goal_restaurant_fleet import RESTAURANT_PUBLIC_TASK_SHA256
    from demo.benchmark.restaurant_service_task import ORDER_KIND_A, ORDER_KIND_B
    from demo.benchmark.restaurant_service_task import RECEIPT_KIND_A, RECEIPT_KIND_B
    from demo.restaurant_benchmark.service_episode import (
        build_service_receipts,
        episode_gates,
        episode_selection,
    )

    assert canonical_sha256(build_goal_task_package("restaurant")["public_task"]) == RESTAURANT_PUBLIC_TASK_SHA256
    selection = episode_selection()
    assert [row["type"] for row in selection["robots"]] == ["pepper", "carter"]
    assert episode_gates()["scoreable"] is False
    assert build_service_receipts([], []) == []
    one = build_service_receipts(
        [{"kind": ORDER_KIND_A}],
        [{"dish_id": "service_plate_apple_a", "drop_shelf_id": "drop_shelf_a", "placed": True}],
    )
    assert [row["kind"] for row in one] == [RECEIPT_KIND_A]
    both = build_service_receipts(
        [{"kind": ORDER_KIND_A}, {"kind": ORDER_KIND_B}],
        [
            {"dish_id": "service_plate_apple_a", "drop_shelf_id": "drop_shelf_a", "placed": True},
            {"dish_id": "service_plate_apple_b", "drop_shelf_id": "drop_shelf_b", "placed": True},
        ],
    )
    assert [row["kind"] for row in both] == [RECEIPT_KIND_A, RECEIPT_KIND_B]
    assert "dining_table_b" not in both[0]["kind"]


def test_service_pepper_stands_on_the_admitted_approach_and_carter_carries_both() -> None:
    from demo.restaurant_benchmark.service_layout import service_layout

    bundle = build_restaurant_service_package()
    public = bundle["public_task"]
    spawn = public["public_world"]["robot_spawn_positions"]["restaurant_pepper_1"]
    stand = service_layout()["pepper_interaction"]["dining_table_a"]["position_xy_m"]
    assert spawn[:2] == [float(stand[0]), float(stand[1])]
    assert spawn[:2] != [18.0, 16.0]
    witness = bundle["hidden_evaluator"]["witness_plans"][0]
    assert witness["plan"]["assigned_team"] == ["restaurant_pepper_1", "restaurant_carter_1"]
    task_ids = [row["task_id"] for row in witness["plan"]["tasks"]]
    assert "service_a_carter_pick_a" in task_ids
    assert "service_a_carter_pick_b" in task_ids
    assert "service_a_m20_pick_a" not in task_ids
    assert witness["outcome_support"]["table_a_dish_delivered"] == ["service_a_carter_place_a"]


def test_customer_order_requires_a_measured_subject_and_the_robot_in_the_region() -> None:
    from demo.benchmark.goal_house_episode import _customer_order_observation

    task = {"arguments": {
        "observation_mode": "customer_order",
        "subject_ref": "static_customer_a",
        "region_id": "dining_table_a",
    }}
    deployment = {
        "service_subjects": {"static_customer_a": {"position_m": [13.5, 21.2, 0.0]}},
        "region_bindings": [{
            "id": "dining_table_a",
            "candidate_geometry": {
                "center_m": [13.475, 21.175, 0.0],
                "aabb_xy_m": [12.725, 20.425, 14.225, 21.925],
            },
        }],
    }
    missing, reason = _customer_order_observation(task, {"region_bindings": deployment["region_bindings"]}, (13.5, 21.2))
    assert missing is None and reason == "observe_subject_unmeasured"
    away, away_reason = _customer_order_observation(task, deployment, (0.0, 0.0))
    assert away is None and away_reason == "observe_agent_not_in_region"
    observed, ok = _customer_order_observation(task, deployment, (13.5, 21.2))
    assert ok is None
    assert observed["objects"][0]["status"] == "measured"
    assert observed["objects"][0]["physx"]["position_m"][0] == 13.5


def test_pepper_table_goal_is_the_interaction_stand_not_the_box_edge() -> None:
    import math

    from demo.benchmark.goal_house_episode import restaurant_pepper_stand
    from demo.restaurant_benchmark.service_layout import TABLE_B_XY_M, service_layout

    stand = restaurant_pepper_stand(
        "RESTAURANT-SERVICE-01", "dining_table_b", ["restaurant_pepper_1"])
    expected = service_layout()["pepper_interaction"]["dining_table_b"]["position_xy_m"]
    assert stand == (float(expected[0]), float(expected[1]))
    assert abs(math.dist(stand, TABLE_B_XY_M) - 0.8) < 1e-6
    assert math.dist(stand, (14.570, 21.528)) > 1.0
    assert restaurant_pepper_stand(
        "RESTAURANT-SERVICE-01", "dining_table_b", ["restaurant_carter_1"]) is None
    assert restaurant_pepper_stand(
        "RESTAURANT-SERVICE-01", "kitchen_service", ["restaurant_pepper_1"]) is None


def test_pepper_order_pose_is_base_link_not_the_fixed_root() -> None:
    from demo.benchmark.goal_house_episode import moving_base_pose

    class _Vec:
        def __init__(self, values):
            self._values = list(values)

        def detach(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return list(self._values)

    class _Bodies:
        def __getitem__(self, key):
            _env, index = key
            assert index == 3
            return _Vec([15.536, 22.186, 0.0])

    class _Robot:
        def __init__(self):
            self.data = type("Data", (), {})()
            self.data.root_state_w = [_Vec([14.21, 20.86, 0.0])]
            self.data.body_state_w = _Bodies()

        def find_bodies(self, name, preserve_order=True):
            assert name == "base_link"
            return [3], ["base_link"]

    class _Session:
        def __init__(self, robot):
            scene = type("Scene", (), {})()
            scene.articulations = {"pepper_1": robot}
            env = type("Env", (), {})()
            env.scene = scene
            self.base_env = env

    session = _Session(_Robot())
    assert moving_base_pose(session, "pepper_1", "pepper")[:2] == [15.536, 22.186]
    assert moving_base_pose(session, "pepper_1", "carter")[:2] == [14.21, 20.86]


def test_restaurant_pick_names_the_live_robot_and_dish_prims() -> None:
    from demo.benchmark.second_mission_episode import bind_restaurant_service_bodies

    deployment = {"robot_instances": [
        {"runtime_id": "carter_1", "public_id": "restaurant_carter_1"},
        {"runtime_id": "pepper_1", "public_id": "restaurant_pepper_1"},
    ]}
    bind_restaurant_service_bodies(deployment)
    assert deployment["robot_instances"][0]["prim_path"] == "/World/envs/env_0/carter_1"
    dishes = {row["public_id"]: row for row in deployment["object_instances"]}
    assert set(dishes) == {"service_plate_apple_a", "service_plate_apple_b"}
    plate = dishes["service_plate_apple_a"]
    assert plate["runtime_id"] == "service_plate_apple_a"
    assert plate["prim_path"] == "/World/envs/env_0/service_plate_apple_a"
    assert plate["physical_properties"]["grasp_points"][0]["id"] == "support_center"
    assert "static_customer_a" not in dishes


def test_dish_support_socket_matches_the_public_local_point() -> None:
    from pxr import Gf, Usd, UsdGeom

    from demo.restaurant_benchmark.service_layout_probe import _author_dish_support_sockets

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root = UsdGeom.Xform.Define(stage, "/World/envs/env_0/service_plate_apple_a")
    _author_dish_support_sockets(root.GetPrim())
    socket = stage.GetPrimAtPath(
        "/World/envs/env_0/service_plate_apple_a/GraspPoints/support_center")
    assert socket.GetCustomDataByKey("goal_interface") == "support_socket_v1"
    local = UsdGeom.XformCache().ComputeRelativeTransform(socket, root.GetPrim())[0]
    point = list(local.Transform(Gf.Vec3d(0)))
    assert [round(value, 6) for value in point] == [0.0, 0.0, 0.04]
