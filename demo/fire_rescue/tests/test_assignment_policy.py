import threading
from types import SimpleNamespace

import pytest

from algorithm.emos.example_policy import assign
from algorithm.emos.policy import assignment_tasks, load_assignment_policy, make_assignment_request
from algorithm.emos.types import compute_subtask_positions
from demo.fire_rescue.scenario import build_factory_emos_scenario


def _manager():
    from algorithm.emos.engine import EMOSDiscussionManager

    return EMOSDiscussionManager(
        SimpleNamespace(), ["m20_1", "m20_2", "carter_1", "scout_1"],
        lambda name: (1.0, 2.0, 0.0), build_factory_emos_scenario(), use_env_resume=False,
    )


def _raise_policy(request):
    raise ValueError("algorithm exploded")


def _mutate_request(request):
    request.scenario.subtasks["red"].name = "corrupted"
    request.subtask_positions["red"] = (99.0, 99.0)
    return {"m20_1": "red"}


def test_targetless_subtask_survives_assignment_tasks_without_invented_xy():
    from algorithm.emos.policy import AssignmentRequest, assignment_tasks
    from algorithm.emos.types import (
        EMOSScenarioConfig,
        PositionRule,
        SubtaskSpec,
        compute_subtask_positions,
        is_targetless_position_rule,
    )

    scenario = EMOSScenarioConfig(
        scenario_id="targetless",
        scene_title="Scene",
        anchor_label="event",
        task_description="relay without travel",
        subtasks={
            "inspect": SubtaskSpec("inspect", "inspect", "go", ["navigation"]),
            "relay": SubtaskSpec("relay", "relay", "publish", ["communication"]),
        },
        position_rules={
            "inspect": PositionRule(kind="fixed", xy=(1.0, 2.0)),
            "relay": PositionRule(kind="none", xy=None),
        },
        robot_profiles={},
        preferred_fallback={},
        yellow_subtask_id="",
        green_subtask_id="",
        yellow_robot_prefix="",
    )
    assert is_targetless_position_rule(scenario.position_rules["relay"])
    positions = compute_subtask_positions(scenario, (0.0, 0.0, 0.0))
    assert positions == {"inspect": (1.0, 2.0)}
    assert "relay" not in positions
    request = AssignmentRequest(
        task_description="task",
        scenario=scenario,
        hazard_id=1,
        hazard_pos=(0.0, 0.0, 0.0),
        robot_positions={"r1": (0.0, 0.0, 0.0), "r2": (1.0, 1.0, 0.0)},
        subtask_positions=positions,
        completed_extinguisher=(),
        completed_rescue=(),
        rescue_constraints=None,
    )
    tasks = assignment_tasks(request, {"r1": "inspect", "r2": "relay"})
    assert tasks["r1"].target_xy == (1.0, 2.0)
    assert tasks["r2"].target_xy is None
    broken = AssignmentRequest(
        task_description="task",
        scenario=scenario,
        hazard_id=1,
        hazard_pos=(0.0, 0.0, 0.0),
        robot_positions={"r1": (0.0, 0.0, 0.0)},
        subtask_positions={},
        completed_extinguisher=(),
        completed_rescue=(),
        rescue_constraints=None,
    )
    with pytest.raises(ValueError, match="missing spatial target"):
        assignment_tasks(broken, {"r1": "inspect"})


def test_example_policy_uses_task_request_and_task_owned_target_positions():
    manager = _manager()
    positions = compute_subtask_positions(manager._scenario, (0.0, 0.0, 0.0))
    request = make_assignment_request(manager, "task", positions)
    tasks = assignment_tasks(request, assign(request))
    assert set(tasks) == set(manager._agents)
    assert tasks["scout_1"].subtask_colour == "green"
    assert {task.subtask_colour for task in tasks.values()} == {"red", "green", "blue", "yellow"}
    for task in tasks.values():
        assert task.target_xy == positions[task.subtask_colour]
    request.scenario.subtasks["red"].name = "changed"
    assert manager._scenario.subtasks["red"].name != "changed"


