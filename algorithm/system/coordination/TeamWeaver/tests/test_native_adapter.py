"""Real upstream solver tests: no mock imports or replacement allocators."""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from algorithm.common_coordination.contract import TaskGraph
from algorithm.system.coordination.native_runtime import verify_source
from algorithm.system.coordination.TeamWeaver.native_adapter import (
    EXPECTED_COMMIT, NativeScopeError, NativeSolverError, NativeTeamWeaverAdapter,
)
from records.algorithm_adapters.teamweaver.run_native import make_package
from task.contracts import TaskRunRequest
from task.runner import TaskRunner


@pytest.fixture
def native():
    adapter = NativeTeamWeaverAdapter()
    if not (adapter.source_root / ".git").exists():
        pytest.skip("official TeamWeaver source is not installed")
    if not Path(adapter.python_executable).exists():
        pytest.skip("isolated native interpreter is not installed")
    return adapter


def test_real_native_decision_and_source_integrity(native):
    task = make_package()
    before = verify_source(native.source_root, EXPECTED_COMMIT)
    decision = native(task, task.robots)
    assert {(a.task_id, a.robot_id) for a in decision.assignments} == {
        ("move_mobile", "mobile"), ("move_sensor", "sensor")}
    assert decision.unassigned_tasks == ()
    assert decision.condition.execution_scope == "supplied_decomposition"
    result = decision.provenance["native_result"]
    assert result["status"] == "Optimal"
    assert result["alpha"] == [[1, 0], [0, 1]]
    assert result["native_robot_bounds"] == [[0, 2], [0, 2]]  # Original defaults.
    assert result["native_error_count"] == 0
    assert result["fallback_used"] is False
    assert Path(result["solver_file"]).resolve() == native.source_root / "TeamWeaver/planner/HRCS/class_def/RTA_task.py"
    assert result["entrypoint"].endswith("RTA.solve_miqp_phase_aware")
    assert verify_source(native.source_root, EXPECTED_COMMIT) == before
    json.dumps(decision.provenance, allow_nan=False)


def test_taskrunner_roundtrip_retains_unverified_physical_evidence(native):
    result = TaskRunner().run(TaskRunRequest(make_package()), native)
    assert len(result.decision.assignments) == 2
    assert len(result.feedback) == 2
    assert all(f.outcome == "not_executed" for f in result.feedback)
    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"
    assert result.ledger.events[0]["payload"]["provenance"]["solver_invoked"] is True


def test_native_coalition_is_preserved_and_never_silently_collapsed(native):
    task = make_package()
    task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "supplied"))
    with pytest.raises(NativeScopeError, match="native coalition") as caught:
        native(task, task.robots)
    raw = caught.value.native_result
    assert raw["status"] == "Optimal"
    assert raw["alpha"] == [[1], [1]]
    assert raw["robot_ids"] == ["mobile", "sensor"]
    assert raw["task_ids"] == ["move_mobile"]
    assert native.last_native_result == raw


def test_native_infeasibility_is_not_replaced_by_hungarian(native):
    task = make_package()
    task = replace(task, robots=task.robots[:1])
    with pytest.raises(NativeSolverError) as caught:
        native(task, task.robots)
    assert caught.value.native_result["status"] != "Optimal"
    assert caught.value.native_result["fallback_used"] is False
    assert not any(any(row) for row in caught.value.native_result["alpha"])


def test_supplied_dependency_frontier_and_completion_are_explicit(native):
    task = make_package()
    first = task.graph.tasks[0]
    second = replace(first, task_id="move_after", prerequisites=(first.task_id,))
    task = replace(task, graph=TaskGraph((first, second), "supplied DAG"), robots=task.robots[:1])
    initial = native(task, task.robots)
    assert [a.task_id for a in initial.assignments] == [first.task_id]
    assert initial.unassigned_tasks == (second.task_id,)
    task = replace(task, metadata={"completed_task_ids": [first.task_id]})
    after = native(task, task.robots)
    assert [a.task_id for a in after.assignments] == [second.task_id]
    assert after.provenance["completed_task_ids"] == [first.task_id]
    assert after.provenance["prompts_invoked"] is False
    task = replace(task, metadata={"completed_task_ids": [first.task_id, second.task_id]})
    complete = native(task, task.robots)
    assert not complete.assignments and not complete.unassigned_tasks
    assert complete.provenance["solver_invoked"] is False


