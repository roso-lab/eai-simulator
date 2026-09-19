from pathlib import Path

import pytest

from demo.benchmark.challenge_contract import validate_hidden_evaluator, validate_participant_api, validate_public_task_spec
from demo.benchmark.scene_challenge import compile_scene_challenge
from demo.modern_house_benchmark.challenge import compile_challenge as compile_house
from demo.warehouse_benchmark.challenge import compile_challenge as compile_warehouse


def _compile_navigation_smoke_for_contract_tests(path: Path):
    """Keep generic contract tests from routing smoke input through a release wrapper."""
    from EAI.scenario_spec import load_scenario

    data = load_scenario(path)
    return compile_scene_challenge(
        path,
        scene_asset=f"test/{data['scene_key']}",
        task_contract={"scene_key": data["scene_key"], "scope": "test_navigation_smoke"},
        participant_requirements=[],
        qa_requirements={"scope": "test_navigation_smoke"},
        business_rules={"release_scope": "test_navigation_smoke"},
    )


@pytest.mark.parametrize("builder,scene_key", [(compile_warehouse, "warehouse_nvd"), (compile_house, "modern_house_nvd")])
def test_non_factory_scenes_reuse_four_layer_contract(builder, scene_key):
    bundle = builder()
    public = bundle["public_task"]
    assert public["scene"]["scene_key"] == scene_key
    assert public["participant"]["runtime_status"] == "scene_transport_allocation_v2"
    assert validate_public_task_spec(public)["validated"]
    assert validate_participant_api(bundle["participant_api"], task_spec=public)["validated"]
    assert validate_hidden_evaluator(bundle["hidden_evaluator"], task_spec=public)["validated"]
    assert bundle["hidden_evaluator"]["scenario_snapshot"]["scene_key"] == scene_key
    admission = bundle["hidden_evaluator"]["benchmark_qa_requirements"][
        "generation_admission"]
    assert admission["validated"] is True
    assert admission["executor_plan"]["validated"] is True
    assert "preferred_agent" not in str(public)
    assert "reference_decision" not in str(public)
    assert set(public["participant"]["controlled_objectives"])
    assert bundle["baseline_manifest"]["solvers"] == [{
        "id": "scene_public_greedy_v2",
        "version": 2,
        "parameters": {},
        "behavior": (
            "rank eligible agents from participant-visible state, allocate or "
            "reassign phase-ready objectives, and submit legal navigation goals"
        ),
    }]


def test_scene_challenge_preserves_task_specific_blueprint_without_exposing_answers():
    bundle = compile_warehouse()
    contract = bundle["hidden_evaluator"]["benchmark_qa_requirements"]
    assert contract["task_profile"]["required_skills"] == ["aerial_scan", "joint_transport"]
    assert bundle["public_task"]["scene"]["task_contract_sha256"]
    assert "scenario_snapshot" not in bundle["public_task"]


def test_scene_challenge_outputs_are_write_once(tmp_path: Path):
    from demo.benchmark.scene_challenge import write_scene_challenge
    write_scene_challenge(compile_house(), tmp_path / "bundle")
    with pytest.raises(FileExistsError):
        write_scene_challenge(compile_house(), tmp_path / "bundle")


def test_scene_neutral_hidden_evaluator_scores_scheduler_truth():
    from demo.benchmark.scene_challenge import evaluate_objectives

    scenario = {
        "phase_graph": {"nodes": [{"id": "scan"}, {"id": "deliver"}]},
    }
    report = {
        "scenario": scenario,
        "scheduling": {
            "states": {
                "scan": {"status": "succeeded", "started_at_s": 0.0, "finished_at_s": 2.0},
                "deliver": {"status": "timed_out", "started_at_s": 2.0, "failure_reason": "deadline"},
            },
            "time_s": 10.0,
        },
        "resource_conflicts": [{"resource": "aisle"}],
        "deadlocks": [],
        "replans": 1,
    }
    result = evaluate_objectives(report)
    assert result["task_success"] is False
    assert result["partial_completion"] == 0.5
    assert result["completed_objectives"] == 1
    assert result["resource_conflicts"] == 1
    assert result["replans"] == 1
    assert result["physical_claim"] == "logic_and_scheduling_evaluation_only"