@pytest.mark.parametrize("result", [
    {}, {"unknown": "red"}, {"m20_1": "unknown"}, {"m20_1": "green"},
    {"carter_1": "yellow"}, {"m20_1": ["red"]},
])
def test_invalid_policy_results_are_rejected_not_reassigned(result):
    manager = _manager()
    positions = compute_subtask_positions(manager._scenario, (0.0, 0.0, 0.0))
    request = make_assignment_request(manager, "task", positions)
    with pytest.raises(ValueError):
        assignment_tasks(request, result)


def test_loader_defaults_to_emos_and_accepts_stateless_algorithm_module():
    assert load_assignment_policy("emos") is None
    assert load_assignment_policy("algorithm.emos.example_policy:assign") is assign
    with pytest.raises(ValueError, match="module:assign"):
        load_assignment_policy("invalid")
    with pytest.raises(ValueError, match="cannot load"):
        load_assignment_policy("algorithm.emos.example_policy:missing")


def test_programmatic_policy_registration_rejects_hidden_closure_state():
    state = {"calls": 0}

    def stateful(request):
        state["calls"] += 1
        return {"m20_1": "red"}

    with pytest.raises(ValueError, match="stateless"):
        _manager().set_assignment_policy(stateful, name="stateful")


def test_loader_rejects_mutable_function_attributes(monkeypatch):
    monkeypatch.setattr(assign, "calls", 1, raising=False)
    with pytest.raises(ValueError, match="stateless"):
        load_assignment_policy("algorithm.emos.example_policy:assign")


def test_custom_algorithm_runs_without_fallback_and_positions_are_read_on_main_thread():
    manager = _manager()
    reads = []
    manager._get_pos = lambda name: reads.append(threading.current_thread().name) or (1.0, 2.0, 0.0)
    manager.set_assignment_policy(assign, name="test")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    assert not manager._thread.is_alive()
    assert manager.poll()
    assert reads == [threading.current_thread().name] * 4
    assert manager.fallback_dispatch_count == 0
    assert manager.discussion_count == 1
    assert manager.should_preserve_raw_llm_assignments()


def test_build_from_agent_specs_prefers_explicit_position_callback():
    from algorithm.emos.engine import EMOSDiscussionManager
    from algorithm.emos.types import EMOSRobotAgentSpec
    from demo.fire_rescue.scenario import build_factory_emos_scenario

    calls = []

    def provider(name):
        calls.append(name)
        return (9.0, 8.0, 0.5)

    specs = {
        "m20_1": EMOSRobotAgentSpec(
            agent_name="m20_1", robot_type="quadruped", capabilities=["nav"]),
    }
    manager = EMOSDiscussionManager.build_from_agent_specs(
        None,
        specs,
        build_factory_emos_scenario(),
        use_env_resume=False,
        get_robot_pos_fn=provider,
        position_source="native_root_state",
    )
    assert manager.position_source == "native_root_state"
    assert manager._get_pos("m20_1") == (9.0, 8.0, 0.5)
    assert calls == ["m20_1"]
    assert manager._assignment_request is None


def test_custom_algorithm_error_is_not_hidden_by_heuristic_fallback():
    manager = _manager()
    manager.set_assignment_policy(_raise_policy, name="failing")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    with pytest.raises(RuntimeError, match="algorithm exploded"):
        manager.poll()
    assert manager.fallback_dispatch_count == 0


def test_callback_mutation_cannot_change_task_targets_or_scenario():
    manager = _manager()
    manager.set_assignment_policy(_mutate_request, name="mutating")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    result = manager.poll()
    assert result["m20_1"].subtask_name != "corrupted"
    assert result["m20_1"].target_xy != (99.0, 99.0)


def test_dynamic_replanning_uses_the_selected_algorithm_not_local_fallback():
    manager = _manager()
    manager.set_assignment_policy(assign, name="test")
    assert manager.trigger_local_fallback("rescue replan")
    manager._thread.join(timeout=5)
    assert manager.poll()
    assert manager.fallback_dispatch_count == 0


