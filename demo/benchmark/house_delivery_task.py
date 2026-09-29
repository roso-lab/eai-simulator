"""Doorstep delivery beside the frozen home-recovery task.

A ground robot support-carries one parcel from the entrance to an indoor
holding spot. Pepper records a static sign-off at a marker. The team then
leaves the entrance path clear. The armchair and book pile stay where
``MODERN_HOUSE-GOAL-01`` starts them; this task removes their handling modes
so the witness cannot move them. There is no resident actor and no claim of
dialogue. Iris is not assigned.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from demo.benchmark.goal_driven_fixtures import _route


DELIVERY_TASK_ID = "MODERN_HOUSE-DELIVERY-01"
PARCEL_ID = "doorstep_parcel_01"
SIGN_OFF_KIND = "house_delivery_signoff:doorstep_parcel_01:delivery_signoff_marker"
FROZEN_OBJECTS = {
    "living_armchair_01": "furniture_side",
    "resident_bookstack_01": "living_area",
}


def _frozen(identifier: str, category: str, role: str) -> dict[str, Any]:
    from demo.benchmark.goal_driven_tasks import _object

    obj = _object(identifier, category, role, [
        "not_moved_by_delivery_task", "preserve_identity", "no_finger_grasp",
    ])
    obj["physical_properties"]["grasp_points"] = []
    obj["physical_properties"]["handling_modes"] = []
    return obj


def _rows(carrier: str, suffix: str) -> list[dict[str, Any]]:
    from demo.benchmark.goal_driven_tasks import _task_row
    from demo.benchmark.skill_ontology import required_capabilities

    pepper = "house_pepper_1"

    def row(name: str, skill: str, arguments: dict[str, Any],
            prerequisites: list[str], agents: list[str]) -> dict[str, Any]:
        return _task_row(f"{name}_{suffix}", skill, arguments, prerequisites,
                         sorted(required_capabilities(skill)), agents)

    placed = f"place_parcel_{suffix}"
    return [
        row("carrier_to_entry", "ground_navigation",
            {"region_id": "entry_hall", "route_ids": ["standby_a_to_entry"]}, [], [carrier]),
        row("pick_parcel", "pick",
            {"object_id": PARCEL_ID, "pickup_region_id": "entry_hall",
             "handling_mode_id": "support_attach",
             "carrier_bindings": [{"agent_id": carrier, "point_id": "support_center"}]},
            [f"carrier_to_entry_{suffix}"], [carrier]),
        row("carry_parcel", "ground_navigation",
            {"region_id": "indoor_holding", "route_ids": ["entry_to_holding"]},
            [f"pick_parcel_{suffix}"], [carrier]),
        row("place_parcel", "place",
            {"object_id": PARCEL_ID, "placement_region_id": "indoor_holding"},
            [f"carry_parcel_{suffix}"], [carrier]),
        row("pepper_to_signoff", "ground_navigation",
            {"region_id": "delivery_signoff", "route_ids": ["standby_b_to_signoff"]}, [], [pepper]),
        row("see_signoff", "observe",
            {"region_id": "delivery_signoff", "subject_ref": "delivery_signoff_marker",
             "observation_mode": "static_signoff"},
            [f"pepper_to_signoff_{suffix}", placed], [pepper]),
        row("record_signoff", "relay",
            {"message_kind": SIGN_OFF_KIND, "region_id": "delivery_signoff"},
            [f"see_signoff_{suffix}"], [pepper]),
        row("carrier_withdraw", "ground_navigation",
            {"region_id": "house_standby_a", "route_ids": ["holding_to_standby_a"]},
            [f"record_signoff_{suffix}"], [carrier]),
        row("pepper_withdraw", "ground_navigation",
            {"region_id": "house_standby_b", "route_ids": ["signoff_to_standby_b"]},
            [f"record_signoff_{suffix}"], [pepper]),
    ]


def _house_delivery_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from demo.benchmark.goal_driven_tasks import _object, _outcome, _plan, _region, _robot, _task

    robots = [
        _robot("house_iris_1", "iris", "aerial",
               ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("house_pepper_1", "pepper", "ground",
               ["ground_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("house_m20_1", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "manipulator", "event_bus"]),
        _robot("house_m20_2", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "manipulator", "event_bus"]),
    ]
    regions = [
        _region("room_overview", "room_overview", "房屋原有空中观察区；本送货见证不使用它", ["aerial_navigation"]),
        _region("entry_hall", "entry", "门口；包裹从这里被支撑吸附搬走", ["ground_navigation"]),
        _region("furniture_side", "work_area", "扶手椅停留的原位置；本任务不移动它", ["ground_navigation"]),
        _region("living_area", "living_space", "书堆停留的原位置；本任务不移动它", ["ground_navigation"]),
        _region("furniture_parking", "furniture_zone", "GOAL-01 的家具停放区；本任务不使用", ["ground_navigation"]),
        _region("keepsake_staging", "staging_area", "GOAL-01 的书堆暂存区；本任务不使用", ["ground_navigation"]),
        _region("resident_path", "circulation_path", "入口生活动线；送货队员最后不得停在这里", ["ground_navigation"]),
        _region("indoor_holding", "holding", "室内暂存点；包裹释放在这里", ["ground_navigation"]),
        _region("delivery_signoff", "signoff_point", "静态签收点；没有住户演员，Pepper 在标记处写下回执", ["ground_navigation"]),
        _region("house_standby_a", "standby", "不占用入口动线的第一待机区", ["ground_navigation"]),
        _region("house_standby_b", "standby", "不占用入口动线的第二待机区", ["ground_navigation"]),
    ]
    objects = [
        _object(PARCEL_ID, "parcel", "门口包裹，用支撑吸附搬到室内暂存点，不是手指抓紧",
                ["support_socket_attach_only", "no_finger_grasp", "preserve_identity"]),
        _object("delivery_signoff_marker", "static_marker",
                "静态签收标记。房屋包没有住户演员，Pepper 只对这个标记做签收，不对话。",
                ["stationary", "no_dialogue", "not_a_payload", "not_a_human"]),
        _frozen("living_armchair_01", "armchair", "GOAL-01 要恢复的扶手椅；本送货任务保持原位并且不能搬运"),
        _frozen("resident_bookstack_01", "bookstack", "GOAL-01 要暂存的书堆；本送货任务保持原位并且不能搬运"),
    ]
    public = _task(
        DELIVERY_TASK_ID, "modern_house_nvd",
        "地面机器人把门口包裹支撑搬运到室内暂存点，Pepper 在静态签收点写下回执，队员离开入口动线。",
        "这是上门投递，不是 MODERN_HOUSE-GOAL-01 的家具恢复。房屋包没有住户演员，签收是静态标记而不是对话。",
        [
            _outcome("parcel_released_indoors", "包裹已在室内暂存点释放。",
                     "只记包裹在 indoor_holding 的这一次释放。",
                     ["release_event", "object_pose"]),
            _outcome("delivery_signoff_recorded", "Pepper 已在静态签收点写下这一份回执。",
                     "只记这一份引用签收标记的回执。",
                     ["message_publication", "delivery_receipt"]),
            _outcome("carrier_left_delivery_path", "搬运包裹的队员已离开投递动线。",
                     "只记这一名搬运者最后停在待机区。",
                     ["robot_withdrawal_trace"]),
            _outcome("pepper_left_signoff_path", "Pepper 已离开签收动线。",
                     "只记 Pepper 最后停在待机区。",
                     ["robot_withdrawal_trace"]),
        ],
        robots, regions, objects, [],
        [
            "包裹搬运是 support_socket 支撑吸附，不是手指抓紧。",
            "Pepper 只到达静态签收点、观察标记并写下回执，不拿包裹。",
            "本任务没有住户演员。delivery_signoff_marker 不会说话，也不能被搬走。",
            "living_armchair_01 和 resident_bookstack_01 保持 GOAL-01 的起始区域，本任务去掉了它们的搬运接口。",
            "Iris 在候选池里只满足公开任务对空中能力的声明，两份作者见证都不分配它。",
            "本包不改变 MODERN_HOUSE-GOAL-01。release gate 保持 benchmark_ready 与 scoreable 为假。",
        ],
        "扶手椅仍从 furniture_side 开始，书堆仍从 living_area 开始，但本任务的对象副本没有抓取点。"
        "签收点是静态标记。场景绑定和真实吸附仍待 episode 验证。",
        "immediate", max_active_agents=3,
    )
    public["public_world"]["routes"].extend([
        _route("standby_a_to_entry", "house_standby_a", "entry_hall", 4.0, 1.2, resource="delivery_entry_lane"),
        _route("entry_to_holding", "entry_hall", "indoor_holding", 5.0, 1.2, resource="delivery_holding_lane"),
        _route("holding_to_standby_a", "indoor_holding", "house_standby_a", 4.0, 1.2, resource="delivery_carrier_exit"),
        _route("standby_b_to_signoff", "house_standby_b", "delivery_signoff", 3.0, 1.0, resource="delivery_signoff_lane"),
        _route("signoff_to_standby_b", "delivery_signoff", "house_standby_b", 3.0, 1.0, resource="delivery_pepper_exit"),
    ])
    outcome_ids = [item["id"] for item in public["required_outcomes"]]
    witnesses = []
    for carrier, suffix in (("house_m20_1", "a"), ("house_m20_2", "b")):
        rows = _rows(carrier, suffix)
        identifier = f"house_delivery_witness_{suffix}"
        witnesses.append({
            "id": identifier,
            "satisfies_outcomes": outcome_ids,
            "outcome_support": {
                "parcel_released_indoors": [f"place_parcel_{suffix}"],
                "delivery_signoff_recorded": [f"record_signoff_{suffix}"],
                "carrier_left_delivery_path": [f"carrier_withdraw_{suffix}"],
                "pepper_left_signoff_path": [f"pepper_withdraw_{suffix}"],
            },
            "plan": _plan(identifier, ["house_pepper_1", carrier], rows),
        })
    return public, witnesses


def review_house_delivery_witness(*, match: Callable[..., list[Mapping[str, Any]]],
                                  require: Callable[[Any, str], None],
                                  precedes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
                                  release_at: Callable[[str, str, str], list[Mapping[str, Any]]],
                                  final_object: Callable[[str, str], None],
                                  constraint_review: Mapping[str, Any],
                                  plan: Mapping[str, Any],
                                  robots: Mapping[str, Mapping[str, Any]],
                                  regions: Mapping[str, Mapping[str, Any]]) -> None:
    picks = match(skills={"pick"}, object_id=PARCEL_ID, handling_mode_id="support_attach",
                  pickup_region_id="entry_hall", outcome="parcel_released_indoors")
    require(picks, "house_delivery:support_attach_pickup")
    releases = release_at(PARCEL_ID, "indoor_holding", "parcel_released_indoors")
    require(releases, "house_delivery:parcel_released")
    final_object(PARCEL_ID, "indoor_holding")
    for object_id, region_id in FROZEN_OBJECTS.items():
        require(all(row["arguments"].get("object_id") != object_id for row in plan["tasks"]),
                f"house_delivery:{object_id}:not_transported")
        final_object(object_id, region_id)
    observations = match(skills={"observe"}, mobility="ground", region_id="delivery_signoff",
                         subject_ref="delivery_signoff_marker", observation_mode="static_signoff",
                         outcome="delivery_signoff_recorded")
    require(observations, "house_delivery:static_signoff_observation")
    require(all(robots[agent]["type"] == "pepper"
                for row in observations for agent in row["assigned_agents"]),
            "house_delivery:pepper_records_signoff")
    receipts = match(skills={"relay"}, message_kind=SIGN_OFF_KIND, region_id="delivery_signoff",
                     outcome="delivery_signoff_recorded")
    require(receipts, "house_delivery:signoff_receipt")
    require(all(any(precedes(release, receipt) for release in releases) for receipt in receipts)
            and all(any(precedes(observation, receipt)
                        for observation in observations) for receipt in receipts),
            "house_delivery:receipt_after_release_and_observation")
    require(match(skills={"ground_navigation"}, outcome="carrier_left_delivery_path"),
            "house_delivery:carrier_withdrawal_is_its_own_outcome")
    require(match(skills={"ground_navigation"}, outcome="pepper_left_signoff_path"),
            "house_delivery:pepper_withdrawal_is_its_own_outcome")
    for agent, final_region in constraint_review["robot_regions"].items():
        require(robots[agent]["mobility"] == "ground", f"house_delivery:{agent}:ground_team")
        require(final_region in regions and regions[final_region]["kind"] == "standby",
                f"house_delivery:{agent}:leaves_the_path")
        require(any(precedes(receipt, row) for receipt in receipts for row in plan["tasks"]
                    if agent in row["assigned_agents"] and row["skill"] == "ground_navigation"
                    and row["arguments"].get("region_id") == final_region),
                f"house_delivery:{agent}:withdrawal_after_signoff")


def build_house_delivery_package() -> dict[str, dict[str, Any]]:
    from demo.benchmark.second_mission_bundle import assemble_unscored_goal_bundle

    public, witnesses = _house_delivery_parts()
    return assemble_unscored_goal_bundle(public, witnesses, [
        "house_delivery_parcel_support_carry_episode_evidence",
        "house_delivery_static_signoff_episode_evidence",
        "house_delivery_path_clear_episode_evidence",
        "reliable_skill_backend_conformance",
        "scene_geometry_and_object_binding",
    ])
