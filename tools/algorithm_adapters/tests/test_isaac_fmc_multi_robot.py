"""Fixture/dispatch guards only; these tests are not physical evidence."""
from types import SimpleNamespace
import json

import pytest

from algorithm.common_coordination.contract import Assignment
from algorithm.system.coordination.fmc_ata.native_adapter import NativeFMCATAAdapter
from task.contracts import EaiTaskPackage
from tools.algorithm_adapters import isaac_fmc_multi_robot as host


def test_explicit_two_task_fixture_preserves_native_units(tmp_path):
    path = tmp_path / 'task.json'
    document = host.case_document()
    host.write_json(path, document)
    task = EaiTaskPackage.from_json(path)
    payload = NativeFMCATAAdapter()._encode(task, task.robots)
    assert [t['task_id'] for t in payload['tasks']] == list(host.MOVES)
    assert len(payload['players']) == 2
    assert all(t['max_players'] == 1 and t['workload'] == 30 for t in payload['tasks'])
    for original, encoded in zip(document['tasks'], payload['tasks']):
        assert not original['prerequisites']
        assert 'bt_native' not in original['metadata']
        assert encoded['position'] == original['metadata']['position']


@pytest.mark.parametrize('pairs', [
    [('move_left','ur5_1')],
    [('move_left','ur5_1'),('move_right','ur5_1')],
    [('move_left','ur5_2'),('move_right','ur5_1')],
    [('move_left','alien'),('move_right','ur5_2')],
])
def test_invalid_native_dispatch_is_rejected_not_reassigned(pairs):
    decision = SimpleNamespace(assignments=tuple(Assignment(t,r,'test') for t,r in pairs))
    with pytest.raises(ValueError):
        host.validate_initial(decision)


def test_offline_does_not_solve_or_launch_isaac(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('prepare must not construct algorithm or simulator')
    monkeypatch.setattr(host, 'build_native_adapter', forbidden)
    monkeypatch.setattr(host, 'FMCPhysicalHost', forbidden)
    assert host.main(['--output', str(tmp_path / 'prepare')]) == 0
    receipt = json.loads((tmp_path / 'prepare/receipt.json').read_text())
    assert receipt['passed'] is False and receipt['physical_success'] is None
    assert receipt['physical_execution_started'] is False
    assert receipt['native_feedback'] is None


def test_no_receipts_cannot_pass(tmp_path):
    assert host.FMCPhysicalHost(tmp_path).evaluate()['status'] == 'failure'