def test_native_replay_restores_algorithm_request_and_state_without_serializing_callback():
    from demo.fire_rescue.runtime.native_replay import build_emos_replay_component

    manager = _manager()
    manager.set_assignment_policy(assign, name="test")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    assert manager.poll()
    component = build_emos_replay_component(manager)
    snapshot = component.capture_state()
    manager._assignment_request = None
    manager._assignment_error = "future failure"
    manager.discussion_count = 99
    component.validate_state(snapshot)
    component.restore_state(snapshot)
    assert manager._assignment_policy is assign
    assert manager._assignment_request.hazard_id == 1
    assert manager._assignment_error is None
    assert manager.discussion_count == 1


def _targetless_scenario():
    from algorithm.emos.types import EMOSScenarioConfig, PositionRule, RobotProfileSpec, SubtaskSpec

    return EMOSScenarioConfig(
        scenario_id="targetless",
        scene_title="Scene",
        anchor_label="event",
        task_description="relay without travel",
        subtasks={
            "inspect": SubtaskSpec("inspect", "inspect", "go inspect", ["inspect", "navigation"]),
            "relay": SubtaskSpec("relay", "relay", "publish relay", ["relay", "communication"]),
        },
        position_rules={
            "inspect": PositionRule(kind="fixed", xy=(1.0, 2.0)),
            "relay": PositionRule(kind="none", xy=None),
        },
        robot_profiles={
            "r1": RobotProfileSpec("ground", ["navigation"]),
            "r2": RobotProfileSpec("aerial", ["communication"]),
        },
        preferred_fallback={"r1": "inspect", "r2": "relay"},
        yellow_subtask_id="",
        green_subtask_id="",
        yellow_robot_prefix="",
    )


def test_position_rule_parsing_rejects_missing_xy_and_unknown_kind_keeps_targetless():
    from algorithm.emos.types import scenario_from_dict

    with pytest.raises(ValueError, match="requires xy"):
        scenario_from_dict({
            "subtasks": {"a": {"name": "a", "description": "", "match_keywords": []}},
            "position_rules": {"a": {"type": "fixed"}},
        })
    with pytest.raises(ValueError, match="unknown position rule type"):
        scenario_from_dict({
            "subtasks": {"a": {"name": "a", "description": "", "match_keywords": []}},
            "position_rules": {"a": {"type": "mystery", "xy": [1, 2]}},
        })
    legacy = scenario_from_dict({
        "subtasks": {
            "a": {"name": "a", "description": "", "match_keywords": []},
            "b": {"name": "b", "description": "", "match_keywords": []},
        },
        "position_rules": {
            "a": {"type": "none"},
            "b": {"type": "fixed", "xy": None},
        },
    })
    assert legacy.position_rules["a"].kind == "none"
    assert legacy.position_rules["b"].kind == "none"


