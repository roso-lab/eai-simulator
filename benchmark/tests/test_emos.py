from algorithm.common_coordination.contract import Assignment, ExecutionFeedback
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from benchmark.evidence import EvaluationReport, EvaluationStatus, EvidenceRecord, EvidenceStatus
from benchmark.emos import EmosDecisionAdapter, package_from_fire_rescue_request
from benchmark.protocol import BranchRole, ProtocolContract, RunKey
from benchmark.runtime import EpisodeRunner, RuntimeTaskBinding
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request


def test_emos_example_policy_adapts_to_typed_algorithm_decision() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_from_fire_rescue_request(request)
    decision = EmosDecisionAdapter(request)(package, package.robots)

    assert decision.condition.algorithm_id == "EMOS-adapted-to-EAI"
    assert decision.provenance["adapter"] == "benchmark.emos.EmosDecisionAdapter"
    assert decision.assignments
    assert all(isinstance(item, Assignment) for item in decision.assignments)
    assert {item.task_id for item in decision.assignments}.isdisjoint(decision.unassigned_tasks)


def test_emos_adapter_does_not_claim_execution_success() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_from_fire_rescue_request(request)
    decision = EmosDecisionAdapter(request)(package, package.robots)

    assert not hasattr(decision, "success")
    assert "physical_success" not in decision.provenance


def test_emos_decision_enters_runtime_but_external_evidence_owns_verdict() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_from_fire_rescue_request(request)

    def capability_aware_emos_policy(_request):
        return {
            "m20_1": "red",
            "m20_2": "yellow",
            "carter_1": "blue",
            "scout_1": "green",
        }

    adapter = EmosDecisionAdapter(request, policy=capability_aware_emos_policy)

    class Runtime:
        def step(self, actions):
            return {"actions": dict(actions)}

    class Skill:
        def plan(self, request, *, observation=None):
            del observation
            return SkillPlan(
                request_id=request.request_id,
                skill_id=request.skill_id,
                robot_id=request.robot_id,
                commands=(SkillCommand("symbolic_command"),),
            )

    class Executor:
        def execute(self, assignment, task, *, scene, plan, step):
            del task, scene, plan
            step({assignment.robot_id: {"symbolic": True}})
            return ExecutionFeedback(assignment.task_id, assignment.robot_id, "command_applied")

    class Evaluator:
        def evaluate(self, *, task, observations, branch):
            del task, observations, branch
            return EvaluationReport(
                status=EvaluationStatus.UNKNOWN,
                reason="no physical sample in decision-only EMOS test",
                evidence=(
                    EvidenceRecord(
                        predicate_id="mission_terminal",
                        predicate_version="fire_rescue.v1",
                        status=EvidenceStatus.UNKNOWN,
                        tick=0,
                        source="test.evaluator",
                        reason="Isaac session not attached",
                    ),
                ),
                evaluator_id="test.evaluator",
                evaluator_version="v1",
            )

    runner = EpisodeRunner(
        Runtime(),
        run_key=RunKey("fire-rescue-0", "emos", "nominal", "repeat-0", "none", BranchRole.FACT),
    )
    result = runner.run(
        # The benchmark contract is supplied by the caller; the EMOS adapter
        # itself only owns the algorithm decision.
        binding=RuntimeTaskBinding(
            task=package,
            contract=ProtocolContract(
                contract_id="fire-rescue-assignment",
                version="v1",
                domain="fire_rescue",
                primary_label="assignment",
                relation_signature=("assignment",),
                observation_schema=package.observation_schema,
            ),
            scene_id=package.scene_id,
            robots=package.robots,
            observation_schema=package.observation_schema,
            session_identity="symbolic",
        ),
        decision_provider=adapter,
        request_factory=lambda assignment, task, *, request_id: SkillRequest(
            request_id=request_id,
            skill_id="emos_symbolic_skill",
            robot_id=assignment.robot_id,
            target_ref=task.target_ref,
        ),
        skill=Skill(),
        executor=Executor(),
        evaluator=Evaluator(),
        scene="symbolic-scene",
    )
    assert result.evaluation.status is EvaluationStatus.UNKNOWN
    assert any(event.kind == "terminal_verdict" for event in runner.ledger.events)
