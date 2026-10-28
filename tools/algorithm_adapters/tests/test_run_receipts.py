"""Receipt regression tests, not algorithm acceptance or performance evidence."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from tools.algorithm_adapters import run


@pytest.mark.parametrize('name', ['old_receipt.json', 'actor.pt', 'manifest.json'])
def test_existing_artifact_rejected_before_loading_provider(tmp_path, monkeypatch, name):
    report = tmp_path / name
    report.write_bytes(b'original artifact')
    monkeypatch.setattr(sys, 'argv', ['run', '--algorithm', 'harl_happo', '--task', str(tmp_path/'task.json'), '--report', str(report)])
    monkeypatch.setattr(run, 'build_native_adapter', lambda *a, **k: pytest.fail('must not run provider'))
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2
    assert report.read_bytes() == b'original artifact'


def test_atomic_writer_preserves_concurrent_winner_and_removes_temp(tmp_path):
    path = tmp_path / 'report.json'
    run.write_new_report(path, {'status': 'first'})
    with pytest.raises(FileExistsError):
        run.write_new_report(path, {'status': 'second'})
    assert json.loads(path.read_text()) == {'status': 'first'}
    assert list(tmp_path.iterdir()) == [path]


def test_nonfinite_receipt_does_not_leave_partial_file(tmp_path):
    path = tmp_path / 'report.json'
    with pytest.raises(ValueError):
        run.write_new_report(path, {'bad': float('nan')})
    assert not path.exists() and not list(tmp_path.iterdir())


def test_dangling_symlink_is_not_followed(tmp_path):
    target = tmp_path / 'absent'
    path = tmp_path / 'report.json'
    path.symlink_to(target)
    with pytest.raises(FileExistsError):
        run.write_new_report(path, {})
    assert path.is_symlink() and not target.exists()


@pytest.mark.parametrize('mutate', [False, True])
def test_input_mutation_prevents_success_receipt(tmp_path, monkeypatch, mutate):
    task = tmp_path/'task.json'
    report = tmp_path/'report.json'
    task.write_text('{}')
    monkeypatch.setattr(sys, 'argv', ['run', '--algorithm', 'sadcher', '--task', str(task), '--report', str(report)])
    monkeypatch.setattr(run.EaiTaskPackage, 'from_json', lambda p: SimpleNamespace())
    monkeypatch.setattr(run, 'build_native_adapter', lambda *a, **k: SimpleNamespace())
    def execute(*args):
        if mutate:
            task.write_text('{"changed": true}')
        return SimpleNamespace(to_dict=lambda: {}, decision=SimpleNamespace(provenance={}))
    monkeypatch.setattr(run, 'TaskRunner', lambda: SimpleNamespace(run=execute))
    with pytest.raises(SystemExit) as exc:
        run.main()
    receipt = json.loads(report.read_text())
    assert exc.value.code == int(mutate)
    assert receipt['input_files_unchanged'] is (not mutate)
    assert receipt['status'] == ('not_passed' if mutate else 'native_decision_returned')
