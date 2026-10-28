"""Integrity envelope tests; these do not constitute native method acceptance."""
import subprocess
import sys
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
from algorithm.system.coordination.native_runtime import SourceIntegrityError
from benchmark import native_algorithms as registry
from task.contracts import AlgorithmDecision


def envelope(tmp_path, monkeypatch, provider):
    # pytest 6 importlib mode may not register its synthetic test module.
    if type(provider).__module__ not in sys.modules:
        monkeypatch.setitem(sys.modules, type(provider).__module__, SimpleNamespace(__file__=__file__))
    root = tmp_path / "upstream"
    root.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    git("init", "-q")
    (root / "policy.py").write_text("VALUE = 1\n")
    git("add", "policy.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "source")
    commit = git("rev-parse", "HEAD")
    monkeypatch.setattr(registry, "source_spec", lambda _: {"commit": commit})
    return registry.VerifiedNativeAdapter("fixture", provider, root), root


def decision():
    return AlgorithmDecision(
        AlgorithmCondition("test", "fixture", "test", "protocol", "test"),
        (Assignment("task", "robot", "original"),),
        ("unassigned",), {"native_result": {"fraction": 0.42}},
    )


def test_envelope_preserves_native_output_and_missing_lifecycle(tmp_path, monkeypatch):
    output = decision()
    class Provider:
        def __call__(self, *_):
            return output
    adapter, _ = envelope(tmp_path, monkeypatch, Provider())
    result = adapter(None, ())
    assert result.assignments is output.assignments
    assert result.unassigned_tasks == output.unassigned_tasks
    assert result.provenance["native_result"] == output.provenance["native_result"]
    assert result.provenance["decision_is_physical_evidence"] is False
    assert "source_integrity" in result.provenance
    assert not hasattr(adapter, "replan")
    assert not hasattr(adapter, "initialize")


def test_replan_is_checked_and_preserves_native_failure(tmp_path, monkeypatch):
    failure = RuntimeError("native optimizer rejected problem")
    class Provider:
        def replan(self, *_):
            raise failure
    adapter, _ = envelope(tmp_path, monkeypatch, Provider())
    with pytest.raises(RuntimeError) as caught:
        adapter.replan(None, (), None)
    assert caught.value is failure
    assert caught.value.native_source_provenance["source_integrity"]["commit"]
    assert caught.value.native_source_provenance["adapter_source_sha256"]
    assert adapter.calls == 0


def test_change_during_call_never_returns_decision(tmp_path, monkeypatch):
    class Provider:
        def __call__(self, *_):
            (root / "policy.py").write_text("VALUE = 2\n")
            return decision()
    adapter, root = envelope(tmp_path, monkeypatch, Provider())
    with pytest.raises(SourceIntegrityError):
        adapter(None, ())
    assert adapter.calls == 0


def test_unregistered_method_cannot_select_legacy_fallback():
    with pytest.raises(ValueError, match="not a registered paper"):
        registry.build_native_adapter("robot_fleet")


def test_keyword_replan_stop_is_preserved_and_timed(tmp_path, monkeypatch):
    class Provider:
        def replan(self, *, task, robots, state):
            assert (task, robots, state) == ('task', (), 'done')
            return None
    adapter, _ = envelope(tmp_path, monkeypatch, Provider())
    assert adapter.replan(task='task', robots=(), state='done') is None
    assert adapter.calls == 0 and adapter.attempts == 1
    timing = adapter.last_call_metrics
    assert timing['operation'] == 'replan'
    assert timing['provider_wall_s'] >= 0
    assert timing['envelope_checks_wall_s'] >= 0
    assert timing['total_wall_s'] == pytest.approx(timing['provider_wall_s'] + timing['envelope_checks_wall_s'])


def test_initial_none_is_not_a_successful_decision(tmp_path, monkeypatch):
    class Provider:
        def __call__(self, *_):
            return None
    adapter, _ = envelope(tmp_path, monkeypatch, Provider())
    with pytest.raises(TypeError) as caught:
        adapter(None, ())
    assert caught.value.native_source_provenance['call_timing']['total_wall_s'] >= 0
    assert adapter.calls == 0


def test_failure_time_is_retained_without_changing_exception(tmp_path, monkeypatch):
    error = RuntimeError('native failure')
    class Provider:
        def __call__(self, *_):
            raise error
    adapter, _ = envelope(tmp_path, monkeypatch, Provider())
    with pytest.raises(RuntimeError) as caught:
        adapter(None, ())
    assert caught.value is error
    timing = error.native_source_provenance['call_timing']
    assert timing == adapter.last_call_metrics
    assert timing['provider_wall_s'] >= 0
