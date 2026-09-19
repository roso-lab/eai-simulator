"""Pure author-side semantics catalog and audit for three benchmark families.

Legacy embedded declarations remain schema/hash compatible. The separate design
catalog maps concrete objectives and qualifies actual code support. Neither
metadata validation nor this audit supplies episode evidence or semantic scoring.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from typing import Any, Mapping


COMMUNICATION_CONDITIONS = {"immediate", "delayed", "dropped", "bandwidth_limited", "stale"}
LIFECYCLE_EVENTS = {"member_join", "member_exit", "capability_change"}

# Code-path inventory, not run evidence. None of these rows certifies a release.
# This module deliberately imports neither runtime nor the scene compiler.
SEMANTIC_VERSION = 2


def _support(status, scope, source):
    return {"status": status, "scope": scope, "source": source,
            "execution_validated": False}


def implementation_support(scene_key):
    """Explicitly distinguish code availability, partial plumbing and proposals."""
    factory = scene_key == "factory"
    _scene_semantics(scene_key)  # Reject unsupported scenes.
    return {
        "lifecycle": {
            "member_exit": _support("conditional_code_path",
                "Only ground-only scene-neutral tasks or F-COV-01; F-INS dispatch rejects faults. "
                "Replacement must already be in the initial fleet. When a live environment binding "
                "is available, the exiting member's command gate closes before the public event; "
                "forced release still uses object XY and configured spawn Z, not measured support height. "
                "Current mixed-aerial templates are excluded and no physical exit episode has passed.",
                "demo/benchmark/runtime.py:execution_contract,plan_member_exit_release; demo/benchmark/isaac_env_lifecycle.py; EAI.scenario_spec.validation"),
            "member_join": _support("runtime_callback_boundary", "A declared preloaded reserve is hidden from the initial fleet, pinned at its authored parking pose by the MultiRobotDirectEnv command gate, activated through a strict callback, and published only after articulation/readback evidence. Maintained scene reserve layouts and reference episodes remain pending; dynamic spawn is not claimed.",
                "demo/benchmark/runtime.py; demo/benchmark/participant_runtime.py; demo/benchmark/isaac_env_lifecycle.py; EAI.hmrs_env.multi_robot_direct_env"),
            "capability_change": _support("runtime_callback_boundary", "Versioned navigation_interface changes, qualification checks and release obligations use the same MultiRobotDirectEnv state that admits controller output. Disabling navigation pins the articulation and blocks primary and auxiliary control before exact readback. Attachment, sensor and payload changes still require a separate scene adapter; reference episodes remain pending.",
                "demo/benchmark/runtime.py; demo/benchmark/participant_runtime.py; demo/benchmark/isaac_env_lifecycle.py; EAI.hmrs_env.multi_robot_direct_env"),
        },
        "communication": {
            "immediate": _support("conditional_code_path" if factory else "runtime_component",
                "F-INS shared mode delivers decoded marker messages." if factory else
                "Captured observations enter the participant queue and gate dependent objectives after explicit consumption; scene semantic detectors remain pending.",
                "demo/factory_benchmark/inventory_dispatch.py:delivered_messages; demo/benchmark/runtime.py"),
            "delayed": _support("protocol_component" if factory else "runtime_component",
                "Deterministic recipient/channel delivery queues and future-publication condition changes are wired to the generic participant loop; live rack/room semantic adapters remain pending.",
                "demo/benchmark/communication.py; demo/benchmark/interventions.py"),
            "dropped": _support("protocol_component" if factory else "runtime_component",
                "Selective deterministic packet dropping with private ledger and replay gates dependent generic-scene objectives; this is not a measured radio model.",
                "demo/benchmark/communication.py"),
            "bandwidth_limited": _support("protocol_component" if factory else "runtime_component", "UTF-8 byte budgets, FIFO windows and replay are wired to generic participant observation delivery; queued bandwidth changes are explicitly rejected.",
                "demo/benchmark/communication.py"),
            "stale": _support("conditional_code_path" if factory else "runtime_component",
                "Expiry rejection exists in ObservationStore; F-INS also filters delivered messages by validity. "
                "Generic scene consumption is applied only after a fresh message is delivered.",
                "demo/benchmark/observations.py:ObservationStore.consume; demo/factory_benchmark/inventory_dispatch.py:delivered_messages"),
        },
        "participant_actions": {
            "implemented_scope": (["navigate_goal", "wait", "consume_message"] if factory else
                                  ["allocate", "reassign", "navigate_goal", "consume_message"]),
            "restriction": ("F-INS route_confirmation only; other three phases are benchmark-managed." if factory else
                "Applied on phase_ready; transport allows team assignment, not navigate_goal retargeting. "
                "consume_message is accepted only on a delivered-observation turn; wait remains benchmark-managed."),
        },
        "semantic_scoring": _support("partial",
            ("F-INS has dedicated pixel/message replay and measured correct-station arrival evaluation, "
             "including explicit success_by_fallback. Production recovery/isolation is not evaluated. " if factory else
             "scene_challenge.evaluate_challenge_report gates business success with private identity/quantity, "
             "causal observations, measured ownership/delivery, dock receipts and planar home access predicates. ")
            + "Domain detection/service runtime adapters and native-asset execution evidence are still required.",
            "demo/benchmark/domain_evaluator.py; demo/warehouse_benchmark/challenge.py:evaluate_report; "
            "demo/modern_house_benchmark/challenge.py:evaluate_report"),
    }


def _factor_support(scene_key, identifier):
    rows = {
        "information_asymmetry": ("conditional_code_path", "F-INS decoded marker messages change station choice; not arbitrary unknown-object search."),
        "shared_aisle_contention": ("partial", "Scheduler resource reservation exists; no generated factorial open/reserved/blocked series."),
        "team_continuity": ("partial", "Join/exit/capability protocol components exist; mixed-aerial Isaac lifecycle application and factor sweeps remain pending."),
        "vertical_access": ("partial", "Separate vertical admission/probe work; these released tasks do not demonstrate climbing."),
        "order_priority": ("pending", "Priority is a node/object name; no order book, deadline ranking, or urgency-dependent acceptance."),
        "aisle_clearance": ("partial", "Map/route admission exists; static template, not a verified multi-level clearance experiment."),
        "payload_team_need": ("partial", "Two-member kinematic ownership and rendezvous exist; mass does not affect execution or prove carrying capacity."),
        "communication_quality": ("partial", "Replayable queue/loss/delay/bandwidth conditions now gate participant observation delivery and dependent objectives; rack/dock/room live semantic adapters and paired reference episodes remain pending."),
        "room_access": ("partial", "Before/after continuous planar paths are checked against a hash-bound map, all measured objects and ground robots. Runtime path/observation adapter pending."),
        "resident_safety": ("partial", "Published 0.8m circulation-width predicate exists; no human-contact model or general resident safety claim."),
        "household_item_identity": ("partial", "Private identity, frame/detection, localization and pre-action consumption predicate exists; live semantic detector and participant item choice pending."),
    }
    status, scope = rows[identifier]
    return _support(status, scope, f"scene={scene_key}; demo/benchmark/runtime.py; demo/benchmark/scene_challenge.py")


# Roles are operational responsibilities, not robot IDs or renamed skill names.
# Target IDs describe public regions; actual subject identity stays evaluator-only.
# Tuple: node, skill, target, role, deliverable, pending domain predicate.
_OBJECTIVE_ROWS = {
    "factory": (
        ("aerial_view", "aerial_scan", "inspection_site", "overhead_evidence_producer",
         "带采集时间和来源的工位俯视帧", "俯视帧解码结果必须关联待检工位，不能用相机位姿代替对象身份"),
        ("ground_approach", "navigate", "ground_checkpoint", "close_view_positioner",
         "近距离观察位到达记录", "该观察位对待检区域的可见性需由视图证据确认"),
        ("ground_view", "wait", "ground_checkpoint", "close_view_evidence_producer",
         "观察窗口内的近景帧及来源记录", "近景与俯视证据必须指向同一待检对象或显式报告歧义"),
        ("route_confirmation", "navigate", None, "evidence_based_station_dispatcher",
         "消费有效识别消息后的候选工位选择与到达记录", "只能评价工位派遣；设备复位、危险隔离与恢复生产尚无验收谓词"),
    ),
    "warehouse_nvd": (
        ("rack_overview_scan", "aerial_scan", "rack_scan_zone", "rack_evidence_producer",
         "指定货架区域的扫描帧", "需新增 SKU/库位识别消息，并在拣选决策前记录消费"),
        ("aisle_left_access", "navigate", "aisle_entry_zone", "pickup_side_positioner",
         "搬运主侧作业位到达记录", "需验证已选订单的取货通道，而非任何点到达"),
        ("aisle_right_access", "navigate", "outbound_dock_zone", "receiving_side_positioner",
         "接收侧作业位到达记录", "需将接收位与同一订单交接区域绑定"),
        ("oversized_crate_transfer", "joint_transport", "consolidation_zone", "oversized_order_carrier_team",
         "大件代理进入集货区的双成员所有权转移记录", "需核对订单货号与数量；双成员配置不是质量驱动的容量判据"),
        ("priority_carton_stage", "transport_object", "consolidation_zone", "order_consolidator",
         "纸箱代理进入集货区的单成员交付记录", "需绑定订单行及集货数量，不能把节点 priority 当作优先级调度"),
        ("dock_lane_check", "navigate", "outbound_dock_zone", "dock_approach_checker",
         "出库区地面到达记录", "需检测 dock 占用及交接权限；到达不等于净空确认"),
        ("priority_carton_dispatch", "transport_object", "outbound_dock_zone", "outbound_order_dispatcher",
         "同一纸箱代理从集货区到出库区的交付链", "需在放行前消费新鲜 dock 状态消息并核对订单行"),
        ("outbound_inventory_scan", "aerial_scan", "outbound_dock_zone", "post_dispatch_evidence_producer",
         "派送完成后的出库区扫描帧", "现有顺序是派送后复核，不能宣称是派送前放行门禁"),
        ("dock_confirmation_hold", "wait", "outbound_dock_zone", "dock_handoff_window_keeper",
         "出库交接窗口的定时保持记录", "需收货确认或超时拒收证据；等待时间不能代替签收"),
        ("aisle_closeout", "navigate", "rack_scan_zone", "aisle_return_positioner",
         "作业机器人返回货架扫描区的到达记录", "需验证载荷和机器人均退出受保护通道；返回点不等于全通道释放"),
    ),
    "modern_house_nvd": (
        ("room_entry_scan", "aerial_scan", "entry_scan_zone", "entry_evidence_producer",
         "整理前房间入口的扫描帧", "需提取门厅净空及家具遮挡，帧获取不能代替通行性判断"),
        ("furniture_side_access", "navigate", "furniture_access_zone", "furniture_side_positioner",
         "家具主作业侧到达记录", "需关联将要移动的家具及可接近面"),
        ("doorway_access_check", "navigate", "service_side_zone", "doorway_side_positioner",
         "门口辅助作业位到达记录", "需记录入口连续路径的整理前净空基线"),
        ("coordinated_furniture_reposition", "joint_transport", "furniture_parking_zone", "furniture_relocation_team",
         "家具代理移入停放区的双成员所有权记录", "需验证完整家具占地处于允许区域且不侵入生活动线"),
        ("furniture_clearance_scan", "aerial_scan", "furniture_review_zone", "post_move_evidence_producer",
         "家具移动后停放区的扫描帧", "需计算移动后的最窄通道净空并与入住阈值比较"),
        ("keepsake_relocation", "transport_object", "keepsake_staging_zone", "belonging_stager",
         "已知小物代理进入暂存区的交付记录", "需新增小物身份识别及观察到交付的绑定，当前不是找回任务"),
        ("room_entry_verification", "navigate", "room_review_zone", "room_review_positioner",
         "最终房间复核位到达记录", "需重走入口到生活区的完整通行路径，不能只检查终点"),
        ("room_condition_scan", "aerial_scan", "room_review_zone", "final_room_evidence_producer",
         "整理后房间状态扫描帧", "需比对家具区域、物品身份与通道净空，而非仅获取图像"),
        ("placement_confirmation_hold", "wait", "reviewer_standby_zone", "placement_observation_window_keeper",
         "整理后观察窗口的定时保持记录", "需物体位置稳定和无动线侵占证据；wait 不自动测量这些条件"),
        ("reviewer_standby", "navigate", "reviewer_standby_zone", "reviewer_departure_positioner",
         "复核机器人退出生活动线并到达独立待机区", "最终动态占地必须确认不再阻塞入口到生活区路径"),
        ("hall_release", "navigate", "primary_standby_zone", "hall_release_positioner",
         "作业机器人退出生活动线并到达独立待机区", "待机后最终通路仍须通过独立净空谓词"),
    ),
}


_CHECKS = {
    "navigate": ("zone_arrival", "runtime arrival_predicate + pose_trajectory", "robot_visited_zone under configured region tolerance"),
    "aerial_scan": ("hover_and_frame", "runtime aerial_scan events + observation_store", "measured hover dwell and acquired camera frame; no semantic recognition"),
    "wait": ("timed_hold", "scheduling.states + node duration_s", "completed simulation-time holding interval; no acknowledgement"),
    "transport_object": ("kinematic_delivery", "runtime relay_event_validation + object trajectory", "ordered pickup/release and object destination under kinematic opt-in"),
    "joint_transport": ("joint_kinematic_delivery", "runtime joint_event_validation + object trajectory", "two members, ordered shared ownership and object destination; no payload dynamics"),
}


def _objective_catalog(scene_key):
    result = {}
    for node, skill, target, role, deliverable, pending in _OBJECTIVE_ROWS[scene_key]:
        check, evidence, condition = _CHECKS[skill]
        result[node] = {
            "id": node, "skill": skill, "target_ref": target, "role": role,
            "purpose": deliverable, "deliverable": deliverable,
            "acceptance_evidence": [evidence],
            "allowed_alternatives": (["wait for a fresh delivered message", "select a public candidate station"]
                if node == "route_confirmation" else ["only actions enabled by this task's participant scope"]),
            "scope": "template_objective", "mapping_status": "catalog_only_compiler_integration_pending",
            "runtime_check": {"id": check, "condition": condition, "evidence": evidence,
                "status": "code_path_available_run_evidence_required"},
            "domain_check": {"status": "pending", "requirement": pending,
                "missing_evidence_result": "unknown", "machine_evaluator": None},
        }
        if scene_key in {"warehouse_nvd", "modern_house_nvd"} and skill in {"transport_object", "joint_transport"}:
            result[node]["domain_check"].update(
                status="evaluator_available_runtime_evidence_required",
                missing_evidence_result="fail_business_success",
                machine_evaluator="demo.benchmark.domain_evaluator.evaluate_domain_evidence")
    if scene_key == "factory":
        result["route_confirmation"]["target_candidates"] = ["assembly_site", "dock_site"]
        result["route_confirmation"]["runtime_check"] = {
            "id": "marker_dispatch_arrival", "status": "code_path_available_run_evidence_required",
            "condition": "selected station agrees with private marker truth and measured arrival follows commit; "
                         "used messages need causal replay; report success_by_fallback separately",
            "evidence": "inventory_dispatch decision/ledger + marker evidence + dispatch trajectory; dedicated Factory scoring required",
        }
    return result


def objective_semantics_for(scene_key: str, objective: Mapping[str, Any]) -> dict[str, Any]:
    """Opt-in compiler mapping. Exact ID/skill/target binding, no skill fallback.

    No evaluator truth, robot assignment, object identity or future intervention
    is read or emitted. Unknown objectives fail instead of inheriting a mission.
    """
    _scene_semantics(scene_key)
    identifier, skill = objective.get("id"), objective.get("skill", objective.get("skill_key"))
    if (scene_key == "factory" and identifier == "stair_access_check"
            and skill == "navigate" and objective.get("target_ref") == "mezzanine_stair_exit"
            and (objective.get("vertical") or {}).get("link_id") == "main_stair"):
        return {
            "id": identifier, "skill": skill, "target_ref": "mezzanine_stair_exit",
            "role": "elevated_access_verifier",
            "purpose": "沿指定楼梯验证通往上层生产工位的可用性",
            "deliverable": "带支撑面、完整姿态和连续健康 dwell 证据的上层到达记录",
            "acceptance_evidence": ["support-relative height progress", "healthy support patch at target level"],
            "allowed_alternatives": ["reassign after an observed member change"],
            "scope": "vertical_probe",
            "mapping_status": "vertical_probe_compiler_integrated_physical_pending",
            "runtime_check": {"id": "vertical_surface_following", "status": "candidate_executor_run_required",
                              "condition": "measured support-relative ascent and healthy target dwell",
                              "evidence": "vertical_task XYZ/WXYZ/support/health/progress trace"},
            "domain_check": {"status": "pending", "requirement": "validated controller and stair contact evidence",
                             "missing_evidence_result": "unknown", "machine_evaluator": None},
        }
    if identifier == "approach" and skill == "navigate" and objective.get("target_ref") == "destination":
        return {"id": identifier, "skill": skill, "target_ref": "destination",
                "role": "navigation_smoke_observer", "purpose": "检查该场景的一段导航链路",
                "deliverable": "destination 区域到达记录；不含订单、家具或物品语义交付",
                "acceptance_evidence": ["runtime arrival_predicate + pose_trajectory"],
                "allowed_alternatives": ["only enabled public navigation actions"],
                "scope": "navigation_smoke", "mapping_status": "catalog_only_compiler_integration_pending"}
    item = deepcopy(_objective_catalog(scene_key).get(identifier))
    if item is None or skill != item["skill"]:
        raise ValueError(f"{scene_key}/{identifier}: no exact objective semantic mapping")
    if scene_key == "modern_house_nvd" and identifier == "hall_release" and objective.get("target_ref") == "entry_scan_zone":
        # Archived v4 task binding remains readable; its final access predicate
        # still fails if the returned robot occupies the resident's path.
        item.update(target_ref="entry_scan_zone", role="hall_return_positioner",
                    purpose="作业机器人返回入口区的到达记录", deliverable="作业机器人返回入口区的到达记录")
        return item
    if item["target_ref"] is not None and objective.get("target_ref") != item["target_ref"]:
        raise ValueError(f"{identifier}: semantic target binding mismatch")
    if identifier == "route_confirmation":
        candidates = objective.get("target_candidates")
        if ((candidates is not None and set(candidates) != set(item["target_candidates"]))
                or (candidates is None and objective.get("target_ref") not in item["target_candidates"])):
            raise ValueError("route_confirmation: dispatch candidates mismatch")
    return item


def _scene_semantics(scene_key: str) -> dict[str, Any]:
    if scene_key == "factory":
        return {
            "task_family": "production_exception_response",
            "domain": "factory_operations",
            "mission_brief": {
                "en": "Restore a production cell after an abnormal condition, verify the affected area, and keep the shared aisle safe for the next material movement.",
                "zh_cn": "生产单元出现异常后，确认受影响区域、恢复作业条件，并保证共享通道可供后续物料流转。",
            },
            "artifacts": [
                {"id": "safety_beam", "asset_key": "safety_beam", "meaning": "temporary safety barrier / exclusion marker"},
                {"id": "inspection_target", "asset_key": "primitive_box", "meaning": "unresolved production item requiring visual confirmation"},
            ],
            "factors": [
                {"id": "information_asymmetry", "levels": ["known_location", "unresolved_location"], "evidence": "observation_message provenance"},
                {"id": "shared_aisle_contention", "levels": ["open", "reserved", "blocked"], "evidence": "resource reservation trace"},
                {"id": "team_continuity", "levels": ["stable", "member_exit", "replacement_join"], "evidence": "causal event and recovery trace"},
                {"id": "vertical_access", "levels": ["ground_only", "measured_link_required"], "evidence": "support-relative pose and level progress"},
            ],
            "objective_semantics": {
                "purpose": "identify and safely isolate a production exception before the line resumes",
                "deliverable": "a time-bounded inspection report plus a confirmed safe route",
                "acceptance_evidence": ["fresh observation linked to the inspection site", "safe shared-resource reservation", "route confirmation event"],
                "allowed_alternatives": ["use a different eligible observer", "delay commitment until a fresh message arrives", "reassign after a member change"],
            },
            "success_evidence": ["inspection subject identity or uncertainty state", "resource-safe route", "member-change recovery when injected"],
        }
    if scene_key == "warehouse_nvd":
        return {
            "task_family": "order_fulfilment_and_dock_recovery",
            "domain": "warehouse_logistics",
            "mission_brief": {
                "en": "Fulfil a priority order by reconciling a rack location, moving an oversized carton through the aisle, and verifying the outbound dock before dispatch.",
                "zh_cn": "完成优先订单：核对货架库位，协同将超尺寸箱体通过通道送往集货区，并在出库前复核 dock。",
            },
            "artifacts": [
                {"id": "rack_carton", "asset_key": "cardbox_c", "meaning": "ordinary inventory carton"},
                {"id": "oversized_crate", "asset_key": "wooden_crate_a", "meaning": "oversized order crate requiring a two-member carry"},
                {"id": "dock_pallet", "asset_key": "nvd_pallet_asm", "meaning": "outbound staging pallet"},
            ],
            "factors": [
                {"id": "order_priority", "levels": ["routine", "priority"], "evidence": "dispatch decision and deadline"},
                {"id": "aisle_clearance", "levels": ["open", "constrained", "blocked"], "evidence": "continuous route admission"},
                {"id": "payload_team_need", "levels": ["single_carton", "joint_oversized_crate"], "evidence": "ownership and team trace"},
                {"id": "communication_quality", "levels": sorted(COMMUNICATION_CONDITIONS), "evidence": "delivered-message ledger"},
            ],
            "objective_semantics": {
                "purpose": "send the right order through the right operational path and leave the dock ready for handoff",
                "deliverable": "a reconciled pick, a staged priority order, and a dock verification record",
                "acceptance_evidence": ["rack observation consumed", "oversized crate has joint pickup and delivery evidence", "dock status confirmed after dispatch"],
                "allowed_alternatives": ["choose any eligible carrier team", "wait for a fresh rack or dock message", "recover with a joining replacement after exit"],
            },
            "success_evidence": ["inventory observation provenance", "joint ownership transitions", "outbound dock confirmation"],
        }
    if scene_key == "modern_house_nvd":
        return {
            "task_family": "home_access_and_safe_rearrangement",
            "domain": "home_service",
            "mission_brief": {
                "en": "Prepare a home for a resident return by checking room access, moving a piece of furniture without closing the circulation path, and locating a small keepsake.",
                "zh_cn": "为住户回家准备房屋：检查房间可达性，在不堵塞生活动线的前提下调整家具，并找回一件小物品。",
            },
            "artifacts": [
                {"id": "living_seat", "asset_key": "mh_armchair", "meaning": "resident furniture whose placement changes access"},
                {"id": "keepsake", "asset_key": "mh_bookstack", "meaning": "small household item to locate and stage"},
                {"id": "hall_marker", "asset_key": "mh_bowl_brass", "meaning": "ordinary room landmark for a human-readable report"},
            ],
            "factors": [
                {"id": "room_access", "levels": ["clear", "furniture_constrained", "blocked"], "evidence": "entry and post-placement route checks"},
                {"id": "resident_safety", "levels": ["circulation_preserved", "circulation_at_risk"], "evidence": "clearance and final room review"},
                {"id": "household_item_identity", "levels": ["located", "not_located"], "evidence": "observation subject and staging record"},
                {"id": "team_continuity", "levels": ["stable", "member_exit", "capability_change"], "evidence": "causal recovery trace"},
            ],
            "objective_semantics": {
                "purpose": "restore safe everyday access while preserving the resident's furniture and belongings",
                "deliverable": "an access check, a safe furniture placement, and a keepsake location record",
                "acceptance_evidence": ["entry path remains usable", "furniture is staged in the intended room", "keepsake observation is linked to its staging action"],
                "allowed_alternatives": ["use another eligible ground carrier", "replan around furniture", "recover after a member or capability change"],
            },
            "success_evidence": ["before/after access checks", "furniture ownership and placement trace", "keepsake location evidence"],
        }
    raise ValueError(f"unsupported semantic scene: {scene_key}")


def build_semantic_task_contract(scene_key: str, *, lifecycle: Mapping[str, Any] | None = None,
                                 communication: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Frozen ScenarioSpec-compatible legacy vocabulary; all claims unverified.

    Do not inject new catalog fields into this closed JSON schema. Use
    build_semantic_design_catalog/review_semantic_design outside the scenario
    and integrate publication/schema changes in a separately reviewed change.
    """
    result = deepcopy(_scene_semantics(scene_key))
    result["team_lifecycle"] = {
        "events": ["member_join", "member_exit", "capability_change"],
        "join_policy": "replacement_may_join_only_after_a_runtime_event_and_must_be_eligible",
        "exit_policy": "carried_objects_are_released_at_measured_carrier_pose_and_must_be_recovered",
        "future_schedule_visibility": "hidden",
        **dict(lifecycle or {}),
    }
    result["communication"] = {
        "conditions": sorted(COMMUNICATION_CONDITIONS),
        "participant_observes": ["delivered_messages", "message_age", "message_sequence", "current_team_events"],
        "evaluator_controls": ["delivery_delay", "drop_decision", "bandwidth_window", "staleness_expiry"],
        "future_delivery_schedule_visibility": "hidden",
        **dict(communication or {}),
    }
    return result