def test_runtime_positions_and_heading_override_package_state(native):
    task = make_package()
    runtime = (replace(task.robots[0], position=(-2., 0., 17.), metadata={"yaw_rad": 0.25}), task.robots[1])
    decision = native(task, runtime)
    assert decision.provenance["native_input"]["x"][0] == [-2, 0, 0.25]
    assert task.robots[0].position == (-1., 0., 0.)


@pytest.mark.parametrize("primitive,state", [
    ("Navigate", {}),
    ("Pick", {"is_holding": False, "holding_robot_id": None}),
    ("Place", {"is_holding": True, "holding_robot_id": "mobile"}),
    ("Open", {"furniture_open_state": False}),
    ("Close", {"furniture_open_state": True}),
])
def test_mapped_primitives_execute_original_utilities(native, primitive, state):
    task = make_package()
    required = frozenset({"navigation"} if primitive == "Navigate" else {"navigation", "manipulation"})
    first = replace(task.graph.tasks[0], description=f"Perform native {primitive}",
                    required_capabilities=required,
                    metadata={"native_task_type": primitive, "target_xy": [0, 0]})
    robot = replace(task.robots[0], capability=replace(task.robots[0].capability, capabilities=required))
    task = replace(task, graph=TaskGraph((first,), "supplied primitive"), robots=(robot,),
                   metadata={"teamweaver_global_vars": state})
    decision = native(task, task.robots)
    assert len(decision.assignments) == 1
    assert decision.provenance["native_result"]["status"] == "Optimal"
    assert decision.provenance["native_result"]["native_error_count"] == 0
    assert decision.provenance["native_input"]["tasks"][0]["task_type"] == primitive
    if primitive == "Place":
        assert decision.provenance["native_input"]["global_vars"]["holding_robot_id"] == 0


def test_source_root_and_interpreter_environment(monkeypatch, native):
    monkeypatch.setenv("HETEROCOORD_TEAMWEAVER_SOURCE_ROOT", str(native.source_root))
    monkeypatch.setenv("HETEROCOORD_TEAMWEAVER_PYTHON", native.python_executable)
    configured = NativeTeamWeaverAdapter()
    assert configured.source_root == native.source_root
    assert configured.python_executable == native.python_executable


@pytest.mark.parametrize("change,match", [
    ("target_conflict", "shares p_goal"),
    ("missing_heading", "yaw_rad"),
    ("unknown_primitive", "native_task_type"),
    ("nonfinite", "finite"),
    ("empty_capabilities", "nonempty"),
    ("missing_pick_state", "explicit is_holding"),
    ("bad_history", "unknown tasks"),
    ("wrong_roster", "roster mismatch"),
])
def test_unsupported_inputs_fail_without_a_solver_substitute(native, change, match):
    task = make_package()
    if change == "target_conflict":
        second = replace(task.graph.tasks[1], metadata={"native_task_type": "Navigate", "target_xy": [2, 3]})
        task = replace(task, graph=TaskGraph((task.graph.tasks[0], second), "supplied"))
    elif change == "missing_heading":
        task = replace(task, robots=(replace(task.robots[0], metadata={}), task.robots[1]))
    elif change in {"unknown_primitive", "missing_pick_state", "nonfinite"}:
        metadata = {"native_task_type": "Pick" if change == "missing_pick_state" else "Navigate",
                    "target_xy": [float("nan"), 0] if change == "nonfinite" else [0, 0]}
        if change == "unknown_primitive":
            metadata["native_task_type"] = "Rescue"
        first = replace(task.graph.tasks[0], metadata=metadata)
        task = replace(task, graph=TaskGraph((first, task.graph.tasks[1]), "supplied"))
    elif change == "empty_capabilities":
        first = replace(task.graph.tasks[0], required_capabilities=frozenset())
        task = replace(task, graph=TaskGraph((first, task.graph.tasks[1]), "supplied"))
    elif change == "bad_history":
        task = replace(task, metadata={"completed_task_ids": ["unknown"]})
    runtime = task.robots[:1] if change == "wrong_roster" else task.robots
    with pytest.raises(NativeScopeError, match=match):
        native(task, runtime)
    assert native.last_native_result is None


