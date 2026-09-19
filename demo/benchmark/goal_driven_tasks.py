"""Scene-native goal-driven task families for Factory, Warehouse and House.

The returned public packet is intentionally outcome based.  The witness plans
are author/evaluator material and are never copied into the participant task.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .goal_driven_contract import (
    GOAL_BASELINE_SCHEMA,
    GOAL_CONTRACT_VERSION,
    GOAL_HIDDEN_EVALUATOR_SCHEMA,
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
from .skill_ontology import SKILL_CATALOG_SCHEMA, SKILL_CATALOG_VERSION
from .goal_driven_fixtures import (
    SKILL_DURATIONS_S,
    execution_contract,
    object_physical_properties,
    quantitative_authoring_boundary,
    region_resource_capacity,
    robot_physical_profile,
    scene_routes,
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
}
_AUTHORING_BUSINESS_FAMILIES = {
    "factory": "production_anomaly_confirmation_and_maintenance_dispatch",
    "warehouse_nvd": "order_fulfilment_inventory_and_dock_closeout",
    "modern_house_nvd": "resident_home_recovery_and_access_restoration",
}


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


def _plan(plan_id: str, team: list[str], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    # These are deliberately conservative author witnesses, not optimal schedules.
    # Serial intervals avoid silently outsourcing resource ordering to navigation.
    scheduled = deepcopy(tasks)
    clock_s = 0.0
    for task in scheduled:
        duration_s = SKILL_DURATIONS_S[task["skill"]]
        task["execution"] = {"start_s": clock_s, "duration_s": duration_s}
        clock_s += duration_s
    return {"schema": "eai.benchmark.goal_plan", "version": 2,
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
    return public, [_warehouse_witness(public, "a", "warehouse_ground_1", carton_first=False),
                    _warehouse_witness(public, "b", "warehouse_ground_3", carton_first=True)]


def _warehouse_witness(public: dict[str, Any], suffix: str, picker: str, *,
                       carton_first: bool) -> dict[str, Any]:
    """Two orders of work under exactly the same initial object state."""
    aerial, partner = "warehouse_aerial_1", "warehouse_ground_2"
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
        "HOUSE-GOAL-01", "modern_house_nvd",
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
    aerial, partner = "house_aerial_1", "house_ground_2"
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
    critical_capability = "stair_traversal" if scene_key == "factory" else "pick"
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
                         factor_level: str | None = None) -> dict[str, Any]:
    builders = {"factory": _factory, "warehouse_nvd": _warehouse, "modern_house_nvd": _house}
    try:
        public, _ = builders[scene_key]()
    except KeyError as exc:
        raise ValueError(f"unsupported goal-driven scene: {scene_key}") from exc
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
                            factor_level: str | None = None) -> dict[str, dict[str, Any]]:
    builders = {"factory": _factory, "warehouse_nvd": _warehouse, "modern_house_nvd": _house}
    try:
        public, witnesses = builders[scene_key]()
    except KeyError as exc:
        raise ValueError(f"unsupported goal-driven scene: {scene_key}") from exc
    _apply_factor(public, factor_id, factor_level)
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


def build_goal_authoring_brief(scene_key: str) -> dict[str, Any]:
    """Return a scene-bounded prompt contract for an authoring LLM."""
    public = build_goal_task_spec(scene_key)
    capabilities = sorted({
        capability
        for robot in public["candidate_robot_pool"]
        for capability in robot["public_capabilities"]
    })
    brief = {
        "schema": "eai.benchmark.goal_authoring_brief",
        "version": 2,
        "visibility": "author_only",
        "scene_key": scene_key,
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
    expected = {"schema", "version", "visibility", "scene_key", "business_family",
                "allowed_object_categories", "allowed_region_kinds", "allowed_capabilities",
                "required_outcome_themes", "factor_axes", "generation_requirements",
                "forbidden_public_fields", "admission_checks", "output_contract", "quantitative_boundary"}
    if set(brief) != expected:
        raise ValueError("goal authoring brief fields mismatch")
    scene_key = brief["scene_key"]
    if scene_key not in _AUTHORING_BUSINESS_FAMILIES:
        raise ValueError("goal authoring brief uses an unsupported scene")
    if (brief["schema"] != "eai.benchmark.goal_authoring_brief"
            or brief["version"] != 2 or brief["visibility"] != "author_only"):
        raise ValueError("unsupported goal authoring brief schema")
    public = build_goal_task_spec(scene_key)
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
                                output_root: str | None = None) -> dict[str, dict[str, Any]]:
    """Generate and optionally publish the three goal-driven packages.

    ``output_root`` is an author-controlled output directory. Existing scene
    directories are never overwritten by ``write_goal_task_bundle``.
    """
    keys = scene_keys or ["factory", "warehouse_nvd", "modern_house_nvd"]
    packages = {scene: build_goal_task_package(scene, factor_id=factor_id, factor_level=factor_level)
                for scene in keys}
    if output_root is not None:
        root = Path(output_root)
        for scene, bundle in packages.items():
            write_goal_task_bundle(bundle, root / scene)
            write_goal_authoring_brief(build_goal_authoring_brief(scene), root / scene / "authoring")
            review = review_goal_task_design(bundle["public_task"], bundle["hidden_evaluator"]["witness_plans"])
            (root / scene / "authoring" / "review.json").write_text(
                json.dumps(review, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return packages


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=["all", "factory", "warehouse_nvd", "modern_house_nvd"],
                        default="all")
    parser.add_argument("--factor-id", choices=["communication_quality", "team_continuity"],
                        default="communication_quality")
    parser.add_argument("--factor-level", choices=["immediate", "delayed", "dropped", "bandwidth_limited", "stale",
                                                    "stable", "member_exit", "member_join", "capability_change"])
    parser.add_argument("--output-root", help="write release/evaluator artifacts below this directory")
    args = parser.parse_args(argv)
    scenes = None if args.scene == "all" else [args.scene]
    packages = generate_goal_task_packages(scenes, factor_id=args.factor_id, factor_level=args.factor_level,
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
    "generate_goal_task_packages", "main", "review_goal_task_design",
    "review_goal_business_witness",
    "validate_goal_authoring_brief", "validate_goal_factor_pair",
    "write_goal_authoring_brief",
]


if __name__ == "__main__":
    raise SystemExit(main())
