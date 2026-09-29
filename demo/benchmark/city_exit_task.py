"""Event or school dismissal beside the frozen accident-response task.

A known person roster is guided to one assembly area, and a ground robot
keeps a continuous corridor clear through to the station. The roster ids are
the existing city person ids. Their accident-response assembly split is not
used. No manipulator is required, and Iris is not assigned. This package
does not add an ``algorithm.city_traffic`` API.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from demo.benchmark.goal_driven_fixtures import _route


EXIT_TASK_ID = "CITY-EXIT-01"
CORRIDOR_ROUTE = "station_corridor_lane"
ROSTER_MODE = "dismissal_roster"
HEADCOUNT_MODE = "dismissal_headcount"
CORRIDOR_MODE = "corridor_clearance"


def dismissal_roster() -> tuple[str, ...]:
    from demo.benchmark.goal_driven_tasks import _CITY_AFFECTED_PERSON_ASSEMBLY

    return tuple(sorted(_CITY_AFFECTED_PERSON_ASSEMBLY))


def _rows(guide: str, corridor: str, suffix: str) -> list[dict[str, Any]]:
    from demo.benchmark.goal_driven_tasks import _task_row
    from demo.benchmark.skill_ontology import required_capabilities

    def row(name: str, skill: str, arguments: dict[str, Any],
            prerequisites: list[str], agents: list[str]) -> dict[str, Any]:
        return _task_row(f"{name}_{suffix}", skill, arguments, prerequisites,
                         sorted(required_capabilities(skill)), agents)

    survey = f"survey_roster_{suffix}"
    guided = f"guide_roster_{suffix}"
    return [
        row("guide_to_gathering", "ground_navigation",
            {"region_id": "dismissal_gathering", "route_ids": ["staging_to_gathering"]}, [], [guide]),
        row("survey_roster", "observe",
            {"region_id": "dismissal_gathering", "subject_ref": "dismissal_gathering",
             "observation_mode": ROSTER_MODE},
            [f"guide_to_gathering_{suffix}"], [guide]),
        row("guide_roster", "guide_people",
            {"person_ids": list(dismissal_roster()), "observation_refs": [survey],
             "assembly_region_id": "dismissal_assembly"},
            [survey], [guide]),
        row("guide_to_assembly", "ground_navigation",
            {"region_id": "dismissal_assembly", "route_ids": ["gathering_to_assembly"]},
            [guided], [guide]),
        row("verify_headcount", "observe",
            {"region_id": "dismissal_assembly", "subject_ref": "dismissal_assembly",
             "observation_mode": HEADCOUNT_MODE},
            [f"guide_to_assembly_{suffix}"], [guide]),
        row("corridor_to_station", "ground_navigation",
            {"region_id": "station_apron",
             "route_ids": ["staging_to_corridor", CORRIDOR_ROUTE]},
            [guided], [corridor]),
        row("check_corridor", "observe",
            {"region_id": "station_apron", "subject_ref": "station_corridor",
             "observation_mode": CORRIDOR_MODE},
            [f"corridor_to_station_{suffix}"], [corridor]),
    ]


def _city_exit_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from demo.benchmark.goal_driven_tasks import _CITY_CORE_SPAWN_XY, _outcome, _plan, _region, _robot, _task

    robots = [
        _robot("go2_1", "go2", "ground",
               ["ground_navigation", "observe", "relay", "guide_people"],
               ["velocity_command", "event_bus", "camera"]),
        _robot("carter_1", "carter", "ground",
               ["ground_navigation", "observe", "relay", "guide_people"],
               ["velocity_command", "event_bus", "camera"]),
        _robot("iris_1", "iris", "aerial",
               ["aerial_navigation", "observe", "relay"],
               ["camera", "event_bus"]),
    ]
    regions = [
        _region("exit_overhead", "overhead", "散场上空观察区；本见证不分配 Iris", ["aerial_navigation"]),
        _region("exit_staging", "response_staging", "散场任务的地面出发区", ["ground_navigation"]),
        _region("dismissal_gathering", "gathering", "已知人员名册的集合前位置", ["ground_navigation"]),
        _region("dismissal_assembly", "assembly_candidate", "散场集合区；名册上的人全部引导到这里", ["ground_navigation"]),
        _region("station_corridor", "corridor", "通向车站且必须保持连续清空的走廊", ["ground_navigation"]),
        _region("station_apron", "station", "车站站台侧；走廊检查在到达这里之后记录", ["ground_navigation"]),
    ]
    public = _task(
        EXIT_TASK_ID, "city",
        "把已知人员名册引导到一个集合区，并保持一条通向车站的连续清空走廊。",
        "这是活动或学校散场，不是 CITY-GOAL-01 的事故响应。人员编号沿用城市基准名册，但不使用事故任务里的南北集合区分工。",
        [
            _outcome("dismissal_roster_observed", "散场名册已在集合前位置被观察。",
                     "只记这一次名册观察，不表示任何人已经到达集合区。",
                     ["observation_provenance"]),
            *[
                _outcome(f"{person_id}_at_dismissal_assembly",
                         f"{person_id} 已到达散场集合区。",
                         "只记这一个人的到达，且只计一次。",
                         ["guidance_acknowledgement", "assembly_arrival"])
                for person_id in dismissal_roster()
            ],
            _outcome("dismissal_headcount_reconciled",
                     "全部名册人员到达集合区之后，人数已经核对。",
                     "只记这一次最终人数核销。任何一个人的到达都不能代替这次核对。",
                     ["headcount_record"]),
            _outcome("station_corridor_clear", "通向车站的走廊保持连续清空。",
                     "只记这一条走廊的连续清空，不能用单点到达代替。",
                     ["continuous_access_path"]),
        ],
        robots, regions, [], [],
        [
            "名册是城市基准里已有的 affected_person_00 到 affected_person_03。本任务把他们全部引导到 dismissal_assembly。",
            "这不是事故响应，不要求事故评估、后续交通管制或事故救援报告。",
            "引导使用现有 guide_people。本包不新增 algorithm.city_traffic 接口。",
            "不需要机械臂。两份作者见证都不分配 Iris。",
            "本包不改变 CITY-GOAL-01。release gate 保持 benchmark_ready 与 scoreable 为假，直到散场回合证据存在。",
        ],
        "人员编号来自目标任务里冻结的城市名册。集合区改成单一散场集合区，走廊单独通向车站。"
        "出生锚点沿用城市核心候选的已有坐标。真实人员跟随和走廊净空仍待 episode 验证。",
        "immediate", max_active_agents=2, robot_spawn_positions=_CITY_CORE_SPAWN_XY,
    )
    public["public_world"]["routes"] = [
        _route("staging_to_gathering", "exit_staging", "dismissal_gathering", 6.0, 1.4, resource="dismissal_approach"),
        _route("gathering_to_assembly", "dismissal_gathering", "dismissal_assembly", 4.0, 1.4, resource="dismissal_assembly_lane"),
        _route("staging_to_corridor", "exit_staging", "station_corridor", 5.0, 1.4, resource="station_approach"),
        _route(CORRIDOR_ROUTE, "station_corridor", "station_apron", 8.0, 1.4, resource="station_corridor"),
    ]
    outcome_ids = [item["id"] for item in public["required_outcomes"]]
    witnesses = []
    for guide, corridor, suffix in (("go2_1", "carter_1", "a"), ("carter_1", "go2_1", "b")):
        rows = _rows(guide, corridor, suffix)
        identifier = f"city_exit_witness_{suffix}"
        witnesses.append({
            "id": identifier,
            "satisfies_outcomes": outcome_ids,
            "outcome_support": {
                "dismissal_roster_observed": [f"survey_roster_{suffix}"],
                **{f"{person_id}_at_dismissal_assembly": [f"guide_roster_{suffix}"]
                   for person_id in dismissal_roster()},
                "dismissal_headcount_reconciled": [f"verify_headcount_{suffix}"],
                "station_corridor_clear": [f"check_corridor_{suffix}"],
            },
            "plan": _plan(identifier, [guide, corridor], rows),
        })
    return public, witnesses


def review_city_exit_witness(*, match: Callable[..., list[Mapping[str, Any]]],
                             require: Callable[[Any, str], None],
                             precedes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
                             constraint_review: Mapping[str, Any],
                             plan: Mapping[str, Any],
                             robots: Mapping[str, Mapping[str, Any]],
                             regions: Mapping[str, Mapping[str, Any]]) -> None:
    del constraint_review
    roster = set(dismissal_roster())
    surveys = match(skills={"observe"}, mobility="ground", region_id="dismissal_gathering",
                    subject_ref="dismissal_gathering", observation_mode=ROSTER_MODE,
                    outcome="dismissal_roster_observed")
    require(surveys, "city_exit:roster_observation")
    guided: dict[str, Mapping[str, Any]] = {}
    guides = []
    seen_guides: set[str] = set()
    for person_id in roster:
        person_guides = match(skills={"guide_people"},
                              outcome=f"{person_id}_at_dismissal_assembly")
        require(any(person_id in row["arguments"]["person_ids"] for row in person_guides),
                f"city_exit:{person_id}:own_arrival")
        for row in person_guides:
            if row["task_id"] not in seen_guides:
                seen_guides.add(row["task_id"])
                guides.append(row)
    require(guides, "city_exit:guide_people_present")
    survey_ids = {row["task_id"] for row in surveys}
    for guide in guides:
        assembly = guide["arguments"]["assembly_region_id"]
        require(assembly in regions and regions[assembly]["kind"] == "assembly_candidate",
                "city_exit:assembly_candidate")
        require(assembly == "dismissal_assembly", "city_exit:single_dismissal_assembly")
        references = set(guide["arguments"]["observation_refs"])
        require(references and references <= survey_ids, "city_exit:guide_cites_roster_observation")
        require(any(precedes(survey, guide) for survey in surveys),
                "city_exit:guide_after_roster_observation")
        for person_id in guide["arguments"]["person_ids"]:
            require(person_id in roster, "city_exit:known_roster_only")
            require(person_id not in guided, "city_exit:each_person_guided_once")
            guided[person_id] = guide
    require(set(guided) == roster, "city_exit:full_roster_assembled")
    counts = match(skills={"observe"}, mobility="ground", region_id="dismissal_assembly",
                   subject_ref="dismissal_assembly", observation_mode=HEADCOUNT_MODE,
                   outcome="dismissal_headcount_reconciled")
    require(counts, "city_exit:headcount_reconciled")
    for count in counts:
        require(all(precedes(guided[person_id], count) for person_id in roster),
                "city_exit:headcount_after_each_arrival")
    traversals = [row for row in plan["tasks"]
                  if row["skill"] == "ground_navigation"
                  and CORRIDOR_ROUTE in row["arguments"].get("route_ids", [])]
    require(traversals, "city_exit:corridor_traversed")
    checks = match(skills={"observe"}, mobility="ground", region_id="station_apron",
                   subject_ref="station_corridor", observation_mode=CORRIDOR_MODE,
                   outcome="station_corridor_clear")
    require(checks, "city_exit:corridor_clearance")
    require(all(any(precedes(guide, check) for guide in guides) for check in checks),
            "city_exit:corridor_after_guidance")
    require(all(any(precedes(traversal, check) for traversal in traversals) for check in checks),
            "city_exit:clearance_after_traversal")
    require(all(robots[agent]["mobility"] == "ground" for agent in plan["assigned_team"]),
            "city_exit:ground_team")
    require(all("manipulator" not in robots[agent]["interfaces"] for agent in plan["assigned_team"]),
            "city_exit:no_manipulator")
    require(all(row["skill"] not in {"pick", "place", "handoff", "cooperative_carry"}
                for row in plan["tasks"]),
            "city_exit:no_manipulation_skill")


def build_city_exit_package() -> dict[str, dict[str, Any]]:
    from demo.benchmark.second_mission_bundle import assemble_unscored_goal_bundle

    public, witnesses = _city_exit_parts()
    return assemble_unscored_goal_bundle(public, witnesses, [
        "city_exit_roster_guidance_episode_evidence",
        "city_exit_station_corridor_episode_evidence",
        "reliable_skill_backend_conformance",
        "scene_geometry_and_object_binding",
    ])