def test_scene_task_review_labels_current_smoke_as_incomplete():
    from demo.benchmark.scene_challenge import review_task_design
    from demo.warehouse_benchmark import scenario as warehouse
    from EAI.scenario_spec import load_scenario

    data = load_scenario("demo/warehouse_benchmark/scenarios/navigation_smoke.json")
    review = review_task_design(data, warehouse.TASK_PROFILE, warehouse.TASK_BLUEPRINT)
    assert review["validated"] is False
    assert review["status"] == "smoke_or_incomplete"
    assert "missing_required_skill:joint_transport" in review["findings"]
    assert "missing_required_skill:aerial_scan" in review["findings"]


@pytest.mark.parametrize("builder", [
    "demo.warehouse_benchmark.challenge:generate_scenario",
    "demo.modern_house_benchmark.challenge:generate_scenario",
])
def test_scene_generators_produce_profile_ready_tasks(builder):
    module_name, function_name = builder.split(":")
    module = __import__(module_name, fromlist=[function_name])
    data = module.generate_scenario(seed=23)
    from EAI.scenario_spec.validation import validate_scenario
    from demo.benchmark.scene_challenge import review_task_design

    validated = validate_scenario(data)
    review = review_task_design(validated, module.scenario.TASK_PROFILE, module.scenario.TASK_BLUEPRINT)
    assert review["validated"] is True
    assert review["node_count"] >= module.scenario.TASK_PROFILE["minimum_nodes"]
    assert all(node["preferred_agent"] for node in validated["phase_graph"]["nodes"])
    robots = {robot["id"]: robot for robot in validated["robots"]}
    assert [item["type"] for item in robots["iris_1"]["attachments"]] == ["camera"]
    assert [item["type"] for item in robots["m20_1"]["attachments"]] == ["z1"]
    expected_timeouts = {
        "aerial_scan": 120.0, "navigate": 180.0, "joint_transport": 300.0,
        "transport_object": 240.0, "wait": 120.0,
    }
    for node in validated["phase_graph"]["nodes"]:
        assert node["timeout_s"] == expected_timeouts[node["skill_key"]]
        if node["skill_key"] == "aerial_scan":
            producer = robots[node["preferred_agent"]]
            assert producer["type"] == "iris"
            assert "camera" in {item["type"] for item in producer["attachments"]}
    assert all("m20" not in node["id"] and "carter" not in node["id"]
               for node in validated["phase_graph"]["nodes"])


@pytest.mark.parametrize("generator", [
    "demo.warehouse_benchmark.challenge:generate_scenario",
    "demo.modern_house_benchmark.challenge:generate_scenario",
])
def test_scene_generators_pass_declared_occupancy_placement(generator):
    module_name, function_name = generator.split(":")
    module = __import__(module_name, fromlist=[function_name])
    from EAI.scenario_spec.geometry import provider_map, validate_geometry
    data = module.generate_scenario(seed=31)
    result = validate_geometry(data, provider_map(scene_key=data["scene_key"]))
    assert result["status"] == "placement_validated"


def test_scene_generators_use_distinct_business_graphs_and_pass_executor_admission():
    from demo.benchmark.scene_challenge import audit_generated_scene_scenario
    from demo.modern_house_benchmark.challenge import generate_scenario as generate_house
    from demo.warehouse_benchmark.challenge import generate_scenario as generate_warehouse

    warehouse = generate_warehouse(seed=41)
    house = generate_house(seed=41)
    warehouse_ids = {node["id"] for node in warehouse["phase_graph"]["nodes"]}
    house_ids = {node["id"] for node in house["phase_graph"]["nodes"]}
    assert warehouse_ids.isdisjoint(house_ids)
    assert {target["id"] for target in warehouse["composition"]["targets"]}.isdisjoint(
        target["id"] for target in house["composition"]["targets"])
    for data in (warehouse, house):
        audit = audit_generated_scene_scenario(data)
        assert audit["validated"] is True
        assert audit["aerial_admission"]["validated"] is True
        assert not audit["aerial_admission"]["execution_validated"]
        planned = audit["executor_plan"]["node_ids"]
        expected = {entry["id"] for entry in data["phase_graph"]["nodes"]}
        assert len(planned) == len(expected)
        assert set(planned) == expected


