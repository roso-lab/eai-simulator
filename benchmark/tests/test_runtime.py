from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    ExecutionFeedback,
    RobotCapability,
    TaskGraph,
)
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from benchmark.evidence import EvaluationReport, EvaluationStatus, EvidenceRecord, EvidenceStatus
from benchmark.protocol import BranchRole, ProtocolContract, RunKey
from benchmark.runtime import EpisodeRunner, RuntimeTaskBinding, StepBoundary
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


class FakeRuntime:
    def __init__(self) -> None:
        self.actions = []

    def step(self, actions):
        self.actions.append(dict(actions))
        return {"ignored": True}

    def observe(self):
        return {"available": True}


def test_episode_runner_owns_monotonic_tick_and_ledger() -> None:
    runtime = FakeRuntime()
    runner = EpisodeRunner(
        runtime,
        run_key=RunKey("base", "system", "condition", "r0", "grid0", BranchRole.FACT),
    )
    boundary = runner.step({"robot-1": "move"}, sim_time_s=0.1)
    assert isinstance(boundary, StepBoundary)
    assert boundary.tick == 0
    assert runner.tick == 1
    assert runner.ledger.events[0].kind == "step_boundary"


def _p2_binding() -> RuntimeTaskBinding:
    task = CoordinationTask(
        task_id="deliver",
        description="Deliver the payload through the handoff point.",
        required_capabilities=frozenset({"navigate"}),
        target_ref="handoff-zone",
    )
    package = EaiTaskPackage(
        task_id="p2_delivery",
        scene_id="fake-p2",
        graph=TaskGraph(tasks=(task,), source="benchmark.tests"),
        robots=(RobotState(RobotCapability("robot-1", frozenset({"navigate"}))),),
    )
    return RuntimeTaskBinding(
        task=package,
        contract=ProtocolContract(
            contract_id="p2-delivery",
            version="v1",
            domain="factory",
            primary_label="handoff",
            relation_signature=("precedence", "handoff"),
            observation_schema=package.observation_schema,
            milestones=("handoff_reached",),
        ),
        scene_id="fake-p2",
        robots=package.robots,
        observation_schema=package.observation_schema,
        session_identity="fake-session",
    )


def test_p2_vertical_slice_preserves_paper_responsibility_boundaries() -> None:
    runtime = FakeRuntime()
    runner = EpisodeRunner(
        runtime,
        run_key=RunKey("instance-0", "baseline", "nominal", "repeat-0", "none", BranchRole.FACT),
    )
    condition = AlgorithmCondition(
        algorithm_id="fixture-brain",
        source_reference="benchmark.tests",
        implementation="fixture",
        execution_scope="decision_only",
        planner="fixture",
    )

    def decide(task, robots):
        assert task.task_id == "p2_delivery"
        assert robots[0].capability.robot_id == "robot-1"
        return AlgorithmDecision(
            condition=condition,
            assignments=(Assignment("deliver", "robot-1", source="fixture"),),
        )

    def request_factory(assignment, task, *, request_id):
        return SkillRequest(
            request_id=request_id,
            skill_id="fixture_navigation",
            robot_id=assignment.robot_id,
            target_ref=task.target_ref,
        )

    class Skill:
        def plan(self, request, *, observation=None):
            assert observation == {"available": True}
            return SkillPlan(
                request_id=request.request_id,
                skill_id=request.skill_id,
                robot_id=request.robot_id,
                commands=(SkillCommand("velocity", {"linear_x": 0.2}),),
            )

    class Executor:
        def execute(self, assignment, task, *, scene, plan, step):
            assert scene == "fake-scene"
            assert task.task_id == assignment.task_id
            assert plan.commands[0].command_type == "velocity"
            step({assignment.robot_id: plan.commands[0].payload})
            return ExecutionFeedback(
                task_id=assignment.task_id,
                robot_id=assignment.robot_id,
                outcome="command_applied",
            )

    class Evaluator:
        def evaluate(self, *, task, observations, branch):
            assert task.task_id == "p2_delivery"
            assert branch is None
            assert observations == ({"handoff_reached": True}, {"available": True})
            return EvaluationReport(
                status=EvaluationStatus.SUCCESS,
                reason="external handoff predicate satisfied",
                evidence=(
                    EvidenceRecord(
                        predicate_id="handoff_reached",
                        predicate_version="p2.v1",
                        status=EvidenceStatus.SATISFIED,
                        tick=0,
                        source="fixture.evaluator",
                        evidence_hash="evidence-0",
                    ),
                ),
                evaluator_id="fixture-evaluator",
                evaluator_version="v1",
            )

    result = runner.run(
        _p2_binding(),
        decision_provider=decide,
        request_factory=request_factory,
        skill=Skill(),
        executor=Executor(),
        evaluator=Evaluator(),
        scene="fake-scene",
        initial_observations=({"handoff_reached": True},),
    )
    assert result.evaluation.status is EvaluationStatus.SUCCESS
    assert result.executions[0].plan.skill_id == "fixture_navigation"
    assert [event.kind for event in runner.ledger.events] == [
        "episode_start",
        "algorithm_decision",
        "skill_request",
        "skill_plan",
        "step_boundary",
        "execution_feedback",
        "evidence",
        "terminal_verdict",
    ]
    assert runner.ledger.sealed


