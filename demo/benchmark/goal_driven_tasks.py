"""Scene-native goal-driven task families for the four benchmark scenes.

The returned public packet is intentionally outcome based.  The witness plans
are author/evaluator material and are never copied into the participant task.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .goal_driven_contract import (
    GOAL_BASELINE_SCHEMA,
    GOAL_CONTRACT_VERSION,
    GOAL_HIDDEN_EVALUATOR_SCHEMA,
    GOAL_PLAN_VERSION,
    GOAL_TASK_SCHEMA,
    build_goal_heterogeneity_certificate,
    build_goal_participant_api,
    semantic_goal_plan_signature,
    validate_goal_bundle,
    validate_goal_hidden_evaluator,
    validate_goal_task_spec,
    write_goal_task_bundle,
)
from .provenance import canonical_sha256
from .measurement_contract import (
    build_calibration_profile,
    build_intervention_contract,
    build_measurement_target,
    build_reference_solution,
)
from .skill_ontology import (
    SKILL_CATALOG_SCHEMA, SKILL_CATALOG_VERSION, CITY_NATIVE_SKILL_CATALOG_VERSION,
    required_capabilities,
)
from .goal_driven_fixtures import (
    SKILL_DURATIONS_S,
    execution_contract,
    object_physical_properties,
    quantitative_authoring_boundary,
    region_resource_capacity,
    robot_physical_profile,
    scene_routes,
)
from .goal_scene_registry import registered_goal_scenes
from .mechanism_templates import (
    M0_MECHANISMS,
    build_mechanism_template,
    canonical_template_id,
)
from .candidate_pool_design import (
    POOL_PROFILES,
    apply_pool_profile,
    pool_design_summary,
    validate_pool_design_summary,
)


_FACTOR_LEVELS = {
    "communication_quality": {"immediate", "delayed", "dropped", "bandwidth_limited", "stale"},
    "team_continuity": {"stable", "member_exit", "member_join", "capability_change"},
}
_FACTOR_RULES = {
    "communication_quality": "Only messages delivered under the current channel condition may be consumed; future delivery times stay hidden.",
    "team_continuity": "Only lifecycle and capability events already delivered by the runtime may affect the current roster; future joins, exits and changes stay hidden.",
}
_DEFAULT_FACTOR_LEVELS = {
    "communication_quality": "immediate",
    "team_continuity": "stable",
}
_TEAM_TREATMENT_TARGETS = {
    "factory": "factory_ground_1",
    "warehouse_nvd": "warehouse_ground_1",
    "modern_house_nvd": "house_ground_1",
    "city": "go2_1",
}
_AUTHORING_BUSINESS_FAMILIES = {
    "factory": "production_anomaly_confirmation_and_maintenance_dispatch",
    "warehouse_nvd": "order_fulfilment_inventory_and_dock_closeout",
    "modern_house_nvd": "resident_home_recovery_and_access_restoration",
    "city": "urban_incident_response",
}
# Frozen city episode identities used by declaration-level witness plans. They
# mirror the private roster naming of ``build_response_people`` and the
# following-vehicle runtime; the real-map assembly binding stays in the city
# preparation draft's episode_binding, while this authoring abstraction binds
# each canonical person to one of the two declared assembly candidates.
_CITY_AFFECTED_PERSON_ASSEMBLY = {
    "affected_person_00": "city_assembly_north",
    "affected_person_01": "city_assembly_north",
    "affected_person_02": "city_assembly_south",
    "affected_person_03": "city_assembly_south",
}
_CITY_FOLLOWING_VEHICLE_IDS = ("following_vehicle_00",)
_CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY = {
    "affected_person_00": "assembly_candidate_004",
    "affected_person_01": "assembly_candidate_001",
    "affected_person_02": "assembly_candidate_003",
    "affected_person_03": "assembly_candidate_002",
}
_CITY_NATIVE_FOLLOWING_VEHICLE_IDS = tuple(f"following_vehicle_{index:02d}" for index in range(6))
_CITY_ROSTER_PROFILES = {"abstract_v1", "bound_native_v1", "bound_native_v2"}
_CITY_RESPONSE_SERVICE_ID = "city_dispatch"
# Frozen semantic map that gates the opt-in bound_native_v2 spawn anchors: the
# road_surface polygon supplies the drivable footprint and static_obstacles
# bounds_m supply the keep-out boxes.  The map is the maintained
# recovery_binding_v2 release input, so a missing file is an authoring error
# rather than a silent validation pass.
CITY_V2_SEMANTIC_MAP_PATH = (Path(__file__).resolve().parents[2] / "tmp" / "city_benchmark"
                             / "recovery_binding_v2_20260922" / "release" / "maps"
                             / "semantic_map.json")
CITY_SPAWN_CLEARANCE_M = 1.5
# Opt-in pool-expansion anchors, validated by validate_city_spawn_points.  The
# sketched carter_2 (-55, 45) and iris_2 (-48, 52) spots fall inside a building
# block hole of the frozen road polygon (iris_2 also sits inside a roadside
# obstacle), so the nearest clean road_surface anchors are frozen instead.
CITY_V2_SPAWN_XY_M = {
    "go2_2": (-72.0, 40.0),
    "carter_2": (-55.0, 58.0),
    "lite3_1": (-70.0, 55.0),
    "iris_2": (-48.0, 57.0),
}
_CITY_V2_POOL_NOTE = (
    "bound_native_v2 为 opt-in 扩池：候选池 7 台，核心成员仍是 go2_1/carter_1/iris_1，"
    "本见证与 bound_native_v1 相同，只使用核心 3 台。新增 go2_2/carter_2 复制同型核心成员的公开技能"
    "（含 guide_people/control_following_traffic），iris_2 复制 iris_1（含 report_incident），"
    "lite3_1 仅 ground_navigation+observe、无城市服务技能，是能力诱饵。四个新增出生锚点已由 "
    "validate_city_spawn_points 对照冻结的 recovery_binding_v2 semantic map 校验"
    f"（road_surface 多边形 point-in-polygon + static_obstacles bounds_m 外扩 {CITY_SPAWN_CLEARANCE_M:g} m 避让）："
    + json.dumps(CITY_V2_SPAWN_XY_M, sort_keys=True)
    + "。除 max_active_agents（扩池按用户原则放开为池大小，团队规模由被测算法自决）外 budgets 维持 bound_native_v1 数值；扩池不改变本见证。"
)


def _legacy_measurement_contract(public: Mapping[str, Any]) -> dict[str, Any]:
    """Attach a conservative contract while legacy tasks await classification.

    The provisional mechanism follows the controlled factor only to make the
    treatment auditable; ``measurement_status=unclassified`` prevents this
    metadata from being presented as a completed mechanism claim.
    """
    scene = public["mission"]["scene_key"]
    factor = public["experiment_factor"]["id"]
    if factor == "communication_quality":
        mechanism = "communication_adaptation"
        kind = "I-comm"
        mutable = ["communication_quality"]
        evidence = ["communication_event", "message_receipt"]
        expected = ["communication_adaptation"]
    else:
        mechanism = "role_churn_recovery"
        kind = "I-role"
        mutable = ["active_agents"]
        evidence = ["membership_event", "reassignment_event"]
        expected = ["role_churn_recovery"]
    target = build_measurement_target(
        scene_key=scene,
        mechanism_family=mechanism,
        failure_mechanisms=["stale_observation" if factor == "communication_quality" else "responsibility_vacuum"],
        eligible_metrics=["coordination_wait" if factor == "communication_quality" else "reassignment_latency"],
        allowed_claims=["factor_condition_coverage"],
        forbidden_claims=["global_optimality", "whole_task_necessity", "physical_causality",
                          "causal_attribution", "statistical_generalization", "runtime_execution_validation"],
    )
    reference = build_reference_solution(
        status="feasible_reference_only", strategy_id=f"{scene}_legacy_witness_reference_v1",
        reference_metrics={"completion_time_s": 0.0},
    )
    intervention = build_intervention_contract(
        scene_key=scene, intervention_id=f"{public['task_id']}-{factor}-legacy",
        kind=kind, start_event="first_cross_mobility_observation_published",
        end_event="first_business_state_commit", phase="decision_window",
        mutable_fields=mutable,
        frozen_fields=["task_goal", "low_level_controller", "world_seed"],
        expected_evidence=evidence,
        invalid_branches=["post_failure_injection", "changes_frozen_field"],
    )
    calibration = build_calibration_profile(
        scene_key=scene, calibration_id=f"{scene}-{factor}-legacy-control",
        role="single_factor_control", expected_mechanisms=expected,
        control_condition="no_fault", expected_evidence=evidence,
    )
    from .measurement_contract import MEASUREMENT_CONTRACT_SCHEMA, MEASUREMENT_CONTRACT_VERSION
    return {
        "schema": MEASUREMENT_CONTRACT_SCHEMA,
        "version": MEASUREMENT_CONTRACT_VERSION,
        "measurement_target": target,
        "reference_solution": reference,
        "intervention_contract": intervention,
        "calibration_profile": calibration,
    }


def _apply_mechanism_template(public: dict[str, Any], template: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a canonical mechanism to an otherwise scene-native goal task.

    The mechanism is kept out of the participant task vocabulary.  The public
    business objective and world remain the source of the agent's decisions;
    the hidden evaluator receives the reviewed template and its measurement
    contract.  This prevents a mechanism label from becoming an answer hint.
    """
    scene = public["mission"]["scene_key"]
    if template["scene_key"] != scene:
        raise ValueError("mechanism template scene does not match public task")
    mechanism = template["mechanism_family"]
    public["task_id"] = f"{public['task_id']}-M0-{mechanism}"
    public["mission"]["context"] = (
        f"{public['mission']['context']} 本题冻结的协作测量关注业务中的机制交互，"
        "评价只使用当前送达信息和业务证据，不公开未来事件或参考分配。"
    )
    public["business_constraints"] = [
        *public["business_constraints"],
        f"机制题包要求保留 {mechanism} 的决策证据链；底层导航和动作执行仍是固定的可靠执行前提。",
    ]
    return public


def _apply_mechanism_probe_to_witnesses(
        witnesses: list[dict[str, Any]], public: Mapping[str, Any],
        mechanism_family: str) -> list[dict[str, Any]]:
    """Add a concrete, legal evidence hook to each canonical witness.

    The hook is intentionally a normal participant skill.  It changes the
    generated plan graph and therefore its semantic signature, while the
    business witness still has to satisfy the scene predicates independently.
    """
    for witness in witnesses:
        plan = witness["plan"]
        rows = {row["id"]: row for row in public["candidate_robot_pool"]}
        relay_agents = [agent for agent in plan["assigned_team"]
                        if "relay" in rows[agent]["public_capabilities"]
                        and "event_bus" in rows[agent]["interfaces"]]
        if not relay_agents:
            raise ValueError(f"{public['mission']['scene_key']} witness has no relay-capable member")
        probe_id = f"mechanism_probe_{mechanism_family}_{witness['id']}"
        if any(row["task_id"] == probe_id for row in plan["tasks"]):
            raise ValueError("mechanism probe id collides with witness task")
        probe = _task_row(
            probe_id, "relay",
            {"message_kind": f"benchmark_mechanism:{mechanism_family}"},
            [], ["relay"], [relay_agents[0]])
        end_s = max(
            row["execution"]["start_s"] + row["execution"]["duration_s"]
            for row in plan["tasks"]
        )
        probe["execution"] = {
            "start_s": end_s,
            "duration_s": SKILL_DURATIONS_S["relay"],
        }
        plan["tasks"].append(probe)
    return witnesses


def _robot(identifier: str, robot_type: str, mobility: str, capabilities: list[str], interfaces: list[str]) -> dict[str, Any]:
    return {
        "id": identifier,
        "type": robot_type,
        "mobility": mobility,
        "public_capabilities": capabilities,
        "interfaces": interfaces,
        "physical_profile": robot_physical_profile(robot_type),
    }


def _region(identifier: str, kind: str, description: str, access_modes: list[str]) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "public_description": description,
            "access_modes": access_modes,
            "resource_capacity": region_resource_capacity(identifier)}


def _object(identifier: str, category: str, role: str, constraints: list[str]) -> dict[str, Any]:
    return {"id": identifier, "category": category, "public_role": role,
            "handling_constraints": constraints,
            "physical_properties": object_physical_properties(identifier)}


def _outcome(identifier: str, description: str, acceptance: str, evidence: list[str]) -> dict[str, Any]:
    return {"id": identifier, "description": description, "acceptance": acceptance,
            "evidence_expectations": evidence}


