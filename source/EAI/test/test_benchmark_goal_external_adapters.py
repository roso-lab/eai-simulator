from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_external_adapters import EmosGoalAdapter, TeamWeaverGoalAdapter
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_session import GoalSession


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