def test_missing_evaluator_is_explicitly_not_applicable() -> None:
    runtime = FakeRuntime()
    runner = EpisodeRunner(
        runtime,
        run_key=RunKey("instance-0", "baseline", "nominal", "repeat-1", "none", BranchRole.FACT),
    )

    def decide(task, robots):
        del task, robots
        return AlgorithmDecision(
            condition=AlgorithmCondition(
                algorithm_id="fixture-brain",
                source_reference="benchmark.tests",
                implementation="fixture",
                execution_scope="decision_only",
                planner="fixture",
            ),
            assignments=(Assignment("deliver", "robot-1", source="fixture"),),
        )

    class Skill:
        def plan(self, request, *, observation=None):
            del observation
            return SkillPlan(
                request_id=request.request_id,
                skill_id=request.skill_id,
                robot_id=request.robot_id,
                commands=(SkillCommand("noop"),),
                status="success",
            )

    class Executor:
        def execute(self, assignment, task, *, scene, plan, step):
            del task, scene, plan
            step({assignment.robot_id: {"noop": True}})
            return ExecutionFeedback(assignment.task_id, assignment.robot_id, "command_applied")

    result = runner.run(
        _p2_binding(),
        decision_provider=decide,
        request_factory=lambda assignment, task, *, request_id: SkillRequest(
            request_id=request_id,
            skill_id="fixture_navigation",
            robot_id=assignment.robot_id,
            target_ref=task.target_ref,
        ),
        skill=Skill(),
        executor=Executor(),
        evaluator=None,
        scene="fake-scene",
    )
    assert result.evaluation.status is EvaluationStatus.NOT_APPLICABLE
    assert result.evaluation.reason == "physical_evidence_required"


def test_runtime_binding_from_session_snapshots_roster_and_controller_metadata():
    from types import SimpleNamespace

    class Pose:
        def tolist(self):
            return [[1.0, 2.0, 3.0], [9.0, 9.0, 9.0]]

    session = SimpleNamespace(
        env_name="EAI-Fake-v0",
        possible_agents=["robot_a"],
        num_envs=2,
        device="cpu",
        selection_data={"scene_key": "factory", "robots": [{"type": "m20", "attachments": [{"type": "navigation_io"}]}]},
        env_cfg=SimpleNamespace(controllers={"robot_a": SimpleNamespace(command_name="goal_position", control_mode="position")}),
        base_env=SimpleNamespace(
            scene=SimpleNamespace(
                articulations={"robot_a": SimpleNamespace(data=SimpleNamespace(root_pos_w=Pose()))}
            )
        ),
    )
    binding = RuntimeTaskBinding.from_session(_p2_binding().task, session, contract=_p2_binding().contract)
    assert binding.scene_id == "EAI-Fake-v0"
    assert binding.robots[0].capability.robot_id == "robot_a"
    assert binding.robots[0].position == (1.0, 2.0, 3.0)
    assert binding.robots[0].metadata["attachments"] == ("navigation_io",)
    assert binding.metadata["selection_digest"]
    assert binding.metadata["controller_profiles_digest"]
    assert binding.session_identity.startswith("session:")