def _task(task_id: str, scene_key: str, goal: str, context: str,
          outcomes: list[dict[str, Any]], robots: list[dict[str, Any]],
          regions: list[dict[str, Any]], objects: list[dict[str, Any]],
          links: list[dict[str, Any]], constraints: list[str], notes: str,
          factor_level: str, factor_id: str = "communication_quality",
          max_active_agents: int | None = None) -> dict[str, Any]:
    return {
        "schema": GOAL_TASK_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": task_id,
        "evaluation_track": "goal_driven_team_planning_v1",
        "mission": {"scene_key": scene_key, "goal": goal, "context": context},
        "required_outcomes": outcomes,
        "candidate_robot_pool": robots,
        "skill_catalog_version": f"{SKILL_CATALOG_SCHEMA}.v{SKILL_CATALOG_VERSION}",
        "execution_contract": execution_contract(),
        "public_world": {
            "regions": regions,
            "objects": objects,
            "vertical_links": links,
            "routes": scene_routes(scene_key),
            "public_notes": (
                notes + " 所有尺寸、载荷、接口、点位和时长均为冻结的 benchmark 实验定义，"
                "不是硬件额定性能或 USD 实测结果；场景绑定尚待核验。抓取点采用物体本地坐标，"
                "有效载荷对应声明的整机与工具配置。Carter 的 support_socket_v1 是实验托举连接端口，"
                "没有机械臂，也不具备单机 pick/place 能力。技能仅在物理、资源与信息前提满足时确定性成功；"
                "碰撞时停靠并报告，模型负责通路选择和资源排序。空中观察只返回所在声明观察区的可见结果，"
                "不提供全局隐藏真值；目前观察区覆盖范围是作者声明，尚未绑定真实相机视锥、距离与场景遮挡。"
            ),
        },
        "business_constraints": constraints,
        "budgets": {
            "simulation_s": 240.0,
            "planning_s": 30.0,
            "message_bytes": 8192,
            "model_tokens": 32768,
            "max_active_agents": len(robots) if max_active_agents is None else max_active_agents,
        },
        "experiment_factor": {
            "id": factor_id,
            "level": factor_level,
            "public_rule": _FACTOR_RULES[factor_id],
            "frozen_inputs": ["scene_layout", "business_objects", "candidate_robot_pool", "budgets", "model_budget"],
        },
    }


