"""Business acceptance predicates for the city response task.

This module is an authoring contract, not a replay evaluator.  It records the
evidence chain a future runtime must provide so that arriving at a waypoint,
submitting a request, or playing a fire effect cannot be mistaken for mission
success.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping


CITY_TASK_LOGIC_SCHEMA = "eai.city.task_logic"
CITY_TASK_LOGIC_VERSION = 1
_DEADLINE_CLOCK = "relative_to_delivered_incident_observation_or_service_request"


def _time(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite time")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite nonnegative time")
    return result


def default_deadline_policy(response_budget_s: float = 180.0) -> dict[str, Any]:
    """Return relative deadlines for one authored city episode.

    These values are task parameters to be admitted against the actual map and
    robot starts.  They are not claims about the visual accident timestamps.
    """
    budget = _time(response_budget_s, "response_budget_s")
    if budget < 120.0:
        raise ValueError("city response budget must allow the default evacuation window")
    policy = {
        "schema": "eai.city.deadline_policy",
        "version": 1,
        "clock": _DEADLINE_CLOCK,
        "incident_confirmation_s": 15.0,
        "first_responder_arrival_s": 35.0,
        "traffic_control_ack_s": 10.0,
        "traffic_reaction_window_s": 5.0,
        "people_guidance_ack_s": 20.0,
        "people_assembly_arrival_s": 120.0,
        "report_receipt_s": 15.0,
        "response_budget_s": budget,
    }
    if policy["people_assembly_arrival_s"] > budget:
        raise ValueError("people assembly deadline exceeds response budget")
    return policy


def review_deadline_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate deadline ordering before physical route admission."""
    required = {
        "schema", "version", "clock", "incident_confirmation_s",
        "first_responder_arrival_s", "traffic_control_ack_s",
        "traffic_reaction_window_s", "people_guidance_ack_s",
        "people_assembly_arrival_s", "report_receipt_s", "response_budget_s",
    }
    if not isinstance(policy, Mapping) or set(policy) != required:
        raise ValueError("city deadline policy fields mismatch")
    if (policy["schema"] != "eai.city.deadline_policy" or type(policy["version"]) is not int
            or policy["version"] != 1 or policy["clock"] != _DEADLINE_CLOCK):
        raise ValueError("unsupported city deadline policy")
    for key in required - {"schema", "version", "clock"}:
        if _time(policy[key], f"deadline {key}") <= 0:
            raise ValueError(f"deadline {key} must be positive")
    if policy["people_assembly_arrival_s"] > policy["response_budget_s"]:
        raise ValueError("people assembly deadline exceeds response budget")
    if policy["traffic_reaction_window_s"] > policy["people_assembly_arrival_s"]:
        raise ValueError("traffic reaction window is inconsistent")
    return {"validated": True, "schema": CITY_TASK_LOGIC_SCHEMA,
            "version": CITY_TASK_LOGIC_VERSION, "deadline_policy": deepcopy(dict(policy))}


def public_decision_principles() -> list[str]:
    """Publicly explain what the participant is judged on without giving a plan."""
    return [
        "选择响应者依据当前公开位姿、可用能力和路线 ETA；机器人类型本身不提供优先级。",
        "能力、路线及在线状态合法且满足业务时限的分配均可接受；最小 ETA 仅作为诊断，不是唯一解。",
        "事故观察必须先送达，之后的派遣和服务请求才能引用该观察证据。",
        "人员成功条件是逐人确认、实际沿合法路线到达可容纳的集合区，并在时限内完成记账。",
        "交通成功条件是服务确认后，经过公开反应观察窗口由车辆轨迹证明停车或改道；请求提交不算保护成功。",
        "一次有效任务必须形成观察、决策、执行、业务回执的因果链；成员退出或通信变化后必须重新规划。",
    ]