def test_full_semantic_is_never_labelled_as_allocation_success(native):
    task = make_package()
    full = NativeTeamWeaverAdapter(native.source_root, scope="full_semantic", source_compatibility=False)
    with pytest.raises(NativeScopeError, match="full_semantic is blocked") as caught:
        full(task, task.robots)
    assert full.last_native_result == caught.value.native_result
    assert caught.value.native_result["planner_import"]["error_type"] == "SyntaxError"
    assert caught.value.native_result["planner_import"]["line"] == 73
    assert caught.value.native_result["missing_runtime_packages"] == []
    mapping = caught.value.native_result["semantic_mapping"]
    assert "inspection_area: counter in kitchen" in mapping["world_graph"]["world_description"]
    assert mapping["environment_interface"]["imported"] is True
    assert mapping["environment_interface"]["instantiated"] is False
    assert mapping["motor_skills_bound"] is False
    assert mapping["llm_calls_made"] is False
    assert [a["robot_id"] for a in mapping["agents"]] == ["mobile", "sensor"]
    assert all(a["dry_run"] is False for a in mapping["agents"])
    assert all("Navigate" in a["tool_names"] for a in mapping["agents"])
    assert task.metadata["instruction"] in mapping["prompt"]["rendered"]
    assert caught.value.native_result["source_unchanged"] is True
    probe = native.probe_full_semantic()
    assert probe["full_semantic_status"] in {
        "blocked", "importable"}
    if probe["full_semantic_status"] == "blocked":
        assert probe["import_error"]
    assert not hasattr(native, "initialize")
    assert not hasattr(native, "update_observation")
    assert callable(native.replan)


@pytest.mark.parametrize("change,match", [
    ("coordinate_frame", "coordinate_frame"), ("runtime_pose", "teamweaver_position"),
    ("duplicate_node", "unique"), ("instruction", "instruction"),
    ("roster", "roster mismatch"),
])
def test_semantic_input_rejects_implicit_or_ambiguous_mapping(native, change, match):
    task = make_package()
    metadata = json.loads(json.dumps(task.metadata))
    robots = task.robots
    if change == "coordinate_frame":
        del metadata["teamweaver_world_graph"]["coordinate_frame"]
    elif change == "runtime_pose":
        robots = (replace(robots[0], metadata={}), robots[1])
    elif change == "duplicate_node":
        metadata["teamweaver_world_graph"]["nodes"].append(
            metadata["teamweaver_world_graph"]["nodes"][0])
    elif change == "instruction":
        del metadata["instruction"]
    elif change == "roster":
        robots = robots[:1]
    task = replace(task, metadata=metadata)
    with pytest.raises(NativeScopeError, match=match):
        native.prepare_full_semantic(task, robots)


def test_semantic_runtime_pose_mapping_preserves_input(native):
    from algorithm.system.coordination.TeamWeaver.native_adapter import _map_semantic_input
    task = make_package()
    robots = (replace(task.robots[0], metadata={"teamweaver_position": [7, 2, 3]}), task.robots[1])
    mapped = _map_semantic_input(task, robots)
    node = next(n for n in mapped["world_graph"]["nodes"] if n["name"] == "agent_0")
    assert node["properties"]["translation"] == [7, 2, 3]
    original = next(n for n in task.metadata["teamweaver_world_graph"]["nodes"] if n["name"] == "agent_0")
    assert original["properties"]["translation"] == [-1, 0, 0]


def test_original_parser_preserves_supplied_coalition_as_boundary_data(native):
    task = make_package()
    result = native.prepare_full_semantic(task, task.robots, parser_probe_response=(
        "Agent_0_Action: Navigate[counter]\nAgent_1_Action: Navigate[counter]\nAssigned!"))
    probe = result["semantic_mapping"]["parser_probe"]
    assert probe["input_source"] == "supplied_boundary_test_not_llm_generation"
    assert probe["robot_actions"] == {
        "mobile": ["Navigate", "counter", None], "sensor": ["Navigate", "counter", None]}
    assert probe["assignments_created"] is False
    assert result["llm_calls_made"] is False


def test_output_seam_is_reject_only_and_does_not_apply_source_repair(native):
    task = make_package()
    task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "single task coalition seam"))
    seam = NativeTeamWeaverAdapter(native.source_root, output_seam=True, source_compatibility=False)
    result = seam.prepare_full_semantic(task, task.robots, parser_probe_response=(
        "Agent_0_Action: Navigate[inspection_area]\n"
        "Agent_1_Action: Navigate[inspection_area]\nAssigned!"))
    output = result["semantic_mapping"]["parser_probe"]["output_seam"]
    assert output["status"] == "rejected"
    assert output["assignments_created"] is False
    assert output["repairs_applied"] is False
    assert output["dispatch_candidates"] == []  # same task target creates a coalition
    assert any(item["code"] == "coalition_not_dispatchable" for item in output["errors"])
    advisory = result["syntax_repair_advisory"]
    assert advisory["applied"] is False
    assert advisory["source_lock_unchanged"] is True
    assert all(item["applied"] is False for item in advisory["files"])
    assert "if len(self.execution_history" in advisory["files"][0]["diff"]