def test_house_entry_scan_parks_outside_furniture_pickup_corridor():
    from demo.benchmark.runtime import planning_radius
    from demo.modern_house_benchmark.challenge import generate_scenario

    scenario = generate_scenario(seed=41)
    targets = {item["id"]: item for item in scenario["composition"]["targets"]}
    entities = {item["id"]: item for item in scenario["composition"]["entities"]}
    robots = {item["id"]: item for item in scenario["robots"]}
    scan = targets["entry_scan_zone"]["position"]
    chair = entities["armchair_proxy"]
    chair_position = chair["spawn_pose"]["position"]
    required_x_separation = (
        chair["size_m"][0] / 2 + planning_radius(robots["iris_1"]) + 0.1
    )

    assert abs(scan[0] - chair_position[0]) > required_x_separation


@pytest.mark.parametrize("module_name", [
    "demo.warehouse_benchmark.challenge",
    "demo.modern_house_benchmark.challenge",
])
def test_scene_generator_cli_writes_admission_sidecar(module_name, tmp_path: Path):
    import json

    module = __import__(module_name, fromlist=["main"])
    scenario_path = tmp_path / "task.json"
    assert module.main(["--generate-scenario", str(scenario_path)]) == 0
    audit = json.loads((tmp_path / "task.audit.json").read_text(encoding="utf-8"))
    assert audit["admission"]["validated"] is True
    assert audit["admission"]["executor_plan"]["validated"] is True
    assert audit["task_design_review"]["validated"] is True


def test_public_greedy_adapter_uses_only_visible_events():
    from demo.benchmark.participant_runtime import ParticipantSession, PublicTaskGreedyAdapter
    # Keep this adapter unit test on the maintained one-node smoke contract;
    # the challenge compiler's default is the reviewed business task.
    bundle = _compile_navigation_smoke_for_contract_tests(
        Path("demo/warehouse_benchmark/scenarios/navigation_smoke.json"))
    session = ParticipantSession(adapter=PublicTaskGreedyAdapter(bundle["public_task"]),
                                 api=bundle["participant_api"], task_spec=bundle["public_task"])
    session.emit("episode_started", time_s=0.0, payload={"fleet": [], "objectives": bundle["public_task"]["objectives"], "resources": []})
    session.emit("phase_ready", time_s=0.0, payload={"node_id": "approach", "eligible_agent_ids": ["carter_1"]})
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["allocate", "navigate_goal"]
    assert actions[1]["payload"]["target_ref"] == "destination"
    assert "scenario_snapshot" not in session.report()["adapter"]


def test_runtime_participant_initialization_returns_controlled_nodes_and_actions():
    from demo.benchmark.runtime import create_scene_participant_session
    from demo.warehouse_benchmark.challenge import generate_scenario

    session, public, controlled = create_scene_participant_session(
        generate_scenario(), scenario_sha256="0" * 64)
    assert controlled == set(public["participant"]["controlled_objectives"])
    assert "rack_overview_scan" in controlled
    session.emit("phase_ready", time_s=0.0, payload={
        "node_id": "rack_overview_scan", "eligible_agent_ids": ["iris_1"],
    })
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["allocate"]
    assert actions[0]["payload"]["agent_ids"] == ["iris_1"]


