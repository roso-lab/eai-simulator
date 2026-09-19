"""Validate live capture windows against their measured observation regions."""

import math


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("execution measurements require finite numbers")
    return float(value)


def capture_window_metrics(report, node, scope, *, max_gap_bound_s=30.0):
    from demo.benchmark.control import SCAN_YAW_TOLERANCE_RAD, WAIT_YAW_TOLERANCE_RAD, scan_hover_goal

    state = report["scheduling"]["states"][node["id"]]
    start = _finite_number(state["started_at_s"])
    end = _finite_number(state["finished_at_s"])
    wait_yaw = node.get("wait_yaw_rad")
    if wait_yaw is not None:
        windows = [event for event in report["events"] if event.get("node") == node["id"]
                   and event.get("event") == "observation_window_started"]
        if len(windows) != 1:
            raise ValueError("oriented observation requires one measured window start")
        window = windows[0]
        aligned_start = _finite_number(window["time_s"])
        yaw_error = abs((_finite_number(window["measured_yaw_rad"]) - wait_yaw + math.pi) % (2 * math.pi) - math.pi)
        if (window.get("agent") != node["preferred_agent"] or window.get("wait_yaw_rad") != wait_yaw
                or not start <= aligned_start <= end or yaw_error > WAIT_YAW_TOLERANCE_RAD
                or not math.isclose(_finite_number(window["yaw_error_rad"]), yaw_error, abs_tol=1e-8)
                or _finite_number(window["alignment_duration_s"]) + 1e-9 < 0.5
                or aligned_start - window["alignment_duration_s"] + 1e-8 < start
                or not math.isclose(aligned_start, (window["step"] - 1) * report["control_dt_s"], abs_tol=1e-8)):
            raise ValueError("oriented observation lacks matching heading alignment evidence")
        start = aligned_start
    if end < start or (node["skill_key"] == "wait" and end - start + 1e-8 < node["duration_s"]):
        raise ValueError("camera sampling did not span the required window")
    aerial = node["skill_key"] == "aerial_scan"
    target = scope["target"]
    goal = (scan_hover_goal(target["position"], scan_height_m=node.get("scan_height_m"))
            if aerial else target["position"][:2])
    position_bound = 0.2 if aerial else _finite_number(target["radius_m"])
    if position_bound <= 0:
        raise ValueError("camera observation region must have a positive radius")
    ids, frame_events = [], {}
    for event in report["events"]:
        if event.get("node") == node["id"] and event.get("event") in {"coverage_observation", "aerial_scan"}:
            if (event.get("observation_source") != "live_isaac_rgb_frame"
                    or event.get("agent") != node["preferred_agent"]
                    or event.get("target_ref") != scope["subject"]["id"]
                    or event["event"] != ("aerial_scan" if aerial else "coverage_observation")):
                raise ValueError("camera protocol requires live Isaac frame events")
            event_ids = list(event.get("observation_ids", []))
            if event.get("observation_id"):
                event_ids.append(event["observation_id"])
            ids.extend(event_ids)
            frame_events.update({identifier: event for identifier in event_ids})
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("camera protocol requires distinct frame observations")
    observations = report["observation_store"]["observations"]
    by_id = {item["observation_id"]: item for item in observations}
    if len(by_id) != len(observations):
        raise ValueError("observation store contains duplicate ids")
    timestamps, position_errors, yaw_errors = [], [], []
    for identifier in ids:
        frame = by_id[identifier]
        config, quality = frame["sensor_config"], frame["quality"]
        shape = config.get("frame_shape", [])
        digest = config.get("frame_sha256", "")
        if (frame["producer"] != node["preferred_agent"] or frame["sensor"] != "camera"
                or frame["target_ref"] != scope["subject"]["id"]
                or quality.get("frame_acquired") is not True
                or config.get("frame_dtype") != "uint8" or len(shape) != 3
                or any(isinstance(size, bool) or not isinstance(size, int) or size < 1 for size in shape)
                or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
                or not 0 < _finite_number(quality.get("valid_pixel_count")) <= math.prod(shape)):
            raise ValueError("camera observation has invalid producer, subject or frame evidence")
        timestamp = _finite_number(frame["timestamp_s"])
        if config.get("archive_path") is not None:
            from demo.benchmark.camera_capture import verify_archived_frame
            verify_archived_frame(config)
        if not start <= timestamp <= end + report["control_dt_s"] + 1e-8:
            raise ValueError("camera timestamp is outside its execution window")
        event = frame_events[identifier]
        step = event.get("step")
        if (isinstance(step, bool) or not isinstance(step, int) or step < 1
                or not math.isclose(timestamp, (step if aerial else step - 1) * report["control_dt_s"],
                                    rel_tol=0.0, abs_tol=1e-8)):
            raise ValueError("camera timestamp differs from its capture event")
        estimate = frame.get("target_estimate", {})
        position = estimate.get("position_m")
        if (estimate.get("kind") != "sensor_pose_only"
                or not isinstance(position, (list, tuple)) or len(position) != 3):
            raise ValueError("camera capture requires a recorded observer position")
        position = tuple(_finite_number(value) for value in position)
        error = math.dist(position if aerial else position[:2], goal)
        if error > position_bound:
            raise ValueError("camera observer is outside its declared observation region")
        if aerial:
            if (event.get("confirmed_after_sim_step") is not True
                    or tuple(event.get("hover_goal", ())) != goal
                    or tuple(event.get("measured_position", ())) != position
                    or not math.isclose(_finite_number(event.get("position_error_m")), error, abs_tol=1e-8)
                    or _finite_number(event.get("hover_duration_s")) + 1e-9 < 0.5):
                raise ValueError("camera capture lacks matching measured hover evidence")
            if node.get("scan_yaw_rad") is not None:
                measured_yaw = _finite_number(event.get("measured_yaw_rad"))
                yaw_error = abs((measured_yaw - node["scan_yaw_rad"] + math.pi) % (2 * math.pi) - math.pi)
                if (event.get("scan_yaw_rad") != node["scan_yaw_rad"]
                        or config.get("observer_yaw_rad") != measured_yaw
                        or not math.isclose(_finite_number(event.get("yaw_error_rad")), yaw_error, abs_tol=1e-8)
                        or yaw_error > SCAN_YAW_TOLERANCE_RAD):
                    raise ValueError("camera capture lacks matching measured scan heading")
                yaw_errors.append(yaw_error)
        elif config.get("node") != node["id"]:
            raise ValueError("camera capture belongs to a different observation node")
        if wait_yaw is not None:
            yaw_error = abs((_finite_number(config.get("observer_yaw_rad")) - wait_yaw + math.pi) % (2 * math.pi) - math.pi)
            if yaw_error > WAIT_YAW_TOLERANCE_RAD:
                raise ValueError("camera observer is outside its declared wait heading")
            yaw_errors.append(yaw_error)
        position_errors.append(error)
        timestamps.append(timestamp)
    timestamps.sort()
    if aerial:
        if len(ids) != 1:
            raise ValueError("aerial camera protocol requires one measured hover capture")
        hover_start = timestamps[-1] - frame_events[ids[0]]["hover_duration_s"]
        if hover_start + 1e-8 < start:
            raise ValueError("measured hover starts before its scheduled window")
        start = hover_start
    else:
        trace = report.get("pose_trajectory", {})
        if trace.get("frame") != "world" or trace.get("position_unit") != "m":
            raise ValueError("camera window requires a world/metre observer trace")
        for sample in trace.get("samples", []):
            if start <= sample["step"] * report["control_dt_s"] <= end:
                position = tuple(_finite_number(value) for value in sample["positions"][node["preferred_agent"]][:2])
                if len(position) != 2 or math.dist(position, goal) > position_bound:
                    raise ValueError("camera observer left its region during the sampled window")
                if wait_yaw is not None:
                    measured_yaw = _finite_number(sample["robot_poses_xyzyaw"][node["preferred_agent"]][3])
                    if abs((measured_yaw - wait_yaw + math.pi) % (2 * math.pi) - math.pi) > WAIT_YAW_TOLERANCE_RAD:
                        raise ValueError("camera observer left its heading during the sampled window")
    end = max(end, timestamps[-1])
    boundaries = [start, *timestamps, end]
    maximum_gap = max(second - first for first, second in zip(boundaries, boundaries[1:]))
    if maximum_gap > max_gap_bound_s or end <= start:
        raise ValueError("camera capture did not meet its window or freshness bound")
    return {"frame_count": len(ids), "window_duration_s": end - start,
            "max_capture_position_error_m": max(position_errors),
            **({"max_capture_yaw_error_rad": max(yaw_errors)} if yaw_errors else {}),
            "max_gap_s": maximum_gap}, {
                "max_gap_bound_s": max_gap_bound_s,
                **({"observer_yaw_bound_rad": WAIT_YAW_TOLERANCE_RAD if wait_yaw is not None
                    else SCAN_YAW_TOLERANCE_RAD} if yaw_errors else {}),
                "observer_position_bound_m": position_bound}


