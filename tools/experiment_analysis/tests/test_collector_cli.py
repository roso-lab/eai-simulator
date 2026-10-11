from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.experiment_analysis.__main__ import main
from tools.experiment_analysis.collector import collector_handoff, bind_outcomes
from tools.experiment_analysis.engine import analyze
from tools.experiment_analysis.schema import AnalysisError, digest, read_json
from tools.experiment_analysis.tests.test_engine import fixture, rebind


def collect(root, metadata=None):
    d, data = fixture([[[[1]] * 3, [[0]] * 3]])
    plan = BranchPlan(scene_key="synthetic", task_id="control", system_id="system", condition_id="z0", slots=2,
                      repeats=3, metadata=metadata or {"checkpoint_round": "e0"})
    plan = replace(plan, explicit_jobs=tuple(job for job in plan.jobs() if job.branch_type == "fact"))
    store = BranchRunStore.create(root, plan, requested_max_workers=2, execution_scope="protocol_synthetic", adapter="dummy")
    BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=2).run()
    handoff = collector_handoff(root)
    # The diagnostic collector generated authoritative IDs; this fixture
    # projects those IDs once into a design before it creates evaluator output.
    # Production designs are frozen before collection by the task owner.
    grouped = {}
    for attempt in handoff["attempts"]:
        ident = attempt["identity"]
        grouped.setdefault(ident["base_instance_id"], []).append(ident)
    assert len(grouped) == 2
    d["units"] = [dict(id=base, cell_id="c0", members={"main": base}, repeats=sorted(row["repeat_id"] for row in rows),
                       supports=["support"], infrastructure_status="valid") for base, rows in sorted(grouped.items())]
    outcome_rows = []
    for attempt in handoff["attempts"]:
        ident = attempt["identity"]
        raw = deepcopy(data["records"][0])
        raw.update(unit_id=ident["base_instance_id"], base_instance_id=ident["base_instance_id"], repeat_id=ident["repeat_id"],
                   condition_id=ident["condition_id"], values={"outcome": 1 if ident["slot"] == 0 else 0})
        del raw["artifact_digest"]
        outcome_rows.append(dict(branch_id=ident["branch_id"], identity_digest=attempt["identity_digest"], outcome=raw))
    payload = dict(schema="eai.analysis.v1.evaluated_outcomes", design_digest=digest(d),
                   collector_seal_sha256=handoff["seal_sha256"], evaluator=dict(id="test_evaluator", version="v1", source_digest=digest("test")),
                   selection_policy_digest=digest("all_no_retry"), records=outcome_rows)
    return d, payload, handoff


def test_collector_to_evaluator_to_analysis_preserves_synthetic(tmp_path):
    root = tmp_path / "collector"
    d, outcomes, handoff = collect(root)
    before = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    data = bind_outcomes(d, outcomes, root)
    report = analyze(d, data)
    assert report["results"][0]["estimate"] == .5
    assert report["provenance"]["kind"] == "synthetic"
    assert report["publishable"] is False
    assert len(handoff["attempts"]) == 6
    assert handoff["attempt_selection"] == "none_external_preregistered_policy_required"
    after = {p.relative_to(root): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("field", ["base_instance_id", "system_id", "condition_id", "branch_role", "repeat_id", "checkpoint_round"])
def test_external_evaluator_cannot_rebind_branch_identity(tmp_path, field):
    root = tmp_path / "collector"
    d, outcomes, _ = collect(root)
    outcomes["records"][0]["outcome"][field] = "different"
    with pytest.raises(AnalysisError, match="mismatch"):
        bind_outcomes(d, outcomes, root)


def test_outcome_seal_and_identity_hash_are_checked(tmp_path):
    root = tmp_path / "collector"
    d, outcomes, _ = collect(root)
    other = deepcopy(outcomes)
    other["collector_seal_sha256"] = digest("different")
    with pytest.raises(AnalysisError, match="seal"):
        bind_outcomes(d, other, root)
    outcomes["records"][0]["identity_digest"] = digest("different")
    with pytest.raises(AnalysisError, match="misbound"):
        bind_outcomes(d, outcomes, root)


def test_invalid_benchmark_metadata_has_structured_error(tmp_path):
    root = tmp_path / "collector"
    d, outcomes, _ = collect(root, metadata={"checkpoint_round": "e0", "benchmark_run_key": "invalid"})
    with pytest.raises(AnalysisError, match="benchmark_run_key must be an object"):
        bind_outcomes(d, outcomes, root)


def test_handoff_rejects_ledger_tampering(tmp_path):
    root = tmp_path / "collector"
    collect(root)
    with (root / "events.jsonl").open("a") as stream:
        stream.write('{"fake":true}\n')
    with pytest.raises(AnalysisError, match="seal"):
        collector_handoff(root)


def test_handoff_rejects_duplicate_terminal_even_when_resealed(tmp_path):
    root = tmp_path / "collector"
    collect(root)
    path = root / "episodes.jsonl"
    with path.open("a") as stream:
        stream.write(path.read_text().splitlines()[0] + "\n")
    seal = root / "checksums.sha256"
    entries = []
    for line in seal.read_text().splitlines():
        _, name = line.split()
        entries.append(hashlib.sha256((root / name).read_bytes()).hexdigest() + "  " + name)
    seal.write_text("\n".join(entries) + "\n")
    with pytest.raises(AnalysisError, match="multiple terminal"):
        collector_handoff(root)


def test_cli_synthetic_analytic_answers_and_export_hashes(tmp_path):
    out = tmp_path / "synthetic"
    assert main(["synthetic", "--output", str(out), "--resamples", "8"]) == 0
    assert read_json(out / "analytic_checks.json")["passed"] is True
    manifest = read_json(out / "output_manifest.json")
    assert all(hashlib.sha256((out / name).read_bytes()).hexdigest() == sha for name, sha in manifest["files"].items())
    assert "synthetic_validation" in (out / "results.csv").read_text()
    assert "Publishable: false" in (out / "report.md").read_text()
    assert main(["readiness", "--design", str(out / "design.json")]) == 0
    second = tmp_path / "recomputed"
    assert main(["compute", "--design", str(out / "design.json"), "--data", str(out / "data.json"), "--output", str(second)]) == 0
    assert (out / "report.json").read_bytes() == (second / "report.json").read_bytes()
    # A caller cannot overwrite a prior report by reusing its path.
    assert main(["compute", "--design", str(out / "design.json"), "--data", str(out / "data.json"), "--output", str(second)]) == 2


def test_cli_binding_exports_raw_evidence_and_results(tmp_path):
    root = tmp_path / "collector"
    d, outcomes, _ = collect(root)
    design_path, outcomes_path = tmp_path / "design.json", tmp_path / "outcomes.json"
    design_path.write_text(json.dumps(d))
    outcomes_path.write_text(json.dumps(outcomes))
    out = tmp_path / "bound"
    assert main(["bind-outcomes", "--design", str(design_path), "--outcomes", str(outcomes_path),
                 "--run-dir", str(root), "--output", str(out)]) == 0
    assert read_json(out / "data.json")["provenance"]["kind"] == "synthetic"
    assert read_json(out / "report.json")["results"][0]["estimate"] == .5
