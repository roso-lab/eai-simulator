"""Warehouse inbound receiving beside the frozen outbound fulfilment task.

Goods arrive at the receiving buffer, are support-carried onto the existing
racks, and the buffer is then closed. This is the opposite direction from
``WAREHOUSE-GOAL-01``. The crate and carton ids are the ones that package
already declares; only this task's copy starts them at the buffer. Carry
uses the declared support socket and the carton's declared grip. It does
not claim finger grasp. Iris is not assigned.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from demo.benchmark.goal_driven_fixtures import _route


INBOUND_TASK_ID = "WAREHOUSE-INBOUND-01"
CRATE_ID = "order_crate_01"
CARTON_ID = "order_carton_01"
ARRIVAL_MODE = "inbound_arrival"
CLOSEOUT_MODE = "receiving_closeout"


def _retarget(identifier: str, category: str, role: str, constraints: list[str],
              region_id: str) -> dict[str, Any]:
    from demo.benchmark.goal_driven_tasks import _object

    obj = _object(identifier, category, role, constraints)
    obj["physical_properties"]["initial_region_id"] = region_id
    return obj


def _rows(picker: str, partner: str, suffix: str) -> list[dict[str, Any]]:
    from demo.benchmark.goal_driven_tasks import _task_row
    from demo.benchmark.skill_ontology import required_capabilities

    carter = "warehouse_carter_1"
    bindings = [{"agent_id": picker, "point_id": "support_left"},
                {"agent_id": partner, "point_id": "support_right"}]
    single = [{"agent_id": picker, "point_id": "center_grip"}]

    def row(name: str, skill: str, arguments: dict[str, Any],
            prerequisites: list[str], agents: list[str]) -> dict[str, Any]:
        return _task_row(f"{name}_{suffix}", skill, arguments, prerequisites,
                         sorted(required_capabilities(skill)), agents)

    identified = [f"see_crate_{suffix}", f"see_carton_{suffix}"]
    return [
        row("carter_to_buffer", "ground_navigation",
            {"region_id": "receiving_buffer", "route_ids": ["standby_a_to_buffer"]}, [], [carter]),
        row("see_crate", "observe",
            {"region_id": "receiving_buffer", "subject_ref": CRATE_ID, "observation_mode": ARRIVAL_MODE},
            [f"carter_to_buffer_{suffix}"], [carter]),
        row("see_carton", "observe",
            {"region_id": "receiving_buffer", "subject_ref": CARTON_ID, "observation_mode": ARRIVAL_MODE},
            [f"see_crate_{suffix}"], [carter]),
        row("picker_to_buffer", "ground_navigation",
            {"region_id": "receiving_buffer", "route_ids": ["standby_a_to_buffer"]},
            identified, [picker]),
        row("partner_to_buffer", "ground_navigation",
            {"region_id": "receiving_buffer", "route_ids": ["standby_b_to_buffer"]},
            identified, [partner]),
        row("shelve_crate", "cooperative_carry",
            {"object_id": CRATE_ID, "destination_region_id": "rack_aisle_a",
             "handling_mode_id": "paired_support", "carrier_bindings": bindings,
             "route_ids": ["buffer_to_rack_a"]},
            [f"picker_to_buffer_{suffix}", f"partner_to_buffer_{suffix}"], [picker, partner]),
        row("picker_back_to_buffer", "ground_navigation",
            {"region_id": "receiving_buffer", "route_ids": ["rack_a_to_buffer"]},
            [f"shelve_crate_{suffix}"], [picker]),
        row("pick_carton", "pick",
            {"object_id": CARTON_ID, "pickup_region_id": "receiving_buffer",
             "handling_mode_id": "single_grip", "carrier_bindings": single},
            [f"picker_back_to_buffer_{suffix}"], [picker]),
        row("shelve_carton", "ground_navigation",
            {"region_id": "rack_aisle_b", "route_ids": ["buffer_to_rack_b"]},
            [f"pick_carton_{suffix}"], [picker]),
        row("place_carton", "place",
            {"object_id": CARTON_ID, "placement_region_id": "rack_aisle_b"},
            [f"shelve_carton_{suffix}"], [picker]),
        row("partner_withdraw", "ground_navigation",
            {"region_id": "warehouse_standby_b", "route_ids": ["rack_a_to_standby_b"]},
            [f"shelve_crate_{suffix}"], [partner]),
        row("picker_withdraw", "ground_navigation",
            {"region_id": "warehouse_standby_a", "route_ids": ["rack_b_to_standby_a"]},
            [f"place_carton_{suffix}"], [picker]),
        row("close_buffer", "observe",
            {"region_id": "receiving_buffer", "subject_ref": "receiving_buffer",
             "observation_mode": CLOSEOUT_MODE},
            [f"partner_withdraw_{suffix}", f"picker_withdraw_{suffix}"], [carter]),
        row("carter_withdraw", "ground_navigation",
            {"region_id": "warehouse_standby_a", "route_ids": ["buffer_to_standby_a"]},
            [f"close_buffer_{suffix}"], [carter]),
    ]


def _warehouse_inbound_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from demo.benchmark.goal_driven_tasks import _outcome, _plan, _region, _robot, _task

    robots = [
        _robot("warehouse_iris_1", "iris", "aerial",
               ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("warehouse_m20_1", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "cooperative_carry", "synchronize"],
               ["camera", "manipulator", "cooperative_transport", "event_bus"]),
        _robot("warehouse_m20_2", "m20", "ground",
               ["ground_navigation", "observe", "pick", "place", "cooperative_carry", "synchronize"],
               ["camera", "manipulator", "cooperative_transport", "event_bus"]),
        _robot("warehouse_carter_1", "carter", "ground",
               ["ground_navigation", "observe", "relay"], ["camera", "event_bus"]),
    ]
    regions = [
        _region("rack_overview", "inventory_overview", "仓库原有的空中观察区；本入库任务的见证不使用它", ["aerial_navigation"]),
        _region("rack_aisle_a", "rack_aisle", "入库木箱要放到的货架通道", ["ground_navigation"]),
        _region("rack_aisle_b", "rack_aisle", "入库纸箱要放到的货架通道", ["ground_navigation"]),
        _region("consolidation_area", "consolidation", "仓库地图上的集货区；入库见证不经过这里", ["ground_navigation"]),
        _region("outbound_dock", "dock", "出库 dock；本任务不向这里出货", ["ground_navigation"]),
        _region("receiving_buffer", "handoff_area", "到货接收缓冲区；木箱和纸箱从这里入库", ["ground_navigation"]),
        _region("warehouse_standby_a", "standby", "接收关闭后的第一地面待机区", ["ground_navigation"]),
        _region("warehouse_standby_b", "standby", "接收关闭后的第二地面待机区", ["ground_navigation"]),
    ]
    objects = [
        _retarget(CRATE_ID, "wooden_crate",
                  "到货木箱，与 WAREHOUSE-GOAL-01 声明的是同一对象；本任务从接收缓冲区上架",
                  ["preserve_identity", "respect_per_carrier_payload", "use_declared_support_points",
                   "support_socket_only", "no_finger_grasp"],
                  "receiving_buffer"),
        _retarget(CARTON_ID, "cardboard_carton",
                  "到货纸箱，与 WAREHOUSE-GOAL-01 声明的是同一对象；使用该对象已声明的握点，不声称手指抓紧",
                  ["preserve_identity", "count_once", "declared_grip_only", "no_finger_grasp"],
                  "receiving_buffer"),
    ]
    public = _task(
        INBOUND_TASK_ID, "warehouse_nvd",
        "确认接收缓冲区的到货，把木箱和纸箱支撑搬运上架，并关闭接收缓冲区。",
        "这是入库，方向与 WAREHOUSE-GOAL-01 的出库履约相反。对象仍是 order_crate_01 和 order_carton_01。",
        [
            _outcome("crate_arrival_identified", "接收缓冲区的木箱到货已被识别。",
                     "只记木箱这一次到货观察。",
                     ["observation_digest", "object_identity"]),
            _outcome("carton_arrival_identified", "接收缓冲区的纸箱到货已被识别。",
                     "只记纸箱这一次到货观察。",
                     ["observation_digest", "object_identity"]),
            _outcome("crate_shelved_on_aisle_a", "木箱已放到货架通道 rack_aisle_a。",
                     "只记木箱的成对支撑释放。",
                     ["release_event", "object_pose"]),
            _outcome("carton_shelved_on_aisle_b", "纸箱已放到货架通道 rack_aisle_b。",
                     "只记纸箱按已声明握点的释放。",
                     ["release_event", "object_pose"]),
            _outcome("receiving_buffer_closed", "接收缓冲区已有关闭观察。",
                     "只记缓冲区这一次关闭观察，不含队员离场。",
                     ["post_receipt_scan"]),
            _outcome("inbound_team_withdrawn",
                     "入库地面队员退出作业区并进入可追溯待机状态。",
                     "最终复核分别记录取货队员、配合队员和关闭观察队员的待机位置；这三处位置只组成一次集体离场。",
                     ["picker_final_position", "partner_final_position", "observer_final_position"]),
        ],
        robots, regions, objects, [],
        [
            "木箱只用已声明的 paired_support / support_socket。纸箱只用已声明的 center_grip，不把该握点说成手指抓紧。",
            "Carter 只识别到货并做关闭观察，不参与木箱的高位支撑吸附。",
            "Iris 在候选池里只满足公开任务对空中能力的声明，两份作者见证都不分配它。",
            "本包不改变 WAREHOUSE-GOAL-01 的出库起点。那份任务里木箱仍从 rack_aisle_a 出发，纸箱仍从 rack_aisle_b 出发。",
            "release gate 保持 benchmark_ready 与 scoreable 为假，直到入库回合证据存在。",
        ],
        "入库副本把两件货物的起点改到 receiving_buffer。这只存在于本任务对象副本，不改共享物体表，因此不改出库任务哈希。",
        "immediate", max_active_agents=3,
        robot_spawn_positions={
            "warehouse_iris_1": (-13.5, -11.75, 1.0),
            "warehouse_m20_1": (-9.0, -11.75, 0.52),
            "warehouse_m20_2": (-8.0, -11.75, 0.52),
            "warehouse_carter_1": (-3.575, -11.75, 0.2),
        },
    )
    public["public_world"]["routes"].extend([
        _route("standby_a_to_buffer", "warehouse_standby_a", "receiving_buffer", 4.0, 1.2, resource="inbound_approach_a"),
        _route("standby_b_to_buffer", "warehouse_standby_b", "receiving_buffer", 4.0, 1.2, resource="inbound_approach_b"),
        _route("buffer_to_rack_a", "receiving_buffer", "rack_aisle_a", 7.0, 1.85, resource="inbound_crate_lane"),
        _route("rack_a_to_buffer", "rack_aisle_a", "receiving_buffer", 7.0, 1.85, resource="inbound_crate_return"),
        _route("buffer_to_rack_b", "receiving_buffer", "rack_aisle_b", 5.0, 1.2, resource="inbound_carton_lane"),
        _route("rack_a_to_standby_b", "rack_aisle_a", "warehouse_standby_b", 4.0, 1.2, resource="inbound_partner_exit"),
        _route("rack_b_to_standby_a", "rack_aisle_b", "warehouse_standby_a", 4.0, 1.2, resource="inbound_picker_exit"),
        _route("buffer_to_standby_a", "receiving_buffer", "warehouse_standby_a", 4.0, 1.2, resource="inbound_closeout_exit"),
    ])
    outcome_ids = [item["id"] for item in public["required_outcomes"]]
    witnesses = []
    for picker, partner, suffix in (
            ("warehouse_m20_1", "warehouse_m20_2", "a"),
            ("warehouse_m20_2", "warehouse_m20_1", "b")):
        rows = _rows(picker, partner, suffix)
        identifier = f"warehouse_inbound_witness_{suffix}"
        witnesses.append({
            "id": identifier,
            "satisfies_outcomes": outcome_ids,
            "outcome_support": {
                "crate_arrival_identified": [f"see_crate_{suffix}"],
                "carton_arrival_identified": [f"see_carton_{suffix}"],
                "crate_shelved_on_aisle_a": [f"shelve_crate_{suffix}"],
                "carton_shelved_on_aisle_b": [f"place_carton_{suffix}"],
                "receiving_buffer_closed": [f"close_buffer_{suffix}"],
                "inbound_team_withdrawn": [
                    f"picker_withdraw_{suffix}",
                    f"partner_withdraw_{suffix}",
                    f"carter_withdraw_{suffix}",
                ],
            },
            "plan": _plan(identifier, ["warehouse_carter_1", picker, partner], rows),
        })
    return public, witnesses


def review_warehouse_inbound_witness(*, match: Callable[..., list[Mapping[str, Any]]],
                                     require: Callable[[Any, str], None],
                                     precedes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
                                     release_at: Callable[[str, str, str], list[Mapping[str, Any]]],
                                     final_object: Callable[[str, str], None],
                                     constraint_review: Mapping[str, Any],
                                     plan: Mapping[str, Any],
                                     robots: Mapping[str, Mapping[str, Any]],
                                     regions: Mapping[str, Mapping[str, Any]]) -> None:
    scans = []
    for object_id in (CRATE_ID, CARTON_ID):
        arrival_outcome = ("crate_arrival_identified" if object_id == CRATE_ID
                           else "carton_arrival_identified")
        found = match(skills={"observe"}, mobility="ground", region_id="receiving_buffer",
                      subject_ref=object_id, observation_mode=ARRIVAL_MODE,
                      outcome=arrival_outcome)
        require(found, f"warehouse_inbound:{object_id}:arrival_seen")
        scans.append(found)
    carries = match(skills={"cooperative_carry"}, object_id=CRATE_ID,
                    destination_region_id="rack_aisle_a", handling_mode_id="paired_support",
                    outcome="crate_shelved_on_aisle_a")
    require(carries, "warehouse_inbound:crate_support_shelved")
    picks = match(skills={"pick"}, object_id=CARTON_ID, handling_mode_id="single_grip",
                  pickup_region_id="receiving_buffer", outcome="carton_shelved_on_aisle_b")
    require(picks, "warehouse_inbound:carton_declared_grip")
    crate_releases = release_at(CRATE_ID, "rack_aisle_a", "crate_shelved_on_aisle_a")
    carton_releases = release_at(CARTON_ID, "rack_aisle_b", "carton_shelved_on_aisle_b")
    require(crate_releases and carton_releases, "warehouse_inbound:both_objects_shelved")
    require(all(any(precedes(scan, release) for scan in scans[0]) for release in crate_releases)
            and all(any(precedes(scan, release) for scan in scans[1]) for release in carton_releases),
            "warehouse_inbound:shelving_after_arrival")
    final_object(CRATE_ID, "rack_aisle_a")
    final_object(CARTON_ID, "rack_aisle_b")
    closeouts = match(skills={"observe"}, mobility="ground", region_id="receiving_buffer",
                      subject_ref="receiving_buffer", observation_mode=CLOSEOUT_MODE,
                      outcome="receiving_buffer_closed")
    require(closeouts, "warehouse_inbound:buffer_closeout")
    releases = crate_releases + carton_releases
    require(all(any(precedes(release, closeout) for release in releases) for closeout in closeouts),
            "warehouse_inbound:closeout_after_shelving")
    standby_navs = [row for row in plan["tasks"]
                    if row["skill"] == "ground_navigation"
                    and regions[row["arguments"]["region_id"]]["kind"] == "standby"]
    closeout_agents = {agent for row in closeouts for agent in row["assigned_agents"]}
    for agent, final_region in constraint_review["robot_regions"].items():
        require(robots[agent]["mobility"] == "ground", f"warehouse_inbound:{agent}:ground_team")
        require(final_region in regions and regions[final_region]["kind"] == "standby",
                f"warehouse_inbound:{agent}:ends_in_standby")
        agent_navs = [row for row in standby_navs if agent in row["assigned_agents"]]
        require(agent_navs, f"warehouse_inbound:{agent}:withdrawal")
        if agent in closeout_agents:
            require(any(precedes(closeout, nav) for closeout in closeouts for nav in agent_navs),
                    "warehouse_inbound:observer_leaves_after_closeout")
        else:
            require(any(precedes(nav, closeout) for nav in agent_navs for closeout in closeouts),
                    "warehouse_inbound:carriers_leave_before_closeout")
    final_positions = [
        row for row in match(skills={"ground_navigation"}, outcome="inbound_team_withdrawn")
        if regions[row["arguments"]["region_id"]]["kind"] == "standby"
    ]
    recorded = {agent for row in final_positions for agent in row["assigned_agents"]}
    require(final_positions and recorded == set(constraint_review["robot_regions"]),
            "warehouse_inbound:team_withdrawn_records_each_final_position")


def build_warehouse_inbound_package() -> dict[str, dict[str, Any]]:
    from demo.benchmark.second_mission_bundle import assemble_unscored_goal_bundle

    public, witnesses = _warehouse_inbound_parts()
    return assemble_unscored_goal_bundle(public, witnesses, [
        "warehouse_inbound_arrival_episode_evidence",
        "warehouse_inbound_support_shelving_episode_evidence",
        "warehouse_inbound_buffer_closeout_episode_evidence",
        "reliable_skill_backend_conformance",
        "scene_geometry_and_object_binding",
    ])