def test_public_greedy_consumes_delivered_observation_for_each_visible_consumer():
    from demo.benchmark.runtime import create_scene_participant_session
    from demo.warehouse_benchmark.challenge import generate_scenario

    session, public, controlled = create_scene_participant_session(
        generate_scenario(), scenario_sha256="0" * 64)
    consumers = [
        node_id for node_id in ("aisle_left_access", "aisle_right_access")
        if node_id in controlled
    ]
    assert len(consumers) == 2
    session.emit("observation_message", time_s=2.0, payload={
        "message_id": "rack-scan:frame:1",
        "producer_id": "iris_1",
        "published_at_s": 1.0,
        "valid_until_s": 10.0,
        "estimate": {
            "observation_id": "rack-scan:frame:1",
            "target_ref": "rack_scan_zone",
            "target_estimate": {"kind": "sensor_pose_only"},
            "quality": {"frame_acquired": True},
            "consumer_ids": consumers,
        },
    })
    actions = session.request_actions()
    assert [action["type"] for action in actions] == [
        "consume_message", "consume_message"]
    assert [action["payload"]["consumer_id"] for action in actions] == consumers
    assert all(action["payload"]["message_id"] == "rack-scan:frame:1"
               for action in actions)
    assert public["participant"]["allowed_action_types"].count("consume_message") == 1


def test_public_greedy_uses_public_distance_for_parallel_warehouse_access():
    from demo.benchmark.participant_runtime import ParticipantSession, PublicTaskGreedyAdapter
    from demo.benchmark.runtime import create_scene_participant_session
    from demo.warehouse_benchmark.challenge import generate_scenario

    session, public, _ = create_scene_participant_session(
        generate_scenario(), scenario_sha256="0" * 64)
    session.emit("phase_ready", time_s=1.0, payload={
        "node_id": "aisle_left_access", "eligible_agent_ids": ["m20_1", "carter_1"],
    })
    left = session.request_actions()
    assert left[0]["payload"]["agent_ids"] == ["m20_1"]
    session.emit("phase_ready", time_s=1.0, payload={
        "node_id": "aisle_right_access", "eligible_agent_ids": ["m20_1", "carter_1"],
    })
    right = session.request_actions()
    assert right[0]["payload"]["agent_ids"] == ["carter_1"]
    assert session.report()["adapter"]["id"] == "scene_public_greedy_v2"


def test_public_protocol_episode_replays_generated_scene_without_truth_leak():
    from demo.benchmark.participant_episode import run_public_episode
    from demo.benchmark.participant_runtime import PublicTaskGreedyAdapter
    from demo.warehouse_benchmark.challenge import compile_challenge, generate_scenario
    import json
    from pathlib import Path

    scenario_path = Path("tmp/test_generated_warehouse_protocol.json")
    scenario_path.parent.mkdir(exist_ok=True)
    scenario_path.write_text(json.dumps(generate_scenario()), encoding="utf-8")
    try:
        bundle = compile_challenge(scenario_path)
        report = run_public_episode(bundle, adapter=PublicTaskGreedyAdapter(bundle["public_task"]))
    finally:
        scenario_path.unlink()
    assert report["harness"]["kind"] == "public_protocol_only"
    assert report["harness"]["action_effect"].startswith("validated")
    assert report["actions"]
    assert "scenario_snapshot" not in report["adapter"]


def test_hidden_evaluator_consumes_runtime_report_and_checks_binding():
    from demo.benchmark.scene_challenge import evaluate_challenge_report
    # This test exercises the generic smoke evaluator.  The default challenge
    # package is the full home recovery task.
    bundle = _compile_navigation_smoke_for_contract_tests(
        Path("demo/modern_house_benchmark/scenarios/navigation_smoke.json"))
    report = {
        "task_id": bundle["public_task"]["task_id"],
        "scene_key": "modern_house_nvd",
        "scheduling": {"states": {"approach": {"status": "succeeded"}}, "time_s": 4.0},
    }
    result = evaluate_challenge_report(bundle, report)
    assert result["task_success"] is True
    assert result["scene_key"] == "modern_house_nvd"
    assert result["evaluator_contract_sha256"]
    with pytest.raises(ValueError, match="scene"):
        evaluate_challenge_report(bundle, {**report, "scene_key": "warehouse_nvd"})


