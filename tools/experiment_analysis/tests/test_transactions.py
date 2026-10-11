from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.experiment_analysis.__main__ import main
from tools.experiment_analysis.batch import read_sources
from tools.experiment_analysis.bundle import publish_bundle, verify_bundle
from tools.experiment_analysis.collector import collector_handoff
from tools.experiment_analysis.collector_snapshot import parse_seal
from tools.experiment_analysis.engine import analyze
from tools.experiment_analysis.planning import readiness
from tools.experiment_analysis.schema import AnalysisError, read_json
from tools.experiment_analysis.snapshot import SnapshotLimits
from tools.experiment_analysis.synthetic_batch import collect as collect_batch
from tools.experiment_analysis.tests.test_collector_cli import collect
from tools.experiment_analysis.tests.test_engine import fixture


@pytest.fixture(scope="module")
def original(tmp_path_factory):
    root = tmp_path_factory.mktemp("transaction-source") / "run"
    return root, collect(root)


@pytest.fixture
def source(original, tmp_path):
    root, info = original
    return Path(shutil.copytree(root, tmp_path / "run")), deepcopy(info)


def reseal(root):
    seal = root / "checksums.sha256"
    members = parse_seal(seal.read_bytes())
    seal.write_text("".join(hashlib.sha256((root / name).read_bytes()).hexdigest() + "  " + name + "\n"
                            for name in members))


def test_handoff_validation_never_rereads_live_directory(source, monkeypatch):
    import tools.experiment_analysis.collector as module
    root, (_, _, expected) = source
    expected["collector_path"] = str(root)
    validate = module.validate_run
    private_paths = []

    def validate_after_removal(snapshot, *, write):
        assert snapshot != root and write is False
        private_paths.append(snapshot)
        root.rename(root.with_name("retained"))
        return validate(snapshot, write=write)

    monkeypatch.setattr(module, "validate_run", validate_after_removal)
    assert collector_handoff(root) == expected
    assert private_paths and not private_paths[0].exists()
    assert not root.exists()


def test_bind_uses_and_serializes_exactly_one_handoff(source, tmp_path, monkeypatch):
    import tools.experiment_analysis.__main__ as cli
    root, (design, outcomes, _) = source
    (tmp_path / "design.json").write_text(json.dumps(design))
    (tmp_path / "outcomes.json").write_text(json.dumps(outcomes))
    acquire, handoffs = cli.collector_handoff, []

    def acquire_once(path, **kwargs):
        assert not handoffs
        handoff = acquire(path, **kwargs)
        handoffs.append(deepcopy(handoff))
        root.rename(tmp_path / "retained")
        return handoff

    monkeypatch.setattr(cli, "collector_handoff", acquire_once)
    output = tmp_path / "bound"
    assert main(["bind-outcomes", "--design", str(tmp_path / "design.json"), "--outcomes", str(tmp_path / "outcomes.json"),
                 "--run-dir", str(root), "--output", str(output)]) == 0
    assert len(handoffs) == 1
    assert read_json(output / "handoff.json") == handoffs[0]
    assert verify_bundle(output, recompute=True)["status"] == "passed"


@pytest.mark.parametrize("attack", ["root_symlink", "ancestor_symlink", "member_symlink", "fifo", "alias", "unknown_sealed",
                                  "unsealed_mirror", "unsealed_evidence", "duplicate_json", "mirror_nonobject"])
