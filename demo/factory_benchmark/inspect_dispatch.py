"""Replay image-derived dispatch messages, policy decisions and measured arrivals."""

import math

from demo.benchmark.camera_validation import validate_camera_windows
from demo.benchmark.challenge_contract import (
    validate_participant_action,
    validate_participant_event,
)
from demo.benchmark.participant_runtime import replay_adapter_actions
from demo.benchmark.provenance import canonical_sha256
from demo.factory_benchmark.baseline_solver import BaselineDispatchAdapter, dispatch_policy_decision
from demo.factory_benchmark.challenge import build_participant_api, build_public_task_spec
from demo.factory_benchmark.inspect_inventory import replay_marker_frames, replay_marker_views
from demo.factory_benchmark.inventory_dispatch import (
    DISPATCH_NODE, PUBLIC_RULES, VIEW_NODES, delivered_messages, dispatch_trajectory,
    validate_dispatch_task, view_messages,
)


def evaluate_dispatch_arrival(report, *, scenario, decision, expected_marker_id):
    from demo.benchmark.runtime import summarize_scheduled_trajectory

    target_ref = PUBLIC_RULES["destinations_by_marker_id"].get(str(expected_marker_id))
    if target_ref is None:
        raise ValueError("evaluator true identity has no public dispatch rule")
    trace = report["pose_trajectory"]
    if trace.get("frame") != "world" or trace.get("position_unit") != "m":
        raise ValueError("dispatch requires world/metre pose samples")
    samples = trace["samples"]
    previous_step, by_step = -1, {}
    for sample in samples:
        step = sample["step"]
        if isinstance(step, bool) or not isinstance(step, int) or step < previous_step:
            raise ValueError("dispatch pose samples must have ordered integer steps")
        position = sample["positions"][decision["agent"]]
        if len(position) != 2 or any(isinstance(value, bool) or not math.isfinite(value) for value in position):
            raise ValueError("dispatch positions must contain two finite coordinates")
        if step in by_step and by_step[step] != position:
            raise ValueError("inconsistent dispatch positions at the same step")
        by_step[step], previous_step = position, step
    if 0 not in by_step or report["steps_taken"] not in by_step or decision["step"] not in by_step:
        raise ValueError("dispatch trace lacks initial, decision or final samples")
    if by_step[decision["step"]] != decision["start_position_m"][:2]:
        raise ValueError("dispatch decision position differs from the real trace")
    effective, trajectory = dispatch_trajectory(scenario, samples, decision)
    verdict = summarize_scheduled_trajectory(effective, trajectory, report["scheduling"])
    if canonical_sha256(verdict) != canonical_sha256(report["evaluation"]):
        raise ValueError("dispatch predicate result differs from the resolved route and pose trace")
    if report["inventory_dispatch"].get("resolved_scenario_sha256") != canonical_sha256(effective):
        raise ValueError("dispatch resolved task differs from the original task plus its decision")
    events = [event for event in report["events"]
              if event.get("event") == "node_complete" and event.get("node") == DISPATCH_NODE]
    state = report["scheduling"]["states"][DISPATCH_NODE]
    arrival_error = None
    arrival_time = None
    route_completed = False
    if events:
        if len(events) != 1:
            raise ValueError("dispatch requires a unique arrival event")
        event = events[0]
        expected_predicate = {"fn": "robot_visited_zone", "args": [decision["agent"], decision["target_ref"]]}
        if (event.get("arrival_predicate") != expected_predicate or event.get("agent") != decision["agent"]
                or event.get("confirmed_after_sim_step") is not True
                or event["step"] < decision["step"] or event.get("position") != by_step.get(event["step"])):
            raise ValueError("dispatch arrival is not supported by a post-decision measured visit")
        target = PUBLIC_RULES["destinations"][decision["target_ref"]]
        arrival_error = math.dist(event["position"], target["position"][:2])
        arrival_time = event["step"] * report["control_dt_s"]
        if (state["status"] != "succeeded" or not math.isclose(state["finished_at_s"], arrival_time, abs_tol=1e-8)
                or arrival_error > target["radius_m"]):
            raise ValueError("dispatch completion is outside the selected station or timestamp")
        route_completed = True
    elif state["status"] == "succeeded":
        raise ValueError("dispatch claims completion without a measured arrival")
    correct_route = decision["target_ref"] == target_ref
    return {"expected_target_ref": target_ref, "selected_target_ref": decision["target_ref"],
            "correct_route_selected": correct_route, "selected_route_completed": route_completed,
            "arrival_error_m": arrival_error, "arrival_time_s": arrival_time,
            "dispatch_success": correct_route and route_completed,
            "used_information": bool(decision["used_message_ids"]),
            "success_by_fallback": correct_route and route_completed and not decision["used_message_ids"],
            "resolved_predicates_validated": True}


