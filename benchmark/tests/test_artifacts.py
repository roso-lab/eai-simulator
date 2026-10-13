import json
import hashlib

import pytest

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability
from benchmark.artifacts import BranchArtifactStore
from benchmark.evidence import BranchLedger, EvaluationReport, EvaluationStatus
from benchmark.protocol import BranchRole, CheckpointRef, ProtocolContract, RunKey
from benchmark.runtime import EpisodeResult, RuntimeTaskBinding
from task.contracts import EaiTaskPackage, RobotState
from algorithm.common_coordination.contract import TaskGraph


def _binding() -> RuntimeTaskBinding:
    task = CoordinationTask(
        task_id="artifact-task",
        description="artifact fixture",
        required_capabilities=frozenset({"navigate"}),
    )
    package = EaiTaskPackage(
        task_id="artifact-task",
        scene_id="fake",
        graph=TaskGraph(tasks=(task,), source="benchmark.tests"),
        robots=(RobotState(RobotCapability("robot-a", frozenset({"navigate"}))),),
    )
    return RuntimeTaskBinding(
        task=package,
        contract=ProtocolContract(
            contract_id="artifact-contract",
            version="v1",
            domain="factory",
            primary_label="handoff",
            relation_signature=("precedence",),
            observation_schema=package.observation_schema,
        ),
        scene_id="fake",
        robots=package.robots,
        observation_schema=package.observation_schema,
        session_identity="artifact-session",
    )


def _result(key: RunKey) -> EpisodeResult:
    ledger = BranchLedger(key)
    ledger.append("episode_start", tick=0, source="runtime", payload={"task_id": "artifact-task"})
    digest = ledger.seal()
    return EpisodeResult(
        decision={"kind": "fixture"},
        executions=(),
        evaluation=EvaluationReport(
            status=EvaluationStatus.NOT_APPLICABLE,
            reason="fixture artifact",
            evaluator_id="benchmark.tests",
            evaluator_version="v1",
        ),
        observations=({"tick": 0},),
        ledger_digest=digest,
        ledger_events=ledger.events,
        ledger_schema_version=ledger.schema_version,
        run_key=key,
    )


def test_branch_artifact_store_round_trips_and_verifies_chain(tmp_path):
    key = RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT)
    store = BranchArtifactStore(tmp_path)
    path = store.write(_result(key), binding=_binding())
    loaded = store.read(key)
    assert loaded.path == path
    assert loaded.run_key == key
    assert loaded.ledger_digest == _result(key).ledger_digest
    with pytest.raises(FileExistsError):
        store.write(_result(key), binding=_binding())


def test_branch_artifact_store_rejects_tampering(tmp_path):
    key = RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT)
    store = BranchArtifactStore(tmp_path)
    path = store.write(_result(key), binding=_binding())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ledger"]["events"][0]["payload"]["task_id"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        store.read(key)


def test_branch_artifact_store_rejects_rehashed_event_tampering(tmp_path):
    key = RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT)
    store = BranchArtifactStore(tmp_path)
    path = store.write(_result(key), binding=_binding())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ledger"]["events"][0]["payload"]["task_id"] = "tampered"
    digest_payload = dict(payload)
    digest_payload.pop("artifact_digest")
    payload["artifact_digest"] = hashlib.sha256(
        json.dumps(digest_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ledger event hash mismatch"):
        store.read(key)