def test_handoff_rejects_unsafe_sources_before_legacy_validation(source, tmp_path, monkeypatch, attack):
    import tools.experiment_analysis.collector as module
    root, _ = source
    selected = root
    if attack == "root_symlink":
        selected = tmp_path / "link"
        selected.symlink_to(root, target_is_directory=True)
    elif attack == "ancestor_symlink":
        (tmp_path / "parent-link").symlink_to(tmp_path, target_is_directory=True)
        selected = tmp_path / "parent-link" / "run"
    elif attack == "member_symlink":
        (root / "plan.json").rename(root / "copy.json")
        (root / "plan.json").symlink_to(root / "copy.json")
    elif attack == "fifo":
        (root / "events.jsonl").unlink()
        os.mkfifo(root / "events.jsonl")
    elif attack in {"alias", "unknown_sealed"}:
        seal = root / "checksums.sha256"
        name = "x/../plan.json" if attack == "alias" else "private.txt"
        with seal.open("a") as stream:
            stream.write("0" * 64 + "  " + name + "\n")
    elif attack.startswith("unsealed"):
        path = root / ("branches/new/ledger.jsonl" if attack == "unsealed_mirror" else "evidence/" + "0" * 64 + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
    elif attack == "duplicate_json":
        path = root / "plan.json"
        path.write_text(path.read_text().replace('"schema":', '"schema": "duplicate", "schema":', 1))
        reseal(root)
    else:
        # Non-object nested JSONL must be rejected, even with valid hashes.
        path = root / "branches/extra/ledger.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("[]\n")
        with (root / "checksums.sha256").open("a") as stream:
            stream.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  branches/extra/ledger.jsonl\n")

    def unexpected_validation(*args, **kwargs):
        pytest.fail("unsafe bytes reached legacy collector validation")

    monkeypatch.setattr(module, "validate_run", unexpected_validation)
    with pytest.raises((AnalysisError, OSError)):
        collector_handoff(selected)


@pytest.mark.parametrize("limit", [SnapshotLimits(max_files=1), SnapshotLimits(max_file_bytes=1), SnapshotLimits(max_total_bytes=1)])
def test_handoff_budget_is_enforced(source, limit):
    with pytest.raises(AnalysisError, match="limit"):
        collector_handoff(source[0], limits=limit)


def test_handoff_detects_mutation_during_copy(source, monkeypatch):
    import tools.experiment_analysis.collector_snapshot as module
    root, _ = source
    copy = module.copy_file

    def mutate_seal(fd, name, target, checksum, budget):
        result = copy(fd, name, target, checksum, budget)
        if name == "episodes.jsonl":
            with (root / "checksums.sha256").open("ab") as stream:
                stream.write(b"\n")
        return result

    monkeypatch.setattr(module, "copy_file", mutate_seal)
    with pytest.raises(AnalysisError, match="snapshot"):
        collector_handoff(root)


def test_batch_sources_share_one_raw_copy_budget(tmp_path):
    root, _, _, sources = collect_batch(tmp_path / "batch")
    manifest = read_json(root / "source_manifest.json")
    counts = []
    for entry in manifest["sources"]:
        counts.append(len(parse_seal((root / entry["run_dir"] / "checksums.sha256").read_bytes())) + 1)
    assert all(n <= max(counts) for n in counts)
    with pytest.raises(AnalysisError, match="limit"):
        read_sources(manifest, base_dir=root, limits=SnapshotLimits(max_files=max(counts)))
    assert read_sources(manifest, base_dir=root, limits=SnapshotLimits(max_files=sum(counts))) == sources
    nested_output = root / manifest["sources"][0]["run_dir"] / "analysis"
    assert main(["assemble", "--design", str(root / "design.json"), "--registration", str(root / "registration.json"),
                 "--sources", str(root / "source_manifest.json"), "--output", str(nested_output)]) == 2
    assert not nested_output.exists()
    (root / "linked-run").symlink_to(root / manifest["sources"][0]["run_dir"], target_is_directory=True)
    manifest["sources"][0]["run_dir"] = "linked-run"
    with pytest.raises(OSError):
        read_sources(manifest, base_dir=root)


@pytest.fixture
def payload():
    design, data = fixture([[[[1]] * 3, [[0]] * 3]])
    return {"design": design, "data": data, "readiness": readiness(design)}, analyze(design, data)


@pytest.mark.parametrize("stage", ["data", "render", "manifest", "verify", "publish", "interrupt", "exit"])
def test_publication_failures_leave_no_target_and_allow_same_path_retry(tmp_path, payload, monkeypatch, stage):
    import tools.experiment_analysis.bundle as bundle
    import tools.experiment_analysis.snapshot as snapshot
    target = tmp_path / "result"
    payloads, report = payload
    failure = KeyboardInterrupt if stage == "interrupt" else SystemExit if stage == "exit" else OSError

    def fail(*args, **kwargs):
        assert not target.exists()
        raise failure("injected publication failure")

    with monkeypatch.context() as patch:
        if stage in {"data", "manifest", "interrupt", "exit"}:
            write = bundle.write_json
            def failing_write(path, value):
                write(path, value)
                if path.name == ("output_manifest.json" if stage == "manifest" else "data.json"):
                    fail()
            patch.setattr(bundle, "write_json", failing_write)
        elif stage == "render":
            patch.setattr(bundle, "render_exports", fail)
        elif stage == "verify":
            patch.setattr(bundle, "verify_bundle", fail)
        else:
            patch.setattr(snapshot, "publish_directory", fail)
        with pytest.raises(failure):
            publish_bundle(target, payloads, report, bundle_type="compute")
    assert not target.exists()
    assert not list(tmp_path.glob(".eai-analysis-build-*"))
    publish_bundle(target, payloads, report, bundle_type="compute")
    assert verify_bundle(target, recompute=True)["status"] == "passed"


@pytest.mark.parametrize("kind", ["empty", "populated", "file", "symlink"])
def test_atomic_publication_preserves_racing_destination(tmp_path, payload, monkeypatch, kind):
    import tools.experiment_analysis.snapshot as module
    target = tmp_path / "result"
    publish = module.publish_directory
    outside = tmp_path / "unrelated"
    outside.write_bytes(b"preserve")

    def race(source, destination):
        assert not destination.exists()
        if kind in {"empty", "populated"}:
            destination.mkdir()
            if kind == "populated":
                (destination / "owned").write_bytes(b"preserve")
        elif kind == "file":
            destination.write_bytes(b"preserve")
        else:
            destination.symlink_to(outside)
        publish(source, destination)

    monkeypatch.setattr(module, "publish_directory", race)
    with pytest.raises(FileExistsError):
        publish_bundle(target, *payload, bundle_type="compute")
    if kind == "empty":
        assert list(target.iterdir()) == []
    elif kind == "populated":
        assert (target / "owned").read_bytes() == b"preserve"
    else:
        assert target.read_bytes() == b"preserve"
    assert outside.read_bytes() == b"preserve"
    assert not list(tmp_path.glob(".eai-analysis-build-*"))


def test_complete_bundle_is_verified_before_it_becomes_visible(tmp_path, payload, monkeypatch):
    import tools.experiment_analysis.bundle as module
    target = tmp_path / "result"
    verify, seen = module.verify_bundle, []

    def check_private(path):
        assert not target.exists()
        assert (path / "output_manifest.json").is_file()
        result = verify(path)
        seen.append(result)
        return result

    monkeypatch.setattr(module, "verify_bundle", check_private)
    publish_bundle(target, *payload, bundle_type="compute")
    assert len(seen) == 1 and target.is_dir()


def test_inconsistent_payload_cannot_be_published(tmp_path, payload):
    payloads, report = payload
    payloads["readiness"]["required_continuations"] += 1
    with pytest.raises(AnalysisError, match="readiness"):
        publish_bundle(tmp_path / "rejected", payloads, report, bundle_type="compute")
    assert not (tmp_path / "rejected").exists()
    assert not list(tmp_path.glob(".eai-analysis-build-*"))


def test_failed_synthetic_analytic_control_does_not_publish(tmp_path, monkeypatch):
    import tools.experiment_analysis.__main__ as cli
    generate = cli.generate
    def incorrect_expected(**kwargs):
        design, data, expected = generate(**kwargs)
        # Change an existing expected scalar independent of fixture names.
        for key, value in expected.items():
            if not isinstance(value, dict):
                expected[key] = -999
        return design, data, expected
    monkeypatch.setattr(cli, "generate", incorrect_expected)
    target = tmp_path / "synthetic"
    assert main(["synthetic", "--output", str(target), "--resamples", "4"]) == 1
    assert not target.exists()


def test_handoff_cli_limits_reject_without_output(source, tmp_path):
    assert main(["collector-handoff", "--run-dir", str(source[0]), "--max-files", "1",
                 "--output", str(tmp_path / "rejected")]) == 2
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("command", ["collector-handoff", "bind-outcomes"])
def test_cli_cannot_publish_into_readonly_collector(source, tmp_path, command):
    root, (design, outcomes, _) = source
    extra = []
    if command == "bind-outcomes":
        for name, value in (("design", design), ("outcomes", outcomes)):
            path = tmp_path / (name + ".json")
            path.write_text(json.dumps(value))
            extra.extend(["--" + name, str(path)])
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert main([command, "--run-dir", str(root), "--output", str(root / "analysis"), *extra]) == 2
    assert not (root / "analysis").exists()
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_unrecoverable_process_exit_leaves_no_partial_target(tmp_path, payload):
    target = tmp_path / "result"
    # Exit from the validated private stage without unwinding Python contexts.
    # Hidden staging may remain, but the public name remains free for retry.
    script = """
import os, sys
from tools.experiment_analysis.snapshot import staged_directory
with staged_directory(sys.argv[1]) as stage:
    (stage / 'partial').write_bytes(b'partial')
    os._exit(23)
"""
    result = subprocess.run([sys.executable, "-c", script, str(target)], check=False, capture_output=True)
    assert result.returncode == 23, result.stderr
    assert not target.exists()
    orphan = list(tmp_path.glob(".eai-analysis-build-*"))
    assert len(orphan) == 1
    publish_bundle(target, *payload, bundle_type="compute")
    assert verify_bundle(target, recompute=True)["status"] == "passed"
    assert orphan[0].is_dir()  # Never delete another process's staging on retry.
