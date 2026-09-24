"""Focused archive-contract tests for autonomous Restaurant business evidence."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_restaurant_algorithm_episode import (
    REGION_BINDINGS,
    ROBOT_BINDINGS,
)
from demo.benchmark.goal_restaurant_business_v2 import (
    RELAY_CHANNEL,
    RELAY_WIRE_SCHEMA,
    trusted_task_regions_from_replayed_audit,
    verify_restaurant_business_v2,
)
from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION
from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session_audit import (
    AuditedGoalSession,
    replay_goal_session_audit,
)
from demo.benchmark.goal_restaurant_episode import (
    MINIMUM_REGION_SUPPORT_SAMPLES,
    REGION_OBSERVATION_TOLERANCE_M,
    REGION_SUPPORT_RAY_DISTANCE_M,
    REGION_SUPPORT_RAY_HEIGHT_M,
    REGION_SUPPORT_SAMPLE_OFFSETS_M,
)
from demo.benchmark.provenance import canonical_sha256
from demo.restaurant_benchmark import contact_probe
from demo.restaurant_benchmark.service_backend_v2 import RestaurantReadinessBackendV2


EPISODE = "restaurant-autonomous-review"
SERVICE = "restaurant_opening_service"
JOINT_TIME_BY_STEP = {
    1: 0.0, 2: 8.0, 3: 12.0,
    4: 8.0, 5: 16.0, 6: 20.0,
    8: 24.0, 9: 25.0, 10: 26.0, 11: 27.0,
}


def _root(xy, z=0.2):
    return [*xy, z, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _time_for_step(step, time_by_step=None):
    return ((time_by_step or {}).get(step, step * 0.1))


def _stamp(step, xy, *, z=0.2, time_by_step=None):
    return {"simulation_step": step,
            "time_s": _time_for_step(step, time_by_step),
            "root_state_w": _root(xy, z)}


def _with_ref(row):
    body = deepcopy(row)
    return {**body, "evidence_ref": "sha256:" + canonical_sha256(body)}


def _task(task_id, skill, producer, arguments):
    return {
        "task_id": task_id, "skill": skill, "arguments": deepcopy(arguments),
        "prerequisites": [], "required_capabilities": [skill],
        "assigned_agents": [producer], "evidence_refs": [],
        "execution": {"start_s": 0.0, "duration_s": 20.0},
    }


def _contact_row(task_id, producer, region, public_route, step, *, time_by_step=None):
    native = ROBOT_BINDINGS[producer]["native_agent_id"]
    center = REGION_BINDINGS[region]["center_xy_m"]
    wheels = {
        name: {"ground_force_N": [0.0, 0.0, 25.0],
               "buffer_capacity_reached": False}
        for name in contact_probe.WHEEL_NAMES
    }
    return _with_ref({
        "kind": "arrival", "episode_id": EPISODE, "task_id": task_id,
        "producer_id": producer, "native_agent_id": native,
        "route_id": f"physical:{producer}:{region}",
        "public_route_id": public_route, "region_id": region,
        "started": _stamp(
            step - 1, ROBOT_BINDINGS[producer]["spawn_xy_m"],
            time_by_step=time_by_step),
        "arrival": _stamp(step, center, time_by_step=time_by_step),
        "steps_taken": 1,
        "control_step_evidence": {"validated": True, "sample_count": 1},
        "contact_view_identities": {
            name: {"sensor_paths": [f"/World/{native}/{name}"],
                   "filter_paths": [contact_probe.GROUND_COLLISION_PATH],
                   "max_contact_data_count": contact_probe.CONTACT_BUFFER_CAPACITY}
            for name in contact_probe.WHEEL_NAMES
        },
        "contact_snapshots": [{
            "route_control_step": 1, "weight_support_ratio": 1.0,
            "wheels": wheels,
        }],
        "wheel_contact_sample_ratios": {
            name: 1.0 for name in contact_probe.WHEEL_NAMES},
        "median_weight_support_ratio": 1.0,
        "contact_buffer_capacity_reached": False,
        "contact_health_validated": True,
    })


def _scene(producer, region, step, *, time_by_step=None):
    center = REGION_BINDINGS[region]["center_xy_m"]
    stamp = _stamp(step, center, time_by_step=time_by_step)
    samples = []
    for dx, dy in REGION_SUPPORT_SAMPLE_OFFSETS_M:
        samples.append({
            "origin_m": [center[0] + dx, center[1] + dy,
                         REGION_SUPPORT_RAY_HEIGHT_M],
            "direction": [0.0, 0.0, -1.0],
            "distance_m": REGION_SUPPORT_RAY_DISTANCE_M,
            "hit": {"hit": True, "collision": contact_probe.GROUND_COLLISION_PATH,
                    "position_m": [center[0] + dx, center[1] + dy, 0.0],
                    "normal": [0.0, 0.0, 1.0]},
            "passed": True,
        })
    return {
        "schema": "eai.restaurant.native_region_observation", "version": 1,
        "region_id": region, "producer_id": producer,
        "query_kind": "episode_timestamped_physx_ground_support_raycast",
        "scene_queries_enabled": True,
        "expected_collision_path": contact_probe.GROUND_COLLISION_PATH,
        "region_center_xy_m": list(center), "observed_robot_xy_m": list(center),
        "region_error_m": 0.0,
        "region_tolerance_m": REGION_OBSERVATION_TOLERANCE_M,
        "support_samples": samples,
        "minimum_support_samples": MINIMUM_REGION_SUPPORT_SAMPLES,
        "passed_support_samples": len(samples),
        "scene_observation_validated": True, "stamp": stamp,
    }


def _bind_task(archive, task, evidence_refs, index, completed_at_s):
    action = f"dispatch-{index}"
    receipt = {
        "backend_id": "native-test-v2", "receipt_ref": f"{EPISODE}/{task['task_id']}",
        "task_id": task["task_id"], "action_id": action, "status": "succeeded",
        "completed_at_s": completed_at_s, "episode_id": EPISODE,
        "evidence_refs": list(evidence_refs),
    }
    archive["session_tasks"][task["task_id"]] = {
        "status": "succeeded", "task": deepcopy(task),
        "dispatch_action_id": action, "dispatched_at_s": 0.0,
        "receipt": deepcopy(receipt),
    }
    archive["skill_receipts"].append(receipt)


def _relay_stamp(region, step, *, time_by_step=None):
    return _stamp(
        step, REGION_BINDINGS[region]["center_xy_m"],
        time_by_step=time_by_step)


def _build_case(*, shared_observer, reverse, selected_retry=False, drop=False,
                time_by_step=None):
    service = RestaurantReadinessBackendV2(episode_id=EPISODE)
    archive = {"session_tasks": {}, "skill_receipts": [], "native_evidence": [],
               "trusted_task_regions": {}}
    first_region, second_region = (("dining_service", "kitchen_service") if reverse
                                   else ("kitchen_service", "dining_service"))
    producers = (["restaurant_carter_1", "restaurant_carter_1"] if shared_observer
                 else ["restaurant_carter_2", "restaurant_carter_1"])
    observations = []
    step = 2
    current_regions = {producer: "restaurant_staging" for producer in set(producers)}
    for number, (region, producer) in enumerate(zip(
            (first_region, second_region), producers), 1):
        from_region = current_regions[producer]
        route_id = {
            ("restaurant_staging", "kitchen_service"): "restaurant_staging_to_kitchen",
            ("restaurant_staging", "dining_service"): "restaurant_staging_to_dining",
            ("kitchen_service", "dining_service"): "restaurant_kitchen_to_dining",
            ("dining_service", "kitchen_service"): "restaurant_dining_to_kitchen",
        }[(from_region, region)]
        arrival_task = _task(
            f"arrive-{number}", "ground_navigation", producer,
            {"region_id": region, "route_ids": [route_id]})
        arrival = _contact_row(
            arrival_task["task_id"], producer, region,
            arrival_task["arguments"]["route_ids"][0], step,
            time_by_step=time_by_step)
        archive["native_evidence"].append(arrival)
        _bind_task(archive, arrival_task, [arrival["evidence_ref"]], number,
                   _time_for_step(step, time_by_step))
        current_regions[producer] = region
        scene = _scene(
            producer, region, step + 1, time_by_step=time_by_step)
        digest = "sha256:" + canonical_sha256({
            "episode_id": EPISODE, "region_id": region,
            "producer_id": producer, "scene_observation": scene,
        })
        service_row = service.record_observation(
            region_id=region, observation_id=f"observation-{number}",
            simulation_step=step + 1,
            time_s=_time_for_step(step + 1, time_by_step),
            producer_id=producer, evidence_digest=digest)
        observe_task = _task(
            f"observe-{number}", "observe", producer,
            {"region_id": region, "subject_ref": region,
             "observation_mode": "service_ground_readiness"})
        native = _with_ref({
            "kind": "observation", "episode_id": EPISODE,
            "task_id": observe_task["task_id"], "producer_id": producer,
            "native_agent_id": ROBOT_BINDINGS[producer]["native_agent_id"],
            "region_id": region, "stamp": scene["stamp"],
            "scene_observation": scene, "service_record": service_row,
        })
        archive["native_evidence"].append(native)
        _bind_task(archive, observe_task, [native["evidence_ref"]], number + 10,
                   _time_for_step(step + 1, time_by_step))
        observations.append(service_row)
        step += 3

    # Reinspect one region without imposing a unique observation count.
    region = second_region
    producer = producers[1]
    scene = _scene(producer, region, step, time_by_step=time_by_step)
    digest = "sha256:" + canonical_sha256({
        "episode_id": EPISODE, "region_id": region,
        "producer_id": producer, "scene_observation": scene,
    })
    repeat = service.record_observation(
        region_id=region, observation_id="observation-repeat",
        simulation_step=step, time_s=_time_for_step(step, time_by_step),
        producer_id=producer, evidence_digest=digest)
    observe_task = _task(
        "observe-repeat", "observe", producer,
        {"region_id": region, "subject_ref": region,
         "observation_mode": "service_ground_readiness"})
    native = _with_ref({
        "kind": "observation", "episode_id": EPISODE,
        "task_id": observe_task["task_id"], "producer_id": producer,
        "native_agent_id": ROBOT_BINDINGS[producer]["native_agent_id"],
        "region_id": region, "stamp": scene["stamp"],
        "scene_observation": scene, "service_record": repeat,
    })
    archive["native_evidence"].append(native)
    _bind_task(
        archive, observe_task, [native["evidence_ref"]], 30,
        _time_for_step(step, time_by_step))

    runtime = CommunicationRuntime({
        **({"rules": [{"recipient": SERVICE, "channel": RELAY_CHANNEL,
                        "drop": True}]} if drop else {})})
    publications = []
    relay_rows = []
    relay_specs = [
        ("relay-old", producers[1], second_region,
         [observations[0]["observation_id"], observations[1]["observation_id"]]),
        ("relay-retry", producers[0], first_region,
         [observations[0]["observation_id"], repeat["observation_id"]]),
    ]
    step += 1
    for index, (message_id, relay_producer, relay_region, refs) in enumerate(relay_specs):
        publication = service.publish_relay(
            message_id=message_id, simulation_step=step,
            time_s=_time_for_step(step, time_by_step),
            observation_refs=refs)
        relay_task = _task(
            f"relay-{index}", "relay", relay_producer,
            {"message_kind": "restaurant_readiness_receipt",
             "region_id": relay_region})
        relay_row = _with_ref({
            "kind": "relay_publication", "episode_id": EPISODE,
            "task_id": relay_task["task_id"], "producer_id": relay_producer,
            "native_agent_id": ROBOT_BINDINGS[relay_producer]["native_agent_id"],
            "publication_stamp": _relay_stamp(
                relay_region, step, time_by_step=time_by_step),
            "relay_publication": publication,
        })
        archive["native_evidence"].append(relay_row)
        wire = {
            "schema": RELAY_WIRE_SCHEMA, "version": 1, "episode_id": EPISODE,
            "producer_id": relay_producer, "relay_message_id": message_id,
            "observation_refs": list(publication["observation_refs"]),
            "relay_publication": deepcopy(publication),
            "source_evidence_ref": relay_row["evidence_ref"],
        }
        runtime.publish(
            message_id=message_id, producer_id=relay_producer,
            recipients=[SERVICE], channel=RELAY_CHANNEL, estimate=wire,
            published_at_s=publication["time_s"], valid_until_s=100.0)
        publications.append(publication)
        relay_rows.append((relay_task, relay_row))
        archive["trusted_task_regions"][relay_task["task_id"]] = {
            relay_producer: relay_region}
        step += 1

    selected = 1 if selected_retry else 0
    selected_task, selected_row = relay_rows[selected]
    service.commit_readiness_receipt(
        receipt_id="ready", simulation_step=step,
        time_s=_time_for_step(step, time_by_step),
        observation_refs=publications[selected]["observation_refs"],
        relay_message_id=publications[selected]["message_id"])
    snapshot = service.snapshot()
    terminal = _with_ref({
        "kind": "readiness_receipt", "episode_id": EPISODE,
        "task_id": selected_task["task_id"],
        "producer_id": selected_row["producer_id"],
        "native_agent_id": selected_row["native_agent_id"],
        "relay_evidence_ref": selected_row["evidence_ref"],
        "receipt_stamp": _relay_stamp(
            selected_task["arguments"]["region_id"], step,
            time_by_step=time_by_step),
        "business_receipt": snapshot["receipt"],
    })
    archive["native_evidence"].append(terminal)
    for index, (relay_task, relay_row) in enumerate(relay_rows):
        refs = [relay_row["evidence_ref"]]
        if index == selected:
            refs.append(terminal["evidence_ref"])
        completed = (_time_for_step(step, time_by_step) if index == selected
                     else relay_row["publication_stamp"]["time_s"])
        _bind_task(archive, relay_task, refs, 40 + index, completed)
    runtime.advance(_time_for_step(step, time_by_step))
    archive.update(
        readiness_service=snapshot,
        robot_bindings={
            key: {**deepcopy(value),
                  "robot_prim_path": f"/World/{value['native_agent_id']}"}
            for key, value in ROBOT_BINDINGS.items()},
        region_bindings={
            key: {**deepcopy(value), "tolerance_m": REGION_OBSERVATION_TOLERANCE_M}
            for key, value in REGION_BINDINGS.items()},
        transport_report=runtime.report(),
    )
    return archive


def _verify(case):
    return verify_restaurant_business_v2(
        episode_id=EPISODE, service_recipient=SERVICE,
        readiness_service=case["readiness_service"],
        session_tasks=case["session_tasks"],
        skill_receipts=case["skill_receipts"],
        native_evidence=case["native_evidence"],
        robot_bindings=case["robot_bindings"],
        region_bindings=case["region_bindings"],
        trusted_task_regions=case["trusted_task_regions"],
        transport_report=case["transport_report"],
    )


def _rehash_native(case, row):
    old = row["evidence_ref"]
    body = {key: value for key, value in row.items() if key != "evidence_ref"}
    row["evidence_ref"] = "sha256:" + canonical_sha256(body)
    task_id = row["task_id"]
    for receipt in (next(item for item in case["skill_receipts"]
                         if item["task_id"] == task_id),
                    case["session_tasks"][task_id]["receipt"]):
        receipt["evidence_refs"] = [row["evidence_ref"] if ref == old else ref
                                    for ref in receipt["evidence_refs"]]


def _audit_action(session, action_id, action_type, payload):
    return {
        "schema": "eai.benchmark.goal_action",
        "version": 1,
        "action_id": action_id,
        "submitted_at_s": session.report()["current_time_s"],
        "type": action_type,
        "payload": deepcopy(payload),
    }


def _audit_inputs(case):
    bundle = build_goal_task_package("restaurant")
    public, api = bundle["public_task"], bundle["participant_api"]
    tasks = [deepcopy(row["task"]) for row in case["session_tasks"].values()]
    team = sorted({agent for task in tasks for agent in task["assigned_agents"]})
    objects = {
        row["id"]: {
            "region_id": row["physical_properties"]["initial_region_id"]}
        for row in public["public_world"]["objects"]
    }
    return public, api, tasks, team, objects


def _joint_audit(case):
    public, api, tasks, team, objects = _audit_inputs(case)
    windows = {
        "arrive-1": (0.0, 8.0),
        "observe-1": (8.0, 4.0),
        "arrive-2": (8.0, 8.0),
        "observe-2": (16.0, 4.0),
        "observe-repeat": (20.0, 4.0),
        "relay-0": (24.0, 3.0),
        "relay-1": (25.0, 1.0),
    }
    prerequisites = {
        "arrive-1": [],
        "observe-1": ["arrive-1"],
        "arrive-2": [],
        "observe-2": ["arrive-2"],
        "observe-repeat": ["observe-2"],
        "relay-0": ["observe-repeat"],
        "relay-1": ["observe-1"],
    }
    for task in tasks:
        start, duration = windows[task["task_id"]]
        task["execution"] = {"start_s": start, "duration_s": duration}
        task["prerequisites"] = prerequisites[task["task_id"]]
        if task["skill"] == "relay":
            task["arguments"]["message_kind"] = "participant_service_summary"
    session = AuditedGoalSession(public, api, episode_id=EPISODE)
    robot_regions = {agent: "restaurant_staging" for agent in team}
    session.update_runtime_state(0.0, robot_regions, objects)
    session.submit_action(_audit_action(
        session, "select-joint-team", "select_team", {"agent_ids": team}))
    session.submit_action(_audit_action(session, "submit-joint-plan", "submit_plan", {
        "plan": {
            "schema": "eai.benchmark.goal_plan",
            "version": GOAL_PLAN_VERSION,
            "plan_id": "joint-business-plan",
            "assigned_team": team,
            "tasks": tasks,
        },
    }))

    def dispatch(task_id):
        action_id = case["session_tasks"][task_id]["dispatch_action_id"]
        session.submit_action(_audit_action(
            session, action_id, "dispatch_skill",
            {"task_id": task_id, "evidence_refs": []}))

    def complete(task_id):
        receipt = next(
            row for row in case["skill_receipts"] if row["task_id"] == task_id)
        session.complete_skill(
            task_id, time_s=receipt["completed_at_s"], status="succeeded",
            receipt=receipt)
        task = case["session_tasks"][task_id]["task"]
        region = task["arguments"].get("region_id")
        if region is not None:
            robot_regions[task["assigned_agents"][0]] = region
        session.update_runtime_state(
            receipt["completed_at_s"], robot_regions, objects)

    dispatch("arrive-1")
    complete("arrive-1")
    dispatch("observe-1")
    dispatch("arrive-2")
    complete("observe-1")
    complete("arrive-2")
    dispatch("observe-2")
    complete("observe-2")
    dispatch("observe-repeat")
    complete("observe-repeat")
    dispatch("relay-0")
    session.update_runtime_state(25.0, robot_regions, objects)
    dispatch("relay-1")
    complete("relay-1")
    complete("relay-0")
    journal = session.audit_journal()
    replay = replay_goal_session_audit(journal, public, api)
    return journal, replay


def _single_completion_audit(case, *, update_offset):
    public, api, tasks, team, objects = _audit_inputs(case)
    task = next(row for row in tasks if row["task_id"] == "arrive-1")
    producer = task["assigned_agents"][0]
    session = AuditedGoalSession(public, api, episode_id=EPISODE)
    robot_regions = {producer: "restaurant_staging"}
    session.update_runtime_state(0.0, robot_regions, objects)
    session.submit_action(_audit_action(
        session, "select-single-team", "select_team",
        {"agent_ids": [producer]}))
    session.submit_action(_audit_action(session, "submit-single-plan", "submit_plan", {
        "plan": {
            "schema": "eai.benchmark.goal_plan",
            "version": GOAL_PLAN_VERSION,
            "plan_id": "single-completion-plan",
            "assigned_team": [producer],
            "tasks": [task],
        },
    }))
    action_id = case["session_tasks"]["arrive-1"]["dispatch_action_id"]
    session.submit_action(_audit_action(
        session, action_id, "dispatch_skill",
        {"task_id": "arrive-1", "evidence_refs": []}))
    receipt = next(row for row in case["skill_receipts"]
                   if row["task_id"] == "arrive-1")
    session.complete_skill(
        "arrive-1", time_s=receipt["completed_at_s"], status="succeeded",
        receipt=receipt)
    if update_offset is not None:
        robot_regions[producer] = task["arguments"]["region_id"]
        session.update_runtime_state(
            receipt["completed_at_s"] + update_offset, robot_regions, objects)
    journal = session.audit_journal()
    replay = replay_goal_session_audit(journal, public, api)
    return journal, replay


@pytest.mark.parametrize("shared_observer,reverse,selected_retry", [
    (True, False, False),
    (False, True, True),
])
def test_alternate_participant_structures_bind_without_fixed_mission_expansion(
        shared_observer, reverse, selected_retry):
    case = _build_case(
        shared_observer=shared_observer, reverse=reverse,
        selected_retry=selected_retry)
    result = _verify(case)
    assert result["validated"] is True
    assert len(result["observation_native_bindings"]) == 3
    assert len(result["relay_native_bindings"]) == 2
    assert result["selected_delivery"]["message_id"] == (
        "relay-retry" if selected_retry else "relay-old")
    assert result["native_worker_wiring_verified"] is False
    assert result["physical_authentication_verified"] is False
    assert result["execution_validated"] is False
    assert result["business_outcomes_validated"] is False
    assert result["scoreable"] is False
    selected = result["selected_delivery"]
    relay_task = next(
        row["task"] for row in case["session_tasks"].values()
        if row["task"]["skill"] == "relay"
        and row["task"]["assigned_agents"] == [selected["producer_id"]]
        and row["task"]["task_id"]
        == ("relay-1" if selected_retry else "relay-0"))
    assert relay_task["arguments"]["region_id"] != ROBOT_BINDINGS[
        selected["producer_id"]]["spawn_region_id"]


def test_cross_episode_native_evidence_is_rejected():
    case = _build_case(shared_observer=True, reverse=False)
    case["native_evidence"][0]["episode_id"] = "foreign"
    with pytest.raises(ValueError, match="foreign episode"):
        _verify(case)


def test_native_observation_cannot_change_real_producer_identity():
    case = _build_case(shared_observer=False, reverse=True)
    observation = next(row for row in case["native_evidence"]
                       if row["kind"] == "observation")
    observation["producer_id"] = "restaurant_carter_1"
    body = {key: value for key, value in observation.items() if key != "evidence_ref"}
    observation["evidence_ref"] = "sha256:" + canonical_sha256(body)
    with pytest.raises(ValueError, match="binding|dispatch|identity"):
        _verify(case)


def test_observation_without_prior_physical_arrival_is_rejected():
    case = _build_case(shared_observer=True, reverse=False)
    first_observation = next(row for row in case["native_evidence"]
                             if row["kind"] == "observation")
    producer, region = first_observation["producer_id"], first_observation["region_id"]
    case["native_evidence"] = [
        row for row in case["native_evidence"]
        if not (row["kind"] == "arrival" and row["producer_id"] == producer
                and row["region_id"] == region)
    ]
    with pytest.raises(ValueError, match="prior physical arrival"):
        _verify(case)


def test_arrival_binds_final_segment_of_a_replayed_multi_segment_route():
    case = _build_case(shared_observer=False, reverse=True)
    arrival = next(row for row in case["native_evidence"]
                   if row["kind"] == "arrival")
    task = case["session_tasks"][arrival["task_id"]]["task"]
    task["arguments"]["route_ids"] = [
        "restaurant_staging_to_kitchen",
        "restaurant_kitchen_to_dining",
    ]
    arrival["public_route_id"] = "restaurant_kitchen_to_dining"
    _rehash_native(case, arrival)
    assert _verify(case)["validated"] is True


def test_publication_without_delivery_to_trusted_service_is_rejected():
    case = _build_case(shared_observer=True, reverse=False, drop=True)
    with pytest.raises(ValueError, match="not delivered"):
        _verify(case)


def test_failed_skill_receipt_cannot_claim_native_observation():
    case = _build_case(shared_observer=True, reverse=False)
    receipt = next(row for row in case["skill_receipts"]
                   if row["task_id"].startswith("observe-"))
    receipt["status"] = "failed"
    case["session_tasks"][receipt["task_id"]]["receipt"]["status"] = "failed"
    with pytest.raises(ValueError, match="succeeded dispatch receipt"):
        _verify(case)


def test_duplicate_native_evidence_ref_is_rejected():
    case = _build_case(shared_observer=True, reverse=False)
    case["native_evidence"].append(deepcopy(case["native_evidence"][0]))
    with pytest.raises(ValueError, match="duplicate native evidence_ref"):
        _verify(case)


def test_failed_early_arrival_does_not_poison_a_later_valid_business_chain():
    case = _build_case(shared_observer=True, reverse=False)
    successful = next(row for row in case["native_evidence"]
                      if row["kind"] == "arrival")
    failed = deepcopy(successful)
    failed["task_id"] = "failed-arrival-attempt"
    failed["evidence_ref"] = "sha256:" + canonical_sha256({
        key: value for key, value in failed.items() if key != "evidence_ref"})
    case["native_evidence"].insert(0, failed)
    task = deepcopy(case["session_tasks"][successful["task_id"]]["task"])
    task["task_id"] = failed["task_id"]
    action = "dispatch-failed-arrival"
    receipt = {
        "backend_id": "native-test-v2", "receipt_ref": f"{EPISODE}/failed-arrival",
        "task_id": failed["task_id"], "action_id": action, "status": "failed",
        "completed_at_s": failed["arrival"]["time_s"], "episode_id": EPISODE,
        "evidence_refs": [failed["evidence_ref"]],
    }
    case["session_tasks"][failed["task_id"]] = {
        "status": "failed", "task": task, "dispatch_action_id": action,
        "dispatched_at_s": 0.0, "receipt": deepcopy(receipt),
    }
    case["skill_receipts"].append(receipt)
    assert _verify(case)["validated"] is True


@pytest.mark.parametrize("boundary", ["arrival", "started_before_dispatch",
                                      "started_before_task_window"])
def test_native_evidence_outside_dispatch_window_is_rejected(boundary):
    case = _build_case(shared_observer=True, reverse=False)
    if boundary == "arrival":
        evidence = next(row for row in case["native_evidence"]
                        if row["kind"] == "observation")
        case["session_tasks"][evidence["task_id"]]["task"]["execution"] = {
            "start_s": evidence["stamp"]["time_s"] + 1.0,
            "duration_s": 1.0,
        }
    else:
        evidence = next(row for row in case["native_evidence"]
                        if row["kind"] == "arrival")
        session_row = case["session_tasks"][evidence["task_id"]]
        if boundary == "started_before_dispatch":
            session_row["dispatched_at_s"] = (
                evidence["started"]["time_s"] + 0.05)
        else:
            session_row["task"]["execution"] = {
                "start_s": evidence["started"]["time_s"] + 0.05,
                "duration_s": 20.0,
            }
    with pytest.raises(ValueError, match="succeeded dispatch receipt"):
        _verify(case)


def test_arrival_position_and_contact_prim_are_bound_to_real_producer():
    position = _build_case(shared_observer=True, reverse=False)
    arrival = next(row for row in position["native_evidence"]
                   if row["kind"] == "arrival")
    arrival["arrival"]["root_state_w"][0] += 5.0
    _rehash_native(position, arrival)
    with pytest.raises(ValueError, match="trusted region binding"):
        _verify(position)

    contact = _build_case(shared_observer=True, reverse=False)
    arrival = next(row for row in contact["native_evidence"]
                   if row["kind"] == "arrival")
    wheel = next(iter(arrival["contact_view_identities"]))
    arrival["contact_view_identities"][wheel]["sensor_paths"] = [
        f"/World/other_robot/{wheel}"]
    _rehash_native(contact, arrival)
    with pytest.raises(ValueError, match="trusted robot prim"):
        _verify(contact)


def test_relay_region_must_come_from_replayed_task_time_state():
    case = _build_case(shared_observer=True, reverse=False)
    selected_task = "relay-0"
    producer = case["session_tasks"][selected_task]["task"]["assigned_agents"][0]
    case["trusted_task_regions"][selected_task][producer] = "restaurant_staging"
    with pytest.raises(ValueError, match="replayed current robot region"):
        _verify(case)


def test_relay_uses_replayed_region_when_optional_region_is_omitted():
    case = _build_case(shared_observer=False, reverse=True)
    for row in case["session_tasks"].values():
        if row["task"]["skill"] == "relay":
            row["task"]["arguments"].pop("region_id")
            row["task"]["arguments"]["message_kind"] = (
                "participant_service_summary")
    assert _verify(case)["validated"] is True


def test_business_verifier_consumes_replayed_session_and_completion_time_state():
    case = _build_case(
        shared_observer=False, reverse=True, selected_retry=False,
        time_by_step=JOINT_TIME_BY_STEP)
    journal, replay = _joint_audit(case)
    case["session_tasks"] = journal["final_report"]["tasks"]
    case["trusted_task_regions"] = trusted_task_regions_from_replayed_audit(
        journal, replay)
    result = _verify(case)
    assert result["validated"] is True
    assert case["trusted_task_regions"]["relay-0"] == {
        "restaurant_carter_1": "kitchen_service"}
    assert case["trusted_task_regions"]["relay-1"] == {
        "restaurant_carter_2": "dining_service"}


@pytest.mark.parametrize("update_offset", [None, 0.01])
def test_trusted_task_regions_require_matching_completion_time_commit(
        update_offset):
    case = _build_case(
        shared_observer=False, reverse=True, selected_retry=False)
    journal, replay = _single_completion_audit(
        case, update_offset=update_offset)
    expected = ("lacks committed runtime state" if update_offset is None
                else "matching committed state")
    with pytest.raises(ValueError, match=expected):
        trusted_task_regions_from_replayed_audit(journal, replay)