def build_semantic_design_catalog(scene_key: str, *, lifecycle: Mapping[str, Any] | None = None,
                                  communication: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Author-side v2 catalog, NOT yet a valid embedded ScenarioSpec payload."""
    result = deepcopy(_scene_semantics(scene_key))
    result["semantic_version"] = SEMANTIC_VERSION
    result["assurance"] = {
        "scope": "design_catalog_not_episode_or_release_validation",
        "mission_status": "intended_domain_outcome_not_current_success_predicate",
        "compiler_mapping": "integrated_exact_objective_mapping",
        "execution_validated": False,
    }
    result["objective_contracts"] = _objective_catalog(scene_key)
    result["implementation_support"] = implementation_support(scene_key)
    result["objective_semantics"]["scope"] = "mission_summary_only_do_not_copy_to_every_objective"
    # This catalog must not be passed to existing compilers until their closed
    # schema accepts it and their objective mapper is integrated.
    result["objective_semantics"]["purpose"] = "场景级设计意图；编译器使用 objective_contracts 的逐目标映射"
    result["objective_semantics"]["deliverable"] = "不可作为本节点交付物；参见 objective_contracts 的精确节点映射"
    result["objective_semantics"]["acceptance_evidence"] = [
        "scene_challenge scheduler success alone does not establish the mission deliverables"]
    result["objective_semantics"]["allowed_alternatives"] = [
        "only actions enabled by the published participant scope; protocol joining does not spawn an Isaac robot"]
    for factor in result["factors"]:
        factor["implementation"] = _factor_support(scene_key, factor["id"])
        factor["levels_status"] = "design_axis_not_an_implemented_or_executed_sweep"
    for artifact in result["artifacts"]:
        artifact["asset_status"] = "design_asset_not_instance_binding"
        artifact["current_template_representation"] = (
            "Factory dispatch uses a dedicated visual fixture; these roles require instance binding"
            if scene_key == "factory" else
            "generated task instances select native USD props and audited bounds; this role catalog alone does not establish asset closure or execution")
    result["team_lifecycle"] = {
        "events": ["member_join", "member_exit", "capability_change"],
        "events_status": "design_vocabulary_see_implementation_support",
        "join_policy": "explicit private preloaded reserve inventory; publish join only after injected Isaac activation and readback evidence; dynamic spawn is not claimed",
        "exit_policy": "preloaded ground replacement with measured carrier pose release; physical drop/contact evidence remains separate",
        "future_schedule_visibility": "hidden",
    }
    result["communication"] = {
        "conditions": sorted(COMMUNICATION_CONDITIONS),
        "conditions_status": "design_vocabulary_see_implementation_support",
        "participant_observes": ["only delivered observation/lifecycle events; envelope sequence and simulation time"],
        "evaluator_controls": ["deterministic queue, per-link drop/delay/bandwidth and validity wired to the generic participant loop; future schedule remains private"],
        "future_delivery_schedule_visibility": "hidden",
    }
    # A caller cannot promote pending implementation by injecting override text.
    # Keep the original keyword API for compatibility, accepting only equivalent
    # declarations. New implementation requires updating this reviewed inventory.
    for name, override in (("team_lifecycle", lifecycle), ("communication", communication)):
        if override is not None and (not isinstance(override, Mapping) or any(
                key not in result[name] or result[name][key] != value for key, value in override.items())):
            raise ValueError(f"{name}: declarations cannot override implementation support")
    return result


def validate_semantic_task_contract(contract: Mapping[str, Any], *, scene_key: str) -> dict[str, Any]:
    """Validate metadata, never a rollout; archived legacy contracts stay readable.

    Version 1 only checked prose. A successful legacy validation explicitly
    reports unverified declarations. V2 support statements cannot be promoted
    through payload edits. Call review_semantic_design for actual node binding.
    """
    if not isinstance(contract, Mapping):
        raise ValueError("semantic contract must be an object")
    expected = _scene_semantics(scene_key)
    if contract.get("task_family") != expected["task_family"]:
        raise ValueError(f"{scene_key}: semantic task family is not scene-specific")
    mission = contract.get("mission_brief")
    if not isinstance(mission, Mapping) or not mission.get("en") or not mission.get("zh_cn"):
        raise ValueError("semantic task requires bilingual mission brief")
    artifacts = contract.get("artifacts")
    if (not isinstance(artifacts, list) or len(artifacts) < 2
            or any(not isinstance(item, Mapping) or not all(
                isinstance(item.get(key), str) and item[key] for key in ("id", "asset_key", "meaning"))
                for item in artifacts)):
        raise ValueError("semantic task requires at least two scene artifacts")
    if len({item.get("id") for item in artifacts}) != len(artifacts):
        raise ValueError("semantic task artifact ids must be unique")
    factors = contract.get("factors")
    if (not isinstance(factors, list) or len(factors) < 3 or any(
            not isinstance(item, Mapping) or not item.get("id") or not item.get("evidence")
            or not isinstance(item.get("levels"), list) or len(item["levels"]) < 2 for item in factors)):
        raise ValueError("semantic task requires three evidenced factors")
    if len({item["id"] for item in factors}) != len(factors):
        raise ValueError("semantic factor ids must be unique")
    objective = contract.get("objective_semantics")
    if not isinstance(objective, Mapping) or any(not objective.get(key) for key in ("purpose", "deliverable", "acceptance_evidence", "allowed_alternatives")):
        raise ValueError("semantic task objective semantics are incomplete")
    lifecycle = contract.get("team_lifecycle")
    if not isinstance(lifecycle, Mapping) or not LIFECYCLE_EVENTS <= set(lifecycle.get("events", ())):
        raise ValueError("semantic task must model join, exit and capability change")
    communication = contract.get("communication")
    if not isinstance(communication, Mapping) or not set(communication.get("conditions", ())) >= COMMUNICATION_CONDITIONS:
        raise ValueError("semantic task must declare communication conditions")
    version = contract.get("semantic_version", 1)
    if type(version) is not int or version not in {1, SEMANTIC_VERSION}:
        raise ValueError("unsupported semantic version")
    if version == SEMANTIC_VERSION:
        current = build_semantic_design_catalog(scene_key)
        if contract.keys() != current.keys() or contract.get("domain") != current["domain"]:
            raise ValueError("unreviewed semantic catalog fields or domain")
        for key in ("assurance", "implementation_support", "objective_contracts", "team_lifecycle", "communication"):
            if contract.get(key) != current[key]:
                raise ValueError(f"{key}: unsupported semantic implementation claim or mapping")
        for kind in ("factors", "artifacts"):
            canonical = {row["id"]: row for row in current[kind]}
            if {row["id"] for row in contract[kind]} != canonical.keys():
                raise ValueError(f"{kind}: unreviewed semantic roles")
            for row in contract[kind]:
                keys = (("implementation", "levels_status") if kind == "factors" else
                        ("asset_key", "asset_status", "current_template_representation"))
                if any(row.get(key) != canonical[row["id"]][key] for key in keys):
                    raise ValueError(f"{row['id']}: unsupported implementation or asset claim")
    return {"validated": True, "scene_key": scene_key, "task_family": contract["task_family"],
            "validation_scope": "metadata_only", "execution_validated": False,
            "semantic_version": version,
            "assurance": "explicit_support_inventory" if version == SEMANTIC_VERSION else "legacy_declarations_unverified",
            "artifact_count": len(artifacts), "factor_ids": [item["id"] for item in factors],
            "lifecycle_events": sorted(set(lifecycle["events"])),
            "communication_conditions": sorted(set(communication["conditions"]))}


def review_semantic_design(task: Mapping[str, Any], *, participant_api=None) -> dict[str, Any]:
    """Read-only author audit for a public task or V2 scenario, without Isaac.

    Reports bounded code support, objective mismatches and missing domain
    predicates. Does not mutate releases, invoke evaluators, score a run or
    grant release readiness, even if a caller supplies success-shaped metadata.
    """
    scene_key = task.get("scene_key") or (task.get("scene") or {}).get("scene_key")
    expected = _objective_catalog(scene_key) if isinstance(scene_key, str) and scene_key in _OBJECTIVE_ROWS else None
    if expected is None:
        raise ValueError(f"unsupported semantic scene: {scene_key}")
    nodes = task.get("objectives", (task.get("phase_graph") or {}).get("nodes", []))
    if not isinstance(nodes, list) or any(not isinstance(node, Mapping) for node in nodes):
        raise ValueError("objectives must be a list of objects")
    vertical_probe = scene_key == "factory" and any(node.get("vertical") for node in nodes)
    if vertical_probe:
        expected = {"stair_access_check": True}
    findings, mappings = [], []

    def finding(code, detail, *, severity="blocking", node_id=None):
        findings.append({"code": code, "severity": severity, "node_id": node_id, "detail": detail})

    semantic = task.get("semantic_task")
    if semantic is None:
        finding("missing_semantic_contract", "No semantic contract accompanies these objectives.")
    else:
        try:
            review = validate_semantic_task_contract(semantic, scene_key=scene_key)
            if review["semantic_version"] == 1:
                finding("legacy_support_unqualified", "Lifecycle, communication and factors have no implementation status.")
        except (ValueError, TypeError, KeyError) as exc:
            finding("invalid_semantic_contract", str(exc))
    identifiers = [node.get("id") for node in nodes]
    if len(set(identifiers)) != len(identifiers):
        finding("duplicate_objective", "Repeated IDs make evidence ownership ambiguous.")
    for node in nodes:
        try:
            meaning = objective_semantics_for(scene_key, node)
            mappings.append(meaning)
            if "objectives" in task:
                fields = ("role", "purpose", "deliverable", "acceptance_evidence", "allowed_alternatives")
                mismatch = [key for key in fields if node.get(key) != meaning[key]]
                if mismatch:
                    finding("objective_mapping_not_integrated", ", ".join(mismatch), node_id=node["id"])
        except ValueError as exc:
            finding("unmapped_or_misbound_objective", str(exc), node_id=node.get("id"))
    signatures = Counter(json.dumps([node.get(k) for k in ("purpose", "deliverable", "acceptance_evidence")],
                                   ensure_ascii=False, sort_keys=True) for node in nodes
                         if any(node.get(k) for k in ("purpose", "deliverable", "acceptance_evidence")))
    if any(count > 1 for count in signatures.values()):
        finding("duplicated_mission_semantics", "Multiple nodes repeat one aggregate purpose/deliverable/evidence tuple.")
    missing = sorted(set(expected) - set(identifiers))
    if missing:
        finding("missing_domain_objectives", "Absent template roles: " + ", ".join(missing))
    smoke = (bool(nodes) and not vertical_probe
             and all(item.get("skill", item.get("skill_key")) in {"navigate", "wait"} for item in nodes))
    if smoke:
        finding("navigation_smoke_only", "Navigation/holding cannot fulfil the declared multi-stage domain mission.")
    else:
        finding("domain_predicates_pending", "Domain predicates exist for full Warehouse/House templates, but native props, live identity/service adapters and episode evidence are still required.")
    support = implementation_support(scene_key)
    participant = task.get("participant") or {}
    controlled = set(participant.get("controlled_objectives", ()))
    managed = sorted(set(identifiers) - controlled) if "objectives" in task else []
    if managed:
        finding("benchmark_managed_objectives", "Outside model control: " + ", ".join(managed), severity="info")
    allowed = set(participant.get("allowed_action_types", ()))
    unapplied = sorted(allowed - set(support["participant_actions"]["implemented_scope"]))
    if unapplied:
        finding("declared_actions_not_applied", ", ".join(unapplied))
    if participant_api is not None:
        events = participant_api.get("event_types", {})
        for name in ("member_join", "capability_change"):
            if name not in events:
                finding("lifecycle_event_api_pending", name, severity="info")
        advertised = set((participant_api.get("implementation_status") or {}).get("supported_action_types", ()))
        if allowed != advertised:
            finding("action_advertisement_mismatch", "Public allowed actions and API implementation_status differ.")
    entities = (task.get("composition") or {}).get("entities")
    if entities is not None:
        proxy_count = sum(e.get("asset_key") == "primitive_box" for e in entities)
        if proxy_count and scene_key != "factory":
            finding("domain_props_are_primitive_proxies", f"{proxy_count} primitive_box entities; native prop catalog is not instance closure.")
    else:
        finding("artifact_instance_binding_unknown", "Public task contains no instance asset bindings; native props cannot be verified.", severity="info")
    # Always explicit: this audit is callable now, compiler/evaluator enforcement
    # is separate work. Do not use this boolean as a deployment gate success.
    return {"schema": "eai.semantic_design_review", "version": 1, "scene_key": scene_key,
            "task_id": task.get("task_id", task.get("task_name")),
            "objective_count": len(nodes), "classification": (
                "vertical_probe_with_pending_physics" if vertical_probe else
                "navigation_smoke" if smoke else "domain_template_with_pending_semantics"),
            "model_controlled_objectives": sorted(controlled), "benchmark_managed_objectives": managed,
            "missing_template_objectives": missing, "objective_mapping": mappings,
            "implementation_support": support, "findings": findings,
            "semantic_contract_ready": False, "release_ready": False, "execution_validated": False,
            "audit_scope": "static author review; exact compiler mapping available; no live domain/physics evidence evaluated"}


def semantic_for_scene(scene_key: str) -> dict[str, Any]:
    contract = build_semantic_task_contract(scene_key)
    validate_semantic_task_contract(contract, scene_key=scene_key)
    return contract


__all__ = ["COMMUNICATION_CONDITIONS", "build_semantic_task_contract",
           "semantic_for_scene", "validate_semantic_task_contract", "implementation_support",
           "objective_semantics_for", "review_semantic_design", "build_semantic_design_catalog"]