def test_targetless_survives_llm_parse_fallback_sanitize_and_custom_policy():
    from types import SimpleNamespace

    from algorithm.emos.assignment import (
        fallback_assignment,
        parse_discussion_results,
        sanitize_llm_assignments,
    )
    from algorithm.emos.policy import AssignmentRequest, assignment_tasks
    from algorithm.emos.types import compute_subtask_positions

    scenario = _targetless_scenario()
    positions = compute_subtask_positions(scenario, (0.0, 0.0, 0.0))
    assert positions == {"inspect": (1.0, 2.0)}
    agents = ["r1", "r2"]
    chat = []

    parsed = parse_discussion_results(
        scenario,
        {
            "r1": SimpleNamespace(subtask_description="inspect the zone"),
            "r2": SimpleNamespace(subtask_description="relay the readiness receipt"),
        },
        positions,
        agents,
        push_chat=lambda *args: chat.append(args),
        generate_report=lambda *_: None,
        sanitize=lambda assignments, pos: sanitize_llm_assignments(
            scenario, assignments, pos, agents, set(), set(),
            lambda p: fallback_assignment(
                scenario, p, agents,
                robots_completed_extinguisher=set(), robots_completed_rescue=set(),
                sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
                generate_report=lambda *_: None,
            ),
        ),
        subtask_tuple_map=scenario.subtask_tuple_map(),
        keyword_map=scenario.keyword_map_for_parse(),
    )
    assert parsed["r1"].target_xy == (1.0, 2.0)
    assert parsed["r2"].target_xy is None
    assert "relay" in {task.subtask_colour for task in parsed.values()}

    fallback = fallback_assignment(
        scenario, positions, agents,
        robots_completed_extinguisher=set(), robots_completed_rescue=set(),
        sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
        generate_report=lambda *_: None,
    )
    assert fallback["r1"].target_xy == (1.0, 2.0)
    assert fallback["r2"].target_xy is None

    sanitized = sanitize_llm_assignments(
        scenario, dict(parsed), positions, agents, set(), set(),
        lambda p: fallback_assignment(
            scenario, p, agents,
            robots_completed_extinguisher=set(), robots_completed_rescue=set(),
            sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
            generate_report=lambda *_: None,
        ),
    )
    assert sanitized["r2"].target_xy is None

    request = AssignmentRequest(
        task_description="task",
        scenario=scenario,
        hazard_id=1,
        hazard_pos=(0.0, 0.0, 0.0),
        robot_positions={"r1": (0.0, 0.0, 0.0), "r2": (1.0, 1.0, 0.0)},
        subtask_positions=positions,
        completed_extinguisher=(),
        completed_rescue=(),
        rescue_constraints=None,
    )
    custom = assignment_tasks(request, {"r1": "inspect", "r2": "relay"})
    assert custom["r2"].target_xy is None

    with pytest.raises(ValueError, match="missing spatial target"):
        assignment_tasks(
            AssignmentRequest(
                task_description="task",
                scenario=scenario,
                hazard_id=1,
                hazard_pos=(0.0, 0.0, 0.0),
                robot_positions={"r1": (0.0, 0.0, 0.0)},
                subtask_positions={},
                completed_extinguisher=(),
                completed_rescue=(),
                rescue_constraints=None,
            ),
            {"r1": "inspect"},
        )


def test_fire_rescue_interpret_rejects_targetless_and_null_component_xy():
    from types import SimpleNamespace

    from demo.fire_rescue.scenario import interpret_factory_tasks, require_spatial_target_xy

    with pytest.raises(ValueError, match="requires spatial target_xy"):
        interpret_factory_tasks({
            "m20_1": SimpleNamespace(
                subtask_colour="red", subtask_name="scout", target_xy=None),
        })
    with pytest.raises(ValueError, match="null components|non-finite"):
        interpret_factory_tasks({
            "m20_1": SimpleNamespace(
                subtask_colour="red", subtask_name="scout", target_xy=(None, 1.0)),
        })
    rows = interpret_factory_tasks({
        "m20_1": SimpleNamespace(
            subtask_colour="red", subtask_name="scout", target_xy=(1.5, -2.5)),
    })
    assert rows[0].target_xy == (1.5, -2.5)
    with pytest.raises(ValueError, match="requires spatial target_xy"):
        require_spatial_target_xy(None, robot_name="scout_1")
    assert require_spatial_target_xy((3.0, 4.0), robot_name="scout_1") == (3.0, 4.0)