@pytest.mark.parametrize("module_name,expected_domain", [
    ("demo.warehouse_benchmark.challenge", "warehouse_logistics"),
    ("demo.modern_house_benchmark.challenge", "modern_house_access_and_object_review"),
])
def test_scene_specific_evaluators_add_domain_predicates(module_name, expected_domain):
    module = __import__(module_name, fromlist=["generate_scenario", "compile_challenge", "evaluate_report"])
    import json
    from pathlib import Path
    path = Path("tmp") / f"{expected_domain}.json"
    path.write_text(json.dumps(module.generate_scenario()), encoding="utf-8")
    try:
        bundle = module.compile_challenge(path)
    finally:
        path.unlink()
    result = module.evaluate_report(bundle, {
        "task_id": bundle["public_task"]["task_id"],
        "scene_key": bundle["public_task"]["scene"]["scene_key"],
        "scheduling": {"states": {item["id"]: {"status": "succeeded"} for item in bundle["public_task"]["objectives"]}, "time_s": 10.0},
    })
    assert result["scene_semantics"]["domain"] == expected_domain
    assert result["scene_semantics"]["design_validated"] is True
    assert result["scene_semantics"]["validated"] is False
    assert result["scheduler_task_success"] is True
    assert result["task_success"] is False
    assert result["domain_evaluation"]["status"] == "missing_or_invalid_evidence"


def test_navigation_action_maps_to_scheduler_entry_without_hidden_truth():
    from demo.benchmark.participant_runtime import apply_scene_navigation_action
    bundle = _compile_navigation_smoke_for_contract_tests(
        Path("demo/modern_house_benchmark/scenarios/navigation_smoke.json"))
    entry = {"node": "approach", "agent": "carter_1", "goal": (0.0, 0.0, 0.0)}
    action = {
        "type": "navigate_goal", "action_id": "a1",
        "payload": {"node_id": "approach", "agent_id": "carter_1", "target_ref": "destination"},
    }
    updated = apply_scene_navigation_action(action, entry=entry, task_spec=bundle["public_task"])
    assert updated["goal"] == (18.6, 1.5, 0.0)
    assert updated["participant_action_id"] == "a1"
    with pytest.raises(ValueError, match="unknown target"):
        apply_scene_navigation_action({**action, "payload": {**action["payload"], "target_ref": "secret"}}, entry=entry, task_spec=bundle["public_task"])


def test_assignment_action_enforces_joint_team_size():
    from demo.benchmark.participant_runtime import apply_scene_assignment_action
    from demo.warehouse_benchmark.challenge import generate_scenario
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    generated = generate_scenario()
    public, _ = build_scene_runtime_contract(generated)
    node_id = "oversized_crate_transfer"
    assert node_id in public["participant"]["controlled_objectives"]
    entry = {"node": node_id, "skill": "joint_transport", "agent": "m20_1", "team": ["m20_1", "carter_1"]}
    action = {"type": "allocate", "action_id": "assign-1", "payload": {"node_id": node_id, "agent_ids": ["m20_1", "carter_1"]}}
    updated = apply_scene_assignment_action(action, entry=entry, task_spec=public)
    assert updated["team"] == ["m20_1", "carter_1"]
    with pytest.raises(ValueError, match="exactly two"):
        apply_scene_assignment_action({**action, "payload": {"node_id": node_id, "agent_ids": ["m20_1"]}}, entry=entry, task_spec=public)
    with pytest.raises(ValueError, match="domain=ground"):
        apply_scene_assignment_action(
            {**action, "payload": {
                "node_id": node_id, "agent_ids": ["iris_1", "carter_1"]}},
            entry=entry, task_spec=public)


def test_public_greedy_adapter_reassigns_after_visible_member_exit():
    from demo.benchmark.participant_runtime import ParticipantSession, PublicTaskGreedyAdapter
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    from demo.warehouse_benchmark.challenge import generate_scenario
    public, api = build_scene_runtime_contract(generate_scenario())
    session = ParticipantSession(adapter=PublicTaskGreedyAdapter(public), api=api, task_spec=public)
    session.emit("member_exit", time_s=4.0, payload={"agent_id": "m20_1", "affected_node_ids": ["oversized_crate_transfer"]})
    actions = session.request_actions()
    assert actions == []