@pytest.mark.parametrize("case,expected", [
    ("unknown_target", "unknown_entity"),
    ("ambiguous_tasks", "ambiguous_task_mapping"),
    ("duplicate_agent", "duplicate_agent_directive"),
    ("unknown_agent", "unknown_agent"),
    ("missing_agent", "missing_agent_directive"),
    ("wait_rewrite", "native_parser_changed_directive"),
    ("unsupported_tool", "unavailable_tool"),
    ("malformed", "malformed_action_directive"),
    ("capability", "capability_mismatch"),
    ("not_ready", "task_not_ready"),
])
def test_output_seam_rejects_without_changing_original_actions(case, expected):
    from copy import deepcopy
    from algorithm.system.coordination.TeamWeaver.native_adapter import (
        _map_semantic_input, _validate_action_output,
    )
    task = make_package()
    task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "boundary fixture"))
    payload = _map_semantic_input(task, task.robots)
    response = "Agent_0_Action: Navigate[inspection_area]\nAgent_1_Action: Wait[]"
    parsed = {0: ("Navigate", "inspection_area", None), 1: ("Wait", None, None)}
    tools = {"0": ["Navigate", "Wait"], "1": ["Navigate", "Wait"]}
    if case == "unknown_target":
        response = response.replace("inspection_area", "invented")
        parsed[0] = ("Navigate", "invented", None)
    elif case == "ambiguous_tasks":
        payload["task_catalog"].append({**payload["task_catalog"][0], "task_id": "duplicate"})
    elif case == "duplicate_agent":
        response += "\nAgent_0_Action: Navigate[inspection_area]"
    elif case == "unknown_agent":
        response += "\nAgent_9_Action: Wait[]"
    elif case == "missing_agent":
        response = response.splitlines()[0]
        del parsed[1]
    elif case == "wait_rewrite":
        response = response.replace("Wait[]", "NotWait[]")
    elif case == "unsupported_tool":
        tools["0"] = ["Wait"]
    elif case == "malformed":
        response = response.replace("Navigate[inspection_area]", "Navigate[inspection_area")
    elif case == "capability":
        payload["capabilities"][0] = []
    elif case == "not_ready":
        payload["task_catalog"][0]["prerequisites"] = ["first"]
    saved = deepcopy(parsed)
    output = _validate_action_output(response, parsed, payload, tools)
    assert expected in {e["code"] for e in output["errors"]}
    assert output["status"] == "rejected"
    assert not output["dispatch_candidates"]
    assert output["raw_response"] == response
    assert parsed == saved
    assert not output["assignments_created"] and not output["repairs_applied"]


def test_output_seam_acceptance_is_only_boundary_evidence(native):
    task = make_package()
    task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "one task with idle agent"))
    seam = NativeTeamWeaverAdapter(native.source_root, output_seam=True)
    result = seam.prepare_full_semantic(task, task.robots, parser_probe_response=(
        "Agent_0_Action: Navigate[inspection_area]\nAgent_1_Action: Wait[]\nAssigned!"))
    mapping = result["semantic_mapping"]
    output = mapping["parser_probe"]["output_seam"]
    assert output["status"] == "boundary_valid_only"
    assert output["dispatch_candidates"] == [{
        "task_id": "move_mobile", "robot_id": "mobile", "action": "Navigate",
        "arguments": ["inspection_area"]}]
    assert not output["full_semantic_success"] and not output["assignments_created"]
    assert result["full_semantic_status"] == "importable"
    assert result["source_compatibility"]["applied"] is True
    assert mapping["prompt"]["output_seam"]["enabled"] is True
    assert mapping["prompt"]["output_seam"]["upstream_template_modified"] is False


