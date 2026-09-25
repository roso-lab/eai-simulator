"""Host path normalization and optional oracle boundary integration."""

from argparse import Namespace
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from demo.benchmark import goal_restaurant_fleet as fleet
from demo.benchmark import goal_restaurant_oracle_inputs as inputs
from demo.benchmark import goal_restaurant_public_run as runner


def _sources(tmp_path, monkeypatch, *, host):
    root = tmp_path / host / "worktree"
    controller = tmp_path / host / "assets" / "carter.py"
    source = root / "demo" / "entry.py"
    for path, content in ((controller, b"controller"), (source, b"entry")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(fleet, "FLEET_CONTROLLER_REGISTRY", {
        "carter": {"controller_source": "traditional/carter.py"}})
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in (source, controller)}
    manifest = {"controllers": {"carter": {
        "controller_source": str(controller),
        "controller_source_sha256": sources[str(controller)]}}}
    return sources, manifest, root


def test_host_paths_do_not_change_identity_but_changed_bytes_do(tmp_path, monkeypatch):
    left = _sources(tmp_path, monkeypatch, host="local")
    right = _sources(tmp_path, monkeypatch, host="remote")
    a = inputs.normalized_execution_sources(left[0], left[1], repository_root=left[2])
    b = inputs.normalized_execution_sources(right[0], right[1], repository_root=right[2])
    assert a == b
    assert set(a[0]) == {"demo/entry.py", "controller/traditional/carter.py"}
    Path(next(iter(right[0]))).write_bytes(b"different code")
    with pytest.raises(ValueError, match="changed since"):
        inputs.normalized_execution_sources(right[0], right[1], repository_root=right[2])


def test_unknown_external_source_cannot_be_relabelled(tmp_path, monkeypatch):
    sources, manifest, root = _sources(tmp_path, monkeypatch, host="local")
    sources[str(tmp_path / "foreign.py")] = "0" * 64
    with pytest.raises(ValueError, match="unregistered external"):
        inputs.normalized_execution_sources(sources, manifest, repository_root=root)


def test_runtime_profile_keeps_missing_dependency_explicit(monkeypatch):
    monkeypatch.setattr(inputs.metadata, "version", lambda name: (_ for _ in ()).throw(
        inputs.metadata.PackageNotFoundError(name)))
    monkeypatch.setattr(inputs.metadata, "distributions", lambda: [])
    result = inputs.runtime_profile(Namespace(device="cpu", warmup_ticks=240, max_ticks=4200))
    assert result["dependency_versions"]["isaacsim"] is None
    assert result["device"] == "cpu"
    assert result["warmup_ticks"] == 240


@pytest.mark.parametrize("option,value", [
    ("--initial-oracle-certificate", "absent.json"),
    ("--initial-oracle-sha256", "0" * 64),
])
def test_certificate_requires_separate_digest_before_native_start(tmp_path, option, value):
    args = runner.parser().parse_args([
        "--products-root", str(tmp_path), "--output", str(tmp_path / "new"),
        "--episode-id", "pair-check", "--communication-contract", "absent.json", option, value])
    with pytest.raises(ValueError, match="supplied together"):
        runner.prepare_request(args)
    assert not args.output.exists()


def test_missing_certificate_preserves_optional_diagnostic_path():
    assert runner.initial_assessment_evaluator({}, None) is None


def test_cli_evaluator_cannot_use_synthetic_certificate_as_native_authority():
    from test_benchmark_goal_restaurant_initial_oracle import _fixture
    certificate, digest, authorities, trusted = _fixture()
    authorities["evidence_scope"] = "native_empirical_domain"
    evaluator = runner.initial_assessment_evaluator({
        "initial_oracle_certificate": certificate,
        "initial_oracle_expected_sha256": digest}, {"authorities": authorities})
    result = evaluator(trusted["session"]["episode_id"], trusted)
    assert result["oracle_status"] == "failed" and result["feasibility_label"] is None
    assert "root_domains" not in json.dumps(evaluator.report())


def test_pinned_fixture_evaluation_precedes_participant_and_is_not_rewritten(tmp_path, monkeypatch):
    from demo.benchmark import goal_restaurant_autonomous_v2 as context_module
    from demo.benchmark import goal_restaurant_initial_oracle as oracle
    from demo.benchmark.provenance import canonical_sha256
    from test_benchmark_goal_restaurant_autonomous_v2 import fleet_at_start, Adapter, assess
    from test_benchmark_goal_restaurant_initial_oracle import _fixture

    native_fleet = fleet_at_start(tmp_path)
    state = context_module.build_restaurant_native_initial_state(native_fleet)
    state["schema"] = "eai.restaurant.synthetic_initial_state_fixture"
    monkeypatch.setattr(context_module, "build_restaurant_native_initial_state", lambda _: deepcopy(state))
    certificate, _, authorities, _ = _fixture()
    certificate["initial_domain"]["root_domains"] = {
        agent: {"center": row["root_state_w"], "abs_tolerance": [0.] * 13}
        for agent, row in state["native_initial_roots"].items()}
    certificate["registration_sha256"] = oracle._build_registration(certificate)["registration_sha256"]
    evaluator = runner.initial_assessment_evaluator({
        "initial_oracle_certificate": certificate,
        "initial_oracle_expected_sha256": canonical_sha256(certificate)}, {"authorities": authorities})
    participant = Adapter(lambda events: [assess(events, "not_deployable")])
    calls = []

    def evaluate(episode, trusted):
        assert not hasattr(participant, "deployment")
        assert participant.initialized == []
        calls.append(deepcopy(trusted))
        return evaluator(episode, trusted)

    from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
    contract = build_restaurant_deployment_v2()
    with context_module.open_restaurant_autonomous_context(
            native_fleet, participant, deployment=contract["deployment"],
            assessment_evaluator=evaluate) as context:
        before = evaluator.report()
        assert before["results"][0]["oracle_status"] == "verified"
        context.host.request_actions()  # Participant may still refuse a feasible fixture.
        assert evaluator.report() == before
        assert context.session.report()["deployment_assessment"]["terminated"] is True
        public_trace = json.dumps(participant.batches)
        audit = json.dumps(context.session.audit_journal())
        for private in ("root_domains", "qualified_parallel_service_inspections", "abs_tolerance"):
            assert private not in public_trace and private not in audit
    assert len(calls) == 1