def test_open_subtask_ids_resolves_full_catalogue_and_rejects_unknown_kind():
    from algorithm.emos.assignment import fallback_assignment, parse_discussion_results
    from algorithm.emos.policy import AssignmentRequest, assignment_tasks
    from algorithm.emos.types import (
        EMOSScenarioConfig,
        PositionRule,
        RobotTask,
        SubtaskSpec,
        open_subtask_ids,
        resolve_subtask_target_xy,
    )
    from types import SimpleNamespace

    # Directly constructed unknown kind must fail at the resolver entry, not
    # only inside scenario_from_dict.
    typo = EMOSScenarioConfig(
        scenario_id="typo",
        scene_title="Scene",
        anchor_label="event",
        task_description="bad rule",
        subtasks={"a": SubtaskSpec("a", "a", "a", ["a"])},
        position_rules={"a": PositionRule(kind="typo", xy=(1.0, 2.0))},  # type: ignore[arg-type]
        robot_profiles={},
        preferred_fallback={},
        yellow_subtask_id="",
        green_subtask_id="",
        yellow_robot_prefix="",
    )
    with pytest.raises(ValueError, match="unknown position rule kind"):
        resolve_subtask_target_xy(typo, "a", {"a": (1.0, 2.0)})
    with pytest.raises(ValueError, match="unknown position rule kind"):
        open_subtask_ids(typo, {"a": (1.0, 2.0)})

    # Ordinary spatial task missing from positions must raise — not vanish.
    spatial = EMOSScenarioConfig(
        scenario_id="spatial",
        scene_title="Scene",
        anchor_label="event",
        task_description="missing coords",
        subtasks={
            "inspect": SubtaskSpec("inspect", "inspect", "go", ["inspect"]),
            "relay": SubtaskSpec("relay", "relay", "publish", ["relay"]),
        },
        position_rules={
            "inspect": PositionRule(kind="fixed", xy=(1.0, 2.0)),
            "relay": PositionRule(kind="none", xy=None),
        },
        robot_profiles={},
        preferred_fallback={},
        yellow_subtask_id="",
        green_subtask_id="",
        yellow_robot_prefix="",
    )
    with pytest.raises(ValueError, match="missing spatial target"):
        open_subtask_ids(spatial, {})  # inspect absent → hard fail
    assert open_subtask_ids(spatial, {"inspect": (1.0, 2.0)}) == ["inspect", "relay"]

    chat = []
    with pytest.raises(ValueError, match="missing spatial target"):
        parse_discussion_results(
            spatial,
            {"r1": SimpleNamespace(subtask_description="inspect the zone")},
            {},
            ["r1"],
            push_chat=lambda *a: chat.append(a),
            generate_report=lambda *_: None,
            sanitize=lambda assignments, pos: assignments,
            subtask_tuple_map=spatial.subtask_tuple_map(),
            keyword_map=spatial.keyword_map_for_parse(),
        )
    with pytest.raises(ValueError, match="missing spatial target"):
        fallback_assignment(
            spatial, {}, ["r1"],
            robots_completed_extinguisher=set(), robots_completed_rescue=set(),
            sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
            generate_report=lambda *_: None,
        )
    with pytest.raises(ValueError, match="missing spatial target"):
        assignment_tasks(
            AssignmentRequest(
                task_description="task",
                scenario=spatial,
                hazard_id=1,
                hazard_pos=(0.0, 0.0, 0.0),
                robot_positions={"r1": (0.0, 0.0, 0.0)},
                subtask_positions={},
                completed_extinguisher=(),
                completed_rescue=(),
                rescue_constraints=None,
            ),
            {"r1": "inspect"},
        )


def test_sanitize_rewrites_forged_targetless_zero_and_rejects_missing_spatial():
    from algorithm.emos.assignment import sanitize_llm_assignments
    from algorithm.emos.types import RobotTask

    scenario = _targetless_scenario()
    agents = ["r1", "r2"]
    forged = {
        "r1": RobotTask("r1", "inspect", "inspect", "go", (1.0, 2.0)),
        # Forged (0, 0) on an explicit targetless mission must become None.
        "r2": RobotTask("r2", "relay", "relay", "publish", (0.0, 0.0)),
    }
    cleaned = sanitize_llm_assignments(
        scenario, forged, {"inspect": (1.0, 2.0)}, agents, set(), set(),
        lambda pos: (_ for _ in ()).throw(AssertionError("fallback must not run")),
    )
    assert cleaned["r1"].target_xy == (1.0, 2.0)
    assert cleaned["r2"].target_xy is None

    missing = {
        "r1": RobotTask("r1", "inspect", "inspect", "go", (9.0, 9.0)),
    }
    with pytest.raises(ValueError, match="missing spatial target"):
        sanitize_llm_assignments(
            scenario, missing, {}, agents, set(), set(),
            lambda pos: (_ for _ in ()).throw(AssertionError("fallback must not mask")),
        )