def test_explicit_task_annotation_resolves_identity_without_changing_native_action(native):
    task = make_package()
    result = NativeTeamWeaverAdapter(output_seam=True).prepare_full_semantic(task, task.robots,
        parser_probe_response="Agent_0_Task: move_mobile\nAgent_0_Action: Navigate[inspection_area]\n"
                              "Agent_1_Task: move_sensor\nAgent_1_Action: Navigate[inspection_area]\nAssigned!")
    output = result["semantic_mapping"]["parser_probe"]["output_seam"]
    assert output["status"] == "boundary_valid_only"
    assert [(c["task_id"], c["robot_id"]) for c in output["dispatch_candidates"]] == [
        ("move_mobile", "mobile"), ("move_sensor", "sensor")]
    assert not output["repairs_applied"]
    assert result["llm_calls_made"] is False  # parser test, not positive LLM evidence


@pytest.mark.parametrize("annotation,code", [
    ("Agent_0_Task: missing", "task_annotation_mismatch"),
    ("Agent_0_Task: move_mobile\nAgent_0_Task: move_sensor", "duplicate_or_unknown_task_annotation_agent"),
    ("Agent_9_Task: move_mobile", "duplicate_or_unknown_task_annotation_agent"),
    ("Agent_0_Task:", "malformed_task_annotation"),
    ("Agent_1_Task: move_mobile", "wait_cannot_claim_task"),
])
def test_invalid_task_annotation_is_never_repaired(annotation, code):
    from algorithm.system.coordination.TeamWeaver.native_adapter import _map_semantic_input, _validate_action_output
    task = make_package()
    task = replace(task, graph=TaskGraph(task.graph.tasks[:1], "one task"))
    payload = _map_semantic_input(task, task.robots)
    output = _validate_action_output(annotation + "\nAgent_0_Action: Navigate[inspection_area]\nAgent_1_Action: Wait[]",
        {0: ("Navigate", "inspection_area", None), 1: ("Wait", None, None)}, payload,
        {"0": ["Navigate", "Wait"], "1": ["Navigate", "Wait"]})
    assert code in {e["code"] for e in output["errors"]}
    assert not output["dispatch_candidates"]


@pytest.mark.parametrize("bad", ["missing_feedback", "failed_feedback", "wrong_owner", "duplicate", "missing_task", "unknown_task"])
def test_replan_rejects_unverified_completion(native, bad):
    from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
    from task.contracts import OrchestrationState
    task = make_package()
    decision = native(task, task.robots)
    feedback = (ExecutionFeedback("move_mobile", "mobile", "succeeded"),)
    state = OrchestrationState(1, ("move_mobile",), (), (), ("move_sensor",), feedback, decision)
    if bad == "missing_feedback": state = replace(state, feedback=())
    elif bad == "failed_feedback": state = replace(state, feedback=(replace(feedback[0], failure_code=FailureCode.MILESTONE_UNVERIFIED),))
    elif bad == "wrong_owner": state = replace(state, feedback=(replace(feedback[0], robot_id="sensor"),))
    elif bad == "duplicate": state = replace(state, feedback=feedback * 2)
    elif bad == "missing_task": state = replace(state, pending_task_ids=())
    else: state = replace(state, completed_task_ids=("unknown",))
    with pytest.raises(NativeScopeError):
        native.replan(task, task.robots, state)


@pytest.mark.parametrize("bad", ["frame", "target", "pose"])
def test_semantic_logical_host_rejects_inconsistent_coordinates_before_llm(bad):
    from tools.algorithm_adapters.teamweaver_semantic import LogicalNavigationExecutor
    task = make_package()
    data = json.loads(json.dumps(task.metadata))
    if bad == "frame": data["teamweaver_world_graph"]["coordinate_frame"] = "unknown"
    elif bad == "target": data["teamweaver_world_graph"]["nodes"][2]["properties"]["translation"] = [8, 0, 0]
    else: task = replace(task, robots=(replace(task.robots[0], position=(8, 0, 0)), task.robots[1]))
    with pytest.raises(ValueError):
        LogicalNavigationExecutor(replace(task, metadata=data))


def test_semantic_snapshot_cannot_silently_resume_completed_tasks():
    task = make_package()
    task = replace(task, metadata={**task.metadata, "completed_task_ids": ["move_mobile"]})
    with pytest.raises(NativeScopeError, match="start fresh"):
        NativeTeamWeaverAdapter(scope="full_semantic")(task, task.robots)


def test_semantic_replan_cannot_silently_reset_native_phase_state():
    task = make_package()
    with pytest.raises(NativeScopeError, match="persistent semantic replanning"):
        NativeTeamWeaverAdapter(scope="full_semantic").replan(task, task.robots, None)
