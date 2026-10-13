from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, CoordinationTask, RobotCapability, TaskGraph
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

from benchmark.batch_runner import BatchEpisodeRunner
from benchmark.artifacts import BranchArtifactStore
from benchmark.batch import build_batch_identity
from benchmark.experiment_plan import default_experiment_plan
from benchmark.protocol import ProtocolContract
from benchmark.queue import BatchExecutionQueue, QueueBranchStatus
from benchmark.release import ReleaseIndex, ReleaseStatus


class _Env:
    def step(self, _actions):
        return {}


class _BaseEnv:
    def __init__(self):
        self.scene = SimpleNamespace(articulations={})
        self.commands = []

    def set_command(self, robot_id, command_name, value):
        self.commands.append((robot_id, command_name, value))


def _session():
    return SimpleNamespace(
        env_name="EAI-Batch-Fake-v0",
        env=_Env(),
        base_env=_BaseEnv(),
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
        task_id="batch-task",
        scene_id="fake",
        graph=TaskGraph((task,), source="benchmark.tests.batch_runner"),
        robots=(RobotState(RobotCapability("robot_a", frozenset({"navigate"}))),),
    )


def _decision(_task, _robots):
    condition = AlgorithmCondition(
        algorithm_id="batch-fixture",
        source_reference="benchmark.tests",
        implementation="fixture",
        execution_scope="decision_only",
        planner="fixture",
    )
    return AlgorithmDecision(condition, assignments=(Assignment("deliver", "robot_a", "fixture"),))


def _batch():
    plan = default_experiment_plan()
    cell = next(item for item in plan.iter_cells() if not item.strict_pair_required)
    return plan, build_batch_identity(cell, plan=plan)


def test_batch_episode_runner_connects_queue_session_artifact_and_release(tmp_path):
    plan, batch = _batch()
    queue = BatchExecutionQueue(tmp_path / "queue.json", plan=plan)
    artifacts = BranchArtifactStore(tmp_path / "artifacts")
    release = ReleaseIndex(plan.catalog, plan_digest=batch.plan_digest)
    runner = BatchEpisodeRunner(queue, artifacts, release_index=release)
    task = _task()
    gate_events = []

    class Gate:
        def acquire(self, branch):
            gate_events.append(("acquire", branch.run_key.branch_role.value))
            return "lease"

        def release(self, branch, lease, *, status):
            gate_events.append(("release", branch.run_key.branch_role.value, lease, status))

    @contextmanager
    def sessions(_batch, _branch):
        yield _session()

    report = runner.run(
        batch,
        task_factory=lambda _batch, _branch: task,
        session_factory=sessions,
        contract_factory=lambda anchor, _branch: anchor.to_protocol_contract(),
        decision_provider_factory=lambda _task, _branch: _decision,
        max_steps=1,
        resource_gate=Gate(),
    )
    assert len(report.branches) == 1
    assert report.branches[0].branch.status is QueueBranchStatus.COMPLETED
    assert report.branches[0].artifact is not None
    assert report.branches[0].evaluation_status is not None
    assert report.branches[0].evaluation_status.value == "N/A"
    assert report.release_report is not None
    assert report.release_report.status is ReleaseStatus.PENDING
    assert report.release_report.gates["artifacts_indexed"] is True
    assert gate_events == [("acquire", "fact"), ("release", "fact", "lease", "completed")]

    resumed = runner.run(
        batch,
        task_factory=lambda _batch, _branch: task,
        session_factory=sessions,
        contract_factory=lambda anchor, _branch: anchor.to_protocol_contract(),
        decision_provider_factory=lambda _task, _branch: _decision,
        max_steps=1,
    )
    assert resumed.artifact_digests == report.artifact_digests


def test_batch_episode_runner_records_session_failure_without_fake_artifact(tmp_path):
    plan, batch = _batch()
    queue = BatchExecutionQueue(tmp_path / "queue.json", plan=plan)
    artifacts = BranchArtifactStore(tmp_path / "artifacts")
    runner = BatchEpisodeRunner(queue, artifacts)

    def unavailable(_batch, _branch):
        raise RuntimeError("session unavailable")

    report = runner.run(
        batch,
        task_factory=lambda _batch, _branch: _task(),
        session_factory=unavailable,
        contract_factory=lambda anchor, _branch: anchor.to_protocol_contract(),
        decision_provider_factory=lambda _task, _branch: _decision,
        max_steps=1,
    )
    assert len(report.failed) == 1
    assert "session unavailable" in report.failed[0].branch.error
    assert report.failed[0].artifact is None


def test_batch_episode_runner_executes_four_branches_but_keeps_strict_pair_unsupported(tmp_path):
    plan = default_experiment_plan()
    cell = next(item for item in plan.iter_cells() if item.strict_pair_required)
    batch = build_batch_identity(cell, plan=plan)
    queue = BatchExecutionQueue(tmp_path / "queue.json", plan=plan)
    artifacts = BranchArtifactStore(tmp_path / "artifacts")
    release = ReleaseIndex(plan.catalog, plan_digest=batch.plan_digest)
    runner = BatchEpisodeRunner(queue, artifacts, release_index=release)
    task = _task()

    report = runner.run(
        batch,
        task_factory=lambda _batch, _branch: task,
        session_factory=lambda _batch, _branch: _session(),
        contract_factory=lambda anchor, _branch: anchor.to_protocol_contract(),
        decision_provider_factory=lambda _task, _branch: _decision,
        max_steps=1,
    )
    assert len(report.branches) == 4
    assert all(item.branch.status is QueueBranchStatus.COMPLETED for item in report.branches)
    assert report.paired_claim_status == "unsupported"
    assert report.paired_claim_reason == "causal_replay_fidelity_and_sham_calibration_required"
    assert report.release_report is not None
    assert report.release_report.status is ReleaseStatus.PENDING
