from types import SimpleNamespace

from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    RobotCapability,
    TaskGraph,
)
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

from benchmark.evidence import EvaluationReport, EvaluationStatus
from benchmark.eai_adapter import (
    CommandNavigationSkillAdapter,
    EaiTaskExecutor,
    command_navigation_request_factory,
)
from benchmark.artifacts import BranchArtifactStore
from benchmark.protocol import BranchRole, ProtocolContract, RunKey
from benchmark.session import run_session_episode


class _Env:
    def __init__(self):
        self.actions = []

    def step(self, actions):
        self.actions.append(dict(actions))
        return {}


class _BaseEnv:
    def __init__(self):
        self.commands = []
        self.scene = SimpleNamespace(articulations={})

    def set_command(self, robot_id, command_name, value):
        self.commands.append((robot_id, command_name, value))


def _session():
    base_env = _BaseEnv()
    return SimpleNamespace(
        env_name="EAI-P2-Fake-v0",
        env=_Env(),
        base_env=base_env,
        env_cfg=SimpleNamespace(controllers={"robot_a": SimpleNamespace(command_name="goal_position")}),
        possible_agents=["robot_a"],
        selection_data={"robots": [{"type": "m20"}]},
        num_envs=1,
        device="cpu",
    )


def _task():
    task = CoordinationTask(
        task_id="deliver",
        description="Move to the handoff point.",
        required_capabilities=frozenset({"navigate"}),
        target_ref="handoff",
        metadata={"target_pose": [1.0, 0.0, 0.0], "horizon_ticks": 1},
    )
    return EaiTaskPackage(
        task_id="p2-session",
        scene_id="fake",
        graph=TaskGraph((task,), source="benchmark.tests"),
        robots=(RobotState(RobotCapability("robot_a", frozenset({"navigate"}))),),
    )


def test_run_session_episode_wires_binding_runtime_executor_and_evaluator(tmp_path):
    task = _task()
    condition = AlgorithmCondition(
        algorithm_id="session-fixture",
        source_reference="benchmark.tests",
        implementation="fixture",
        execution_scope="decision_only",
        planner="fixture",
    )

    def decide(bound_task, robots):
        assert bound_task.scene_id == "EAI-P2-Fake-v0"
        assert robots[0].capability.robot_id == "robot_a"
        return AlgorithmDecision(condition, assignments=(Assignment("deliver", "robot_a", "fixture"),))

    class Evaluator:
        def evaluate(self, *, task, observations, branch):
            assert task.task_id == "p2-session"
            assert branch is None
            assert observations[0] == {"physical": "sample"}
            assert observations[1] == {"robots": {}}
            return EvaluationReport(
                status=EvaluationStatus.SUCCESS,
                reason="fixture external evidence",
                evaluator_id="benchmark.tests.session",
                evaluator_version="v1",
            )

    session = _session()
    artifact_store = BranchArtifactStore(tmp_path / "artifacts")
    result = run_session_episode(
        session,
        task,
        contract=ProtocolContract(
            contract_id="p2",
            version="v1",
            domain="factory",
            primary_label="handoff",
            relation_signature=("precedence", "handoff"),
            observation_schema=task.observation_schema,
        ),
        run_key=RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT),
        decision_provider=decide,
        evaluator=Evaluator(),
        request_factory=command_navigation_request_factory,
        skill=CommandNavigationSkillAdapter(),
        executor=EaiTaskExecutor(max_steps=1),
        initial_observations=({"physical": "sample"},),
        artifact_store=artifact_store,
    )
    assert result.evaluation.status is EvaluationStatus.SUCCESS
    assert len(result.executions) == 1
    assert session.base_env.commands[0][0:2] == ("robot_a", "goal_position")
    assert len(session.env.actions) == 1
    stored = artifact_store.read(result.run_key)
    assert stored.ledger_digest == result.ledger_digest
    assert stored.payload["fleet_audit"]["lifecycle"] == "sealed"
