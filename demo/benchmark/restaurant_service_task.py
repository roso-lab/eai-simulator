"""Restaurant order-and-delivery task beside the frozen opening inspection.

Phase 1 records a static customer order with Pepper, then carries a rigid
plate-plus-apple payload from the kitchen to the matching table. The carry
uses the existing support-socket attach. It does not claim finger grasp,
arm posing, or a live Isaac receipt. ``RESTAURANT-GOAL-01`` stays unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from demo.benchmark.goal_driven_fixtures import _route
from demo.restaurant_benchmark.config import BENCHMARK_ROBOT_SPAWN_XYZ_M


SERVICE_TASK_ID = "RESTAURANT-SERVICE-01"
SERVICE_PROP_DIR = Path("usd/scene/restaurant/Assets/ServiceProps")
SERVICE_PROP_FILES = (
    "plate_large.usd",
    "Apple.usd",
    "dish_plate_apple.usda",
    "textures/Plates_Large_BaseColor.png",
    "textures/Plates_Large_Normal.png",
    "textures/Plates_Large_Roughness.png",
    "textures/Plates_Large_Metallic.png",
    "textures/apple_albedo.png",
    "textures/apple_normal.png",
    "textures/apple_rough.png",
    "textures/apple_metal.png",
    "service_props_manifest.json",
)
ORDER_KIND_A = "restaurant_order:dining_table_a:service_plate_apple_a"
ORDER_KIND_B = "restaurant_order:dining_table_b:service_plate_apple_b"
RECEIPT_KIND_A = "restaurant_service_receipt:dining_table_a:service_plate_apple_a"
RECEIPT_KIND_B = "restaurant_service_receipt:dining_table_b:service_plate_apple_b"
_SERVICE_PAIRS = (
    ("dining_table_a", "static_customer_a", "service_plate_apple_a",
     ORDER_KIND_A, "table_a_order_recorded", "table_a_dish_delivered",
     RECEIPT_KIND_A, "table_a_service_receipt"),
    ("dining_table_b", "static_customer_b", "service_plate_apple_b",
     ORDER_KIND_B, "table_b_order_recorded", "table_b_dish_delivered",
     RECEIPT_KIND_B, "table_b_service_receipt"),
)


def service_prop_paths() -> tuple[Path, ...]:
    return tuple(SERVICE_PROP_DIR / relative for relative in SERVICE_PROP_FILES)


def _support_binding(agent_id: str) -> list[dict[str, str]]:
    return [{"agent_id": agent_id, "point_id": "support_center"}]


def _restaurant_service_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
    from demo.benchmark.goal_driven_fixtures import SKILL_DURATIONS_S
    from demo.benchmark.goal_driven_tasks import (
        _object, _outcome, _plan, _region, _robot, _task, _task_row,
    )

    from demo.restaurant_benchmark.service_layout import service_layout

    # (18, 16) sits on occupied map cells. The admitted dining approach is
    # the interaction stand already used by the service layout.
    pepper_xy = service_layout()["pepper_interaction"]["dining_table_a"]["position_xy_m"]
    pepper_spawn = (float(pepper_xy[0]), float(pepper_xy[1]), 0.0)
    spawns = {
        "restaurant_pepper_1": pepper_spawn,
        "restaurant_carter_1": tuple(BENCHMARK_ROBOT_SPAWN_XYZ_M["carter_1"]),
        "restaurant_m20_1": tuple(BENCHMARK_ROBOT_SPAWN_XYZ_M["m20_1"]),
        "restaurant_iris_1": tuple(BENCHMARK_ROBOT_SPAWN_XYZ_M["iris_1"]),
    }
    robots = [
        _robot("restaurant_pepper_1", "pepper", "ground",
               ["ground_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("restaurant_carter_1", "carter", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "event_bus", "manipulator"]),
        _robot("restaurant_m20_1", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "event_bus", "manipulator"]),
        # The public task contract requires an aerial candidate. Iris cannot
        # take an order or carry a dish, and neither witness assigns it.
        _robot("restaurant_iris_1", "iris", "aerial",
               ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
    ]
    regions = [
        _region("restaurant_overhead", "overhead", "餐厅上空观察区；两份送餐见证都不分配 Iris", ["aerial_navigation"]),
        _region("restaurant_staging", "staging_area", "送餐任务的地面出发区", ["ground_navigation"]),
        _region("kitchen_service", "service_zone", "后厨出餐区；餐盘和食物从这里被吸附搬走", ["ground_navigation"]),
        _region("dining_service", "service_zone", "用餐服务区；两张餐桌都在这个区域内", ["ground_navigation"]),
        _region("dining_table_a", "dining_table", "第一张餐桌；静态顾客在此点餐，餐品必须放到这张桌子上", ["ground_navigation"]),
        _region("dining_table_b", "dining_table", "第二张餐桌；静态顾客在此点餐，餐品必须放到这张桌子上", ["ground_navigation"]),
    ]
    objects = [
        _object("static_customer_a", "static_human", "站在第一张餐桌旁的静态顾客，保持打电话的第一帧，不对话、不移动",
                ["stationary", "no_dialogue", "not_a_payload"]),
        _object("static_customer_b", "static_human", "站在第二张餐桌旁的静态顾客，保持打电话的第一帧，不对话、不移动",
                ["stationary", "no_dialogue", "not_a_payload"]),
        _object("service_plate_apple_a", "service_payload", "第一份餐：复制到餐厅资产目录的大盘加苹果，用支撑吸附搬运",
                ["support_socket_attach_only", "identity_must_match_order", "no_finger_grasp_in_phase_1"]),
        _object("service_plate_apple_b", "service_payload", "第二份餐：复制到餐厅资产目录的大盘加苹果，用支撑吸附搬运",
                ["support_socket_attach_only", "identity_must_match_order", "no_finger_grasp_in_phase_1"]),
    ]
    public = _task(
        SERVICE_TASK_ID, "restaurant",
        "Pepper 在两张餐桌记录静态顾客的订单，地面机器人把对应餐盘从后厨送到该餐桌，并在订单一致时提交送餐回执。",
        "这是开业检查之外的服务任务。第一阶段只要求 Pepper 开到交互距离并保持观察窗口写下订单；送餐使用支撑吸附，不使用手指抓紧。",
        [
            _outcome("table_a_order_recorded", "A 桌静态顾客订单已记录。",
                     "只记 dining_table_a 这一份订单消息。",
                     ["message_publication"]),
            _outcome("table_b_order_recorded", "B 桌静态顾客订单已记录。",
                     "只记 dining_table_b 这一份订单消息。",
                     ["message_publication"]),
            _outcome("table_a_dish_delivered", "A 餐已释放到第一张餐桌。",
                     "只记 service_plate_apple_a 在 dining_table_a 的这一次释放。",
                     ["release_event", "object_pose"]),
            _outcome("table_b_dish_delivered", "B 餐已释放到第二张餐桌。",
                     "只记 service_plate_apple_b 在 dining_table_b 的这一次释放。",
                     ["release_event", "object_pose"]),
            _outcome("table_a_service_receipt", "A 桌送餐回执已提交。",
                     "只记引用 dining_table_a 与 service_plate_apple_a 的这一份回执。",
                     ["delivery_receipt"]),
            _outcome("table_b_service_receipt", "B 桌送餐回执已提交。",
                     "只记引用 dining_table_b 与 service_plate_apple_b 的这一份回执。",
                     ["delivery_receipt"]),
        ],
        robots, regions, objects, [],
        [
            "静态顾客不是机器人，不对话，也不能被搬运。",
            "Pepper 只负责到达、保持观察窗口和写下订单，不拿餐盘。",
            "餐盘搬运是支撑吸附：Carter 与 M20 的 manipulator 接口在本任务里表示 support_socket 刚性连接，不是手指抓紧，也不是机械臂姿态控制。",
            "回执只有在对应桌子的订单已经写下、并且餐品已经在该桌子区域内释放后才算完成。",
            "Iris 在候选池里只满足公开任务对空中能力的声明，不能点餐也不能送餐，两份作者见证都不分配它。",
            "本包不改变 RESTAURANT-GOAL-01，不写入也不依赖任何 GOAL-01 内容哈希，release gate 保持 benchmark_ready 与 scoreable 为假。",
            "每个 required outcome 只表示一件不可再分的业务事实。",
            "餐盘与苹果网格来自 Modern House 资产的副本，组合成 usd/scene/restaurant/Assets/ServiceProps/dish_plate_apple.usda。站位见 demo/restaurant_benchmark/service_layout.py。",
            "餐盘放在后厨站位两侧的运动学出餐台上。2026-09-28 的仿真里餐盘落到台面后停在根高度 0.90 m；静态顾客停在打电话动作的第一帧，脚在地面。这还不是手指抓取，也不是可评分回合。",
        ],
        "出生锚点沿用餐厅已有的 Carter 与 M20 位置，Pepper 单独放在用餐区附近。场景绑定、真实吸附和静态人放置仍待 episode 验证。",
        "immediate", max_active_agents=3, robot_spawn_positions=spawns,
    )
    public["public_world"]["routes"].extend([
        _route("service_staging_to_table_a", "restaurant_staging", "dining_table_a",
               6.0, 1.2, resource="service_dining_lane"),
        _route("service_table_a_to_table_b", "dining_table_a", "dining_table_b",
               4.0, 1.2, resource="service_dining_lane"),
        _route("service_kitchen_to_table_a", "kitchen_service", "dining_table_a",
               5.0, 1.2, resource="service_table_a_lane"),
        _route("service_table_a_to_kitchen", "dining_table_a", "kitchen_service",
               5.0, 1.2, resource="service_table_a_lane"),
        _route("service_kitchen_to_table_b", "kitchen_service", "dining_table_b",
               6.0, 1.2, resource="service_table_b_lane"),
    ])

    def row(name: str, skill: str, arguments: dict[str, Any],
            prerequisites: list[str], agents: list[str]) -> dict[str, Any]:
        return _task_row(name, skill, arguments, prerequisites, [skill], agents)

    pepper = "restaurant_pepper_1"
    carter = "restaurant_carter_1"
    m20 = "restaurant_m20_1"
    serial_rows = [
        row("service_a_pepper_nav_table_a", "ground_navigation",
            {"region_id": "dining_table_a", "route_ids": ["service_staging_to_table_a"]}, [], [pepper]),
        row("service_a_pepper_observe_a", "observe",
            {"region_id": "dining_table_a", "subject_ref": "static_customer_a", "observation_mode": "customer_order"},
            ["service_a_pepper_nav_table_a"], [pepper]),
        row("service_a_pepper_order_a", "relay",
            {"message_kind": ORDER_KIND_A, "region_id": "dining_table_a"},
            ["service_a_pepper_observe_a"], [pepper]),
        row("service_a_pepper_nav_table_b", "ground_navigation",
            {"region_id": "dining_table_b", "route_ids": ["service_table_a_to_table_b"]},
            ["service_a_pepper_order_a"], [pepper]),
        row("service_a_pepper_observe_b", "observe",
            {"region_id": "dining_table_b", "subject_ref": "static_customer_b", "observation_mode": "customer_order"},
            ["service_a_pepper_nav_table_b"], [pepper]),
        row("service_a_pepper_order_b", "relay",
            {"message_kind": ORDER_KIND_B, "region_id": "dining_table_b"},
            ["service_a_pepper_observe_b"], [pepper]),
        row("service_a_carter_nav_kitchen", "ground_navigation",
            {"region_id": "kitchen_service", "route_ids": ["restaurant_staging_to_kitchen"]},
            ["service_a_pepper_order_a"], [carter]),
        row("service_a_carter_pick_a", "pick",
            {"object_id": "service_plate_apple_a", "pickup_region_id": "kitchen_service",
             "handling_mode_id": "support_attach", "carrier_bindings": _support_binding(carter)},
            ["service_a_carter_nav_kitchen", "service_a_pepper_order_a"], [carter]),
        row("service_a_carter_nav_table_a", "ground_navigation",
            {"region_id": "dining_table_a", "route_ids": ["service_kitchen_to_table_a"]},
            ["service_a_carter_pick_a"], [carter]),
        row("service_a_carter_place_a", "place",
            {"object_id": "service_plate_apple_a", "placement_region_id": "dining_table_a"},
            ["service_a_carter_nav_table_a"], [carter]),
        row("service_a_carter_return_kitchen", "ground_navigation",
            {"region_id": "kitchen_service", "route_ids": ["service_table_a_to_kitchen"]},
            ["service_a_carter_place_a"], [carter]),
        row("service_a_carter_pick_b", "pick",
            {"object_id": "service_plate_apple_b", "pickup_region_id": "kitchen_service",
             "handling_mode_id": "support_attach", "carrier_bindings": _support_binding(carter)},
            ["service_a_carter_return_kitchen", "service_a_pepper_order_b"], [carter]),
        row("service_a_carter_nav_table_b", "ground_navigation",
            {"region_id": "dining_table_b", "route_ids": ["service_kitchen_to_table_b"]},
            ["service_a_carter_pick_b"], [carter]),
        row("service_a_carter_place_b", "place",
            {"object_id": "service_plate_apple_b", "placement_region_id": "dining_table_b"},
            ["service_a_carter_nav_table_b"], [carter]),
        row("service_a_receipt_a", "relay",
            {"message_kind": RECEIPT_KIND_A, "region_id": "dining_table_b"},
            ["service_a_pepper_order_a", "service_a_pepper_order_b", "service_a_carter_place_a"],
            [pepper]),
        row("service_a_receipt_b", "relay",
            {"message_kind": RECEIPT_KIND_B, "region_id": "dining_table_b"},
            ["service_a_pepper_order_b", "service_a_carter_place_b", "service_a_receipt_a"],
            [pepper]),
    ]
    parallel_rows = [
        row("service_b_pepper_nav_table_a", "ground_navigation",
            {"region_id": "dining_table_a", "route_ids": ["service_staging_to_table_a"]}, [], [pepper]),
        row("service_b_pepper_observe_a", "observe",
            {"region_id": "dining_table_a", "subject_ref": "static_customer_a", "observation_mode": "customer_order"},
            ["service_b_pepper_nav_table_a"], [pepper]),
        row("service_b_pepper_order_a", "relay",
            {"message_kind": ORDER_KIND_A, "region_id": "dining_table_a"},
            ["service_b_pepper_observe_a"], [pepper]),
        row("service_b_pepper_nav_table_b", "ground_navigation",
            {"region_id": "dining_table_b", "route_ids": ["service_table_a_to_table_b"]},
            ["service_b_pepper_order_a"], [pepper]),
        row("service_b_pepper_observe_b", "observe",
            {"region_id": "dining_table_b", "subject_ref": "static_customer_b", "observation_mode": "customer_order"},
            ["service_b_pepper_nav_table_b"], [pepper]),
        row("service_b_pepper_order_b", "relay",
            {"message_kind": ORDER_KIND_B, "region_id": "dining_table_b"},
            ["service_b_pepper_observe_b"], [pepper]),
        row("service_b_carter_nav_kitchen", "ground_navigation",
            {"region_id": "kitchen_service", "route_ids": ["restaurant_staging_to_kitchen"]},
            ["service_b_pepper_order_a"], [carter]),
        row("service_b_carter_pick_a", "pick",
            {"object_id": "service_plate_apple_a", "pickup_region_id": "kitchen_service",
             "handling_mode_id": "support_attach", "carrier_bindings": _support_binding(carter)},
            ["service_b_carter_nav_kitchen"], [carter]),
        row("service_b_carter_nav_table_a", "ground_navigation",
            {"region_id": "dining_table_a", "route_ids": ["service_kitchen_to_table_a"]},
            ["service_b_carter_pick_a"], [carter]),
        row("service_b_carter_place_a", "place",
            {"object_id": "service_plate_apple_a", "placement_region_id": "dining_table_a"},
            ["service_b_carter_nav_table_a"], [carter]),
        row("service_b_m20_nav_kitchen", "ground_navigation",
            {"region_id": "kitchen_service", "route_ids": ["restaurant_staging_to_kitchen"]},
            ["service_b_pepper_order_b"], [m20]),
        row("service_b_m20_pick_b", "pick",
            {"object_id": "service_plate_apple_b", "pickup_region_id": "kitchen_service",
             "handling_mode_id": "support_attach", "carrier_bindings": _support_binding(m20)},
            ["service_b_m20_nav_kitchen"], [m20]),
        row("service_b_m20_nav_table_b", "ground_navigation",
            {"region_id": "dining_table_b", "route_ids": ["service_kitchen_to_table_b"]},
            ["service_b_m20_pick_b"], [m20]),
        row("service_b_m20_place_b", "place",
            {"object_id": "service_plate_apple_b", "placement_region_id": "dining_table_b"},
            ["service_b_m20_nav_table_b"], [m20]),
        row("service_b_receipt_a", "relay",
            {"message_kind": RECEIPT_KIND_A, "region_id": "dining_table_b"},
            ["service_b_pepper_order_a", "service_b_pepper_order_b", "service_b_carter_place_a"],
            [pepper]),
        row("service_b_receipt_b", "relay",
            {"message_kind": RECEIPT_KIND_B, "region_id": "dining_table_b"},
            ["service_b_pepper_order_b", "service_b_m20_place_b", "service_b_receipt_a"],
            [pepper]),
    ]
    _schedule(parallel_rows, {
        "service_b_pepper_nav_table_a": 0.0,
        "service_b_pepper_observe_a": 8.0,
        "service_b_pepper_order_a": 12.0,
        "service_b_pepper_nav_table_b": 13.0,
        "service_b_pepper_observe_b": 21.0,
        "service_b_pepper_order_b": 25.0,
        "service_b_carter_nav_kitchen": 13.0,
        "service_b_carter_pick_a": 21.0,
        "service_b_carter_nav_table_a": 25.0,
        "service_b_carter_place_a": 33.0,
        "service_b_m20_nav_kitchen": 26.0,
        "service_b_m20_pick_b": 34.0,
        "service_b_m20_nav_table_b": 38.0,
        "service_b_m20_place_b": 46.0,
        "service_b_receipt_a": 41.0,
        "service_b_receipt_b": 54.0,
    }, SKILL_DURATIONS_S)
    witnesses = [
        {"id": "restaurant_service_witness_a",
         "satisfies_outcomes": [item["id"] for item in public["required_outcomes"]],
         "outcome_support": {
             "table_a_order_recorded": ["service_a_pepper_order_a"],
             "table_b_order_recorded": ["service_a_pepper_order_b"],
             "table_a_dish_delivered": ["service_a_carter_place_a"],
             "table_b_dish_delivered": ["service_a_carter_place_b"],
             "table_a_service_receipt": ["service_a_receipt_a"],
             "table_b_service_receipt": ["service_a_receipt_b"],
         },
         "plan": _plan("restaurant_service_witness_a", [pepper, carter], serial_rows)},
        {"id": "restaurant_service_witness_b",
         "satisfies_outcomes": [item["id"] for item in public["required_outcomes"]],
         "outcome_support": {
             "table_a_order_recorded": ["service_b_pepper_order_a"],
             "table_b_order_recorded": ["service_b_pepper_order_b"],
             "table_a_dish_delivered": ["service_b_carter_place_a"],
             "table_b_dish_delivered": ["service_b_m20_place_b"],
             "table_a_service_receipt": ["service_b_receipt_a"],
             "table_b_service_receipt": ["service_b_receipt_b"],
         },
         "plan": {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
                  "plan_id": "restaurant_service_witness_b",
                  "assigned_team": [pepper, carter, m20], "tasks": parallel_rows}},
    ]
    return public, witnesses


def _schedule(rows: list[dict[str, Any]], starts: Mapping[str, float],
              durations: Mapping[str, float]) -> None:
    for item in rows:
        item["execution"] = {
            "start_s": starts[item["task_id"]],
            "duration_s": durations[item["skill"]],
        }


def review_restaurant_service_witness(*, match: Callable[..., list[Mapping[str, Any]]],
                                      require: Callable[[Any, str], None],
                                      precedes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
                                      release_at: Callable[[str, str, str], list[Mapping[str, Any]]],
                                      final_object: Callable[[str, str], None]) -> None:
    """Symbolic checks for orders, matching releases, and the service receipt."""
    for (table, customer, dish, order_kind, order_outcome, delivery_outcome,
         receipt_kind, receipt_outcome) in _SERVICE_PAIRS:
        observations = match(
            skills={"observe"}, mobility="ground", region_id=table, subject_ref=customer,
            observation_mode="customer_order", outcome=order_outcome)
        require(observations, f"restaurant_service:{table}:order_observation")
        arrivals = match(
            skills={"ground_navigation"}, mobility="ground", region_id=table,
            outcome=order_outcome)
        require(any(
            any(precedes(arrival, observation)
                and set(arrival["assigned_agents"]) & set(observation["assigned_agents"])
                for arrival in arrivals)
            for observation in observations),
            f"restaurant_service:{table}:order_after_arrival")
        relays = match(
            skills={"relay"}, message_kind=order_kind, region_id=table,
            outcome=order_outcome)
        require(relays, f"restaurant_service:{table}:order_relay")
        require(any(precedes(observation, relay)
                    for observation in observations for relay in relays),
                f"restaurant_service:{table}:order_relay_after_observation")
        releases = release_at(dish, table, delivery_outcome)
        require(releases, f"restaurant_service:{dish}:released_on_{table}")
        require(any(precedes(relay, release) for relay in relays for release in releases),
                f"restaurant_service:{dish}:delivery_after_matching_order")
        final_object(dish, table)
        receipts = match(
            skills={"relay"}, message_kind=receipt_kind, outcome=receipt_outcome)
        require(receipts, f"restaurant_service:{table}:receipt")
        require(all(any(precedes(relay, receipt) for relay in relays) for receipt in receipts),
                f"restaurant_service:{table}:receipt_after_its_order")
        require(all(any(precedes(release, receipt) for release in releases) for receipt in receipts),
                f"restaurant_service:{table}:receipt_after_its_delivery")


def build_restaurant_service_package() -> dict[str, dict[str, Any]]:
    """Authoring bundle for the service task. Release gates stay closed."""
    from copy import deepcopy

    from demo.benchmark.goal_driven_contract import (
        GOAL_BASELINE_SCHEMA, GOAL_CONTRACT_VERSION, GOAL_HIDDEN_EVALUATOR_SCHEMA,
        build_goal_heterogeneity_certificate, build_goal_participant_api,
        validate_goal_bundle, validate_goal_hidden_evaluator,
    )
    from demo.benchmark.goal_driven_tasks import (
        _apply_factor, _build_hidden_event_schedule, _legacy_measurement_contract,
        review_goal_task_design,
    )
    from demo.benchmark.provenance import canonical_sha256

    public, witnesses = _restaurant_service_parts()
    _apply_factor(public, "communication_quality", None)
    review = review_goal_task_design(public, witnesses)
    hidden = {
        "schema": GOAL_HIDDEN_EVALUATOR_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "outcome_predicates": [
            {
                "id": outcome["id"],
                "evidence_types": ["business_state", *outcome["evidence_expectations"]],
                "predicate": f"hidden_business_predicate:{outcome['id']}",
                "failure_codes": ["missing_evidence", "inconsistent_identity", "business_state_not_reached"],
            }
            for outcome in public["required_outcomes"]
        ],
        "witness_plans": deepcopy(witnesses),
        "heterogeneity_certificate": build_goal_heterogeneity_certificate(public, witnesses),
        "hidden_event_schedule": _build_hidden_event_schedule(public),
        "factor_effect": {
            "changed_dimension": public["experiment_factor"]["id"],
            "frozen_inputs": list(public["experiment_factor"]["frozen_inputs"]),
            "effect_status": "execution_pending",
        },
        "truth_sources": {
            "scene_objects": "immutable scene/object binding",
            "observations": "runtime frame and message provenance",
            "team_events": "runtime lifecycle callback and capability readback",
            "business_state": "scene-owned identity, service, handoff and access predicates",
            "motion_evidence": "measured pose and object trajectory records",
        },
        "measurement_contract": _legacy_measurement_contract(public),
        "measurement_status": "unclassified",
        "mechanism_template": None,
        "team_necessity_certificate": None,
        "release_gate": {
            "authoring_status": review["authoring_status"],
            "execution_status": review["execution_status"],
            "benchmark_ready": False,
            "scoreable": False,
            "blockers": [
                "restaurant_service_static_customer_stage_binding",
                "restaurant_service_copied_prop_spawn_binding",
                "restaurant_service_support_attach_execution",
                "reliable_skill_backend_conformance",
                "scene_geometry_and_object_binding",
                "business_evidence_and_receipt_binding",
            ],
        },
    }
    validate_goal_hidden_evaluator(hidden, task_spec=public)
    baseline = {
        "schema": GOAL_BASELINE_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "role": "optional participant-visible planning baselines; not normative answers",
        "solvers": [
            {"id": "capability_first", "version": 1, "behavior": "choose a legal team and submit a capability-matched plan"},
            {"id": "message_aware_replan", "version": 1, "behavior": "replan only after visible communication or team events"},
        ],
        "limitations": ["No optimality claim.", "No witness assignment is published."],
    }
    bundle = {"public_task": public, "participant_api": build_goal_participant_api(public),
              "hidden_evaluator": hidden, "baseline_manifest": baseline}
    validate_goal_bundle(bundle)
    return bundle