def _evaluate_participant_trace(report, *, scenario, messages, decision, options, state):
    payload = report["inventory_dispatch"]
    participant = payload.get("participant")
    if not isinstance(participant, dict):
        raise ValueError("dispatch v3 requires a participant trace")
    public_task = build_public_task_spec(
        scenario,
        scenario_sha256=report["execution_provenance"]["request"]["scenario_sha256"],
    )
    api = build_participant_api(public_task)
    if (participant.get("public_task_sha256") != canonical_sha256(public_task)
            or participant.get("participant_api_sha256") != canonical_sha256(api)):
        raise ValueError("participant trace is not bound to the current public task and API")
    events = participant.get("events")
    actions = participant.get("actions")
    actor = participant.get("adapter")
    if not isinstance(events, list) or not isinstance(actions, list) or not isinstance(actor, dict):
        raise ValueError("participant trace requires adapter, events and actions")
    if actor != decision.get("actor"):
        raise ValueError("dispatch decision actor differs from the participant trace")
    previous_sequence = -1
    previous_event_time = 0.0
    for event in events:
        validate_participant_event(event, api=api)
        if event["sequence"] != previous_sequence + 1:
            raise ValueError("participant event sequence is not contiguous")
        if event.get("turn_id") is None or event["time_s"] > decision["time_s"] + 1e-9:
            raise ValueError("participant event turn or time is invalid")
        if event["time_s"] < previous_event_time:
            raise ValueError("participant event time is not monotonic")
        previous_sequence = event["sequence"]
        previous_event_time = event["time_s"]
    action_ids = set()
    previous_action_time = 0.0
    for action in actions:
        validate_participant_action(action, api=api, task_spec=public_task)
        if action["action_id"] in action_ids or action.get("turn_id") is None:
            raise ValueError("participant action ids and turns must be valid")
        action_ids.add(action["action_id"])
        if action["submitted_at_s"] > decision["time_s"] + 1e-9:
            raise ValueError("participant action occurs after the dispatch decision")
        if action["submitted_at_s"] < previous_action_time:
            raise ValueError("participant action time is not monotonic")
        turn_events = [event for event in events if event["turn_id"] == action["turn_id"]]
        if (not turn_events or not math.isclose(
                action["submitted_at_s"], max(event["time_s"] for event in turn_events), abs_tol=1e-9)):
            raise ValueError("participant action time differs from its event turn")
        previous_action_time = action["submitted_at_s"]

    ready_events = [event for event in events if event["type"] == "phase_ready"]
    if len(ready_events) != 1:
        raise ValueError("participant trace requires one dispatch phase_ready event")
    ready = ready_events[0]
    if (not math.isclose(ready["time_s"], state["started_at_s"], abs_tol=1e-8)
            or ready["payload"] != {
                "node_id": DISPATCH_NODE, "eligible_agent_ids": [decision["agent"]]}):
        raise ValueError("participant phase_ready event differs from the real dispatch stage")

    source_by_id = {message["message_id"]: message for message in messages}
    observed_message_ids = set()
    for event in (item for item in events if item["type"] == "observation_message"):
        message_id = event["payload"]["message_id"]
        if message_id in observed_message_ids or message_id not in source_by_id:
            raise ValueError("participant observation message is duplicate or unknown")
        visible, _ = delivered_messages(
            messages, mode=options["inventory_dispatch_mode"],
            delay_s=options["inventory_message_delay_s"], time_s=event["time_s"])
        if message_id not in {message["message_id"] for message in visible}:
            raise ValueError("participant received a message before delivery or after expiry")
        source = source_by_id[message_id]
        expected_payload = {
            "message_id": message_id,
            "producer_id": source["producer"],
            "published_at_s": source["published_at_s"],
            "valid_until_s": source["valid_until_s"],
            "estimate": source["estimate"],
        }
        if canonical_sha256(event["payload"]) != canonical_sha256(expected_payload):
            raise ValueError("participant observation event differs from its source message")
        observed_message_ids.add(message_id)
    visible_at_decision, _ = delivered_messages(
        messages, mode=options["inventory_dispatch_mode"],
        delay_s=options["inventory_message_delay_s"], time_s=decision["time_s"])
    expected_visible = {message["message_id"] for message in visible_at_decision}
    if (expected_visible - observed_message_ids
            or set(decision.get("available_message_ids", expected_visible)) != expected_visible):
        raise ValueError("participant trace omits or invents a currently delivered message")

    committed = [action for action in actions
                 if action["action_id"] == decision.get("committed_action_id")]
    if len(committed) != 1 or committed[0]["type"] != "navigate_goal":
        raise ValueError("dispatch decision is not bound to one participant navigation action")
    committed_index = actions.index(committed[0])
    expected_navigation = {
        "node_id": DISPATCH_NODE,
        "agent_id": decision["agent"],
        "target_ref": decision["target_ref"],
    }
    if (committed[0]["payload"] != expected_navigation
            or not math.isclose(committed[0]["submitted_at_s"], decision["time_s"], abs_tol=1e-8)):
        raise ValueError("participant navigation action differs from the dispatch decision")
    consumed = [action["payload"]["message_id"] for action in actions[:committed_index]
                if action["type"] == "consume_message"]
    if (len(consumed) != len(set(consumed))
            or sorted(consumed) != sorted(decision["used_message_ids"])
            or set(consumed) - observed_message_ids):
        raise ValueError("dispatch decision message use differs from participant consume actions")
    for action in (item for item in actions[:committed_index]
                   if item["type"] == "consume_message"):
        visible, _ = delivered_messages(
            messages, mode=options["inventory_dispatch_mode"],
            delay_s=options["inventory_message_delay_s"], time_s=action["submitted_at_s"])
        if action["payload"]["message_id"] not in {
                message["message_id"] for message in visible}:
            raise ValueError("participant consume action used an unavailable or expired message")

    replay_required = actor.get("kind") == "baseline_solver"
    replay_validated = None
    if replay_required:
        replayed = replay_adapter_actions(BaselineDispatchAdapter(
            policy=actor["id"], max_wait_s=actor["parameters"]["max_wait_s"]), events)
        if canonical_sha256(replayed) != canonical_sha256(actions):
            raise ValueError("baseline action trace differs from participant-visible event replay")
        replay_validated = True
    elif actor.get("kind") != "participant":
        raise ValueError("unknown participant actor kind")
    return {
        "participant_action_trace_validated": True,
        "baseline_replay_required": replay_required,
        "decision_replay_validated": replay_validated,
        "strategy_validation": "baseline_replay" if replay_required else "participant_action_binding_only",
        "participant_event_count": len(events),
        "participant_action_count": len(actions),
    }


