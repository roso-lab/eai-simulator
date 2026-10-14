from dataclasses import replace
from types import SimpleNamespace
import json
import pytest

from benchmark.artifacts import BranchArtifactStore
from benchmark.evidence import EvaluationReport, EvaluationStatus, EvidenceRecord, EvidenceStatus
from benchmark.metrics import MetricRegistry, MetricStatus
from benchmark.protocol import BranchRole, RunKey
from benchmark.release import ReleaseIndex, ReleaseStatus, ReleaseError
from benchmark.task_catalog import load_default_catalog
from benchmark.tests.test_artifacts import _binding, _result


def setup_index(tmp_path, *, condition='nominal', history='history', system='system'):
    catalog = load_default_catalog()
    index = ReleaseIndex(catalog, plan_digest='plan')
    key = RunKey('base', system, condition, 'r1', 'round', BranchRole.FACT, history)
    evidence = EvidenceRecord('handoff', 'v1', EvidenceStatus.SATISFIED, 0, 'measured', 'actual-evidence')
    result = replace(_result(key), evaluation=EvaluationReport(EvaluationStatus.SUCCESS, 'verified', (evidence,), 'test', 'v1'))
    store = BranchArtifactStore(tmp_path)
    store.write(result, binding=_binding())
    artifact = store.read(key)
    digest = index.add_artifact(artifact)
    return index, artifact, dict(anchor_id='prod-p2-01', condition_id=condition, branch_role=key.branch_role,
        base_instance_id=key.base_instance_id, repeat_id=key.repeat_id, system_id=system,
        checkpoint_round=key.checkpoint_round, untreated_history_id=history, artifact_digest=digest)


def test_pending_and_na_remain_missing_without_inference(tmp_path):
    index, artifact, fields = setup_index(tmp_path)
    registry = MetricRegistry()
    index.add_observation(registry.make_observation('D3.p2_handoff_e2e', status=MetricStatus.PENDING), **fields)
    index.add_observation(registry.make_observation('D1.initial_plan_validity', status=MetricStatus.NA,
                                                 na_reason='evidence_missing'), **fields)
    report = index.finalize()
    assert report.status is ReleaseStatus.PENDING
    assert all(r.observation.value is None for r in report.records)
    assert report.inferentially_ready is False


def test_index_ready_is_only_index_qualification(tmp_path):
    index, artifact, fields = setup_index(tmp_path)
    observation = MetricRegistry().make_observation('D3.p2_handoff_e2e', status=MetricStatus.VALID,
        value=1, denominator=1, evidence_hash='actual-evidence')
    index.add_observation(observation, **fields)
    report = index.finalize()
    assert report.status is ReleaseStatus.READY
    assert report.inferentially_ready is False
    assert report.to_dict()['qualification'] == 'artifact_index_only'
    with pytest.raises(ReleaseError, match='duplicate metric'):
        index.add_observation(observation, **fields)


@pytest.mark.parametrize('field', ['base_instance_id', 'system_id', 'condition_id', 'repeat_id',
                                  'checkpoint_round', 'untreated_history_id', 'branch_role'])
def test_full_run_identity_required(tmp_path, field):
    index, artifact, fields = setup_index(tmp_path)
    fields[field] = BranchRole.SHAM if field == 'branch_role' else 'other'
    with pytest.raises(ReleaseError, match='identity mismatch'):
        index.add_observation(MetricRegistry().make_observation('D3.p2_handoff_e2e', status=MetricStatus.PENDING), **fields)


def test_only_verified_artifact_and_actual_evaluator_evidence_admitted(tmp_path):
    index, artifact, fields = setup_index(tmp_path)
    with pytest.raises(ReleaseError, match='StoredBranchArtifact'):
        index.add_artifact(SimpleNamespace(payload=artifact.payload))
    with pytest.raises(ReleaseError, match='absent'):
        index.add_observation(MetricRegistry().make_observation('D3.p2_handoff_e2e', status=MetricStatus.VALID,
            value=1, denominator=1, evidence_hash='invented'), **fields)
    missing = {**fields, 'artifact_digest': 'unknown'}
    with pytest.raises(ReleaseError, match='admitted verified'):
        index.add_observation(MetricRegistry().make_observation('D3.p2_handoff_e2e', status=MetricStatus.NA,
            na_reason='evidence_missing'), **missing)
    payload = json.loads(artifact.path.read_text())
    payload['evaluation']['reason'] = 'tampered'
    artifact.path.write_text(json.dumps(payload))
    fresh = ReleaseIndex(load_default_catalog(), plan_digest='plan')
    with pytest.raises(ReleaseError, match='verification failed'):
        fresh.add_artifact(artifact)


def test_distinct_system_or_history_are_not_duplicate_metric_identity(tmp_path):
    index, artifact, fields = setup_index(tmp_path / 'first')
    other, artifact2, fields2 = setup_index(tmp_path / 'second', system='other', history='history-2')
    index.add_artifact(artifact2)
    obs = MetricRegistry().make_observation('D3.p2_handoff_e2e', status=MetricStatus.PENDING)
    index.add_observation(obs, **fields)
    index.add_observation(obs, **fields2)
    assert len(index.records) == 2
