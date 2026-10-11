import hashlib
import json

import pytest

from tools.experiment_analysis.__main__ import main
from tools.experiment_analysis.bundle import export_batch, render_exports, verify_bundle
from tools.experiment_analysis.schema import AnalysisError, digest, read_json
from tools.experiment_analysis.tests.test_batch import collected, inputs


@pytest.fixture
def bundle(tmp_path, inputs):
    design, registration, sources = inputs
    return export_batch(tmp_path / "bundle", design, registration, sources)


def reseal(root, name, value):
    path = root / name
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    manifest = read_json(root / "output_manifest.json")
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "output_manifest.json").write_text(json.dumps(manifest))


def test_offline_bundle_replays_selection_and_recomputes_without_source_paths(tmp_path, inputs):
    d, reg, sources = inputs
    # Original collectors need not be available; selection is replayed from
    # preserved snapshots. Their live seals are explicitly NOT reverified.
    for source in sources:
        source["handoff"]["collector_path"] = "/nonexistent/collector"
    bundle = export_batch(tmp_path / "offline", d, reg, sources)
    original = {p.name: p.read_bytes() for p in bundle.iterdir()}
    result = verify_bundle(bundle, recompute=True)
    assert result["selection_replayed"] is True
    assert result["calculation_recomputed"] is True
    assert result["raw_collector_seal_reverified"] is False
    assert result["registration_time_verified"] is False
    assert original == {p.name: p.read_bytes() for p in bundle.iterdir()}


@pytest.mark.parametrize("field", ["publishable", "qualification"])
def test_resealed_report_cannot_promote_any_result(bundle, field):
    report = read_json(bundle / "report.json")
    report["results"][0][field] = True if field == "publishable" else "paper_verified"
    report["results_digest"] = digest(report["results"])
    reseal(bundle, "report.json", report)
    with pytest.raises(AnalysisError, match="qualification"):
        verify_bundle(bundle)


@pytest.mark.parametrize("member", ["data.json", "batch_audit.json", "results.csv", "report.md"])
def test_raw_tampering_fails_checksum(bundle, member):
    with (bundle / member).open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(AnalysisError, match="checksum"):
        verify_bundle(bundle)


def test_resealed_selection_audit_is_replayed(bundle):
    audit = read_json(bundle / "batch_audit.json")
    audit["observations"][0]["attempts"][0]["disposition"] = "selected"
    reseal(bundle, "batch_audit.json", audit)
    with pytest.raises(AnalysisError, match="selection or audit"):
        verify_bundle(bundle)


def test_resealed_derived_csv_cannot_disagree_with_report(bundle):
    path = bundle / "results.csv"
    path.write_bytes(path.read_bytes().replace(b"false", b"true").replace(b"False", b"True"))
    manifest = read_json(bundle / "output_manifest.json")
    manifest["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (bundle / "output_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(AnalysisError, match="derived export"):
        verify_bundle(bundle)


def test_recompute_detects_consistently_resealed_wrong_estimate(bundle):
    report = read_json(bundle / "report.json")
    report["results"][0]["estimate"] = .99
    report["results_digest"] = digest(report["results"])
    reseal(bundle, "report.json", report)
    for name, content in render_exports(report).items():
        (bundle / name).write_bytes(content.encode())
    manifest = read_json(bundle / "output_manifest.json")
    for name in render_exports(report):
        manifest["files"][name] = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
    (bundle / "output_manifest.json").write_text(json.dumps(manifest))
    assert verify_bundle(bundle)["calculation_recomputed"] is False
    with pytest.raises(AnalysisError, match="recomputed report differs"):
        verify_bundle(bundle, recompute=True)


@pytest.mark.parametrize("field", ["python", "source_files"])
def test_recompute_requires_exact_environment(bundle, field):
    report = read_json(bundle / "report.json")
    env = report["analysis_environment"]
    if field == "python":
        env["python"] = "0.0"
    else:
        env["source_files"]["engine.py"] = digest("other_code")
        env["code_digest"] = digest(env["source_files"])
    reseal(bundle, "report.json", report)
    with pytest.raises(AnalysisError, match="environment differs"):
        verify_bundle(bundle, recompute=True)


@pytest.mark.parametrize("attack", ["extra", "missing", "traversal", "symlink", "directory", "duplicate_json", "nonfinite"])
def test_bundle_file_boundary_is_strict(bundle, tmp_path, attack):
    if attack == "extra":
        (bundle / "extra.json").write_text("{}")
    elif attack == "missing":
        (bundle / "data.json").unlink()
    elif attack == "traversal":
        manifest = read_json(bundle / "output_manifest.json")
        manifest["files"]["../outside.json"] = digest("outside")
        (bundle / "output_manifest.json").write_text(json.dumps(manifest))
    elif attack == "symlink":
        target = tmp_path / "outside-data.json"
        (bundle / "data.json").rename(target)
        (bundle / "data.json").symlink_to(target)
    elif attack == "directory":
        (bundle / "data.json").unlink()
        (bundle / "data.json").mkdir()
    elif attack == "duplicate_json":
        path = bundle / "output_manifest.json"
        path.write_text(path.read_text().replace('"publishable": false', '"publishable": false, "publishable": false'))
    else:
        path = bundle / "output_manifest.json"
        path.write_text(path.read_text().replace('"publishable": false', '"publishable": NaN'))
    with pytest.raises((AnalysisError, OSError, ValueError)):
        verify_bundle(bundle, recompute=True)


def test_output_is_exclusive_and_rejected_input_creates_no_bundle(tmp_path, inputs):
    d, reg, sources = inputs
    root = export_batch(tmp_path / "exclusive", d, reg, sources)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        export_batch(root, d, reg, sources)
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}
    sources[0]["outcomes"]["selection_policy_digest"] = digest("changed")
    with pytest.raises(AnalysisError):
        export_batch(tmp_path / "invalid", d, reg, sources)
    assert not (tmp_path / "invalid").exists()


def test_cli_freeze_assemble_verify_and_synthetic_controls(tmp_path, collected):
    root, d, reg, _ = collected
    schedule_path = tmp_path / "schedule.json"
    schedule_path.write_text(json.dumps(reg["schedule"]))
    frozen = tmp_path / "frozen"
    assert main(["freeze", "--design", str(root / "design.json"), "--schedule", str(schedule_path), "--output", str(frozen)]) == 0
    assert read_json(frozen / "registration.json") == reg
    merged = tmp_path / "merged"
    assert main(["assemble", "--design", str(root / "design.json"), "--registration", str(frozen / "registration.json"),
                 "--sources", str(root / "source_manifest.json"), "--output", str(merged)]) == 0
    assert main(["verify", "--bundle", str(merged), "--recompute"]) == 0
    synthetic = tmp_path / "synthetic"
    assert main(["synthetic", "--output", str(synthetic), "--instances", "40", "--resamples", "4"]) == 0
    assert main(["verify", "--bundle", str(synthetic), "--recompute"]) == 0
    assert main(["synthetic-batch", "--output", str(tmp_path / "batch-control")]) == 0