def test_public_fleet_declares_domain_without_inventing_payload_evidence():
    bundle = compile_warehouse()
    robots = {robot["id"]: robot for robot in bundle["public_task"]["initial_fleet"]}
    assert robots["carter_1"]["capability_declaration"]["domain"] == "ground"
    assert robots["carter_1"]["capability_declaration"]["rated_payload_kg"] is None
    assert robots["carter_1"]["capability_declaration"][
        "physical_manipulation_evidence"] == "not_established"


def test_runtime_candidates_use_public_requirements_not_hidden_preference():
    from demo.benchmark.participant_runtime import eligible_public_agents
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    from demo.warehouse_benchmark.challenge import generate_scenario

    public, _ = build_scene_runtime_contract(generate_scenario())
    assert eligible_public_agents(public, node_id="aisle_left_access") == ["m20_1", "carter_1"]
    assert eligible_public_agents(public, node_id="priority_carton_stage") == ["m20_1"]


def test_transport_allocation_is_now_within_participant_scope():
    from demo.benchmark.challenge_contract import validate_participant_action
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    from demo.warehouse_benchmark.challenge import generate_scenario
    from demo.benchmark.participant_runtime import ParticipantSession, PublicTaskGreedyAdapter

    public, api = build_scene_runtime_contract(generate_scenario())
    session = ParticipantSession(adapter=PublicTaskGreedyAdapter(public), api=api, task_spec=public)
    session.emit("episode_started", time_s=0.0, payload={
        "fleet": [], "objectives": public["objectives"], "resources": []})
    # single-agent transport: only the arm-equipped candidate is eligible
    session.emit("phase_ready", time_s=1.0, payload={
        "node_id": "priority_carton_stage", "eligible_agent_ids": ["m20_1"]})
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["allocate"]
    assert actions[0]["payload"]["agent_ids"] == ["m20_1"]
    validate_participant_action(actions[0], api=api, task_spec=public)
    # joint transport: two ground agents, no navigation target chosen by the participant
    session.emit("phase_ready", time_s=2.0, payload={
        "node_id": "oversized_crate_transfer", "eligible_agent_ids": ["m20_1", "carter_1"]})
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["allocate"]
    assert sorted(actions[0]["payload"]["agent_ids"]) == ["carter_1", "m20_1"]


def test_generated_scene_tasks_open_transport_objectives_to_participants(tmp_path):
    import json

    for module_name in ("demo.warehouse_benchmark.challenge", "demo.modern_house_benchmark.challenge"):
        module = __import__(module_name, fromlist=["generate_scenario", "compile_challenge"])
        path = tmp_path / f"{module_name.split('.')[-2]}_generated.json"
        path.write_text(json.dumps(module.generate_scenario(seed=19)), encoding="utf-8")
        public = module.compile_challenge(path)["public_task"]
        controlled = set(public["participant"]["controlled_objectives"])
        transport = {objective["id"] for objective in public["objectives"]
                     if objective["skill"] in {"transport_object", "joint_transport"}}
        assert transport
        assert transport <= controlled
        assert public["participant"]["runtime_status"] == "scene_transport_allocation_v2"
        assert "object_ownership" in public["business_rules"]


def test_navigation_action_is_rejected_for_transport_objectives():
    from demo.benchmark.participant_runtime import apply_scene_navigation_action
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    from demo.warehouse_benchmark.challenge import generate_scenario

    public, _ = build_scene_runtime_contract(generate_scenario())
    for node_id, skill in (("oversized_crate_transfer", "joint_transport"),
                           ("priority_carton_stage", "transport_object")):
        entry = {"node": node_id, "skill": skill, "agent": "m20_1", "goal": (0.0, 0.0, 0.0)}
        objective = next(item for item in public["objectives"] if item["id"] == node_id)
        action = {
            "type": "navigate_goal", "action_id": "nav-1",
            "payload": {"node_id": node_id, "agent_id": "m20_1",
                        "target_ref": objective["target_ref"]},
        }
        with pytest.raises(ValueError, match="navigate_goal is not accepted"):
            apply_scene_navigation_action(action, entry=entry, task_spec=public)


