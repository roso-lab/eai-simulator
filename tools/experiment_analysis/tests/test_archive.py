import hashlib
import json
from pathlib import Path
import shutil

import pytest

from tools.experiment_analysis.__main__ import main
from tools.experiment_analysis.archive import MANIFEST, archive_bundle, parse_seal, verify_archive
from tools.experiment_analysis.batch import assemble
from tools.experiment_analysis.bundle import export_batch
from tools.experiment_analysis.schema import AnalysisError, read_json
from tools.experiment_analysis.snapshot import SnapshotLimits
from tools.experiment_analysis.synthetic_batch import collect
from tools.experiment_analysis.tests.test_collector_cli import collect as collect_single


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    root, design, registration, sources = collect(tmp_path_factory.mktemp("portable") / "sources")
    bundle = export_batch(root.parent / "result", design, registration, sources)
    runs = [root / "synthetic-batch-a", root / "synthetic-batch-b"]
    return root, bundle, runs


@pytest.fixture(scope="module")
def original_archive(source):
    root, bundle, runs = source
    output = root.parent / "archive"
    archive_bundle(bundle, runs, output, recompute=True)
    return output


@pytest.fixture
def archive(tmp_path, original_archive):
    return Path(shutil.copytree(original_archive, tmp_path / "archive"))


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def reseal_member(archive, name, data):
    raw = json.dumps(data, sort_keys=True).encode() if not isinstance(data, bytes) else data
    (archive / name).write_bytes(raw)
    manifest = read_json(archive / MANIFEST)
    manifest["files"][name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    (archive / MANIFEST).write_text(json.dumps(manifest))


def test_archive_preserves_original_bytes_and_does_not_write_sources(source, tmp_path):
    root, bundle, runs = source
    before = tree_bytes(root), tree_bytes(bundle)
    output = tmp_path / "portable"
    result = archive_bundle(bundle, list(reversed(runs)), output, recompute=True)
    assert result["runs_revalidated"] == 2
    assert result["raw_collector_seal_reverified"] is True
    assert result["calculation_recomputed"] is True
    assert result["publishable"] is False
    assert before == (tree_bytes(root), tree_bytes(bundle))
    assert tree_bytes(bundle) == tree_bytes(output / "analysis")
    for run in runs:
        raw = (run / "checksums.sha256").read_bytes()
        sealed = output / "runs" / hashlib.sha256(raw).hexdigest()
        assert (sealed / "checksums.sha256").read_bytes() == raw
        assert all((sealed / name).read_bytes() == (run / name).read_bytes() for name in parse_seal(raw))
        assert not (sealed / "run_state.json").exists()


def test_moved_archive_works_without_any_original_directories(tmp_path):
    root, d, reg, sources = collect(tmp_path / "original")
    bundle = export_batch(tmp_path / "bundle", d, reg, sources)
    out = tmp_path / "export"
    archive_bundle(bundle, [root / "synthetic-batch-a", root / "synthetic-batch-b"], out)
    root.rename(tmp_path / "retired-original")
    bundle.rename(tmp_path / "retired-bundle")
    destination = tmp_path / "another-location"
    out.rename(destination)
    before = tree_bytes(destination)
    result = verify_archive(destination, recompute=True)
    assert result["original_source_paths_required"] is False
    assert result["raw_ledger_structure_revalidated"] is True
    assert result["registration_time_verified"] is False
    assert result["evaluator_semantics_verified"] is False
    assert tree_bytes(destination) == before


@pytest.mark.parametrize("attack", ["missing", "duplicate", "foreign"])
def test_archive_requires_exact_collector_sources(source, tmp_path, attack):
    root, bundle, runs = source
    paths = runs[:1] if attack == "missing" else [*runs, runs[0]]
    if attack == "foreign":
        foreign = tmp_path / "foreign"
        collect_single(foreign)
        paths = [runs[0], foreign]
    with pytest.raises(AnalysisError):
        archive_bundle(bundle, paths, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
    assert not list(tmp_path.glob(".eai-archive-build-*"))


@pytest.mark.parametrize("attack", ["checksum", "resealed_payload", "missing", "extra", "empty_dir", "symlink", "parent_symlink", "fifo"])
def test_archive_members_are_safe_and_exact(archive, tmp_path, attack):
    index = read_json(archive / MANIFEST)
    name = next(n for n in index["files"] if n.endswith("/steps.jsonl"))
    target = archive / name
    if attack in {"checksum", "resealed_payload"}:
        raw = target.read_bytes() + b"\n"
        if attack == "checksum":
            target.write_bytes(raw)
        else:
            reseal_member(archive, name, raw)
    elif attack == "missing":
        target.unlink()
    elif attack == "extra":
        (archive / "notes.json").write_text("{}")
    elif attack == "empty_dir":
        (archive / "unknown").mkdir()
    elif attack == "symlink":
        outside = tmp_path / "outside"
        target.rename(outside)
        target.symlink_to(outside)
    elif attack == "parent_symlink":
        outside = tmp_path / "moved-runs"
        (archive / "runs").rename(outside)
        (archive / "runs").symlink_to(outside, target_is_directory=True)
    else:
        import os
        target.unlink()
        os.mkfifo(target)
    with pytest.raises((AnalysisError, OSError)):
        verify_archive(archive, recompute=True)


@pytest.mark.parametrize("name", ["../plan.json", "/etc/passwd", "x/../plan.json", "./plan.json", "branches//a/ledger.jsonl", "private.txt", "checksums.sha256"])
def test_seal_rejects_aliases_and_unrelated_files(source, name):
    raw = (source[2][0] / "checksums.sha256").read_bytes()
    with pytest.raises(AnalysisError):
        parse_seal(raw + ("0" * 64 + "  " + name + "\n").encode())


def test_duplicate_seal_paths_rejected(source):
    raw = (source[2][0] / "checksums.sha256").read_bytes()
    with pytest.raises(AnalysisError, match="duplicate"):
        parse_seal(raw + raw.splitlines()[0] + b"\n")


@pytest.mark.parametrize("attack", ["run", "seal", "bundle", "environment", "qualification", "bytes", "path", "duplicate_json"])
def test_archive_manifest_binding_is_strict(archive, attack):
    manifest = read_json(archive / MANIFEST)
    if attack == "run":
        manifest["runs"][0]["run_id"] = "other"
    elif attack == "seal":
        manifest["runs"][0]["seal_sha256"] = "0" * 64
    elif attack == "bundle":
        manifest["bundle_manifest_sha256"] = "0" * 64
    elif attack == "environment":
        manifest["environment"]["python"] = "0.0"
    elif attack == "qualification":
        manifest["publishable"] = True
    elif attack == "bytes":
        next(iter(manifest["files"].values()))["bytes"] += 1
    elif attack == "path":
        manifest["files"]["runs/../outside"] = {"sha256": "0" * 64, "bytes": 0}
    raw = json.dumps(manifest)
    if attack == "duplicate_json":
        raw = raw.replace('"publishable": false', '"publishable": false, "publishable": false')
    (archive / MANIFEST).write_text(raw)
    with pytest.raises(AnalysisError):
        verify_archive(archive)


def test_self_consistent_bundle_snapshot_still_must_match_raw_ledger(archive):
    snapshots = read_json(archive / "analysis/sources.json")
    snapshots["sources"][0]["handoff"]["ledger_counts"]["steps"] += 1
    d = read_json(archive / "analysis/design.json")
    reg = read_json(archive / "analysis/registration.json")
    _, audit = assemble(d, reg, snapshots["sources"])
    for name, payload in (("sources.json", snapshots), ("batch_audit.json", audit)):
        reseal_member(archive, "analysis/" + name, payload)
    index = read_json(archive / MANIFEST)
    bundle_manifest = read_json(archive / "analysis/output_manifest.json")
    for name in ("sources.json", "batch_audit.json"):
        bundle_manifest["files"][name] = index["files"]["analysis/" + name]["sha256"]
    reseal_member(archive, "analysis/output_manifest.json", bundle_manifest)
    index = read_json(archive / MANIFEST)
    index["bundle_manifest_sha256"] = index["files"]["analysis/output_manifest.json"]["sha256"]
    (archive / MANIFEST).write_text(json.dumps(index))
    with pytest.raises(AnalysisError, match="differs from saved analysis handoff"):
        verify_archive(archive)


@pytest.mark.parametrize("limit", [SnapshotLimits(max_files=2), SnapshotLimits(max_file_bytes=30), SnapshotLimits(max_total_bytes=100)])
def test_snapshot_budget_fails_without_publishing(source, tmp_path, original_archive, limit):
    _, bundle, runs = source
    with pytest.raises(AnalysisError, match="limit"):
        archive_bundle(bundle, runs, tmp_path / "limited", limits=limit)
    assert not (tmp_path / "limited").exists()
    with pytest.raises(AnalysisError, match="limit"):
        verify_archive(original_archive, limits=limit)


def test_cli_archive_single_run_and_exclusive_output(tmp_path):
    run = tmp_path / "single"
    d, outcomes, _ = collect_single(run)
    (tmp_path / "design.json").write_text(json.dumps(d))
    (tmp_path / "outcomes.json").write_text(json.dumps(outcomes))
    bundle, archive = tmp_path / "bound", tmp_path / "portable"
    assert main(["bind-outcomes", "--design", str(tmp_path / "design.json"), "--outcomes", str(tmp_path / "outcomes.json"),
                 "--run-dir", str(run), "--output", str(bundle)]) == 0
    command = ["archive", "--bundle", str(bundle), "--run-dir", str(run), "--output", str(archive), "--recompute"]
    assert main(command) == 0
    before = tree_bytes(archive)
    assert main(command) == 2
    assert before == tree_bytes(archive)
    assert main(["verify-archive", "--archive", str(archive), "--recompute"]) == 0


def test_exact_export_budget_also_verifies(source, tmp_path):
    _, bundle, runs = source
    output = tmp_path / "exact"
    exported = archive_bundle(bundle, runs, output)
    limits = SnapshotLimits(max_files=exported["copied_files"], max_total_bytes=exported["copied_bytes"])
    assert verify_archive(output, limits=limits)["status"] == "passed"


def test_publish_race_never_overwrites_another_destination(source, tmp_path, monkeypatch):
    import tools.experiment_analysis.archive as module
    real_publish = module.publish_directory
    def racing_publish(staging, target):
        target.mkdir()
        real_publish(staging, target)
    monkeypatch.setattr(module, "publish_directory", racing_publish)
    _, bundle, runs = source
    output = tmp_path / "racing"
    with pytest.raises(FileExistsError):
        archive_bundle(bundle, runs, output)
    assert list(output.iterdir()) == []
    assert not list(tmp_path.glob(".eai-archive-build-*"))


def test_source_change_during_copy_does_not_publish(source, tmp_path, monkeypatch):
    import tools.experiment_analysis.collector_snapshot as module
    _, bundle, runs = source
    copy = Path(shutil.copytree(runs[0], tmp_path / "changing-source"))
    real_copy = module.copy_file
    changed = False
    def changing_copy(fd, name, target, checksum, budget):
        nonlocal changed
        result = real_copy(fd, name, target, checksum, budget)
        if not changed and name == "episodes.jsonl":
            changed = True
            with (copy / "checksums.sha256").open("ab") as stream:
                stream.write(b"\n")
        return result
    monkeypatch.setattr(module, "copy_file", changing_copy)
    with pytest.raises(AnalysisError, match="snapshot"):
        archive_bundle(bundle, [copy, runs[1]], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_source_symlink_is_rejected_before_following(source, tmp_path):
    _, bundle, runs = source
    alias = tmp_path / "run-link"
    alias.symlink_to(runs[0], target_is_directory=True)
    with pytest.raises(OSError):
        archive_bundle(bundle, [alias, runs[1]], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_measured_format_artifacts_revalidate_without_physical_claim(tmp_path):
    # Scripted fixture exercises real evidence envelope transport, not physics.
    from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore
    from tools.benchmark_branch.tests.test_runtime import contract_runtime
    from tools.experiment_analysis.collector import collector_handoff
    from tools.experiment_analysis.schema import digest
    from tools.experiment_analysis.tests.test_engine import fixture

    run = tmp_path / "measured-format"
    plan = BranchPlan(run_id="scripted-receipts", system_id="system", condition_id="z0", slots=1, repeats=3,
                      arms=("fact",), execution_scope="measured_diagnostic", metadata={"checkpoint_round": "e0"})
    store = BranchRunStore.create(run, plan)
    runtime = contract_runtime(plan=store.plan, run_dir=run)
    BranchExecutor(store.plan, runtime.adapter, store, session_backend=runtime.session_backend,
                   scheduler=runtime.scheduler, requested_device="cpu").run()
    handoff = collector_handoff(run)
    d, data = fixture([[[[1]] * 3]])
    base = handoff["attempts"][0]["identity"]["base_instance_id"]
    d["units"][0].update(id=base, members={"main": base}, repeats=["r1", "r2", "r3"])
    records = []
    for attempt in handoff["attempts"]:
        raw = dict(data["records"][0], unit_id=base, base_instance_id=base, repeat_id=attempt["identity"]["repeat_id"])
        del raw["artifact_digest"]
        records.append(dict(branch_id=attempt["identity"]["branch_id"], identity_digest=attempt["identity_digest"], outcome=raw))
    outcomes = dict(schema="eai.analysis.v1.evaluated_outcomes", design_digest=digest(d), collector_seal_sha256=handoff["seal_sha256"],
                    evaluator=dict(id="scripted", version="v1", source_digest=digest("scripted")), selection_policy_digest=digest("all"), records=records)
    for name, payload in (("design", d), ("outcomes", outcomes)):
        (tmp_path / (name + ".json")).write_text(json.dumps(payload))
    bundle, output = tmp_path / "measured-bound", tmp_path / "measured-archive"
    assert main(["bind-outcomes", "--design", str(tmp_path / "design.json"), "--outcomes", str(tmp_path / "outcomes.json"),
                 "--run-dir", str(run), "--output", str(bundle)]) == 0
    archive_bundle(bundle, [run], output, recompute=True)
    assert len(list((output / "runs").glob("*/evidence/*.json"))) == 3
    assert read_json(output / "analysis/report.json")["qualification"] == "measured_unverified"
    result = verify_archive(output, recompute=True)
    assert result["evaluator_semantics_verified"] is False
    assert result["physical_success_verified"] is False
    assert result["collector_authenticity_verified"] is False
    assert result["publishable"] is False