def _plan(plan_id: str, team: list[str], tasks: list[dict[str, Any]], *,
          durations: Mapping[str, float] = SKILL_DURATIONS_S) -> dict[str, Any]:
    # These are deliberately conservative author witnesses, not optimal schedules.
    # Serial intervals avoid silently outsourcing resource ordering to navigation.
    scheduled = deepcopy(tasks)
    clock_s = 0.0
    for task in scheduled:
        duration_s = durations[task["skill"]]
        task["execution"] = {"start_s": clock_s, "duration_s": duration_s}
        clock_s += duration_s
    return {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": plan_id, "assigned_team": team, "tasks": scheduled}


def _task_row(task_id: str, skill: str, arguments: dict[str, Any], prerequisites: list[str],
              required_capabilities: list[str], assigned_agents: list[str]) -> dict[str, Any]:
    return {"task_id": task_id, "skill": skill, "arguments": arguments,
            "prerequisites": prerequisites, "required_capabilities": required_capabilities,
            "assigned_agents": assigned_agents, "evidence_refs": []}


def _factory() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    robots = [
        _robot("factory_aerial_1", "iris", "aerial", ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("factory_ground_1", "m20", "ground", ["ground_navigation", "observe", "stair_traversal", "pick", "place", "handoff", "synchronize"], ["camera", "manipulator", "event_bus"]),
        _robot("factory_ground_2", "carter", "ground", ["ground_navigation", "observe", "handoff", "synchronize"], ["camera", "event_bus"]),
        _robot("factory_ground_3", "m20", "ground", ["ground_navigation", "observe", "stair_traversal", "pick", "place", "handoff", "synchronize"], ["camera", "manipulator", "event_bus"]),
    ]
    regions = [
        _region("factory_overview", "inspection_area", "俯视观察生产异常和通行状态的空中区域", ["aerial_navigation"]),
        _region("assembly_cell", "workcell", "下层装配工位和维护交接区", ["ground_navigation"]),
        _region("stair_approach", "vertical_access", "主楼梯下端的可停靠区域", ["ground_navigation"]),
        _region("mezzanine_workcell", "workcell", "上层夹层生产工位", ["stair_traversal"]),
        _region("response_staging", "handoff_area", "维护班组接收处置记录和工具的区域", ["ground_navigation"]),
    ]
    objects = [
        _object("production_anomaly_marker", "inspection_marker", "下层工位的生产异常记录标记，状态用于识别需要维护的工位", ["identity_must_be_confirmed", "stationary_inspection_record"]),
        _object("maintenance_service_cart", "service_cart", "停放于维护响应区、用于归档现场处置记录的服务车", ["preserve_identity", "stationary_handoff_station"]),
    ]
    links = [{
        "id": "main_stair",
        "from_region_id": "stair_approach",
        "to_region_id": "mezzanine_workcell",
        "traversal_modes": ["stair_traversal"],
        "public_description": "开放主楼梯，双向共享 main_stair 资源，一次仅一个成员；净宽 0.85 m、台阶高 0.18 m。本实例仅开放空载通行，载物上楼上限为 0 kg；服务车留在 response_staging。",
    }]
    public = _task(
        "FACTORY-GOAL-01", "factory",
        "确认生产异常所在的业务区域并派出能够完成现场交接的异构团队。",
        "生产线同时包含下层工位和经主楼梯连接的夹层工位；空中证据用于缩小范围，地面团队负责现场确认和维护交接。",
        [
            _outcome("anomaly_confirmed", "异常标记的身份、状态和所在业务区域得到一致确认。", "独立来源的当前观察能够支持同一异常标记和区域判断。", ["cross_view_identity_record", "fresh_observation"]),
            _outcome("response_area_served", "维护响应到达并服务于实际需要处置的工位区域。", "响应成员在不违反公开通行约束的情况下完成现场到达和服务状态记录。", ["measured_arrival", "service_state_record"]),
            _outcome("maintenance_handoff_recorded", "维护班组获得连续、可追溯的异常处置交接。", "异常身份、现场区域和服务交接在同一业务记录中保持一致。", ["handoff_receipt", "object_identity_continuity"]),
        ], robots, regions, objects, links,
        [
            "异常身份和现场区域必须由运行证据确认，机器人自报不能替代证据。",
            "主楼梯是开放的可用通行设施；需要上层现场时，合法计划必须使用可验证的楼梯通行能力。",
            "空中避障和地面导航是基础执行条件，不单独计入异构协作分数。",
            "响应成员须真实到达 response_staging 后，在固定服务车处读取维护班组签发的服务接收记录；这是业务记录核验，不是抓起或搬运服务车。",
            "成员加入、退出和能力变化只在运行时事件实际送达后进入当前团队状态，未来事件不公开。",
        ],
        "异常记录标记固定在 assembly_cell，记录中的异常工位和状态需观察后获得；夹层现场可复核上层业务状态。factory_overview 可观察下层标记和上下层工位概况；近距观察须在对应工位。服务车固定在 response_staging，不要求搬运服务车。",
        "immediate", max_active_agents=3,
    )
    plan_a = _plan("factory_witness_a", ["factory_aerial_1", "factory_ground_1"], [
        _task_row("scan_from_above", "observe", {"region_id": "factory_overview", "subject_ref": "production_anomaly_marker", "observation_mode": "overhead"}, [], ["observe"], ["factory_aerial_1"]),
        _task_row("visit_lower_cell", "ground_navigation", {"region_id": "assembly_cell", "route_ids": ["staging_to_lower"]}, ["scan_from_above"], ["ground_navigation"], ["factory_ground_1"]),
        _task_row("close_view_lower", "observe", {"region_id": "assembly_cell", "subject_ref": "production_anomaly_marker", "observation_mode": "close_view"}, ["visit_lower_cell"], ["observe"], ["factory_ground_1"]),
        _task_row("reach_stair", "ground_navigation", {"region_id": "stair_approach", "route_ids": ["lower_to_stair"]}, ["close_view_lower"], ["ground_navigation"], ["factory_ground_1"]),
        _task_row("climb_open_stair", "stair_traversal", {"link_id": "main_stair", "exit_region_id": "mezzanine_workcell", "route_ids": ["main_stair_up"]}, ["reach_stair"], ["stair_traversal"], ["factory_ground_1"]),
        _task_row("close_view_upper", "observe", {"region_id": "mezzanine_workcell", "subject_ref": "mezzanine_workcell", "observation_mode": "close_view"}, ["climb_open_stair"], ["observe"], ["factory_ground_1"]),
        _task_row("descend_open_stair", "stair_traversal", {"link_id": "main_stair", "exit_region_id": "stair_approach", "route_ids": ["main_stair_down"]}, ["close_view_upper"], ["stair_traversal"], ["factory_ground_1"]),
        _task_row("return_to_response", "ground_navigation", {"region_id": "response_staging", "route_ids": ["stair_to_staging"]}, ["descend_open_stair"], ["ground_navigation"], ["factory_ground_1"]),
        _task_row("record_maintenance_handoff", "observe", {"region_id": "response_staging", "subject_ref": "maintenance_service_cart", "observation_mode": "maintenance_service_receipt"}, ["return_to_response"], ["observe"], ["factory_ground_1"]),
    ])
    plan_b = _plan("factory_witness_b", ["factory_aerial_1", "factory_ground_2", "factory_ground_3"], [
        _task_row("inspect_overhead", "observe", {"region_id": "factory_overview", "subject_ref": "production_anomaly_marker", "observation_mode": "overhead"}, [], ["observe"], ["factory_aerial_1"]),
        _task_row("approach_lower_cell", "ground_navigation", {"region_id": "assembly_cell", "route_ids": ["staging_to_lower"]}, ["inspect_overhead"], ["ground_navigation"], ["factory_ground_2"]),
        _task_row("inspect_lower_cell", "observe", {"region_id": "assembly_cell", "subject_ref": "production_anomaly_marker", "observation_mode": "close_view"}, ["approach_lower_cell"], ["observe"], ["factory_ground_2"]),
        _task_row("parallel_stair_approach", "ground_navigation", {"region_id": "stair_approach", "route_ids": ["staging_to_stair"]}, ["inspect_overhead"], ["ground_navigation"], ["factory_ground_3"]),
        _task_row("parallel_stair_climb", "stair_traversal", {"link_id": "main_stair", "exit_region_id": "mezzanine_workcell", "route_ids": ["main_stair_up"]}, ["parallel_stair_approach"], ["stair_traversal"], ["factory_ground_3"]),
        _task_row("inspect_upper_cell", "observe", {"region_id": "mezzanine_workcell", "subject_ref": "mezzanine_workcell", "observation_mode": "close_view"}, ["parallel_stair_climb"], ["observe"], ["factory_ground_3"]),
        _task_row("parallel_stair_descend", "stair_traversal", {"link_id": "main_stair", "exit_region_id": "stair_approach", "route_ids": ["main_stair_down"]}, ["inspect_upper_cell"], ["stair_traversal"], ["factory_ground_3"]),
        _task_row("return_upper_responder", "ground_navigation", {"region_id": "response_staging", "route_ids": ["stair_to_staging"]}, ["parallel_stair_descend"], ["ground_navigation"], ["factory_ground_3"]),
        _task_row("return_lower_responder", "ground_navigation", {"region_id": "response_staging", "route_ids": ["lower_to_staging"]}, ["inspect_lower_cell"], ["ground_navigation"], ["factory_ground_2"]),
        _task_row("handoff_parallel_record", "observe", {"region_id": "response_staging", "subject_ref": "maintenance_service_cart", "observation_mode": "maintenance_service_receipt"}, ["return_lower_responder", "return_upper_responder"], ["observe"], ["factory_ground_2"]),
    ])
    return public, [
        {"id": "factory_witness_a", "satisfies_outcomes": [row["id"] for row in public["required_outcomes"]], "outcome_support": {"anomaly_confirmed": ["scan_from_above", "close_view_lower", "close_view_upper"], "response_area_served": ["visit_lower_cell", "climb_open_stair"], "maintenance_handoff_recorded": ["record_maintenance_handoff"]}, "plan": plan_a},
        {"id": "factory_witness_b", "satisfies_outcomes": [row["id"] for row in public["required_outcomes"]], "outcome_support": {"anomaly_confirmed": ["inspect_overhead", "inspect_lower_cell", "inspect_upper_cell"], "response_area_served": ["approach_lower_cell", "parallel_stair_climb"], "maintenance_handoff_recorded": ["handoff_parallel_record"]}, "plan": plan_b},
    ]


def _warehouse() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    robots = [
        _robot("warehouse_aerial_1", "iris", "aerial", ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("warehouse_ground_1", "m20", "ground", ["ground_navigation", "observe", "pick", "place", "handoff", "cooperative_carry", "synchronize"], ["camera", "manipulator", "cooperative_transport", "event_bus"]),
        _robot("warehouse_ground_2", "carter", "ground", ["ground_navigation", "observe", "handoff", "cooperative_carry", "synchronize"], ["camera", "cooperative_transport", "event_bus"]),
        _robot("warehouse_ground_3", "m20", "ground", ["ground_navigation", "observe", "pick", "place", "handoff", "cooperative_carry", "synchronize"], ["camera", "manipulator", "cooperative_transport", "event_bus"]),
    ]
    regions = [
        _region("rack_overview", "inventory_overview", "覆盖订单货架和通道状态的空中观察区", ["aerial_navigation"]),
        _region("rack_aisle_a", "rack_aisle", "订单货架的主取货通道", ["ground_navigation", "aerial_navigation"]),
        _region("rack_aisle_b", "rack_aisle", "订单货架的另一侧取货通道", ["ground_navigation"]),
        _region("consolidation_area", "consolidation", "订单集货和身份复核区域", ["ground_navigation"]),
        _region("outbound_dock", "dock", "出库 dock 的交接区域", ["ground_navigation", "aerial_navigation"]),
        _region("receiving_buffer", "handoff_area", "承运方接收出库货物的缓冲区", ["ground_navigation"]),
        _region("warehouse_standby_a", "standby", "出库完成后的第一地面待机区", ["ground_navigation"]),
        _region("warehouse_standby_b", "standby", "出库完成后的第二地面待机区", ["ground_navigation"]),
    ]
    objects = [
        _object("order_crate_01", "wooden_crate", "指定订单的大件货物，须与纸箱一起完成集货和 dock 接收", ["preserve_identity", "respect_per_carrier_payload", "use_declared_support_points"]),
        _object("order_carton_01", "cardboard_carton", "指定订单的纸箱货物", ["preserve_identity", "count_once"]),
    ]
    public = _task(
        "WAREHOUSE-GOAL-01", "warehouse_nvd",
        "完成指定订单的货架核对、集货、出库交接和库存闭环复核。",
        "仓库任务同时涉及货架两侧、木箱和纸箱、集货区与 dock；通信条件可能变化，团队需要保持订单身份连续性。",
        [
            _outcome("order_inventory_reconciled", "指定订单的货物种类和数量与货架记录一致。", "每个订单对象都有当前身份和数量证据，且没有重复计入或漏计。", ["rack_identity_record", "quantity_reconciliation"]),
            _outcome("order_consolidated", "订单货物在集货区域形成可交接的连续订单状态。", "木箱和纸箱的身份、归属和集货状态保持一致。", ["object_ownership_trace", "consolidation_record"]),
            _outcome("dock_handoff_confirmed", "木箱和纸箱两件订单货物均按 dock 规则交给出库接收方。", "order_crate_01 和 order_carton_01 分别具有 dock 接收记录，订单身份和交接时间匹配；任一缺失均不算订单完成。", ["dock_receipt", "handoff_identity"]),
            _outcome("warehouse_closeout", "出库后货架和作业通道达到可复核的关闭状态。", "复核证据确认订单链完整，作业成员和货物不再占用受保护通道。", ["post_dispatch_scan", "aisle_clearance_record"]),
        ], robots, regions, objects, [],
        [
            "订单身份、数量和交接顺序必须由独立运行证据确认。",
            "每件货物必须使用公开 handling mode，按固定支撑点份额核验各成员载荷和接口；不能把两机总载荷直接当作任一支撑点的承载能力。",
            "dock 接收和库存复核是业务结果：联合搬运在 dock 释放后读取接收方的木箱收据；纸箱通过持有后的 handoff 转交。搬运动作和机器人自报均不能替代接收记录。",
            "通信变化只影响当时可见的信息和恢复决策，场景、对象和预算保持冻结。",
            "成员加入、退出和能力变化只在运行时事件实际送达后进入当前团队状态，未来事件不公开。",
        ],
        "木箱固定起始于 rack_aisle_a，纸箱固定起始于 rack_aisle_b，两者必须经过 consolidation_area 后在 outbound_dock 完成交接。rack_overview 声明覆盖两处取货区、集货区及 dock 的库存和净空观察；最短取货近路可能不容纳携物队形。两件货物均可由 dock 接收后留在其接收占地内，不能占用通道；未来事件时序不公开。",
        "immediate", max_active_agents=3,
    )
    # 双 M20 承载对（ground_1 + ground_3）；两见证互换 picker/partner 角色
    # 保持语义差异（取箱顺序 + 分工不同）。
    return public, [_warehouse_witness(public, "a", "warehouse_ground_1", carton_first=False),
                    _warehouse_witness(public, "b", "warehouse_ground_3", carton_first=True)]


def _warehouse_witness(public: dict[str, Any], suffix: str, picker: str, *,
                       carton_first: bool) -> dict[str, Any]:
    """Two orders of work under exactly the same initial object state."""
    # 双 M20 承载对：ground_2（carter）在 goal_constraints 的实测锚点注册表
    # 下对高位 support 锚点不可达（阶段18 v10-v14，见 STAGE18_REVIEW）；配对
    # 选择属被测模型方案空间，见证各取一个可行组合并互换分工。
    partner = "warehouse_ground_1" if picker == "warehouse_ground_3" else "warehouse_ground_3"
    aerial = "warehouse_aerial_1"
    survey = "survey_racks" if suffix == "a" else "survey_racks_b"
    position_id = f"position_overview_{suffix}"
    rows = [
        _task_row(position_id, "aerial_navigation", {"region_id": "rack_overview", "route_ids": ["aisle_to_overview_air"]}, [], ["aerial_navigation"], [aerial]),
        _task_row(survey, "observe", {"region_id": "rack_overview", "subject_ref": "order_carton_01", "observation_mode": "rack_inventory"}, [position_id], ["observe"], [aerial]),
    ]

    def append(name: str, skill: str, arguments: dict[str, Any], agents: list[str]) -> str:
        identifier = f"{name}_{suffix}"
        rows.append(_task_row(identifier, skill, arguments, [rows[-1]["task_id"]], [skill], agents))
        return identifier

    append("survey_crate", "observe", {"region_id": "rack_overview", "subject_ref": "order_crate_01", "observation_mode": "rack_inventory"}, [aerial])
    bindings = [{"agent_id": picker, "point_id": "support_left"},
                {"agent_id": partner, "point_id": "support_right"}]
    single = [{"agent_id": picker, "point_id": "center_grip"}]

    def collect_crate() -> None:
        append("approach_crate_picker", "ground_navigation", {"region_id": "rack_aisle_a", "route_ids": ["consolidation_to_rack_a"]}, [picker])
        append("approach_crate_partner", "ground_navigation", {"region_id": "rack_aisle_a", "route_ids": ["consolidation_to_rack_a"]}, [partner])
        append("carry_crate", "cooperative_carry", {"object_id": "order_crate_01", "destination_region_id": "consolidation_area", "handling_mode_id": "paired_support", "carrier_bindings": bindings, "route_ids": ["rack_a_to_consolidation"]}, [picker, partner])

    def collect_carton() -> None:
        append("reach_carton", "ground_navigation", {"region_id": "rack_aisle_b", "route_ids": ["consolidation_to_rack_b"]}, [picker])
        append("pick_carton", "pick", {"object_id": "order_carton_01", "pickup_region_id": "rack_aisle_b", "handling_mode_id": "single_grip", "carrier_bindings": single}, [picker])
        append("stage_carton", "place", {"object_id": "order_carton_01", "placement_region_id": "consolidation_area", "route_ids": ["rack_b_to_consolidation"]}, [picker])

    def dispatch_crate() -> None:
        append("dispatch_crate", "cooperative_carry", {"object_id": "order_crate_01", "destination_region_id": "outbound_dock", "handling_mode_id": "paired_support", "carrier_bindings": bindings, "route_ids": ["consolidation_to_dock"]}, [picker, partner])
        append("handoff_crate", "observe", {"region_id": "outbound_dock", "subject_ref": "order_crate_01", "observation_mode": "dock_receiver_receipt"}, [picker])

    def dispatch_carton() -> None:
        append("pick_staged_carton", "pick", {"object_id": "order_carton_01", "pickup_region_id": "consolidation_area", "handling_mode_id": "single_grip", "carrier_bindings": single}, [picker])
        append("handoff_order", "handoff", {"object_id": "order_carton_01", "receiving_region_id": "outbound_dock", "recipient_role": "dock_receiver", "route_ids": ["consolidation_to_dock"]}, [picker])

    if carton_first:
        collect_carton()
        collect_crate()
        dispatch_carton()
        append("return_for_crate", "ground_navigation", {"region_id": "consolidation_area", "route_ids": ["dock_to_consolidation"]}, [picker])
        dispatch_crate()
    else:
        collect_crate()
        collect_carton()
        dispatch_crate()
        append("return_for_carton", "ground_navigation", {"region_id": "consolidation_area", "route_ids": ["dock_to_consolidation"]}, [picker])
        dispatch_carton()
    withdrawn = [
        append("withdraw_picker", "ground_navigation", {"region_id": "warehouse_standby_a", "route_ids": ["dock_to_standby_a"]}, [picker]),
        append("withdraw_partner", "ground_navigation", {"region_id": "warehouse_standby_b", "route_ids": ["dock_to_standby_b"]}, [partner]),
    ]
    rows.append(_task_row(f"scan_closeout_{suffix}", "observe", {"region_id": "rack_overview", "subject_ref": "outbound_dock", "observation_mode": "post_dispatch"}, withdrawn, ["observe"], [aerial]))
    identifier = f"warehouse_witness_{suffix}"
    return {
        "id": identifier, "satisfies_outcomes": [row["id"] for row in public["required_outcomes"]],
        "outcome_support": {
            "order_inventory_reconciled": [survey, f"survey_crate_{suffix}"],
            "order_consolidated": [f"carry_crate_{suffix}", f"stage_carton_{suffix}"],
            "dock_handoff_confirmed": [f"handoff_crate_{suffix}", f"handoff_order_{suffix}"],
            "warehouse_closeout": [*withdrawn, f"scan_closeout_{suffix}"],
        },
        "plan": _plan(identifier, [aerial, picker, partner], rows),
    }


def _house() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    robots = [
        _robot("house_aerial_1", "iris", "aerial", ["aerial_navigation", "observe", "relay"], ["camera", "event_bus"]),
        _robot("house_ground_1", "m20", "ground", ["ground_navigation", "observe", "pick", "place", "cooperative_carry", "synchronize"], ["camera", "manipulator", "cooperative_transport", "event_bus"]),
        _robot("house_ground_2", "carter", "ground", ["ground_navigation", "observe", "cooperative_carry", "synchronize"], ["camera", "cooperative_transport", "event_bus"]),
        _robot("house_ground_3", "m20", "ground", ["ground_navigation", "observe", "pick", "place", "cooperative_carry", "synchronize"], ["camera", "manipulator", "cooperative_transport", "event_bus"]),
    ]
    regions = [
        _region("room_overview", "room_overview", "覆盖入口、家具和生活动线的空中观察区", ["aerial_navigation"]),
        _region("entry_hall", "entry", "住户从入口进入生活区的起始区域", ["ground_navigation", "aerial_navigation"]),
        _region("furniture_side", "work_area", "扶手椅主作业侧", ["ground_navigation"]),
        _region("living_area", "living_space", "住户活动的生活区", ["ground_navigation", "aerial_navigation"]),
        _region("furniture_parking", "furniture_zone", "允许扶手椅放置且不侵入动线的区域", ["ground_navigation"]),
        _region("keepsake_staging", "staging_area", "书堆暂存和确认区域", ["ground_navigation"]),
        _region("resident_path", "circulation_path", "入口到生活区的连续通行区域", ["ground_navigation"]),
        _region("house_standby_a", "standby", "不占用住户动线的第一待机区", ["ground_navigation"]),
        _region("house_standby_b", "standby", "不占用住户动线的第二待机区", ["ground_navigation"]),
    ]
    objects = [
        _object("living_armchair_01", "armchair", "需要恢复位置的扶手椅", ["respect_per_carrier_payload", "use_declared_support_points", "preserve_floor_clearance"]),
        _object("resident_bookstack_01", "bookstack", "住户指定的书堆", ["preserve_identity", "stable_placement"]),
    ]
    public = _task(
        "MODERN_HOUSE-GOAL-01", "modern_house_nvd",
        "在住户回家前恢复家具布置、物品暂存和从入口到生活区的真实通行路径。",
        "房间有扶手椅、书堆和入口动线；允许多个满足净空和摆放约束的合法布置，团队需要在通信变化下保持物品身份和退出状态。",
        [
            _outcome("furniture_repositioned", "扶手椅被恢复到合理且不侵入生活动线的区域。", "家具完整占地位于允许区域，入口到生活区的净空不低于住户阈值。", ["furniture_pose", "clearance_measurement"]),
            _outcome("belonging_staged", "指定书堆被安全暂存并保持住户物品身份。", "书堆身份、放置稳定性和暂存区域记录一致。", ["object_identity", "stable_placement"]),
            _outcome("resident_path_restored", "入口到生活区的连续生活通路恢复。", "独立路径检查通过整条通路，而不是只检查终点位置。", ["continuous_access_path", "obstacle_clearance"]),
            _outcome("team_withdrawn", "作业机器人退出住户通行区域并进入可追溯待机状态。", "最终复核时机器人占地不阻塞入口、生活区或书堆暂存区。", ["robot_withdrawal_trace", "final_scene_snapshot"]),
        ], robots, regions, objects, [],
        [
            "扶手椅和书堆必须保持场景中的对象身份，搬运完成不能只由机器人自报。",
            "家具移动和书堆暂存是家居恢复业务的一部分；导航、观察和协作搬运只是手段。",
            "住户通路必须在 entry_hall、resident_path、living_area 的整条路径上保持至少 0.9 m 净宽和 2.0 m 净高，允许多个不侵入动线的摆放方案。",
            "机器人团队退出是最终结果的一部分，退出时的成员变化和通信状态必须可追溯。",
            "成员加入、退出和能力变化只在运行时事件实际送达后进入当前团队状态，未来事件不公开。",
        ],
        "扶手椅固定起始于 furniture_side，书堆固定起始于 living_area；住户净空阈值公开，不由隐藏评价临时指定。room_overview 声明覆盖家具、书堆及 entry_hall 到 living_area 的生活动线；家具运输有宽通路和仅容单机的近路，队形宽度必须参与路线选择。",
        "immediate", max_active_agents=3,
    )
    return public, [_house_witness(public, "a", "house_ground_1", books_first=False),
                    _house_witness(public, "b", "house_ground_3", books_first=True)]


def _house_witness(public: dict[str, Any], suffix: str, picker: str, *,
                   books_first: bool) -> dict[str, Any]:
    # 双 M20 承载对（与仓库同理：Carter 高位锚点实测不可达，注册表拒绝）。
    partner = "house_ground_1" if picker == "house_ground_3" else "house_ground_3"
    aerial = "house_aerial_1"
    rows = [_task_row(f"inspect_entry_{suffix}", "observe", {"region_id": "room_overview", "subject_ref": "living_armchair_01", "observation_mode": "home_access"}, [], ["observe"], [aerial])]

    def append(name: str, skill: str, arguments: dict[str, Any], agents: list[str]) -> str:
        identifier = f"{name}_{suffix}"
        rows.append(_task_row(identifier, skill, arguments, [rows[-1]["task_id"]], [skill], agents))
        return identifier

    append("inspect_books", "observe", {"region_id": "room_overview", "subject_ref": "resident_bookstack_01", "observation_mode": "home_access"}, [aerial])

    def move_chair() -> None:
        approach = "staging_to_chair" if books_first else "entry_to_chair"
        append("approach_chair_primary", "ground_navigation", {"region_id": "furniture_side", "route_ids": [approach]}, [picker])
        append("approach_chair_partner", "ground_navigation", {"region_id": "furniture_side", "route_ids": ["entry_to_chair"]}, [partner])
        append("move_chair", "cooperative_carry", {
            "object_id": "living_armchair_01", "destination_region_id": "furniture_parking",
            "handling_mode_id": "paired_support",
            "carrier_bindings": [{"agent_id": picker, "point_id": "support_left"},
                                 {"agent_id": partner, "point_id": "support_right"}],
            "route_ids": ["chair_to_parking_wide"],
        }, [picker, partner])

    def stage_books() -> None:
        approach = "entry_to_living" if books_first else "parking_to_living"
        append("reach_books", "ground_navigation", {"region_id": "living_area", "route_ids": [approach]}, [picker])
        append("pick_books", "pick", {"object_id": "resident_bookstack_01", "pickup_region_id": "living_area", "handling_mode_id": "single_grip", "carrier_bindings": [{"agent_id": picker, "point_id": "center_grip"}]}, [picker])
        append("place_books", "place", {"object_id": "resident_bookstack_01", "placement_region_id": "keepsake_staging", "route_ids": ["living_to_staging"]}, [picker])

    if books_first:
        stage_books()
        move_chair()
    else:
        move_chair()
        stage_books()
    primary_route = "parking_to_standby_a" if books_first else "staging_to_standby_a"
    withdrawn = [
        append("withdraw_primary", "ground_navigation", {"region_id": "house_standby_a", "route_ids": [primary_route]}, [picker]),
        append("withdraw_partner", "ground_navigation", {"region_id": "house_standby_b", "route_ids": ["parking_to_standby_b"]}, [partner]),
    ]
    rows.append(_task_row(f"verify_path_{suffix}", "observe", {"region_id": "room_overview", "subject_ref": "resident_path", "observation_mode": "final_clearance"}, withdrawn, ["observe"], [aerial]))
    identifier = f"house_witness_{suffix}"
    return {
        "id": identifier, "satisfies_outcomes": [row["id"] for row in public["required_outcomes"]],
        "outcome_support": {
            "furniture_repositioned": [f"move_chair_{suffix}"],
            "belonging_staged": [f"place_books_{suffix}"],
            "resident_path_restored": [f"verify_path_{suffix}"],
            "team_withdrawn": withdrawn,
        },
        "plan": _plan(identifier, [aerial, picker, partner], rows),
    }


def _point_in_ring(x: float, y: float, ring: Any) -> bool:
    """Even-odd ray-casting containment for one closed GeoJSON linear ring."""
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = float(ring[index][0]), float(ring[index][1])
        x2, y2 = float(ring[index + 1][0]), float(ring[index + 1][1])
        if (y1 > y) != (y2 > y):
            crossing_x = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < crossing_x:
                inside = not inside
    return inside


def validate_city_spawn_points(points: Any, *,
                                semantic_map: Mapping[str, Any] | None = None,
                                clearance_m: float = CITY_SPAWN_CLEARANCE_M) -> dict[str, Any]:
    """Admit City spawn anchors against the frozen road surface and obstacles.

    Every point must lie inside the semantic map's ``road_surface`` polygon
    (exterior ring minus its holes) and outside each ``static_obstacles``
    ``bounds_m`` box inflated by ``clearance_m`` in XY.  The frozen
    recovery_binding_v2 map is the default authority; callers may pass their
    own map for isolated tests.  Any rejected point raises ``ValueError`` with
    per-point reasons, so a pass means every anchor is a clean road point.
    """
    map_source = "caller_supplied" if semantic_map is not None else str(CITY_V2_SEMANTIC_MAP_PATH)
    if semantic_map is None:
        if not CITY_V2_SEMANTIC_MAP_PATH.is_file():
            raise ValueError(f"City spawn semantic map is missing: {CITY_V2_SEMANTIC_MAP_PATH}")
        semantic_map = json.loads(CITY_V2_SEMANTIC_MAP_PATH.read_text(encoding="utf-8"))
    binding = next((row for row in semantic_map.get("bindings", ())
                    if row.get("id") == "road_surface"), None)
    if binding is None or binding.get("geometry", {}).get("type") != "Polygon":
        raise ValueError("semantic map has no road_surface Polygon binding")
    rings = binding["geometry"]["coordinates"]
    obstacles: list[tuple[str, list[float], list[float]]] = []
    for row in semantic_map.get("static_obstacles", ()):
        bounds = row.get("bounds_m")
        if (not isinstance(bounds, list) or len(bounds) != 2
                or len(bounds[0]) < 2 or len(bounds[1]) < 2):
            raise ValueError(f"static obstacle {row.get('id')!r} lacks a bounds_m min/max pair")
        obstacles.append((str(row.get("id")), [float(bounds[0][0]), float(bounds[0][1])],
                          [float(bounds[1][0]), float(bounds[1][1])]))
    labelled = ([(str(key), points[key]) for key in points] if isinstance(points, Mapping)
                else [(f"point[{index}]", point) for index, point in enumerate(points)])
    if not labelled:
        raise ValueError("City spawn point validation requires at least one point")
    failures: list[str] = []
    accepted: list[str] = []
    for label, point in labelled:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            failures.append(f"{label}: not a two-coordinate point")
            continue
        if not _point_in_ring(x, y, rings[0]):
            failures.append(f"{label} ({x:g}, {y:g}): outside the road_surface polygon")
            continue
        if any(_point_in_ring(x, y, hole) for hole in rings[1:]):
            failures.append(f"{label} ({x:g}, {y:g}): inside a road_surface hole (non-road block)")
            continue
        near = sorted(obstacle_id for obstacle_id, low, high in obstacles
                      if low[0] - clearance_m <= x <= high[0] + clearance_m
                      and low[1] - clearance_m <= y <= high[1] + clearance_m)
        if near:
            failures.append(
                f"{label} ({x:g}, {y:g}): within {float(clearance_m):g} m of static obstacles {near}")
            continue
        accepted.append(label)
    if failures:
        raise ValueError("City spawn point validation failed: " + "; ".join(failures))
    return {"validated": True, "point_count": len(accepted), "accepted": accepted,
            "semantic_map_source": map_source, "clearance_m": float(clearance_m)}


def _city_v2_pool(core: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extend the bound-native pool with the opt-in seven-robot roster.

    go2_2/carter_2/iris_2 reuse the public skill composition and interfaces of
    their same-type core members (including the city service skills), while
    lite3_1 is a deliberate decoy limited to ground_navigation and observe so
    it can carry no city service task.  The four additions use the frozen
    ``goal_driven_fixtures`` physical profiles (lite3 keeps its own
    benchmark_lite3_navigation_v1 profile); the core three keep their
    city-specific profiles unchanged.
    """
    core_by_id = {row["id"]: row for row in core}

    def member(identifier: str, robot_type: str, same_as: str,
               capabilities: list[str] | None = None) -> dict[str, Any]:
        source = core_by_id[same_as]
        return {
            "id": identifier,
            "type": robot_type,
            "mobility": source["mobility"],
            "public_capabilities": list(source["public_capabilities"] if capabilities is None
                                        else capabilities),
            "interfaces": list(source["interfaces"]),
            "physical_profile": robot_physical_profile(robot_type),
        }

    return [
        *core,
        member("go2_2", "go2", same_as="go2_1"),
        member("carter_2", "carter", same_as="carter_1"),
        member("lite3_1", "lite3", same_as="go2_1",
               capabilities=["ground_navigation", "observe"]),
        member("iris_2", "iris", same_as="iris_1"),
    ]


def _city(roster_profile: str = "abstract_v1") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """City goal bundle: the urban incident-response family as a four-layer package.

    The public world is the authoring abstraction of the response theater (not
    the participant-facing city preparation map): the incident is observed from
    a declared aerial overview area, ground responders reach the incident
    vicinity and a declared traffic control point, and two assembly candidates
    carry the per-person assembly binding. Declaration-level witness plans use
    the frozen roster/service identities; execution evidence stays pending.
    """
    if roster_profile not in _CITY_ROSTER_PROFILES:
        raise ValueError(f"unsupported City roster profile: {roster_profile}")
    from demo.city_benchmark.contracts import candidate_pool, city_outcome_rows
    from demo.city_benchmark.task_logic import default_deadline_policy, public_deadline_constraints

    # The bundle pool extends the preparation draft's interface-only pool with
    # the public skill composition rights the declaration-level witnesses need.
    # It still certifies nothing about human response, traffic authority or
    # dispatch receipts; those remain service preconditions and runtime gates.
    pool = candidate_pool()
    service_skills = {
        "go2_1": {"guide_people", "control_following_traffic"},
        "carter_1": {"guide_people", "control_following_traffic"},
        "iris_1": {"report_incident"},
    }
    for robot in pool:
        robot["public_capabilities"] = sorted(
            set(robot["public_capabilities"]) | service_skills.get(robot["id"], set()))
    native = roster_profile in {"bound_native_v1", "bound_native_v2"}
    if roster_profile == "bound_native_v2":
        # Hard spawn gate: the four opt-in anchors must sit on the frozen
        # recovery_binding_v2 road surface and clear every static obstacle by
        # the declared clearance before the pool is extended.
        validate_city_spawn_points(CITY_V2_SPAWN_XY_M)
        pool = _city_v2_pool(pool)

    regions = [
        _region("road_surface", "road_network", "城市地面道路网络；救援、管制与报告成员的公共通行区域",
                ["ground_navigation", "aerial_navigation"]),
        _region("response_staging", "response_staging", "响应团队集结与接收响应服务回执的出发区域",
                ["ground_navigation", "aerial_navigation"]),
        _region("city_incident_overview", "aerial_overview", "覆盖事故现场、后续交通与集合候选的空中观察区",
                ["aerial_navigation"]),
        _region("incident_vicinity", "incident_area", "事故影响地面区域；人员引导接触与危险边界在此确认",
                ["ground_navigation"]),
        _region("traffic_control_point", "traffic_control_zone", "已声明的安全进入控制点；交通管制请求从这里发出",
                ["ground_navigation"]),
        _region("city_assembly_north", "assembly_candidate", "北侧候选集合区；容量、可达性与当前危险净空需运行核验",
                ["ground_navigation"]),
        _region("city_assembly_south", "assembly_candidate", "南侧候选集合区；容量、可达性与当前危险净空需运行核验",
                ["ground_navigation"]),
    ]
    if native:
        regions = [row for row in regions if row["kind"] != "assembly_candidate"]
        regions.extend(_region(
            region_id, "assembly_candidate", "候选集合区；容量、可达性与当前危险净空需运行核验",
            ["ground_navigation"]) for region_id in sorted(set(
                _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY.values())))
    policy = default_deadline_policy(180.0)
    contract = execution_contract()
    if native:
        contract["skill_durations_s"]["guide_people"] = policy["people_guidance_ack_s"]
    public = {
        "schema": GOAL_TASK_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": ("CITY-GOAL-NATIVE-02" if roster_profile == "bound_native_v2"
                    else "CITY-GOAL-NATIVE-01" if roster_profile == "bound_native_v1"
                    else "CITY-GOAL-01"),
        "evaluation_track": "goal_driven_team_planning_v1",
        "mission": {"scene_key": "city",
                    "goal": "应对城市道路突发事故：查明影响，保护人员和后续交通，保持救援通路。",
                    "context": "城市街区有背景交通、步行人员和建筑；根据实际到达的告警与观察组织响应。具体事故位置和未来事件不预先公开。"},
        "required_outcomes": [_outcome(*row) for row in city_outcome_rows()],
        "candidate_robot_pool": pool,
        "skill_catalog_version": f"{SKILL_CATALOG_SCHEMA}.v{CITY_NATIVE_SKILL_CATALOG_VERSION}"
                                 if native
                                 else f"{SKILL_CATALOG_SCHEMA}.v{SKILL_CATALOG_VERSION}",
        "execution_contract": contract,
        "public_world": {
            "regions": regions,
            "objects": [],
            "vertical_links": [],
            "routes": scene_routes("city"),
            "public_notes": (
                "本包是城市事故响应的作者/评价侧目标束，公共世界为响应舞台的声明抽象，不是城市静态地图的替代。"
                "事故位置、受影响人员与后续车辆身份不公开，须由送达的观察证据绑定；集合区为候选，实时安全与容量须另行核验。"
                "所有区域、路线、时长和容量均为冻结的 benchmark 实验定义，不是硬件额定性能或 USD 实测结果。"
                "人员引导、交通管制与事件报告是具备回执语义的业务服务；请求提交不等于业务成功。"
            ),
        },
        "business_constraints": [
            "事故身份、受影响人员和后续车辆必须由当前送达的观察证据确认；机器人自报或预设动画不能替代证据。",
            "不得用机器人撞击或机体阻挡事故车；后续交通保护使用具备确认回执的停车或改道服务接口。",
            "静态空地不能自动判定为安全集合区；需满足人数容量、实测可达性和当前危险净空要求。",
            "不能仅因导航到人员附近就判定人员已疏散，也不能仅因提交管制请求就判定车辆已停止。",
            "救援通路验收需要覆盖回执时刻、持续时间大于零的连续净空检查；单点到达或仅提交报告不满足验收。",
            "成员加入、退出和能力变化只在运行时事件实际送达后进入当前团队状态，未来事件不公开。",
        ] + public_deadline_constraints(policy),
        "budgets": {
            "simulation_s": 180.0,
            "planning_s": 30.0,
            "message_bytes": 8192,
            "model_tokens": 12000,
            # User principle (2026-09-22): team size is the algorithm's own
            # decision; using more robots raises coordination cost, which is
            # a measurement signal, not a restriction the benchmark imposes.
            # The expanded pool therefore declares its full size as the
            # budget; the frozen v1 profiles keep their historical value.
            "max_active_agents": (len(pool) if roster_profile == "bound_native_v2" else 3),
        },
        "experiment_factor": {
            "id": "communication_quality",
            "level": "immediate",
            "public_rule": _FACTOR_RULES["communication_quality"],
            "frozen_inputs": ["scene_layout", "incident_instance", "actor_initial_states",
                              "candidate_robot_pool", "budgets", "model_budget"],
        },
    }
    witnesses = [_city_witness(public, "a"), _city_witness(public, "b")]
    if roster_profile == "bound_native_v2":
        for witness in witnesses:
            witness["pool_note"] = _CITY_V2_POOL_NOTE
    return public, witnesses


def _city_witness(public: dict[str, Any], suffix: str) -> dict[str, Any]:
    """Two causally distinct declaration-level response chains.

    Witness A is air-led: the aerial observer establishes the incident, the
    people and the following traffic first, then one ground responder guides
    both roster groups and holds the following vehicle before the aerial
    continuous-access check and the dispatch report. Witness B is
    traffic-led: the control point is manned and the diversion is applied
    first, then two ground responders split the guidance work, and the aerial
    observer only verifies the readback, the access corridor and the report.
    """
    aerial, ground_go2, ground_carter = "iris_1", "go2_1", "carter_1"
    overview = "city_incident_overview"
    north = sorted(person for person, assembly in _CITY_AFFECTED_PERSON_ASSEMBLY.items()
                   if assembly == "city_assembly_north")
    south = sorted(person for person, assembly in _CITY_AFFECTED_PERSON_ASSEMBLY.items()
                   if assembly == "city_assembly_south")
    identifier = f"city_witness_{suffix}"

    def row(name, skill, arguments, prerequisites, agents):
        return _task_row(f"{name}_{suffix}", skill, arguments, prerequisites,
                         sorted(required_capabilities(skill)), agents)

    def guide(name, persons, scan, prerequisites, agent):
        return row(name, "guide_people",
                   {"person_ids": persons, "observation_refs": [f"{scan}_{suffix}"],
                    "assembly_region_id": _CITY_AFFECTED_PERSON_ASSEMBLY[persons[0]]},
                   prerequisites, [agent])

    if suffix == "a":
        rows = [
            row("aerial_recon", "aerial_navigation",
                {"region_id": overview, "route_ids": ["city_air_staging_to_overview"]}, [], [aerial]),
            row("observe_incident", "observe",
                {"region_id": overview, "subject_ref": "incident_vicinity",
                 "observation_mode": "incident_overview"}, [f"aerial_recon_{suffix}"], [aerial]),
            row("observe_people", "observe",
                {"region_id": overview, "subject_ref": "incident_vicinity",
                 "observation_mode": "people_survey"}, [f"observe_incident_{suffix}"], [aerial]),
            row("observe_following", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "following_traffic_survey"}, [f"observe_people_{suffix}"], [aerial]),
            row("reach_incident", "ground_navigation",
                {"region_id": "incident_vicinity", "route_ids": ["city_road_to_incident"]},
                [f"observe_incident_{suffix}"], [ground_go2]),
            guide("guide_north", north, "observe_people",
                  [f"reach_incident_{suffix}", f"observe_people_{suffix}"], ground_go2),
            guide("guide_south", south, "observe_people", [f"guide_north_{suffix}"], ground_go2),
            row("reach_control", "ground_navigation",
                {"region_id": "traffic_control_point", "route_ids": ["city_incident_to_control"]},
                [f"guide_south_{suffix}"], [ground_go2]),
            row("control_traffic", "control_following_traffic",
                {"vehicle_ids": list(_CITY_FOLLOWING_VEHICLE_IDS),
                 "observation_refs": [f"observe_following_{suffix}"], "mode": "hold"},
                [f"reach_control_{suffix}", f"observe_following_{suffix}"], [ground_go2]),
            row("verify_traffic", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "controlled_traffic_readback"},
                [f"control_traffic_{suffix}", f"observe_following_{suffix}"], [aerial]),
            row("verify_access", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "response_access_clearance"},
                [f"verify_traffic_{suffix}", f"guide_south_{suffix}"], [aerial]),
        ]
        team = [aerial, ground_go2]
        support = {
            "incident_assessed": [f"observe_incident_{suffix}"],
            "exposed_people_accounted_for": [f"guide_north_{suffix}", f"guide_south_{suffix}"],
            "secondary_traffic_protected": [f"control_traffic_{suffix}", f"verify_traffic_{suffix}"],
            "response_access_preserved": [f"verify_access_{suffix}", f"report_incident_{suffix}"],
        }
        report_refs = [f"observe_incident_{suffix}", f"verify_access_{suffix}"]
    else:
        rows = [
            row("aerial_recon", "aerial_navigation",
                {"region_id": overview, "route_ids": ["city_air_staging_to_overview"]}, [], [aerial]),
            row("observe_incident", "observe",
                {"region_id": overview, "subject_ref": "incident_vicinity",
                 "observation_mode": "incident_overview"}, [f"aerial_recon_{suffix}"], [aerial]),
            row("observe_following", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "following_traffic_survey"}, [f"observe_incident_{suffix}"], [aerial]),
            row("reach_control", "ground_navigation",
                {"region_id": "traffic_control_point", "route_ids": ["city_road_to_control"]},
                [f"observe_incident_{suffix}"], [ground_carter]),
            row("control_traffic", "control_following_traffic",
                {"vehicle_ids": list(_CITY_FOLLOWING_VEHICLE_IDS),
                 "observation_refs": [f"observe_following_{suffix}"], "mode": "divert"},
                [f"reach_control_{suffix}", f"observe_following_{suffix}"], [ground_carter]),
            row("observe_people", "observe",
                {"region_id": overview, "subject_ref": "incident_vicinity",
                 "observation_mode": "people_survey"}, [f"observe_following_{suffix}"], [aerial]),
            row("reach_incident", "ground_navigation",
                {"region_id": "incident_vicinity", "route_ids": ["city_road_to_incident"]},
                [f"observe_people_{suffix}"], [ground_go2]),
            guide("guide_south", south, "observe_people",
                  [f"reach_incident_{suffix}", f"observe_people_{suffix}"], ground_go2),
            row("control_relay", "ground_navigation",
                {"region_id": "incident_vicinity", "route_ids": ["city_control_to_incident"]},
                [f"control_traffic_{suffix}"], [ground_carter]),
            guide("guide_north", north, "observe_people",
                  [f"control_relay_{suffix}", f"observe_people_{suffix}"], ground_carter),
            row("verify_traffic", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "controlled_traffic_readback"},
                [f"control_traffic_{suffix}", f"observe_people_{suffix}"], [aerial]),
            row("verify_access", "observe",
                {"region_id": overview, "subject_ref": "road_surface",
                 "observation_mode": "response_access_clearance"},
                [f"verify_traffic_{suffix}", f"guide_north_{suffix}", f"guide_south_{suffix}"], [aerial]),
        ]
        team = [aerial, ground_carter, ground_go2]
        support = {
            "incident_assessed": [f"observe_incident_{suffix}"],
            "exposed_people_accounted_for": [f"guide_north_{suffix}", f"guide_south_{suffix}"],
            "secondary_traffic_protected": [f"control_traffic_{suffix}", f"verify_traffic_{suffix}"],
            "response_access_preserved": [f"verify_access_{suffix}", f"report_incident_{suffix}"],
        }
        report_refs = [f"observe_incident_{suffix}", f"verify_access_{suffix}"]
    # Serial author scheduling: the report's submission time is the sum of the
    # preceding skill durations, matching the execution window assigned below.
    report_start = math.fsum(SKILL_DURATIONS_S[task["skill"]] for task in rows)
    rows.append(row("report_incident", "report_incident",
                    {"observation_refs": report_refs,
                     "recipient_service_id": _CITY_RESPONSE_SERVICE_ID,
                     "time_s": report_start},
                    [f"verify_access_{suffix}"], [aerial]))
    witness = {
        "id": identifier, "satisfies_outcomes": [row["id"] for row in public["required_outcomes"]],
        "outcome_support": support,
        "plan": _plan(identifier, team, rows),
    }
    return (_city_native_witness(witness, public["execution_contract"]["skill_durations_s"])
            if public["task_id"] in ("CITY-GOAL-NATIVE-01", "CITY-GOAL-NATIVE-02")
            else witness)


def _city_native_witness(witness: dict[str, Any], durations: Mapping[str, float]) -> dict[str, Any]:
    """Expand each abstract guidance group into the frozen native roster."""
    result = deepcopy(witness)
    expanded: list[dict[str, Any]] = []
    replacements: dict[str, list[str]] = {}
    for original in result["plan"]["tasks"]:
        row = deepcopy(original)
        row["prerequisites"] = [replacements[parent][-1] if parent in replacements else parent
                                for parent in row["prerequisites"]]
        if row["skill"] == "guide_people":
            previous = None
            replacement_ids = []
            for person_id in row["arguments"]["person_ids"]:
                guide = deepcopy(row)
                guide["task_id"] = f"guide_{person_id}_{witness['id'][-1]}"
                guide["arguments"]["person_ids"] = [person_id]
                guide["arguments"]["assembly_region_id"] = (
                    _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY[person_id])
                if previous is not None:
                    guide["prerequisites"] = [previous]
                expanded.append(guide)
                replacement_ids.append(guide["task_id"])
                previous = guide["task_id"]
            replacements[row["task_id"]] = replacement_ids
            continue
        if row["skill"] == "control_following_traffic":
            row["arguments"]["vehicle_ids"] = list(_CITY_NATIVE_FOLLOWING_VEHICLE_IDS)
        if row["skill"] == "report_incident":
            row["arguments"]["time_s"] = math.fsum(
                durations[task["skill"]] for task in expanded)
        expanded.append(row)
    result["id"] = result["id"].replace("city_witness_", "city_native_witness_", 1)
    result["outcome_support"] = {
        outcome: [new_id for old_id in references
                  for new_id in replacements.get(old_id, [old_id])]
        for outcome, references in result["outcome_support"].items()
    }
    result["plan"] = _plan(result["id"], result["plan"]["assigned_team"], expanded,
                           durations=durations)
    return result


def _apply_factor(public: dict[str, Any], factor_id: str, factor_level: str | None) -> dict[str, Any]:
    if factor_id not in _FACTOR_LEVELS:
        raise ValueError(f"unsupported goal factor: {factor_id}")
    if factor_level is None:
        factor_level = _DEFAULT_FACTOR_LEVELS[factor_id]
    if factor_level not in _FACTOR_LEVELS[factor_id]:
        raise ValueError(f"unsupported {factor_id} level: {factor_level}")
    public["experiment_factor"] = {
        "id": factor_id,
        "level": factor_level,
        "public_rule": _FACTOR_RULES[factor_id],
        "frozen_inputs": ["scene_layout", "business_objects", "candidate_robot_pool", "budgets", "model_budget"],
    }
    return public


def _communication_treatment(level: str, message_budget: int) -> dict[str, Any]:
    treatment = {
        "id": "cross_mobility_observation_delivery",
        "trigger": "first_cross_mobility_observation_before_first_business_commit",
        "operation": "deliver",
        "delay_s": 0.0,
        "drop": False,
        "bytes_per_window": message_budget,
        "window_s": 1.0,
        "publication_validity_s": 30.0,
    }
    if level == "delayed":
        treatment["delay_s"] = 5.0
    elif level == "dropped":
        treatment["operation"] = "drop"
        treatment["drop"] = True
    elif level == "bandwidth_limited":
        treatment["bytes_per_window"] = min(64, message_budget - 1)
    elif level == "stale":
        treatment["delay_s"] = 5.0
        treatment["publication_validity_s"] = 1.0
    elif level != "immediate":
        raise ValueError(f"unsupported communication treatment: {level}")
    return treatment


def _team_treatment(public: Mapping[str, Any], level: str) -> list[dict[str, Any]]:
    if level == "stable":
        return []
    scene_key = public["mission"]["scene_key"]
    target_id = _TEAM_TREATMENT_TARGETS[scene_key]
    target = deepcopy(next(
        row for row in public["candidate_robot_pool"] if row["id"] == target_id))
    critical_capability = {"factory": "stair_traversal", "city": "relay"}.get(scene_key, "pick")
    event = {
        "id": f"{scene_key}_{level}_treatment",
        "trigger": "after_first_cross_mobility_observation_before_first_business_commit",
        "event_type": level,
        "target_selector": {
            "mode": "selected_active_capability_member",
            "required_capability": critical_capability,
            "tie_break": "lowest_agent_id",
        },
        "agent_declaration": None,
        "capability_patch": None,
        "capability_version": 1,
    }
    if level == "member_join":
        declaration = deepcopy(target)
        declaration["id"] = f"{scene_key}_hidden_joiner_1"
        event["target_selector"] = {
            "mode": "fixed_hidden_identity",
            "agent_id": declaration["id"],
        }
        event["agent_declaration"] = declaration
    elif level == "capability_change":
        event["capability_patch"] = {"remove": [critical_capability], "add": []}
    elif level != "member_exit":
        raise ValueError(f"unsupported team treatment: {level}")
    return [event]


def _build_hidden_event_schedule(public: Mapping[str, Any]) -> dict[str, Any]:
    factor = public["experiment_factor"]
    communication_level = (
        factor["level"] if factor["id"] == "communication_quality" else "immediate")
    team_level = factor["level"] if factor["id"] == "team_continuity" else "stable"
    return {
        "visibility": "author_evaluator_only",
        "factor_id": factor["id"],
        "factor_level": factor["level"],
        "decision_window": {
            "opens_after": "first_cross_mobility_observation_published",
            "closes_before": "first_business_state_commit",
        },
        "communication_events": [
            _communication_treatment(communication_level, public["budgets"]["message_bytes"]),
        ],
        "team_events": _team_treatment(public, team_level),
    }


def build_goal_task_spec(scene_key: str, *, factor_id: str = "communication_quality",
                         factor_level: str | None = None,
                         mechanism_family: str | None = None,
                         pool_profile: str = "legacy_v1",
                         city_roster_profile: str = "abstract_v1") -> dict[str, Any]:
    if city_roster_profile != "abstract_v1" and scene_key != "city":
        raise ValueError("City roster profile applies only to the City scene")
    builders = {"factory": _factory, "warehouse_nvd": _warehouse, "modern_house_nvd": _house,
                "city": lambda: _city(city_roster_profile)}
    try:
        public, _ = builders[scene_key]()
    except KeyError as exc:
        raise ValueError(f"unsupported goal-driven scene: {scene_key}") from exc
    public = apply_pool_profile(public, pool_profile)
    if mechanism_family is not None:
        _apply_mechanism_template(public, build_mechanism_template(scene_key, mechanism_family))
    _apply_factor(public, factor_id, factor_level)
    validate_goal_task_spec(public)
    return public


def review_goal_business_witness(
        public_task: Mapping[str, Any], witness: Mapping[str, Any], *,
        constraint_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Reject numerically legal plans that cannot establish the scene's outcomes.

    This is a necessary-condition authoring check, not an execution scorer.
    Task ids are arbitrary. Checks use action semantics, causal support and the
    constraint model's final object/robot states. Receipts and observations still
    require external scene binding; a planned observation does not prove either.
    """
    if constraint_review is None:
        from .goal_driven_contract import validate_goal_plan
        constraint_review = validate_goal_plan(
            witness["plan"], task_spec=public_task)["constraint_review"]
    plan = witness["plan"]
    tasks = {row["task_id"]: row for row in plan["tasks"]}
    robots = {row["id"]: row for row in public_task["candidate_robot_pool"]}
    regions = {row["id"]: row for row in public_task["public_world"]["regions"]}
    checked: list[str] = []
    ancestors: dict[str, set[str]] = {}

    def parents(identifier: str) -> set[str]:
        if identifier not in ancestors:
            ancestors[identifier] = set(tasks[identifier]["prerequisites"])
            for predecessor in tasks[identifier]["prerequisites"]:
                ancestors[identifier].update(parents(predecessor))
        return ancestors[identifier]

    def require(condition: Any, code: str) -> None:
        if not condition:
            raise ValueError(
                f"witness {witness.get('id')}: business necessary condition failed: {code}")
        checked.append(code)

    support = witness.get("outcome_support", {})
    outcome_ids = {row["id"] for row in public_task["required_outcomes"]}
    registered_outcomes = {
        "factory": {"anomaly_confirmed", "response_area_served", "maintenance_handoff_recorded"},
        "warehouse_nvd": {"order_inventory_reconciled", "order_consolidated",
                          "dock_handoff_confirmed", "warehouse_closeout"},
        "modern_house_nvd": {"furniture_repositioned", "belonging_staged",
                             "resident_path_restored", "team_withdrawn"},
        "city": {"incident_assessed", "exposed_people_accounted_for",
                 "secondary_traffic_protected", "response_access_preserved"},
    }
    require(outcome_ids == registered_outcomes.get(public_task["mission"]["scene_key"]),
            "every_outcome_requires_registered_business_review")
    require(isinstance(support, Mapping) and set(support) == outcome_ids,
            "complete_outcome_support")
    coverage: dict[str, set[str]] = {}
    for outcome, references in support.items():
        require(isinstance(references, list) and references and all(
            isinstance(ref, str) and ref in tasks for ref in references),
            f"{outcome}:known_evidence_tasks")
        coverage[outcome] = set(references)
        for reference in references:
            coverage[outcome].update(parents(reference))

    def match(*, skills: set[str], mobility: str | None = None,
              outcome: str | None = None, **arguments: Any) -> list[Mapping[str, Any]]:
        return [row for row in tasks.values()
                if row["skill"] in skills
                and all(row["arguments"].get(key) == value for key, value in arguments.items())
                and (outcome is None or row["task_id"] in coverage[outcome])
                and (mobility is None or all(
                    robots[agent]["mobility"] == mobility for agent in row["assigned_agents"]))]

    def precedes(earlier: Mapping[str, Any], later: Mapping[str, Any]) -> bool:
        return earlier["task_id"] in parents(later["task_id"])

    def release_at(object_id: str, region_id: str, outcome: str) -> list[Mapping[str, Any]]:
        destination_fields = {"cooperative_carry": "destination_region_id",
                              "place": "placement_region_id", "handoff": "receiving_region_id"}
        return [row for skill, field in destination_fields.items()
                for row in match(skills={skill}, object_id=object_id, outcome=outcome,
                                 **{field: region_id})]

    def final_object(object_id: str, region_id: str) -> None:
        state = constraint_review["object_states"].get(object_id, {})
        require(state.get("region_id") == region_id and "carrier_bindings" not in state,
                f"{object_id}:released_at_{region_id}")

    def final_ground_withdrawals(outcome: str) -> list[Mapping[str, Any]]:
        withdrawals = []
        for agent in plan["assigned_team"]:
            if robots[agent]["mobility"] != "ground":
                continue
            final_region = constraint_review["robot_regions"].get(agent)
            require(final_region in regions and regions[final_region]["kind"] == "standby",
                    f"{agent}:final_standby_region")
            last = max((row for row in tasks.values() if agent in row["assigned_agents"]),
                       key=lambda row: row["execution"]["start_s"] + row["execution"]["duration_s"])
            require(last["skill"] == "ground_navigation"
                    and last["arguments"].get("region_id") == final_region
                    and last["task_id"] in coverage[outcome], f"{agent}:withdrawal_evidence")
            withdrawals.append(last)
        return withdrawals

    scene = public_task["mission"]["scene_key"]
    if scene == "warehouse_nvd":
        receipts_by_object: list[list[Mapping[str, Any]]] = []
        for object_id in ("order_crate_01", "order_carton_01"):
            inventory = match(skills={"observe"}, mobility="aerial", subject_ref=object_id,
                              observation_mode="rack_inventory", outcome="order_inventory_reconciled")
            collection = release_at(object_id, "consolidation_area", "order_consolidated")
            deliveries = release_at(object_id, "outbound_dock", "dock_handoff_confirmed")
            receipt_candidates = match(
                skills={"handoff"}, object_id=object_id, receiving_region_id="outbound_dock",
                recipient_role="dock_receiver", outcome="dock_handoff_confirmed") + match(
                skills={"observe"}, subject_ref=object_id, region_id="outbound_dock",
                observation_mode="dock_receiver_receipt", outcome="dock_handoff_confirmed")
            receipts = [receipt for receipt in receipt_candidates if any(
                (delivery["task_id"] == receipt["task_id"] or precedes(delivery, receipt))
                and any(precedes(staged, delivery) and any(precedes(scan, staged) for scan in inventory)
                        for staged in collection)
                for delivery in deliveries)]
            require(receipts, f"{object_id}:inventory_then_consolidation_then_dock_receipt")
            receipts_by_object.append(receipts)
            final_object(object_id, "outbound_dock")
        withdrawals = final_ground_withdrawals("warehouse_closeout")
        finals = match(skills={"observe"}, mobility="aerial", subject_ref="outbound_dock",
                       observation_mode="post_dispatch", outcome="warehouse_closeout")
        require(any(all(precedes(row, final) for row in withdrawals)
                    and all(any(precedes(receipt, final) for receipt in receipts)
                            for receipts in receipts_by_object) for final in finals),
                "warehouse_closeout:both_receipts_and_all_ground_withdrawals_before_scan")
    elif scene == "modern_house_nvd":
        moves = []
        for object_id, destination, outcome in (
                ("living_armchair_01", "furniture_parking", "furniture_repositioned"),
                ("resident_bookstack_01", "keepsake_staging", "belonging_staged")):
            observations = match(skills={"observe"}, mobility="aerial", subject_ref=object_id,
                                 observation_mode="home_access")
            releases = [row for row in release_at(object_id, destination, outcome)
                        if any(precedes(scan, row) for scan in observations)]
            require(releases, f"{object_id}:observed_then_relocated")
            moves.append(releases)
            final_object(object_id, destination)
        withdrawals = final_ground_withdrawals("team_withdrawn")
        finals = match(skills={"observe"}, mobility="aerial", subject_ref="resident_path",
                       observation_mode="final_clearance", outcome="resident_path_restored")
        require(any(all(precedes(row, final) for row in withdrawals)
                    and all(any(precedes(move, final) for move in releases) for releases in moves)
                    for final in finals), "resident_path:all_work_and_ground_withdrawals_before_scan")
    elif scene == "factory":
        overhead = match(skills={"observe"}, mobility="aerial", region_id="factory_overview",
                         subject_ref="production_anomaly_marker", observation_mode="overhead",
                         outcome="anomaly_confirmed")
        lower = match(skills={"observe"}, mobility="ground", region_id="assembly_cell",
                      subject_ref="production_anomaly_marker", observation_mode="close_view",
                      outcome="anomaly_confirmed")
        upper = match(skills={"observe"}, mobility="ground", region_id="mezzanine_workcell",
                      subject_ref="mezzanine_workcell", observation_mode="close_view",
                      outcome="anomaly_confirmed")
        arrivals = match(skills={"ground_navigation"}, region_id="assembly_cell",
                         outcome="response_area_served")
        climbs = match(skills={"stair_traversal"}, link_id="main_stair",
                       exit_region_id="mezzanine_workcell", outcome="response_area_served")
        lower_checks = [row for row in lower if any(precedes(scan, row) for scan in overhead)
                        and any(precedes(arrival, row) for arrival in arrivals)]
        upper_checks = [row for row in upper if any(precedes(scan, row) for scan in overhead)
                        and any(precedes(climb, row) for climb in climbs)]
        require(lower_checks and upper_checks, "factory:cross_view_checks_and_open_stair_arrival")
        returns = match(skills={"ground_navigation"}, region_id="response_staging")
        receipts = match(skills={"observe"}, mobility="ground", region_id="response_staging",
                         subject_ref="maintenance_service_cart", observation_mode="maintenance_service_receipt",
                         outcome="maintenance_handoff_recorded")
        require(any(any(precedes(row, receipt) for row in lower_checks)
                    and any(precedes(row, receipt) for row in upper_checks)
                    and any(precedes(row, receipt) and set(receipt["assigned_agents"]) <= set(row["assigned_agents"])
                            for row in returns) for receipt in receipts),
                "factory:service_receipt_after_field_checks_and_actual_return")
        final_object("maintenance_service_cart", "response_staging")
    elif scene == "city":
        if public_task["task_id"] in ("CITY-GOAL-NATIVE-01", "CITY-GOAL-NATIVE-02"):
            person_assembly = _CITY_NATIVE_AFFECTED_PERSON_ASSEMBLY
            following_ids = _CITY_NATIVE_FOLLOWING_VEHICLE_IDS
        else:
            person_assembly = _CITY_AFFECTED_PERSON_ASSEMBLY
            following_ids = _CITY_FOLLOWING_VEHICLE_IDS
        surveys = match(skills={"observe"}, mobility="aerial", region_id="city_incident_overview",
                        observation_mode="incident_overview", outcome="incident_assessed")
        require(surveys, "city:aerial_incident_observation_with_provenance")
        people_surveys = match(skills={"observe"}, mobility="aerial",
                               observation_mode="people_survey")
        require(people_surveys, "city:people_observation_available")
        guided: dict[str, Mapping[str, Any]] = {}
        for guidance in match(skills={"guide_people"}):
            assembly = guidance["arguments"]["assembly_region_id"]
            require(assembly in regions and regions[assembly]["kind"] == "assembly_candidate",
                    "city:guidance_targets_public_assembly_candidate")
            require(guidance["task_id"] in coverage["exposed_people_accounted_for"],
                    "city:guidance_inside_outcome_support")
            references = set(guidance["arguments"]["observation_refs"])
            require(references and references <= {scan["task_id"] for scan in people_surveys},
                    "city:guidance_cites_people_observation")
            require(any(precedes(tasks[reference], guidance) for reference in references),
                    "city:guidance_after_delivered_people_observation")
            for person_id in guidance["arguments"]["person_ids"]:
                require(person_id in person_assembly,
                        "city:guidance_references_frozen_roster")
                require(person_assembly[person_id] == assembly,
                        "city:person_bound_assembly_respected")
                require(person_id not in guided, "city:each_person_guided_exactly_once")
                guided[person_id] = guidance
        require(set(guided) == set(person_assembly), "city:full_roster_guided")
        traffic_surveys = match(skills={"observe"}, mobility="aerial",
                                observation_mode="following_traffic_survey")
        controls = match(skills={"control_following_traffic"})
        require(controls, "city:traffic_control_present")
        for control in controls:
            require(control["task_id"] in coverage["secondary_traffic_protected"],
                    "city:traffic_control_inside_outcome_support")
            require(set(control["arguments"]["vehicle_ids"]) == set(following_ids),
                    "city:control_covers_frozen_following_traffic")
            references = set(control["arguments"]["observation_refs"])
            require(references and references <= {scan["task_id"] for scan in traffic_surveys},
                    "city:control_cites_traffic_observation")
            require(any(precedes(tasks[reference], control) for reference in references),
                    "city:control_after_delivered_traffic_observation")
        readbacks = match(skills={"observe"}, mobility="aerial",
                          observation_mode="controlled_traffic_readback",
                          outcome="secondary_traffic_protected")
        require(readbacks, "city:measured_traffic_readback_present")
        require(all(any(precedes(control, readback) for control in controls) for readback in readbacks),
                "city:readback_after_acknowledged_control")
        access_checks = match(skills={"observe"}, mobility="aerial",
                              observation_mode="response_access_clearance",
                              outcome="response_access_preserved")
        reports = match(skills={"report_incident"}, outcome="response_access_preserved")
        require(access_checks and reports, "city:continuous_access_and_report_present")
        for report in reports:
            require(report["arguments"]["recipient_service_id"] == _CITY_RESPONSE_SERVICE_ID,
                    "city:report_uses_frozen_response_service_endpoint")
            references = set(report["arguments"]["observation_refs"])
            require(any(check["task_id"] in references for check in access_checks),
                    "city:report_cites_continuous_access_check")
            require(any(survey["task_id"] in references for survey in surveys),
                    "city:report_cites_incident_observation")
        services = [row for row in tasks.values()
                    if row["skill"] in {"guide_people", "control_following_traffic"}]
        require(any(all(precedes(service, check) for service in services) for check in access_checks),
                "city:access_check_after_all_business_services")
        require(any(all(precedes(check, report) for check in access_checks) for report in reports),
                "city:report_after_continuous_access_check")
    else:
        raise ValueError(f"no symbolic business review for scene: {scene}")
    return {
        "validated": True, "witness_id": witness["id"], "scene_key": scene,
        "scope": "symbolic_business_necessary_conditions", "checked_conditions": sorted(checked),
        "runtime_outcomes_validated": False, "observation_visibility_validated": False,
        "receipt_authenticity_validated": False,
    }


def review_goal_task_design(public_task: Mapping[str, Any], witnesses: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Author-only admission for a goal task, without asserting execution."""
    from .goal_driven_contract import validate_goal_plan

    validate_goal_task_spec(public_task)
    if not isinstance(witnesses, list) or len(witnesses) < 2:
        raise ValueError("goal task needs at least two witnesses")
    outcome_ids = {row["id"] for row in public_task["required_outcomes"]}
    signatures = set()
    team_signatures = set()
    business_reviews = []
    numerical_reviews = []
    for witness in witnesses:
        plan_review = validate_goal_plan(witness["plan"], task_spec=public_task)
        if set(witness["satisfies_outcomes"]) != outcome_ids:
            raise ValueError(f"witness {witness.get('id')} does not cover every outcome")
        support = witness.get("outcome_support")
        if not isinstance(support, Mapping) or set(support) != outcome_ids:
            raise ValueError(f"witness {witness.get('id')} lacks outcome evidence bindings")
        task_ids = {task["task_id"] for task in witness["plan"]["tasks"]}
        if any(not isinstance(refs, list) or not refs or not set(refs) <= task_ids
               for refs in support.values()):
            raise ValueError(f"witness {witness.get('id')} has invalid outcome evidence bindings")
        business_reviews.append(review_goal_business_witness(
            public_task, witness, constraint_review=plan_review["constraint_review"]))
        numerical_reviews.append({"witness_id": witness["id"], **plan_review["constraint_review"]})
        signatures.add(semantic_goal_plan_signature(witness["plan"]))
        team_signatures.add(tuple(sorted(witness["plan"]["assigned_team"])))
    if len(signatures) < 2:
        raise ValueError("goal task has only one semantically distinct legal plan witness")
    heterogeneity = build_goal_heterogeneity_certificate(public_task, witnesses)
    return {
        "validated": True,
        "scene_key": public_task["mission"]["scene_key"],
        "outcome_count": len(outcome_ids),
        "witness_count": len(witnesses),
        "distinct_plan_count": len(signatures),
        "distinct_team_count": len(team_signatures),
        "business_witness_reviews": business_reviews,
        "quantitative_authoring_review": numerical_reviews,
        "heterogeneity_certificate_sha256": canonical_sha256(heterogeneity),
        "authoring_status": "passed",
        "execution_status": "external_pending",
        "scoreable": False,
        "review_scope": [
            "business_outcomes_and_scene_native_objects",
            "public_information_boundary",
            "capability_compatible_witness_plans",
            "symbolic_business_necessary_conditions_and_final_states",
            "multiple_legal_decompositions",
            "declaration_level_cross_mobility_counterfactuals",
        ],
        "not_checked": [
            "reliable-skill backend conformance to frozen physical and resource parameters",
            "USD object, geometry, observation visibility and occlusion binding",
            "live communication effect",
            "hardware payload ratings, contact dynamics or autonomous grasp success (outside main-track scope)",
            "hidden-event replay and outcome scoring",
        ],
    }


def build_goal_task_package(scene_key: str, *, factor_id: str = "communication_quality",
                            factor_level: str | None = None,
                            mechanism_family: str | None = None,
                            pool_profile: str = "legacy_v1",
                            city_roster_profile: str = "abstract_v1") -> dict[str, dict[str, Any]]:
    if city_roster_profile != "abstract_v1" and scene_key != "city":
        raise ValueError("City roster profile applies only to the City scene")
    builders = {"factory": _factory, "warehouse_nvd": _warehouse, "modern_house_nvd": _house,
                "city": lambda: _city(city_roster_profile)}
    try:
        public, witnesses = builders[scene_key]()
    except KeyError as exc:
        raise ValueError(f"unsupported goal-driven scene: {scene_key}") from exc
    mechanism_template = None
    if mechanism_family is not None:
        mechanism_template = build_mechanism_template(scene_key, mechanism_family)
        _apply_mechanism_template(public, mechanism_template)
    _apply_factor(public, factor_id, factor_level)
    public = apply_pool_profile(public, pool_profile)
    if mechanism_template is not None:
        _apply_mechanism_probe_to_witnesses(witnesses, public, mechanism_family)
    validate_goal_task_spec(public)
    review = review_goal_task_design(public, witnesses)
    api = build_goal_participant_api(public)
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
        "measurement_contract": (
            deepcopy(mechanism_template["measurement_contract"])
            if mechanism_template is not None else _legacy_measurement_contract(public)
        ),
        "measurement_status": "classified" if mechanism_template is not None else "unclassified",
        "mechanism_template": deepcopy(mechanism_template) if mechanism_template is not None else None,
        "team_necessity_certificate": (
            deepcopy(mechanism_template["team_necessity_certificate"])
            if mechanism_template is not None else None
        ),
        "release_gate": {
            "authoring_status": review["authoring_status"],
            "execution_status": review["execution_status"],
            "benchmark_ready": False,
            "scoreable": False,
            "blockers": [
                "reliable_skill_backend_conformance",
                "scene_geometry_and_object_binding",
                "declared_visibility_and_occlusion_binding",
                "business_evidence_and_receipt_binding",
                "communication_factor_effect_validation",
                "team_continuity_factor_effect_validation",
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
    bundle = {"public_task": public, "participant_api": api,
              "hidden_evaluator": hidden, "baseline_manifest": baseline}
    validate_goal_bundle(bundle)
    return bundle


def generate_goal_mechanism_templates(
        scene_keys: list[str] | None = None,
        *, mechanisms: list[str] | None = None,
        factor_id: str = "communication_quality",
        factor_level: str | None = None,
        pool_profile: str = "legacy_v1",
        output_root: str | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    """Generate the four-scene M0 matrix (16 canonical goal bundles).

    The returned mapping is keyed by stable ``scene.m0.mechanism`` template
    identifiers.  ``output_root`` uses a fresh directory per template and
    never overwrites an existing authoring artifact.
    """
    scenes = list(scene_keys) if scene_keys is not None else [
        row["scene_key"] for row in registered_goal_scenes()
        if row["package_kind"] == "goal_bundle"
    ]
    selected_mechanisms = list(mechanisms) if mechanisms is not None else list(M0_MECHANISMS)
    if not scenes or len(scenes) != len(set(scenes)):
        raise ValueError("mechanism template generation requires distinct scenes")
    if not selected_mechanisms or len(selected_mechanisms) != len(set(selected_mechanisms)):
        raise ValueError("mechanism template generation requires distinct mechanisms")
    packages: dict[str, dict[str, dict[str, Any]]] = {}
    for scene in scenes:
        for mechanism in selected_mechanisms:
            template_id = canonical_template_id(scene, mechanism)
            bundle = build_goal_task_package(
                scene, factor_id=factor_id, factor_level=factor_level,
                mechanism_family=mechanism, pool_profile=pool_profile)
            packages[template_id] = bundle
            if output_root is not None:
                root = Path(output_root) / template_id
                write_goal_task_bundle(bundle, root)
                write_goal_authoring_brief(
                    build_goal_authoring_brief(scene, pool_profile=pool_profile), root / "authoring")
                if pool_profile != "legacy_v1":
                    design = pool_design_summary(bundle["public_task"], pool_profile)
                    validate_pool_design_summary(design, bundle["public_task"])
                    (root / "authoring" / "candidate_pool_design.json").write_text(
                        json.dumps(design, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                        encoding="utf-8")
                review = review_goal_task_design(
                    bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
                (root / "authoring" / "review.json").write_text(
                    json.dumps(review, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return packages


# More discoverable alias used by release tooling and external authoring jobs.
generate_canonical_goal_task_packages = generate_goal_mechanism_templates


def validate_goal_factor_pair(
        control: Mapping[str, Mapping[str, Any]],
        variant: Mapping[str, Mapping[str, Any]], *, factor_id: str) -> dict[str, Any]:
    """Validate one-factor isolation for two generated goal-task bundles."""
    if factor_id not in _FACTOR_LEVELS:
        raise ValueError(f"unsupported goal factor: {factor_id}")
    control_public = control["public_task"]
    variant_public = variant["public_task"]
    validate_goal_task_spec(control_public)
    validate_goal_task_spec(variant_public)
    if (control_public["task_id"] != variant_public["task_id"]
            or control_public["mission"]["scene_key"] != variant_public["mission"]["scene_key"]):
        raise ValueError("goal factor pair must use the same task and scene")
    control_factor = control_public["experiment_factor"]
    variant_factor = variant_public["experiment_factor"]
    if control_factor["id"] != factor_id or variant_factor["id"] != factor_id:
        raise ValueError("goal factor pair does not use the named factor")
    if control_factor["level"] == variant_factor["level"]:
        raise ValueError("goal factor pair must change the selected factor level")

    frozen_public = []
    for public in (control_public, variant_public):
        normalized_public = deepcopy(dict(public))
        normalized_public["experiment_factor"]["level"] = "<target-level>"
        frozen_public.append(normalized_public)
    if frozen_public[0] != frozen_public[1]:
        raise ValueError("goal factor pair changed a frozen task, team, budget, or evaluator input")
    validate_goal_bundle(control)
    validate_goal_bundle(variant)

    control_schedule = control["hidden_evaluator"]["hidden_event_schedule"]
    variant_schedule = variant["hidden_evaluator"]["hidden_event_schedule"]
    target_key = "communication_events" if factor_id == "communication_quality" else "team_events"
    frozen_key = "team_events" if factor_id == "communication_quality" else "communication_events"
    if control_schedule[target_key] == variant_schedule[target_key]:
        raise ValueError("factor labels changed without a changed hidden treatment")
    if control_schedule[frozen_key] != variant_schedule[frozen_key]:
        raise ValueError(f"non-target hidden treatment changed: {frozen_key}")

    def normalized(bundle: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        result = deepcopy(dict(bundle))
        result["public_task"]["experiment_factor"]["level"] = "<target-level>"
        result["participant_api"]["public_task_sha256"] = "<derived-public-hash>"
        result["baseline_manifest"]["public_task_sha256"] = "<derived-public-hash>"
        hidden = result["hidden_evaluator"]
        hidden["public_task_sha256"] = "<derived-public-hash>"
        hidden["heterogeneity_certificate"]["capability_audit"]["public_task_sha256"] = "<derived-public-hash>"
        hidden["hidden_event_schedule"]["factor_level"] = "<target-level>"
        hidden["hidden_event_schedule"][target_key] = "<target-treatment>"
        return result

    normalized_control = normalized(control)
    normalized_variant = normalized(variant)
    if normalized_control != normalized_variant:
        raise ValueError("goal factor pair changed a frozen task, team, budget, or evaluator input")
    return {
        "validated": True,
        "scene_key": control_public["mission"]["scene_key"],
        "factor_id": factor_id,
        "control_level": control_factor["level"],
        "variant_level": variant_factor["level"],
        "visibility": "author_evaluator_only",
        "frozen_input_sha256": canonical_sha256(normalized_control),
        "factor_effect_validated": False,
        "execution_validated": False,
    }


def build_goal_authoring_brief(scene_key: str, *, pool_profile: str = "legacy_v1") -> dict[str, Any]:
    """Return a scene-bounded prompt contract for an authoring LLM."""
    public = build_goal_task_spec(scene_key, pool_profile=pool_profile)
    capabilities = sorted({
        capability
        for robot in public["candidate_robot_pool"]
        for capability in robot["public_capabilities"]
    })
    brief = {
        "schema": "eai.benchmark.goal_authoring_brief",
        "version": 3,
        "visibility": "author_only",
        "scene_key": scene_key,
        "pool_profile": pool_profile,
        "business_family": _AUTHORING_BUSINESS_FAMILIES[scene_key],
        "allowed_object_categories": sorted({
            row["category"] for row in public["public_world"]["objects"]
        }),
        "allowed_region_kinds": sorted({
            row["kind"] for row in public["public_world"]["regions"]
        }),
        "allowed_capabilities": capabilities,
        "quantitative_boundary": quantitative_authoring_boundary(public),
        "required_outcome_themes": [
            {"id": row["id"], "business_meaning": row["description"]}
            for row in public["required_outcomes"]
        ],
        "factor_axes": {
            factor: sorted(levels) for factor, levels in _FACTOR_LEVELS.items()
        },
        "generation_requirements": [
            "Use only objects, regions and robot capabilities declared by the selected scene instance.",
            "Express required outcomes as business state and evidence expectations, without execution order.",
            "Outcome ids bind registered business semantics; new outcome ids or changed semantics require a new authoring review rule before admission.",
            "Require a meaningful aerial-to-ground or ground-to-aerial information dependency.",
            "Keep navigation and simple aerial obstacle avoidance as execution prerequisites, not scored collaboration.",
            "Define one experiment factor per instance and freeze scene, objects, candidate pool and budgets.",
            "Use frozen numerical capacities, object-local grasp points, interface compatibility and load fractions; never retune them to rescue an infeasible plan.",
            "Derive carrier counts from feasible handling modes and per-carrier payload, not a requires_cooperative_carry label.",
            "Check the whole body/carried formation envelope plus both clearance margins, directed route continuity and shared-resource capacity.",
            "Author timed tasks with explicit routes, dependency ordering and object ownership; cooperative_carry includes acquisition, transport and release.",
            "Use one initial object state for every witness; all warehouse order objects require receiving evidence and home verification follows ground-team withdrawal.",
            "Disclose benchmark-defined parameter provenance and pending scene/visibility binding; do not claim hardware validation or make VLA grasp success a main-track requirement.",
        ],
        "forbidden_public_fields": [
            "affected_node_ids", "eligible_agent_ids", "future_event_time",
            "ground_truth", "preferred_agent", "reference_assignment",
            "reference_dag", "reference_plan", "witness_plans",
        ],
        "admission_checks": [
            "public information is sufficient to identify legal skills, regions, objects and candidate capabilities",
            "at least two semantically distinct author-only witness plans satisfy every outcome",
            "distinguish witness dependence on a mobility class from impossibility after replanning or a matched-budget alternative team",
            "quantitative admission checks all witness loads, interfaces, formation clearances, temporal dependencies and resource conflicts",
            "the selected factor has a concrete hidden treatment inside a decision-relevant window",
            "execution and factor-effect claims remain pending until external evidence exists",
        ],
        "output_contract": {
            "public_schema": GOAL_TASK_SCHEMA,
            "evaluation_track": "goal_driven_team_planning_v1",
            "participant_outputs": [
                "team selection", "agent-defined plan", "plan revisions",
                "skill dispatches", "message consumption",
            ],
            "author_private_artifacts": [
                "semantic plan witnesses", "outcome-to-task evidence bindings",
                "heterogeneity counterfactual certificate", "factor treatment",
                "numerical feasibility review and declared binding limitations",
            ],
        },
    }
    validate_goal_authoring_brief(brief)
    return brief


def validate_goal_authoring_brief(brief: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(brief, Mapping):
        raise ValueError("goal authoring brief must be an object")
    expected = {"schema", "version", "visibility", "scene_key", "pool_profile", "business_family",
                "allowed_object_categories", "allowed_region_kinds", "allowed_capabilities",
                "required_outcome_themes", "factor_axes", "generation_requirements",
                "forbidden_public_fields", "admission_checks", "output_contract", "quantitative_boundary"}
    if set(brief) != expected:
        raise ValueError("goal authoring brief fields mismatch")
    scene_key = brief["scene_key"]
    if scene_key not in _AUTHORING_BUSINESS_FAMILIES:
        raise ValueError("goal authoring brief uses an unsupported scene")
    if (brief["schema"] != "eai.benchmark.goal_authoring_brief"
            or brief["version"] != 3 or brief["visibility"] != "author_only"):
        raise ValueError("unsupported goal authoring brief schema")
    if brief["pool_profile"] not in POOL_PROFILES:
        raise ValueError("goal authoring brief uses an unsupported candidate-pool profile")
    public = build_goal_task_spec(scene_key, pool_profile=brief["pool_profile"])
    expected_categories = sorted({row["category"] for row in public["public_world"]["objects"]})
    expected_regions = sorted({row["kind"] for row in public["public_world"]["regions"]})
    expected_capabilities = sorted({
        capability for robot in public["candidate_robot_pool"]
        for capability in robot["public_capabilities"]
    })
    if brief["business_family"] != _AUTHORING_BUSINESS_FAMILIES[scene_key]:
        raise ValueError("goal authoring brief business family mismatch")
    if brief["allowed_object_categories"] != expected_categories:
        raise ValueError("goal authoring brief contains out-of-scene object categories")
    if brief["allowed_region_kinds"] != expected_regions:
        raise ValueError("goal authoring brief contains out-of-scene region kinds")
    if brief["allowed_capabilities"] != expected_capabilities:
        raise ValueError("goal authoring brief capability vocabulary mismatch")
    if brief["quantitative_boundary"] != quantitative_authoring_boundary(public):
        raise ValueError("goal authoring brief changed frozen quantitative parameters")
    themes = brief["required_outcome_themes"]
    expected_themes = [
        {"id": row["id"], "business_meaning": row["description"]}
        for row in public["required_outcomes"]
    ]
    if themes != expected_themes:
        raise ValueError("goal authoring brief outcome themes mismatch")
    if ({name: set(levels) for name, levels in brief["factor_axes"].items()}
            != _FACTOR_LEVELS):
        raise ValueError("goal authoring brief factor axes mismatch")
    for key in ("generation_requirements", "forbidden_public_fields", "admission_checks"):
        values = brief[key]
        if (not isinstance(values, list) or not values
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))):
            raise ValueError(f"goal authoring brief {key} must be a unique nonempty string list")
    required_forbidden = {"ground_truth", "preferred_agent", "reference_dag",
                          "reference_plan", "witness_plans"}
    if not required_forbidden <= set(brief["forbidden_public_fields"]):
        raise ValueError("goal authoring brief does not protect the answer boundary")
    output = brief["output_contract"]
    if (not isinstance(output, Mapping)
            or set(output) != {"public_schema", "evaluation_track", "participant_outputs",
                              "author_private_artifacts"}
            or output["public_schema"] != GOAL_TASK_SCHEMA
            or output["evaluation_track"] != "goal_driven_team_planning_v1"):
        raise ValueError("goal authoring brief output contract mismatch")
    return {"validated": True, "scene_key": scene_key, "sha256": canonical_sha256(brief)}


def write_goal_authoring_brief(brief: Mapping[str, Any], output_dir: str | Path) -> Path:
    validate_goal_authoring_brief(brief)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    path = output_dir / "goal_authoring_brief.json"
    path.write_text(json.dumps(brief, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def generate_goal_task_packages(scene_keys: list[str] | None = None, *, factor_id: str = "communication_quality",
                                factor_level: str | None = None,
                                pool_profile: str = "legacy_v1",
                                output_root: str | None = None) -> dict[str, dict[str, Any]]:
    """Generate and optionally publish the four registered goal packages.

    ``output_root`` is an author-controlled output directory. Existing scene
    directories are never overwritten by ``write_goal_task_bundle``.
    """
    if scene_keys is None:
        keys = [row["scene_key"] for row in registered_goal_scenes()
                if row["package_kind"] == "goal_bundle"]
    else:
        keys = list(scene_keys)
    packages = {scene: build_goal_task_package(
                    scene, factor_id=factor_id, factor_level=factor_level,
                    pool_profile=pool_profile)
                for scene in keys}
    if output_root is not None:
        root = Path(output_root)
        for scene, bundle in packages.items():
            write_goal_task_bundle(bundle, root / scene)
            write_goal_authoring_brief(
                build_goal_authoring_brief(scene, pool_profile=pool_profile), root / scene / "authoring")
            if pool_profile != "legacy_v1":
                design = pool_design_summary(bundle["public_task"], pool_profile)
                validate_pool_design_summary(design, bundle["public_task"])
                (root / scene / "authoring" / "candidate_pool_design.json").write_text(
                    json.dumps(design, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            review = review_goal_task_design(bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
            (root / scene / "authoring" / "review.json").write_text(
                json.dumps(review, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return packages


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=["all", *[row["scene_key"] for row in registered_goal_scenes()]],
                        default="all")
    parser.add_argument("--factor-id", choices=["communication_quality", "team_continuity"],
                        default="communication_quality")
    parser.add_argument("--mechanism-family", choices=list(M0_MECHANISMS),
                        help="generate classified canonical M0 bundles for this mechanism")
    parser.add_argument("--pool-profile", choices=list(POOL_PROFILES), default="legacy_v1",
                        help="candidate-pool design; expanded_v1 is opt-in and execution-pending")
    parser.add_argument("--factor-level", choices=["immediate", "delayed", "dropped", "bandwidth_limited", "stale",
                                                    "stable", "member_exit", "member_join", "capability_change"])
    parser.add_argument("--output-root", help="write release/evaluator artifacts below this directory")
    args = parser.parse_args(argv)
    scenes = None if args.scene == "all" else [args.scene]
    if args.mechanism_family is None:
        packages = generate_goal_task_packages(
            scenes, factor_id=args.factor_id, factor_level=args.factor_level,
            pool_profile=args.pool_profile, output_root=args.output_root)
    else:
        packages = generate_goal_mechanism_templates(
            scenes, mechanisms=[args.mechanism_family], factor_id=args.factor_id,
            factor_level=args.factor_level, pool_profile=args.pool_profile,
            output_root=args.output_root)
    print(json.dumps({scene: {
        "task_id": bundle["public_task"]["task_id"],
        "authoring_status": bundle["hidden_evaluator"]["release_gate"]["authoring_status"],
        "execution_status": bundle["hidden_evaluator"]["release_gate"]["execution_status"],
        "scoreable": bundle["hidden_evaluator"]["release_gate"]["scoreable"],
    } for scene, bundle in packages.items()}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


__all__ = [
    "build_goal_authoring_brief", "build_goal_task_package", "build_goal_task_spec",
    "generate_canonical_goal_task_packages", "generate_goal_mechanism_templates",
    "generate_goal_task_packages", "main", "review_goal_task_design",
    "review_goal_business_witness", "validate_city_spawn_points",
    "validate_goal_authoring_brief", "validate_goal_factor_pair",
    "write_goal_authoring_brief",
]


if __name__ == "__main__":
    raise SystemExit(main())
