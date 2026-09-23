from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_external_adapters import EmosGoalAdapter, TeamWeaverGoalAdapter
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment


def _run(adapter):
    public, api = (lambda b: (b["public_task"], b["participant_api"]))(build_goal_task_package("warehouse_nvd"))
    session = GoalSession(public, api, episode_id="external-adapter-test")
    with GoalAlgorithmHost(public, api, adapter, session=session) as host:
        actions = host.request_actions()
        return actions, adapter.trace, session.report()


def test_teamweaver_goal_adapter_emits_high_level_actions_and_trace():
    actions, trace, report = _run(TeamWeaverGoalAdapter())
    assert [row["type"] for row in actions] == ["select_team", "submit_plan", "dispatch_skill"]
    assert trace[0]["planner"] == "TeamWeaverPipeline"
    assert trace[1]["solver"] == "hungarian"
    assert report["selected_team"] == ["warehouse_ground_1"]


def test_emos_goal_adapter_uses_manager_policy_and_trace():
    actions, trace, report = _run(EmosGoalAdapter())
    assert [row["type"] for row in actions] == ["select_team", "submit_plan", "dispatch_skill"]
    assert trace[0]["planner"] == "EMOSDiscussionManager"
    assert trace[1]["manager"] == "EMOSDiscussionManager"
    assert report["selected_team"] == ["warehouse_ground_1"]


def test_external_adapters_answer_v3_deployment_gate_before_planning():
    bundle = build_goal_task_package("warehouse_nvd")
    public = enable_deployment_assessment(bundle["public_task"])
    api = bundle["participant_api"]
    # The participant API is rebuilt from the upgraded public contract so the
    # deployment action is visible without changing the frozen v2 package.
    from demo.benchmark.goal_driven_contract import build_goal_participant_api
    api = build_goal_participant_api(public)
    for adapter in (TeamWeaverGoalAdapter(), EmosGoalAdapter()):
        session = GoalSession(public, api, episode_id="external-v3")
        with GoalAlgorithmHost(public, api, adapter, session=session) as host:
            first = host.request_actions()
            assert [row["type"] for row in first] == ["deployment_assessment"]
            assert session.report()["actions"][-1]["payload"]["decision"] == "deployable"
            second = host.request_actions()
            assert [row["type"] for row in second] == [
                "select_team", "submit_plan", "dispatch_skill"]


def test_external_adapter_reassessment_uses_current_public_roster():
    bundle = build_goal_task_package("warehouse_nvd")
    public = enable_deployment_assessment(bundle["public_task"])
    from demo.benchmark.goal_driven_contract import build_goal_participant_api
    api = build_goal_participant_api(public)
    adapter = TeamWeaverGoalAdapter()
    adapter.initialize(public, api)
    required = {"type": "deployment_assessment_required", "time_s": 1.0,
                "payload": {"assessment_id": "deployment-assessment-1",
                            "assessment_epoch": 1, "roster_version": 1,
                            "current_agent_ids": ["warehouse_aerial_1"],
                            "capability_versions": {"warehouse_aerial_1": 1}}}
    action = adapter.act([required])[0]
    assert action["type"] == "deployment_assessment"
    assert action["payload"]["decision"] == "not_deployable"
    assert action["payload"]["reason_codes"] == ["missing_capability"]