def test_assignment_rejected_while_object_is_carried():
    from demo.benchmark.participant_runtime import apply_scene_assignment_action
    from demo.benchmark.scene_challenge import build_scene_runtime_contract
    from demo.warehouse_benchmark.challenge import generate_scenario

    public, _ = build_scene_runtime_contract(generate_scenario())
    entry = {"node": "priority_carton_stage", "skill": "transport_object",
             "agent": "m20_1", "object": "priority_carton_proxy"}
    action = {"type": "reassign", "action_id": "reassign-1",
              "payload": {"node_id": "priority_carton_stage", "agent_ids": ["m20_1"], "reason": "member_exit"}}
    with pytest.raises(ValueError, match="carried object"):
        apply_scene_assignment_action(
            action, entry=dict(entry), task_spec=public,
            carried_objects={"priority_carton_proxy"})
    # without live ownership the same action applies
    updated = apply_scene_assignment_action(action, entry=dict(entry), task_spec=public)
    assert updated["team"] == ["m20_1"]


def _warehouse_plan(seed=41):
    from demo.benchmark.runtime import build_plan, planning_template
    from demo.warehouse_benchmark.challenge import generate_scenario
    from EAI.scenario_spec.geometry import provider_map
    from EAI.scenario_spec.validation import validate_scenario

    spec = validate_scenario(generate_scenario(seed=seed))
    occupancy = provider_map(scene_key=spec["scene_key"])
    plan = build_plan(spec, occupancy, planning_template(spec["scene_key"], occupancy))
    return spec, occupancy, plan


def test_rebuild_transport_routes_replans_single_agent_legs_from_live_state():
    import math

    from demo.benchmark.runtime import rebuild_transport_routes

    spec, occupancy, plan = _warehouse_plan()
    entry = next(item for item in plan if item["node"] == "priority_carton_dispatch")
    # build_plan planned this relay leg from the carton's staged position; a
    # rebuild after a participant reassignment uses the live agent pose and the
    # object's original spawn, both of which earlier legs proved reachable.
    spawn = next(robot for robot in spec["robots"] if robot["id"] == "m20_1")[
        "spawn_pose"]["position"]
    carton_spawn = next(
        entity for entity in spec["composition"]["entities"]
        if entity["id"] == "priority_carton_proxy")["spawn_pose"]["position"]
    assert tuple(entry["pickup_goal"][:2]) != tuple(carton_spawn[:2])
    summary = rebuild_transport_routes(
        entry, spec=spec, occupancy=occupancy,
        member_positions={"m20_1": spawn[:2]}, object_position=carton_spawn[:2])
    assert tuple(entry["pickup_goal"]) == tuple(carton_spawn[:2])
    assert entry["agent"] == "m20_1"
    assert math.dist(entry["to_object"][0], spawn[:2]) < 2.0
    assert math.dist(entry["to_target"][0], carton_spawn[:2]) < 2.0
    assert summary["skill"] == "transport_object"
    assert summary["team"] == ["m20_1"]
    with pytest.raises(ValueError, match="transport objectives only"):
        rebuild_transport_routes(
            {"node": "dock_lane_check", "skill": "navigate", "agent": "carter_1",
             "goal": (0.0, 0.0, 0.0), "ground_tracking_reserve_m": 0.15},
            spec=spec, occupancy=occupancy, member_positions={"carter_1": (0.0, 0.0)})


def test_rebuild_transport_routes_rekeys_joint_team_legs():
    import math

    from demo.benchmark.runtime import rebuild_transport_routes

    spec, occupancy, plan = _warehouse_plan()
    entry = next(item for item in plan if item["node"] == "oversized_crate_transfer")
    spawns = {robot["id"]: tuple(robot["spawn_pose"]["position"][:2])
              for robot in spec["robots"] if robot["id"] != "iris_1"}
    # a participant reassignment swaps the leader while keeping the two-ground
    # robot team; routes must re-key to the declared team from live poses.
    entry["team"] = ["carter_1", "m20_1"]
    entry["agent"] = "carter_1"
    summary = rebuild_transport_routes(
        entry, spec=spec, occupancy=occupancy, member_positions=spawns)
    assert set(entry["team_to_object"]) == {"carter_1", "m20_1"}
    for member, position in spawns.items():
        assert math.dist(entry["team_to_object"][member][0], position) < 2.0
    assert summary["skill"] == "joint_transport"
    assert summary["planning_radius_m"] > 0.0
    # rebuilding keeps a fresh planning radius and waypoint tolerance pair
    assert entry["route_planning_radius_m"] == entry["agent_radius"]
    assert entry["carry_z"] > 0.0