def public_deadline_constraints(policy: Mapping[str, Any]) -> list[str]:
    """Render draft deadlines exactly; physical feasibility remains unvalidated."""
    review_deadline_policy(policy)
    return [
        f"时间戳统一使用本轮 episode_started 为零点的仿真秒；全部结果须在 {policy['response_budget_s']:g} 秒总预算内完成，相对窗口不能延长总预算，视觉动画和 loop 不重置时钟。",
        f"事故观察实际送达后 {policy['incident_confirmation_s']:g} 秒内完成态势确认；引用该观察的派遣不能早于送达，派遣后 {policy['first_responder_arrival_s']:g} 秒内由能力、路线和在线状态均被接纳的响应者到达。最小 ETA 仅为诊断，不是成功必要条件。",
        f"后续交通控制请求提交后 {policy['traffic_control_ack_s']:g} 秒内获得服务确认，确认后至少观察 {policy['traffic_reaction_window_s']:g} 秒车辆轨迹；请求或确认本身不代表车辆已受保护。",
        f"每名受影响人员的引导请求提交后 {policy['people_guidance_ack_s']:g} 秒内确认引导，并在确认后 {policy['people_assembly_arrival_s']:g} 秒内到达当前有容量且安全的集合区。",
        f"事件报告提交后 {policy['report_receipt_s']:g} 秒内取得响应服务回执；观察、决策、到达、确认和回执的时间顺序必须真实成立。",
        "向响应方交接时，须有覆盖接收回执时刻、持续时间大于零的完整救援进入及人员撤离通路净空检查；单点到达或仅提交报告不满足通路验收。",
    ]


def _roster_failures(rows: list, expected: set[str] | None, *, kind: str) -> list[str]:
    """Check an author-bound full roster without deduplicating evidence rows."""
    label = "affected_person" if kind == "person" else "following_traffic"
    roster_name = "affected" if kind == "person" else "following"
    failures = []
    actual = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue  # The per-row review reports malformed evidence.
        identifier = row.get(f"{kind}_id")
        if not isinstance(identifier, str) or not identifier.strip():
            failures.append(f"invalid_{kind}_identity")
        else:
            actual.append(identifier)
    if len(actual) != len(set(actual)):
        failures.append("duplicate_affected_person_evidence" if kind == "person"
                        else "duplicate_following_vehicle_evidence")
    if expected is None:
        failures.append(f"{label}_roster_binding_missing")
        return failures
    if (not isinstance(expected, (set, frozenset, list, tuple)) or not expected
            or any(not isinstance(value, str) or not value.strip() for value in expected)
            or len(expected) != len(set(expected))):
        raise ValueError(f"{label} roster must bind unique nonempty string identities")
    for identifier in sorted(set(expected) - set(actual)):
        failures.append(f"{kind}:{identifier}:evidence_missing_from_{roster_name}_roster")
    for identifier in sorted(set(actual) - set(expected)):
        failures.append(f"{kind}:{identifier}:not_in_hidden_{roster_name}_roster")
    return failures


def factor_effect_requirements() -> dict[str, Any]:
    """Describe what a future factor pair must be able to change causally."""
    return {
        "communication_quality": {
            "decision_dependency": "delivered_observation_to_dispatch_and_service_request",
            "required_effect": "delay_or_drop_can_make_a_current_response_deadline_unreachable",
            "metrics": ["observation_delivery_latency_s", "stale_decision_count",
                         "deadline_miss_count", "replan_count"],
        },
        "team_continuity": {
            "decision_dependency": "active_responder_or_observer_before_service_completion",
            "required_effect": "member_exit_join_or_capability_change_requires_a_new_admitted_plan",
            "metrics": ["event_detection_latency_s", "invalidated_action_count",
                         "reassignment_latency_s", "deadline_miss_count"],
        },
        "frozen": ["scene_layout", "incident_instance", "actor_initial_states",
                   "candidate_robot_pool", "budgets", "model_budget"],
    }