def validate_camera_windows(data, report):
    targets = {target["id"]: target for target in data["composition"]["targets"]}
    subjects = {**targets, **{entity["id"]: entity for entity in data["composition"]["entities"]}}
    robots = {robot["id"]: robot for robot in data["robots"]}
    checks = {}
    required_waits = data.get("benchmark_task_id") in {"F-COV-01", "F-INS-01"}
    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] not in {"wait", "aerial_scan"}:
            continue
        has_camera = any(attachment["type"] == "camera"
                         for attachment in robots[node["preferred_agent"]]["attachments"])
        if node["skill_key"] == "wait" and not (required_waits or has_camera):
            continue
        state = report["scheduling"]["states"][node["id"]]
        if state["status"] == "skipped":
            continue
        partial = (node["skill_key"] == "wait" and state["status"] == "failed"
                   and node["id"] in report["scheduling"].get("recovered_nodes", ()))
        try:
            if state["status"] != "succeeded" and not partial:
                raise ValueError("camera node did not complete or recover")
            scope = {"target": targets[node["target_ref"]],
                     "subject": subjects[node.get("observation_ref") or node["target_ref"]]}
            metrics, acceptance = capture_window_metrics(
                report, {**node, "duration_s": 0.0} if partial else node, scope)
        except (ValueError, KeyError, TypeError, IndexError, OSError) as error:
            checks[node["id"]] = {"validated": False, "reason": str(error), "partial_recovered_window": partial}
        else:
            checks[node["id"]] = {"validated": True, "metrics": metrics, "acceptance": acceptance,
                                  "partial_recovered_window": partial}
    return {"validated": all(check["validated"] for check in checks.values()), "nodes": checks,
            "position_source": "robot_base_world_position_proxy",
            "limitations": ["No calibrated camera extrinsics or target visibility claim",
                            "Containment is checked at capture and trajectory sample times"]}