def test_arrival_predicate_follows_participant_reassignment():
    from demo.benchmark.runtime import rebind_arrival_predicate

    entry = {"node": "aisle_right_access", "skill": "navigate", "agent": "m20_1",
             "arrival_predicate": {"fn": "robot_visited_zone", "args": ["carter_1", "aisle_entry_zone"]}}
    predicate = rebind_arrival_predicate(entry)
    assert predicate["args"] == ["m20_1", "aisle_entry_zone"]
    assert entry["arrival_predicate"] == predicate
    assert rebind_arrival_predicate({"node": "n", "skill": "navigate", "agent": "m20_1"}) is None


def test_member_exit_release_plan_covers_only_owned_objects():
    from demo.benchmark.runtime import plan_member_exit_release

    entries = [
        {"node": "carry_a", "skill": "transport_object", "agent": "m20_1", "object": "box"},
        {"node": "joint_b", "skill": "joint_transport", "agent": "carter_1",
         "team": ["carter_1", "m20_1"], "object": "crate"},
        {"node": "nav_c", "skill": "navigate", "agent": "m20_1"},
    ]
    carrying = {"box": "m20_1", "crate": ["carter_1", "m20_1"]}
    releases = plan_member_exit_release(entries, failed_agent="m20_1", carrying=carrying)
    assert [release["object"] for release in releases] == ["box", "crate"]
    assert releases[0]["owner_before"] == ["m20_1"]
    assert releases[1]["owner_before"] == ["carter_1", "m20_1"]
    assert all(release["owner_after"] is None and release["reason"] == "member_exit"
               for release in releases)
    assert plan_member_exit_release(
        entries, failed_agent="carter_1", carrying={"box": "m20_1"}) == []


def test_execution_contract_allows_ground_only_transport_member_exit():
    from demo.benchmark.runtime import execution_contract

    def ground_spec(**overrides):
        data = {
            "scene_key": "warehouse_nvd",
            "robots": [
                {"id": "m20_1", "type": "m20", "controller": {"cfg": "M20_ROUGH_RSL_CFG"}},
                {"id": "carter_1", "type": "carter", "controller": {"cfg": "CARTER_DIFF_CFG"}},
            ],
            "phase_graph": {"nodes": [
                {"id": "stage", "skill_key": "transport_object", "preferred_agent": "m20_1",
                 "agents": None, "target_ref": "out_zone", "object_ref": "carton",
                 "prerequisites": [], "timeout_s": 60.0, "duration_s": None, "resources": []},
                {"id": "stage_recovery", "skill_key": "transport_object",
                 "preferred_agent": "m20_1", "agents": None, "target_ref": "out_zone",
                 "object_ref": "carton", "recovery_for": "stage",
                 "prerequisites": ["stage"], "timeout_s": 60.0, "duration_s": None,
                 "resources": []},
            ]},
            "fault_events": [{"id": "f1", "kind": "member_exit", "time_s": 5.0, "agent": "m20_1"}],
        }
        data.update(overrides)
        return data

    contract = execution_contract(ground_spec(), transport_mode="kinematic")
    assert contract["transport_mode"] == "kinematic"
    with pytest.raises(ValueError, match="ground-only"):
        aerial = ground_spec()
        aerial["robots"].append({"id": "cf2x_1", "type": "cf2x",
                                 "controller": {"cfg": "QUADCOPTER_GOAL_SKRL_CFG"}})
        execution_contract(aerial, transport_mode="kinematic")
    with pytest.raises(ValueError, match="scene-neutral tasks and F-COV-01"):
        execution_contract(ground_spec(benchmark_task_id="F-INS-01"), transport_mode="kinematic")