def evaluate_city_acceptance(
        evidence: Mapping[str, Any], policy: Mapping[str, Any], *,
        expected_affected_person_ids: set[str] | None = None,
        expected_following_vehicle_ids: set[str] | None = None) -> dict[str, Any]:
    """Evaluate a future runtime evidence envelope with explainable failures.

    The result is deliberately predicate-level and contains no score weights.
    Runtime/evaluator owners can bind these predicates to their own event
    schema after the dynamic actor and service APIs are available. Evidence
    flags are trusted runtime summaries, never participant self-reports. Both
    hidden rosters are mandatory for success; omitted bindings fail closed.
    ``guidance_time_s`` denotes the person's acknowledgement, while
    ``guidance_request_time_s`` denotes the request. All times share the public
    episode clock. Passing this skeleton cannot authorize scoring or release.
    """
    review_deadline_policy(policy)
    if not isinstance(evidence, Mapping):
        raise ValueError("city acceptance evidence must be an object")
    outcomes: dict[str, dict[str, Any]] = {}
    budget_failures: set[str] = set()

    def read_time(value: Any, label: str) -> float:
        timestamp = _time(value, label)
        if timestamp > policy["response_budget_s"]:
            budget_failures.add("response_budget_exceeded")
        return timestamp

    incident = evidence.get("incident_assessed")
    incident_ok = isinstance(incident, Mapping)
    incident_failures: list[str] = []
    if not incident_ok:
        incident_failures.append("incident_observation_missing")
    else:
        if not incident.get("observation_ref"):
            incident_failures.append("observation_provenance_missing")
        if incident.get("fresh_at_decision") is not True:
            incident_failures.append("incident_observation_stale")
        if incident.get("source_visible_to_participant") is not True:
            incident_failures.append("observation_not_delivered")
        if "decision_time_s" not in incident or "observation_delivery_time_s" not in incident:
            incident_failures.append("incident_timing_missing")
        else:
            elapsed = read_time(incident["decision_time_s"], "incident decision time") - read_time(
                incident["observation_delivery_time_s"], "incident delivery time")
            if elapsed < 0:
                incident_failures.append("incident_decision_before_observation_delivery")
            elif elapsed > policy["incident_confirmation_s"]:
                incident_failures.append("incident_confirmation_deadline_exceeded")
    outcomes["incident_assessed"] = {"success": not incident_failures,
                                      "failure_codes": incident_failures}

    dispatch = evidence.get("responder_dispatch")
    dispatch_failures: list[str] = []
    if not isinstance(dispatch, Mapping):
        dispatch_failures.append("responder_dispatch_missing")
    else:
        if not dispatch.get("selected_agent_id"):
            dispatch_failures.append("responder_identity_missing")
        if dispatch.get("agent_active_at_dispatch") is not True:
            dispatch_failures.append("responder_inactive")
        if dispatch.get("capability_admitted") is not True:
            dispatch_failures.append("responder_capability_not_admitted")
        if dispatch.get("route_admitted") is not True:
            dispatch_failures.append("responder_route_not_admitted")
        if "arrival_time_s" not in dispatch or "dispatch_time_s" not in dispatch:
            dispatch_failures.append("responder_timing_missing")
        else:
            elapsed = read_time(dispatch["arrival_time_s"], "responder arrival time") - read_time(
                dispatch["dispatch_time_s"], "responder dispatch time")
            if elapsed < 0:
                dispatch_failures.append("responder_arrival_before_dispatch")
            elif elapsed > policy["first_responder_arrival_s"]:
                dispatch_failures.append("first_responder_arrival_deadline_exceeded")
            if (isinstance(incident, Mapping) and "observation_delivery_time_s" in incident
                    and dispatch["dispatch_time_s"] < read_time(
                        incident["observation_delivery_time_s"], "incident delivery time")):
                dispatch_failures.append("responder_dispatch_before_observation_delivery")
    outcomes["response_dispatch"] = {
        "success": not dispatch_failures,
        "failure_codes": dispatch_failures,
        "diagnostics": {
            "eta_was_minimum_among_admitted_options": bool(
                isinstance(dispatch, Mapping)
                and dispatch.get("eta_was_minimum_among_admitted_options", False)),
        },
    }

    people_failures: list[str] = []
    people = evidence.get("people")
    if not isinstance(people, list) or not people:
        people_failures.append("affected_person_evidence_missing")
        people = []
    people_failures.extend(_roster_failures(people, expected_affected_person_ids, kind="person"))
    for row in people:
        if not isinstance(row, Mapping):
            people_failures.append("malformed_person_evidence")
            continue
        person_id = row.get("person_id", "unknown")
        prefix = f"person:{person_id}"
        if not row.get("observation_ref"):
            people_failures.append(f"{prefix}:observation_missing")
        if row.get("guidance_acknowledged") is not True:
            people_failures.append(f"{prefix}:guidance_not_acknowledged")
        if row.get("route_admitted") is not True:
            people_failures.append(f"{prefix}:evacuation_route_not_admitted")
        if row.get("entered_hazard") is not False:
            people_failures.append(f"{prefix}:hazard_boundary_violated")
        if row.get("arrived_at_admitted_assembly") is not True:
            people_failures.append(f"{prefix}:assembly_arrival_missing")
        if "guidance_request_time_s" in row and "guidance_time_s" in row:
            elapsed = read_time(row["guidance_time_s"], f"{prefix} acknowledgement") - read_time(
                row["guidance_request_time_s"], f"{prefix} request")
            if elapsed < 0:
                people_failures.append(f"{prefix}:guidance_acknowledgement_before_request")
            elif elapsed > policy["people_guidance_ack_s"]:
                people_failures.append(f"{prefix}:guidance_acknowledgement_deadline_exceeded")
        else:
            people_failures.append(f"{prefix}:guidance_acknowledgement_timing_missing")
        if "arrival_time_s" in row and "guidance_time_s" in row:
            elapsed = read_time(row["arrival_time_s"], f"{prefix} arrival") - read_time(
                row["guidance_time_s"], f"{prefix} guidance")
            if elapsed < 0:
                people_failures.append(f"{prefix}:arrival_before_guidance")
            elif elapsed > policy["people_assembly_arrival_s"]:
                people_failures.append(f"{prefix}:assembly_deadline_exceeded")
        else:
            people_failures.append(f"{prefix}:arrival_timing_missing")
    outcomes["exposed_people_accounted_for"] = {"success": not people_failures,
                                                 "failure_codes": people_failures}

    traffic_failures: list[str] = []
    traffic = evidence.get("traffic")
    if not isinstance(traffic, list) or not traffic:
        traffic_failures.append("following_traffic_evidence_missing")
        traffic = []
    traffic_failures.extend(_roster_failures(traffic, expected_following_vehicle_ids, kind="vehicle"))
    for row in traffic:
        if not isinstance(row, Mapping):
            traffic_failures.append("malformed_traffic_evidence")
            continue
        vehicle_id = row.get("vehicle_id", "unknown")
        prefix = f"vehicle:{vehicle_id}"
        if row.get("following_incident") is not True:
            traffic_failures.append(f"{prefix}:control_target_not_following_traffic")
        if row.get("control_acknowledged") is not True:
            traffic_failures.append(f"{prefix}:control_not_acknowledged")
        if row.get("trajectory_sample_after_ack") is not True:
            traffic_failures.append(f"{prefix}:trajectory_response_missing")
        if row.get("entered_exclusion_boundary") is not False:
            traffic_failures.append(f"{prefix}:controlled_ingress_violation")
        if "request_time_s" in row and "ack_time_s" in row:
            elapsed = read_time(row["ack_time_s"], f"{prefix} acknowledgement") - read_time(
                row["request_time_s"], f"{prefix} request")
            if elapsed < 0:
                traffic_failures.append(f"{prefix}:control_acknowledgement_before_request")
            elif elapsed > policy["traffic_control_ack_s"]:
                traffic_failures.append(f"{prefix}:control_acknowledgement_deadline_exceeded")
        else:
            traffic_failures.append(f"{prefix}:control_acknowledgement_timing_missing")
        if "ack_time_s" in row and "trajectory_check_time_s" in row:
            elapsed = read_time(row["trajectory_check_time_s"], f"{prefix} trajectory check") - read_time(
                row["ack_time_s"], f"{prefix} acknowledgement")
            if elapsed < 0:
                traffic_failures.append(f"{prefix}:trajectory_check_before_acknowledgement")
            elif elapsed < policy["traffic_reaction_window_s"]:
                traffic_failures.append(f"{prefix}:reaction_window_not_observed")
        else:
            traffic_failures.append(f"{prefix}:reaction_timing_missing")
    outcomes["secondary_traffic_protected"] = {"success": not traffic_failures,
                                                "failure_codes": traffic_failures}

    report = evidence.get("incident_report")
    report_failures: list[str] = []
    if not isinstance(report, Mapping):
        report_failures.append("incident_report_missing")
    else:
        if not report.get("observation_refs"):
            report_failures.append("report_observation_refs_missing")
        if report.get("receipt_received") is not True:
            report_failures.append("response_service_receipt_missing")
        if "receipt_time_s" in report and "submit_time_s" in report:
            elapsed = read_time(report["receipt_time_s"], "report receipt") - read_time(
                report["submit_time_s"], "report submission")
            if elapsed < 0:
                report_failures.append("report_receipt_before_submission")
            elif elapsed > policy["report_receipt_s"]:
                report_failures.append("report_receipt_deadline_exceeded")
        else:
            report_failures.append("report_timing_missing")
    access = evidence.get("response_access")
    if not isinstance(access, Mapping):
        report_failures.append("continuous_access_evidence_missing")
    else:
        if access.get("continuous_access_check") is not True:
            report_failures.append("continuous_access_check_failed")
        if access.get("route_admitted") is not True:
            report_failures.append("response_route_not_admitted")
        if access.get("hazard_free_throughout") is not True:
            report_failures.append("response_route_hazard_clearance_missing")
        if "check_start_time_s" not in access or "check_end_time_s" not in access:
            report_failures.append("continuous_access_timing_missing")
        else:
            start = read_time(access["check_start_time_s"], "continuous access start")
            end = read_time(access["check_end_time_s"], "continuous access end")
            if end < start:
                report_failures.append("continuous_access_end_before_start")
            elif end == start:
                report_failures.append("continuous_access_interval_empty")
            if (isinstance(report, Mapping) and "receipt_time_s" in report
                    and not start <= read_time(report["receipt_time_s"], "report receipt") <= end):
                report_failures.append("continuous_access_does_not_cover_receipt")
    outcomes["response_access_preserved"] = {"success": not report_failures,
                                              "failure_codes": report_failures}

    return {
        "schema": CITY_TASK_LOGIC_SCHEMA,
        "version": CITY_TASK_LOGIC_VERSION,
        "predicate_only": True,
        "outcomes": outcomes,
        "mission_success": not budget_failures and all(row["success"] for row in outcomes.values()),
        "failure_codes": sorted(budget_failures),
        "execution_validated": False,
        "scoreable": False,
        "score_status": "requires_runtime_evidence_and_evaluator_policy",
    }


__all__ = ["CITY_TASK_LOGIC_SCHEMA", "CITY_TASK_LOGIC_VERSION",
           "default_deadline_policy", "evaluate_city_acceptance",
           "factor_effect_requirements", "public_decision_principles",
           "public_deadline_constraints",
           "review_deadline_policy"]
