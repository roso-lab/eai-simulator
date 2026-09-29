"""Factory fire-response mission beside the frozen inspection task.

This package names the existing ``demo/fire_rescue`` scene as a fire response:
Scout's UR5 strikes the channel button, Carter senses the fire, and one M20
carries the extinguisher to the fire on a fixed-joint support socket so the
ground team meets there. It does not rewrite the demo, and it is not another
inspection. ``FACTORY-GOAL-01`` and the task id stay unchanged.

The hidden evaluator still requires two semantically distinct author
witnesses. Both witnesses perform this same mission; they only swap which
M20 carries the extinguisher. Iris stays in the pool and is not assigned.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from demo.benchmark.goal_driven_fixtures import _route


FIRE_TASK_ID = "FACTORY-FIRE-01"
CHANNEL_ROUTE = "rescue_channel_to_fire"
BUTTON_MODE = "fixed_joint_button_strike"
SENSE_MODE = "fire_scene_sense"
EXTINGUISHER_ID = "factory_extinguisher_01"


def _parts(carrier: str) -> list[dict[str, Any]]:
    from demo.benchmark.goal_driven_tasks import _task_row
    from demo.benchmark.skill_ontology import required_capabilities

    scout, carter = "factory_scout_1", "factory_carter_1"
    suffix = "a" if carrier.endswith("_1") else "b"

    def row(name: str, skill: str, arguments: dict[str, Any],
            prerequisites: list[str], agents: list[str]) -> dict[str, Any]:
        return _task_row(f"{name}_{suffix}", skill, arguments, prerequisites,
                         sorted(required_capabilities(skill)), agents)

    strike = f"strike_button_{suffix}"
    return [
        row("nav_button", "ground_navigation",
            {"region_id": "rescue_button", "route_ids": ["staging_to_button"]}, [], [scout]),
        row("strike_button", "observe",
            {"region_id": "rescue_button", "subject_ref": "rescue_channel_button",
             "observation_mode": BUTTON_MODE}, [f"nav_button_{suffix}"], [scout]),
        row("nav_sense", "ground_navigation",
            {"region_id": "fire_site", "route_ids": ["staging_to_channel", CHANNEL_ROUTE]},
            [strike], [carter]),
        row("sense_fire", "observe",
            {"region_id": "fire_site", "subject_ref": "fire_site", "observation_mode": SENSE_MODE},
            [f"nav_sense_{suffix}"], [carter]),
        row("nav_cache", "ground_navigation",
            {"region_id": "extinguisher_cache", "route_ids": ["staging_to_cache"]},
            [strike], [carrier]),
        row("pick_extinguisher", "pick",
            {"object_id": EXTINGUISHER_ID, "pickup_region_id": "extinguisher_cache",
             "handling_mode_id": "support_attach",
             "carrier_bindings": [{"agent_id": carrier, "point_id": "support_center"}]},
            [f"nav_cache_{suffix}"], [carrier]),
        row("carry_extinguisher", "ground_navigation",
            {"region_id": "fire_site", "route_ids": ["cache_to_channel", CHANNEL_ROUTE]},
            [f"pick_extinguisher_{suffix}", strike], [carrier]),
        row("place_extinguisher", "place",
            {"object_id": EXTINGUISHER_ID, "placement_region_id": "fire_site"},
            [f"carry_extinguisher_{suffix}"], [carrier]),
        row("scout_to_fire", "ground_navigation",
            {"region_id": "fire_site", "route_ids": ["button_to_channel", CHANNEL_ROUTE]},
            [strike], [scout]),
    ]


def _factory_fire_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from demo.benchmark.goal_driven_tasks import _object, _outcome, _plan, _region, _robot, _task

    robots = [
        _robot("factory_iris_1", "iris", "aerial",
               ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("factory_m20_1", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "manipulator", "event_bus"]),
        _robot("factory_m20_2", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "relay"],
               ["camera", "manipulator", "event_bus"]),
        _robot("factory_carter_1", "carter", "ground",
               ["ground_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("factory_scout_1", "scout", "ground",
               ["ground_navigation", "observe", "relay"],
               ["camera", "manipulator", "event_bus"]),
    ]
    regions = [
        _region("fire_overhead", "overhead", "火场上空观察区；两份火灾响应见证都不分配 Iris", ["aerial_navigation"]),
        _region("fire_staging", "staging_area", "火灾响应的地面出发区", ["ground_navigation"]),
        _region("rescue_button", "control_point", "通道按钮；只由 Scout 背部 UR5 做固定关节敲击", ["ground_navigation"]),
        _region("rescue_channel", "corridor", "按钮打开后才能进入火场的通道", ["ground_navigation"]),
        _region("extinguisher_cache", "cache", "灭火器的取用点", ["ground_navigation"]),
        _region("fire_site", "hazard", "火源附近；灭火器在此释放，地面队员在此会合", ["ground_navigation"]),
    ]
    objects = [
        _object("rescue_channel_button", "wall_button",
                "通道按钮。Scout 用背部 UR5 做固定关节敲击，按钮本身不被搬走。",
                ["stationary", "scout_ur5_fixed_joint_strike_only", "not_a_payload"]),
        _object(EXTINGUISHER_ID, "extinguisher",
                "灭火器。由一台 M20 的 UR5 以 support_socket 固定关节吸附搬到火源，不是手指抓紧。",
                ["support_socket_fixed_joint_only", "no_finger_grasp", "preserve_identity"]),
    ]
    public = _task(
        FIRE_TASK_ID, "factory",
        "打开通向火场的通道，感知火场，把灭火器送到火源，并让地面队员在火源附近会合。",
        "这是工厂火灾响应，不是 FACTORY-GOAL-01 的生产巡检。"
        "来源场景是 demo/fire_rescue。Scout 敲按钮，Carter 感知，一台 M20 运送灭火器；另一台 M20 只作为替换搬运者。",
        [
            _outcome("rescue_channel_opened", "通道按钮已被 Scout 的 UR5 敲开。",
                     "只记这一次固定关节敲击观察。",
                     ["observation_digest"]),
            _outcome("extinguisher_at_fire", "灭火器已在火源区域释放。",
                     "只记灭火器在 fire_site 的这一次释放。",
                     ["release_event", "object_pose"]),
            _outcome("fire_scene_sensed", "Carter 已在火源留下现场感知。",
                     "感知只记这一次火源观察，不表示队员已经会合。",
                     ["observation_digest"]),
            _outcome("scout_at_fire_site", "Scout 最后停在火源。",
                     "只记 Scout 的最终位置。",
                     ["measured_arrival"]),
            _outcome("carter_at_fire_site", "Carter 最后停在火源。",
                     "只记 Carter 的最终位置，不含它的感知记录。",
                     ["measured_arrival"]),
            _outcome("extinguisher_carrier_at_fire_site", "运送灭火器的地面机器人最后停在火源。",
                     "只记这一名搬运者的最终位置。",
                     ["measured_arrival"]),
        ],
        robots, regions, objects, [],
        [
            "任务来源是 demo/fire_rescue 的场景说明和 unified_mission_success：通道按钮、灭火器到位、至少三台地面机器人在火源附近。",
            "通道按钮只由 factory_scout_1 执行。敲击是背部 UR5 的固定关节接触，不是夹取，按钮也不被搬走。",
            "灭火器由一台 M20 的 UR5 以 support_socket 固定关节吸附搬运。这不是手指抓紧，也不是机械臂姿态规划。",
            "Carter 只负责到达火场并留下感知，不敲按钮，也不拿灭火器。",
            "Iris 在候选池里只满足公开任务对空中能力的声明。两份作者见证都不分配它。",
            "本包不改变 FACTORY-GOAL-01，任务编号仍是 FACTORY-FIRE-01。release gate 保持 benchmark_ready 与 scoreable 为假，直到火灾响应回合证据存在。",
        ],
        "按钮、灭火器和会合条件对应 demo/fire_rescue/scenario.py 与 runtime/mission_success.py。"
        "这里没有把巡检工位、楼梯或维护交接写成目标。场景绑定和真实吸附仍待 episode 验证。",
        "immediate", max_active_agents=3,
    )
    public["public_world"]["routes"] = [
        _route("staging_to_button", "fire_staging", "rescue_button", 6.0, 1.2, resource="button_lane"),
        _route("staging_to_cache", "fire_staging", "extinguisher_cache", 6.0, 1.2, resource="cache_lane"),
        _route("staging_to_channel", "fire_staging", "rescue_channel", 5.0, 1.4, resource="staging_channel_lane"),
        _route("button_to_channel", "rescue_button", "rescue_channel", 3.0, 1.2, resource="button_channel_lane"),
        _route("cache_to_channel", "extinguisher_cache", "rescue_channel", 4.0, 1.4, resource="cache_channel_lane"),
        _route(CHANNEL_ROUTE, "rescue_channel", "fire_site", 5.0, 1.4, resource="opened_rescue_channel"),
    ]
    outcome_ids = [item["id"] for item in public["required_outcomes"]]
    witnesses = []
    for carrier, suffix in (("factory_m20_1", "a"), ("factory_m20_2", "b")):
        rows = _parts(carrier)
        identifier = f"factory_fire_witness_{suffix}"
        witnesses.append({
            "id": identifier,
            "satisfies_outcomes": outcome_ids,
            "outcome_support": {
                "rescue_channel_opened": [f"strike_button_{suffix}"],
                "extinguisher_at_fire": [f"place_extinguisher_{suffix}"],
                "fire_scene_sensed": [f"sense_fire_{suffix}"],
                "scout_at_fire_site": [f"scout_to_fire_{suffix}"],
                "carter_at_fire_site": [f"nav_sense_{suffix}"],
                "extinguisher_carrier_at_fire_site": [f"place_extinguisher_{suffix}"],
            },
            "plan": _plan(identifier, ["factory_scout_1", "factory_carter_1", carrier], rows),
        })
    return public, witnesses


def review_factory_fire_witness(*, match: Callable[..., list[Mapping[str, Any]]],
                                require: Callable[[Any, str], None],
                                precedes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
                                release_at: Callable[[str, str, str], list[Mapping[str, Any]]],
                                final_object: Callable[[str, str], None],
                                constraint_review: Mapping[str, Any],
                                plan: Mapping[str, Any],
                                robots: Mapping[str, Mapping[str, Any]]) -> None:
    strikes = match(skills={"observe"}, mobility="ground", region_id="rescue_button",
                    subject_ref="rescue_channel_button", observation_mode=BUTTON_MODE,
                    outcome="rescue_channel_opened")
    require(strikes, "factory_fire:button_strike")
    require(all(robots[agent]["type"] == "scout"
                for strike in strikes for agent in strike["assigned_agents"]),
            "factory_fire:scout_strikes_button")
    arrivals = match(skills={"ground_navigation"}, mobility="ground", region_id="rescue_button",
                     outcome="rescue_channel_opened")
    require(any(precedes(arrival, strike)
                and set(arrival["assigned_agents"]) & set(strike["assigned_agents"])
                for arrival in arrivals for strike in strikes),
            "factory_fire:strike_after_scout_arrival")
    channel_users = [row for row in plan["tasks"]
                     if CHANNEL_ROUTE in row["arguments"].get("route_ids", [])]
    require(channel_users, "factory_fire:rescue_channel_used")
    require(all(any(precedes(strike, row) for strike in strikes) for row in channel_users),
            "factory_fire:channel_after_button")
    picks = match(skills={"pick"}, object_id=EXTINGUISHER_ID, outcome="extinguisher_at_fire")
    require(picks and all(row["arguments"].get("handling_mode_id") == "support_attach"
                          for row in picks),
            "factory_fire:fixed_joint_support_attach")
    releases = release_at(EXTINGUISHER_ID, "fire_site", "extinguisher_at_fire")
    require(releases, "factory_fire:extinguisher_released_at_fire")
    require(all(any(precedes(strike, release) for strike in strikes) for release in releases),
            "factory_fire:release_after_channel_opens")
    final_object(EXTINGUISHER_ID, "fire_site")
    senses = match(skills={"observe"}, mobility="ground", region_id="fire_site",
                   subject_ref="fire_site", observation_mode=SENSE_MODE,
                   outcome="fire_scene_sensed")
    require(senses and all(robots[agent]["type"] == "carter"
                           for sense in senses for agent in sense["assigned_agents"]),
            "factory_fire:carter_senses_fire")
    assigned = plan["assigned_team"]
    require(len(assigned) >= 3, "factory_fire:at_least_three_ground_robots")
    require(all(robots[agent]["mobility"] == "ground" for agent in assigned),
            "factory_fire:meeting_team_is_ground")
    for agent in assigned:
        require(constraint_review["robot_regions"].get(agent) == "fire_site",
                f"factory_fire:{agent}:meets_at_fire")
    require(match(skills={"ground_navigation"}, region_id="fire_site", outcome="scout_at_fire_site"),
            "factory_fire:scout_arrival_is_its_own_outcome")
    require(match(skills={"ground_navigation"}, region_id="fire_site", outcome="carter_at_fire_site"),
            "factory_fire:carter_arrival_is_its_own_outcome")
    require(match(skills={"place"}, object_id=EXTINGUISHER_ID, outcome="extinguisher_carrier_at_fire_site"),
            "factory_fire:carrier_arrival_is_its_own_outcome")


def build_factory_fire_package() -> dict[str, dict[str, Any]]:
    from demo.benchmark.second_mission_bundle import assemble_unscored_goal_bundle

    public, witnesses = _factory_fire_parts()
    return assemble_unscored_goal_bundle(public, witnesses, [
        "factory_fire_rescue_channel_button_episode_evidence",
        "factory_fire_extinguisher_fixed_joint_carry_episode_evidence",
        "factory_fire_sensing_and_team_meeting_episode_evidence",
        "reliable_skill_backend_conformance",
        "scene_geometry_and_object_binding",
    ])