def test_yellow_excluded_when_no_eligible_robots_allows_llm_parse_and_fallback():
    """Factory repro: m20_* completed/blocked → yellow closed, remaining tasks assignable."""
    from types import SimpleNamespace

    from algorithm.emos.assignment import (
        excluded_subtask_ids_for_completed,
        fallback_assignment,
        parse_discussion_results,
        sanitize_llm_assignments,
    )
    from algorithm.emos.engine import EMOSDiscussionManager
    from algorithm.emos.types import compute_subtask_positions, open_subtask_ids
    from demo.fire_rescue.scenario import build_factory_emos_scenario

    scenario = build_factory_emos_scenario()
    agents = ["m20_1", "m20_2", "carter_1", "scout_1"]
    completed = {"m20_1", "m20_2"}
    positions = compute_subtask_positions(scenario, (0.0, 0.0, 0.0))
    assert "yellow" in positions  # coords stay; closure is not deletion

    excluded = excluded_subtask_ids_for_completed(
        scenario, agents, completed, set())
    assert excluded == frozenset({"yellow"})
    open_ids = open_subtask_ids(
        scenario, positions, excluded_subtask_ids=excluded)
    assert "yellow" not in open_ids
    assert set(open_ids) == {"blue", "green", "red"}

    # Illegal missing-target still fails for a non-excluded ordinary task.
    with pytest.raises(ValueError, match="missing spatial target"):
        open_subtask_ids(
            scenario, {k: v for k, v in positions.items() if k != "red"},
            excluded_subtask_ids=excluded,
        )

    chat: list = []
    fallback = fallback_assignment(
        scenario, positions, agents,
        robots_completed_extinguisher=completed, robots_completed_rescue=set(),
        sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
        generate_report=lambda *_: None,
        excluded_subtask_ids=set(excluded),
    )
    colours = {task.subtask_colour for task in fallback.values()}
    assert "yellow" not in colours
    assert colours <= {"red", "green", "blue"}
    assert fallback["scout_1"].subtask_colour == "green"

    parsed = parse_discussion_results(
        scenario,
        {
            "carter_1": SimpleNamespace(subtask_description="blue data collection"),
            "scout_1": SimpleNamespace(subtask_description="green rescue channel"),
            "m20_1": SimpleNamespace(subtask_description="red fire scout"),
        },
        positions,
        agents,
        push_chat=lambda *a: chat.append(a),
        generate_report=lambda *_: None,
        sanitize=lambda assignments, pos: sanitize_llm_assignments(
            scenario, assignments, pos, agents, completed, set(),
            lambda p: fallback_assignment(
                scenario, p, agents,
                robots_completed_extinguisher=completed, robots_completed_rescue=set(),
                sys_log=lambda *_: None, push_chat=lambda *a: chat.append(a),
                generate_report=lambda *_: None,
                excluded_subtask_ids=set(excluded),
            ),
        ),
        subtask_tuple_map=scenario.subtask_tuple_map(),
        keyword_map=scenario.keyword_map_for_parse(),
        excluded_subtask_ids=set(excluded),
    )
    assert "yellow" not in {task.subtask_colour for task in parsed.values()}

    # Engine fallback path (local replan / LLM-unavailable) must also succeed.
    manager = EMOSDiscussionManager(
        SimpleNamespace(), agents, lambda name: (1.0, 2.0, 0.0), scenario,
        use_env_resume=False,
    )
    manager._robots_completed_extinguisher = set(completed)
    manager._hazard_id = 1
    manager._hazard_pos = (0.0, 0.0, 0.0)
    assert manager.trigger_local_fallback("yellow blocked replan")
    manager._thread.join(timeout=5) if manager._thread else None
    result = manager.poll()
    assert result is not None
    assert "yellow" not in {task.subtask_colour for task in result.values()}
    for task in result.values():
        assert task.target_xy is not None