def evaluate_dispatch(report, *, scenario):
    validate_dispatch_task(scenario)
    payload = report["inventory_dispatch"]
    if (payload["schema"] != "eai.factory_inventory_dispatch" or payload["version"] not in {2, 3}
            or payload["public_rules"] != PUBLIC_RULES
            or payload["public_rules_sha256"] != canonical_sha256(PUBLIC_RULES)):
        raise ValueError("dispatch public rules differ from the frozen implementation")
    observations = replay_marker_frames(report)
    replay_marker_views(report, observations)
    windows = validate_camera_windows(scenario, report)
    if not windows["validated"]:
        raise ValueError("dispatch source camera windows are invalid")
    messages = []
    for node in VIEW_NODES:
        state = report["scheduling"]["states"][node]
        if state["status"] == "succeeded":
            messages.extend(view_messages(report["inventory_detection"]["frames"], list(observations.values()),
                                          node=node, published_at_s=state["finished_at_s"]))
    if canonical_sha256(messages) != canonical_sha256(payload["messages"]):
        raise ValueError("dispatch messages differ from completed pixel observation windows")
    decision = payload["decision"]
    if decision is None:
        return {"schema": "eai.factory_dispatch_evaluation", "version": 1,
                "dispatch_success": False, "decision_replay_validated": False,
                "reason": "dispatch stage did not start"}
    options = report["execution_provenance"]["request"]["options"]
    state = report["scheduling"]["states"][DISPATCH_NODE]
    if (not math.isclose(decision["time_s"], decision["step"] * report["control_dt_s"], abs_tol=1e-8)
            or decision["ready_at_s"] != state["started_at_s"]
            or decision["time_s"] < state["started_at_s"]):
        raise ValueError("dispatch decision timing differs from its real stage")
    if payload["version"] == 2:
        start_step = round(state["started_at_s"] / report["control_dt_s"])
        for step in range(start_step, decision["step"]):
            premature = dispatch_policy_decision(
                messages, mode=options["inventory_dispatch_mode"],
                delay_s=options["inventory_message_delay_s"],
                time_s=step * report["control_dt_s"],
                policy=options["inventory_dispatch_policy"], ready_at_s=state["started_at_s"],
                max_wait_s=options["inventory_dispatch_wait_s"])
            if premature is not None:
                raise ValueError("dispatch policy could have committed before the recorded decision")
        replayed = dispatch_policy_decision(
            messages, mode=options["inventory_dispatch_mode"],
            delay_s=options["inventory_message_delay_s"], time_s=decision["time_s"],
            policy=options["inventory_dispatch_policy"], ready_at_s=state["started_at_s"],
            max_wait_s=options["inventory_dispatch_wait_s"])
        if replayed is None:
            raise ValueError("dispatch policy was still waiting at the recorded decision")
        if any(decision.get(key) != value for key, value in replayed.items()):
            raise ValueError("dispatch decision differs from the available-message policy replay")
        strategy_validation = {
            "participant_action_trace_validated": False,
            "baseline_replay_required": True,
            "decision_replay_validated": True,
            "strategy_validation": "legacy_baseline_replay",
        }
    else:
        strategy_validation = _evaluate_participant_trace(
            report, scenario=scenario, messages=messages, decision=decision,
            options=options, state=state)
    node = next(node for node in scenario["phase_graph"]["nodes"] if node["id"] == DISPATCH_NODE)
    if decision["node"] != DISPATCH_NODE or decision["agent"] != node["preferred_agent"]:
        raise ValueError("dispatch decision uses an undeclared recipient")
    events = [event for event in report["events"] if event.get("event") == "inventory_dispatch_decision"]
    if events != [{"event": "inventory_dispatch_decision", **decision}]:
        raise ValueError("dispatch event does not uniquely bind the decision")
    wait_events = [event for event in report["events"]
                   if event.get("event") == "inventory_dispatch_wait_started"]
    waited = decision["waited_s"] > 0 and any(
        action.get("type") == "wait"
        for action in payload.get("participant", {}).get("actions", ()))
    if payload["version"] == 2:
        waited = decision["decision_policy"] == "wait_for_message" and decision["waited_s"] > 0
    if bool(wait_events) != waited or len(wait_events) > 1:
        raise ValueError("dispatch wait event differs from the replayed decision policy")
    if (any(event.get("consumer") == DISPATCH_NODE for event in report["observation_store"]["consumptions"])
            or any(item.get("consumer") == DISPATCH_NODE for observation in observations.values()
                   for item in observation["consumed_by"])):
        raise ValueError("dispatch bypassed its message channel by consuming raw observations")
    variant = report["inventory_detection"]["world_variant"]
    if variant["marker_id"] != options["inventory_label_id"]:
        raise ValueError("dispatch evaluator truth differs from the frozen world")
    return {"schema": "eai.factory_dispatch_evaluation", "version": 1,
            **strategy_validation, "source_windows_validated": True,
            "message_count": len(messages), "available_message_ids": decision["available_message_ids"],
            **evaluate_dispatch_arrival(report, scenario=scenario, decision=decision,
                                        expected_marker_id=variant["marker_id"]),
            "limitations": ["One-shot route commitment; participant strategy is not treated as a normative answer",
                            "Station arrival is not material delivery or physical manipulation",
                            "No claim of aerial, multiple-view or heterogeneous-team necessity"]}
