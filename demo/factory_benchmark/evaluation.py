"""Factory business metrics over scheduler, pose, and observation evidence."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping


def _intervals(scheduling: Mapping[str, Any], resource: str) -> list[tuple[str, float, float]]:
    intervals = []
    requirements = scheduling.get("resource_requirements", {})
    for node, state in scheduling.get("states", {}).items():
        resources = requirements.get(node, ())
        if resource not in resources:
            continue
        start = state.get("started_at_s")
        finish = state.get("finished_at_s")
        if start is not None:
            intervals.append((node, float(start), float(finish if finish is not None else scheduling.get("time_s", start))))
    return sorted(intervals, key=lambda item: (item[1], item[2], item[0]))


def scheduling_metrics(scheduling: Mapping[str, Any], *, required_nodes: Iterable[str] = (),
                       recovered_nodes: Iterable[str] = ()) -> dict[str, Any]:
    required = set(required_nodes)
    states = scheduling.get("states", {})
    claimed = set(recovered_nodes) | set(scheduling.get("recovered_nodes", ()))
    recovered = {
        original for original, replacements in scheduling.get("recovered_by", {}).items()
        if replacements and states.get(original, {}).get("status") in {"failed", "timed_out"}
        and set(replacements) == set(scheduling.get("recovery_requirements", {}).get(original, ()))
        and all(states.get(node, {}).get("status") == "succeeded" for node in replacements)
    }
    completed = sorted(node for node, state in states.items() if state.get("status") == "succeeded")
    skipped = {node for node, state in states.items() if state.get("status") == "skipped"}
    missing = sorted(required - set(completed) - recovered - skipped)
    waiting = sum(
        max(0.0, float(state["started_at_s"]) - float(state["ready_at_s"]))
        for state in states.values()
        if state.get("ready_at_s") is not None and state.get("started_at_s") is not None
    )
    return {
        "required_nodes": sorted(required),
        "completed_nodes": completed,
        "recovered_nodes": sorted(recovered),
        "invalid_recovery_claims": sorted(claimed - recovered),
        "skipped_nodes": sorted(skipped),
        "missing_nodes": missing,
        "waiting_time_s": round(waiting, 6),
        "throughput_count": len(set(completed) & required) if required else len(completed),
        "terminal_time_s": scheduling.get("time_s"),
        "validated": not missing and not claimed - recovered,
    }


def coverage_member_exit_metrics(scheduling: Mapping[str, Any], *,
                                 execution_events: Iterable[Mapping[str, Any]],
                                 fault_events: Iterable[Mapping[str, Any]],
                                 plan: Iterable[Mapping[str, Any]],
                                 observation_report: Mapping[str, Any],
                                 freshness_bound_s: float = 30.0) -> dict[str, Any]:
    """Measure whether a coverage station recovered after a member exit.

    This is a scheduling and observation continuity metric.  It does not infer
    robot reliability, safety, or physical payload capability from a takeover.
    """
    if not math.isfinite(freshness_bound_s) or freshness_bound_s <= 0:
        raise ValueError("freshness_bound_s must be finite and positive")
    plan_by_node = {entry["node"]: entry for entry in plan}
    events = list(execution_events)
    states = scheduling.get("states", {})
    now = scheduling.get("time_s")

    def finite(value):
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)

    def frames(node_id, agent, subject):
        state = states.get(node_id, {})
        start, finish = state.get("started_at_s"), state.get("finished_at_s")
        if not all(finite(value) for value in (start, finish, now)):
            return []
        frame_ids = {
            event.get("observation_id") for event in events
            if event.get("event") == "coverage_observation" and event.get("node") == node_id
            and event.get("agent") == agent and event.get("target_ref") == subject
            and event.get("observation_source") == "live_isaac_rgb_frame"
        }
        selected = []
        for observation in observation_report.get("observations", ()):
            timestamp = observation.get("timestamp_s")
            config = observation.get("sensor_config", {})
            digest = config.get("frame_sha256", "")
            if (observation.get("observation_id") in frame_ids
                    and observation.get("producer") == agent
                    and observation.get("target_ref") == subject
                    and observation.get("sensor") == "camera"
                    and config.get("node") == node_id
                    and observation.get("quality", {}).get("frame_acquired") is True
                    and isinstance(digest, str) and len(digest) == 64
                    and finite(timestamp) and start <= timestamp <= min(finish, now)):
                selected.append(observation)
        return sorted(selected, key=lambda item: item["timestamp_s"])

    results = []
    for fault in sorted(fault_events, key=lambda item: (item["time_s"], item["id"])):
        errors = []
        matching = [event for event in events if event.get("event") == "member_exit"
                    and event.get("fault_id") == fault["id"]]
        event = matching[0] if len(matching) == 1 else {}
        exit_time = event.get("time_s")
        if (fault.get("kind") != "member_exit" or not event
                or event.get("agent") != fault.get("agent")
                or event.get("replacement_agent") != fault.get("replacement_agent")
                or not finite(exit_time) or not finite(now)
                or not fault["time_s"] <= exit_time <= now):
            errors.append("missing_or_invalid_member_exit_event")
        affected = list(event.get("affected_nodes", ()))
        if not affected:
            errors.append("no_active_coverage_node_was_interrupted")
        recovery_entries = [
            entry for entry in plan_by_node.values()
            if entry.get("recovery_for") in affected
            and entry.get("agent") == fault.get("replacement_agent")
        ]
        recovery_nodes = [entry["node"] for entry in recovery_entries]
        successful = bool(recovery_nodes) and all(
            states.get(node, {}).get("status") == "succeeded"
            for node in recovery_nodes
        )
        recovery_start = min(
            (states.get(node, {}).get("started_at_s")
             for node in recovery_nodes
             if finite(states.get(node, {}).get("started_at_s"))),
            default=None,
        )
        if not successful or not finite(recovery_start) or not finite(exit_time) or recovery_start < exit_time:
            errors.append("recovery_branch_did_not_complete_after_exit")
        freshness_gaps = {}
        first_observations = []
        for node_id in affected:
            original = plan_by_node.get(node_id, {})
            original_state = states.get(node_id, {})
            subject = original.get("observation_ref") or original.get("target_ref")
            if (original.get("skill") != "wait" or original.get("agent") != fault.get("agent")
                    or event.get("affected_node_statuses", {}).get(node_id) != "running"
                    or original_state.get("status") != "failed"
                    or original_state.get("reason") != f"member_exit:{fault.get('agent')}"
                    or not finite(exit_time) or original_state.get("finished_at_s") != exit_time):
                errors.append(f"{node_id}:not_an_interrupted_coverage_window")
            members = [entry for entry in recovery_entries if entry.get("recovery_for") == node_id]
            windows = [entry for entry in members if entry.get("skill") == "wait"
                       and (entry.get("observation_ref") or entry.get("target_ref")) == subject]
            if not windows or set(scheduling.get("recovered_by", {}).get(node_id, ())) != {
                    entry["node"] for entry in members}:
                errors.append(f"{node_id}:missing_completed_recovery_window")
            before = [item for item in frames(node_id, fault.get("agent"), subject)
                      if finite(exit_time) and item["timestamp_s"] < exit_time]
            after = []
            for window in windows:
                window_state = states.get(window["node"], {})
                start, finish, duration = (window_state.get("started_at_s"),
                                           window_state.get("finished_at_s"), window.get("duration_s"))
                if (not all(finite(value) for value in (start, finish, duration))
                        or finish - start + 1e-9 < duration or duration <= 0):
                    errors.append(f"{window['node']}:incomplete_observation_duration")
                after.extend(item for item in frames(window["node"], fault.get("replacement_agent"), subject)
                             if finite(exit_time) and item["timestamp_s"] >= exit_time)
            after.sort(key=lambda item: item["timestamp_s"])
            gap = after[0]["timestamp_s"] - before[-1]["timestamp_s"] if before and after else None
            freshness_gaps[subject or node_id] = round(gap, 6) if gap is not None else None
            if gap is None or gap > freshness_bound_s:
                errors.append(f"{node_id}:missing_or_stale_replacement_frame")
            if after:
                first_observations.append(after[0])
        recovery_latency = (
            round(max(item["timestamp_s"] for item in first_observations) - exit_time, 6)
            if first_observations and finite(exit_time) else None
        )
        results.append({
            "fault_id": fault["id"],
            "exited_agent": fault.get("agent"),
            "replacement_agent": fault.get("replacement_agent"),
            "affected_nodes": affected,
            "recovery_nodes": recovery_nodes,
            "scheduled_exit_time_s": float(fault["time_s"]),
            "exit_time_s": exit_time,
            "recovery_start_time_s": recovery_start,
            "dispatch_latency_s": round(recovery_start - exit_time, 6)
            if finite(recovery_start) and finite(exit_time) else None,
            "recovery_latency_s": recovery_latency,
            "first_replacement_observation_ids": [item["observation_id"] for item in first_observations],
            "freshness_gap_by_site_s": freshness_gaps,
            "recovery_succeeded": not errors,
            "validated": not errors,
            "violations": errors,
        })
    return {
        "fault_count": len(results),
        "recoveries": results,
        "required": bool(results),
        "freshness_bound_s": freshness_bound_s,
        "validated": not results or all(item["validated"] for item in results),
        "model": "member_exit_scheduling_and_observation_continuity",
        "limitations": [
            "does not establish physical safety or robot reliability",
            "does not establish payload, grasp force, wind, or load dynamics",
        ],
    }


def shared_resource_metrics(scheduling: Mapping[str, Any], resources: Iterable[str]) -> dict[str, Any]:
    normalized = [resource if resource.startswith("shared:") else f"shared:{resource}"
                  for resource in resources]
    conflicts = []
    occupancy = {}
    for resource in sorted(set(normalized)):
        intervals = _intervals(scheduling, resource)
        occupancy[resource] = [
            {"node": node, "start_s": start, "finish_s": finish}
            for node, start, finish in intervals
        ]
        for previous, current in zip(intervals, intervals[1:]):
            if current[1] < previous[2] - 1.0e-9:
                conflicts.append({"resource": resource, "first": previous[0], "second": current[0]})
    return {
        "resources": sorted(set(normalized)),
        "occupancy": occupancy,
        "reservation_conflicts": conflicts,
        "validated": not conflicts,
    }


def robot_spacing_metrics(pose_trajectory: Iterable[Mapping[str, Any]], *, minimum_m: float | None = None) -> dict[str, Any]:
    distances = []
    for sample in pose_trajectory:
        positions = sample.get("positions", {})
        ids = sorted(positions)
        for index, first in enumerate(ids):
            for second in ids[index + 1:]:
                one, two = positions[first], positions[second]
                if len(one) < 2 or len(two) < 2:
                    raise ValueError("pose samples require two-dimensional positions")
                distances.append(math.hypot(float(one[0]) - float(two[0]),
                                            float(one[1]) - float(two[1])))
    minimum = min(distances) if distances else None
    valid = minimum_m is None or (minimum is not None and minimum >= minimum_m)
    return {
        "minimum_robot_separation_m": None if minimum is None else round(minimum, 6),
        "required_minimum_m": minimum_m,
        "sample_pair_count": len(distances),
        "validated": valid,
    }


def inspection_observation_metrics(observation_report: Mapping[str, Any], *, target_ref: str,
                                   required_consumers: Iterable[str] = (),
                                   minimum_observations: int = 1,
                                   observation_ids: Iterable[str] | None = None) -> dict[str, Any]:
    selected_ids = set(observation_ids) if observation_ids is not None else None
    observations = [item for item in observation_report.get("observations", [])
                    if item.get("target_ref") == target_ref
                    and (selected_ids is None or item.get("observation_id") in selected_ids)]
    consumers = {consumer for item in observations for consumer in item.get("consumed_by", [])
                 for consumer in [consumer.get("consumer")]}
    required = set(required_consumers)
    return {
        "target_ref": target_ref,
        "observation_count": len(observations),
        "observation_ids": [item.get("observation_id") for item in observations],
        "frame_observation_count": sum(
            item.get("quality", {}).get("frame_acquired") is True
            for item in observations
        ),
        "detector_output_count": sum(
            1 for item in observation_report.get("detections", [])
            if item.get("source_observation_id") in {obs.get("observation_id") for obs in observations}
        ),
        "estimate_kinds": sorted({
            item.get("target_estimate", {}).get("kind", "unknown")
            for item in observations
        }),
        "consumers": sorted(consumers),
        "missing_consumers": sorted(required - consumers),
        "minimum_observations": minimum_observations,
        "observation_chain_validated": len(observations) >= minimum_observations and required <= consumers,
        "validated": len(observations) >= minimum_observations and required <= consumers,
        "target_identification_validated": False,
        "target_localization_validated": False,
        "accuracy_validated": False,
        "limitations": [
            "frame acquisition and downstream consumption do not establish target identity",
            "sensor_pose_only is not a target position estimate",
            "no detector, localization error, occlusion replay, or second-view confirmation is registered",
        ],
    }


def inspection_detection_metrics(observation_report: Mapping[str, Any], *, target_ref: str,
                                 expected_target_id: str,
                                 ground_truth_position_m: Iterable[float],
                                 localization_tolerance_m: float,
                                 minimum_detections: int = 1,
                                 require_second_view: bool = False) -> dict[str, Any]:
    """Evaluate detector claims against truth owned by the evaluator.

    The truth position is supplied by the evaluator and is intentionally not
    read from or written into :class:`ObservationStore`.  This keeps a live
    observation report auditable as a sensor/detector claim rather than making
    it self-confirming.
    """
    if not isinstance(expected_target_id, str) or not expected_target_id:
        raise ValueError("expected_target_id is required")
    truth = tuple(float(value) for value in ground_truth_position_m)
    if len(truth) != 3 or not all(math.isfinite(value) for value in truth):
        raise ValueError("ground_truth_position_m must contain three finite values")
    tolerance = float(localization_tolerance_m)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("localization_tolerance_m must be positive and finite")
    if minimum_detections < 1:
        raise ValueError("minimum_detections must be positive")
    observations = {
        item.get("observation_id"): item
        for item in observation_report.get("observations", [])
        if item.get("target_ref") == target_ref
    }
    detections = [
        item for item in observation_report.get("detections", [])
        if item.get("source_observation_id") in observations
    ]
    correct_identity = [item for item in detections
                        if item.get("target_id") == expected_target_id]
    errors = []
    for item in correct_identity:
        estimate = item.get("estimated_position_m")
        if isinstance(estimate, (list, tuple)) and len(estimate) == 3:
            errors.append(math.sqrt(sum((float(a) - b) ** 2
                                        for a, b in zip(estimate, truth))))
    distinct_views = {
        observations[item["source_observation_id"]].get("frame_id")
        for item in detections if item["source_observation_id"] in observations
    }
    identification_validated = len(detections) >= minimum_detections and (
        len(correct_identity) == len(detections))
    localization_validated = identification_validated and len(errors) >= minimum_detections and (
        max(errors) <= tolerance)
    second_view_validated = len(distinct_views) >= 2
    return {
        "target_ref": target_ref,
        "expected_target_id": expected_target_id,
        "detection_count": len(detections),
        "correct_identity_count": len(correct_identity),
        "distinct_view_count": len(distinct_views),
        "localization_errors_m": [round(value, 6) for value in errors],
        "max_localization_error_m": max(errors) if errors else None,
        "localization_tolerance_m": tolerance,
        "minimum_detections": minimum_detections,
        "identification_validated": identification_validated,
        "localization_validated": localization_validated,
        "second_view_validated": second_view_validated,
        "validated": identification_validated and localization_validated and (
            not require_second_view or second_view_validated),
        "ground_truth_source": "evaluator_only",
        "limitations": [
            "confidence is reported by the detector and is not calibrated here",
            "a detector claim is evaluated only when linked to a captured observation",
        ],
    }


def coverage_freshness_metrics(observation_report: Mapping[str, Any], *, site_ids: Iterable[str],
                               now_s: float, freshness_bound_s: float) -> dict[str, Any]:
    if not math.isfinite(float(now_s)) or not math.isfinite(float(freshness_bound_s)) or freshness_bound_s <= 0:
        raise ValueError("now_s must be finite and freshness_bound_s must be positive")
    sites = set(site_ids)
    latest = {}
    timestamps: dict[str, list[float]] = {site: [] for site in sites}
    for item in observation_report.get("observations", []):
        site = item.get("target_ref")
        timestamp = item.get("timestamp_s")
        if site in sites and isinstance(timestamp, (int, float)):
            value = float(timestamp)
            latest[site] = max(value, latest.get(site, float("-inf")))
            timestamps[site].append(value)
    stale = sorted(site for site in sites if site not in latest
                   or float(now_s) - latest[site] > freshness_bound_s)
    ages = [float(now_s) - latest[site] for site in sites if site in latest]
    max_gap_by_site = {}
    for site, values in timestamps.items():
        ordered = sorted(values)
        gaps = [right - left for left, right in zip(ordered, ordered[1:])]
        if ordered:
            gaps.append(float(now_s) - ordered[-1])
        max_gap_by_site[site] = max(gaps, default=None)
    max_gap = max((gap for gap in max_gap_by_site.values() if gap is not None), default=None)
    return {
        "site_ids": sorted(sites),
        "latest_observation_s": {site: latest[site] for site in sorted(latest)},
        "stale_sites": stale,
        "max_age_s": max(ages) if ages else None,
        "max_gap_by_site_s": max_gap_by_site,
        "max_gap_s": max_gap,
        "freshness_bound_s": float(freshness_bound_s),
        "validated": not stale and (max_gap is None or max_gap <= freshness_bound_s),
        "coverage_accuracy_validated": False,
    }


__all__ = [
    "coverage_freshness_metrics", "coverage_member_exit_metrics",
    "inspection_observation_metrics",
    "inspection_detection_metrics",
    "robot_spacing_metrics", "scheduling_metrics", "shared_resource_metrics",
]
