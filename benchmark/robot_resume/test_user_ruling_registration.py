from __future__ import annotations

import contextlib
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from register_historical_evidence import ROOT, register


HERE = Path(__file__).resolve().parent
ROSTER = HERE / "robot_runtime_roster.v1.json"
RULING_RECORD = "benchmark/robot_resume/RUNTIME_RULING_20261026.zh-CN.md"
SCRATCH_FILES = ("register_historical_evidence.py", "historical_runtime_receipt.py",
                 "runtime_qualification_receipt.py", "RUNTIME_RULING_20261026.zh-CN.md")
MODULE_NAMES = ("historical_runtime_receipt", "runtime_qualification_receipt",
                "register_historical_evidence")


def _rows() -> dict[str, dict]:
    return {row["instance_id"]: row
            for row in json.loads(ROSTER.read_text(encoding="utf-8"))["robots"]}


def _record_text() -> str:
    return (HERE / "RUNTIME_RULING_20261026.zh-CN.md").read_text(encoding="utf-8")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def _scratch_repo(tmp_path: Path, *, ruling_text: str | None = None,
                  mutate_after_commit=None) -> tuple[Path, str]:
    """Minimal git repo whose only payload is the ruling record.

    All git operations happen inside pytest's tmp_path; the real repository is
    never written to.  ``mutate_after_commit`` tampers the record after the
    commit so provenance rechecks can be exercised against real history.
    """
    scratch = tmp_path / "scratch-repo"
    mod_dir = scratch / "benchmark" / "robot_resume"
    mod_dir.mkdir(parents=True)
    for name in SCRATCH_FILES:
        shutil.copy2(HERE / name, mod_dir / name)
    if ruling_text is not None:
        (mod_dir / "RUNTIME_RULING_20261026.zh-CN.md").write_text(ruling_text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(scratch)], check=True, capture_output=True)
    _git(scratch, "add", "-A")
    _git(scratch, "-c", "user.email=scratch@test.local", "-c", "user.name=scratch",
         "commit", "-qm", "scratch: 2026-10-26 runtime ruling record")
    commit = _git(scratch, "rev-parse", "HEAD").stdout.strip()
    if mutate_after_commit is not None:
        mutate_after_commit(mod_dir / "RUNTIME_RULING_20261026.zh-CN.md")
    return scratch, commit


@contextlib.contextmanager
def _scratch_modules(scratch: Path):
    """Import the scratch copy of the robot_resume modules, isolated."""
    mod_dir = scratch / "benchmark" / "robot_resume"
    saved = {name: sys.modules.pop(name) for name in MODULE_NAMES if name in sys.modules}
    sys.path.insert(0, str(mod_dir))
    try:
        hist = importlib.import_module("historical_runtime_receipt")
        rt = importlib.import_module("runtime_qualification_receipt")
        reg = importlib.import_module("register_historical_evidence")
        yield hist, rt, reg
    finally:
        sys.path.remove(str(mod_dir))
        for name in MODULE_NAMES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def _build_ruling_receipt(reg, scratch: Path, ctx, row: dict) -> dict:
    return reg.make_historical_receipt(
        roster_row=row, receipt_id=f"{row['instance_id']}.user-ruling.20261026",
        authority=ctx["authority"], platform_scope=reg._scope(row),
        sources=[ctx["source"]], checks=ctx["checks"], root=scratch)


def test_partial_ruling_arguments_are_rejected():
    with pytest.raises(ValueError, match="both ruling_commit and ruling_record"):
        register(ROSTER, ruling_commit="a" * 40)
    with pytest.raises(ValueError, match="both ruling_commit and ruling_record"):
        register(ROSTER, ruling_record=RULING_RECORD)


def test_ruling_receipts_for_all_twelve_build_and_validate(tmp_path):
    scratch, commit = _scratch_repo(tmp_path)
    rows = _rows()
    with _scratch_modules(scratch) as (_hist, rt, reg):
        ctx = reg._ruling_context(commit, RULING_RECORD)
        assert ctx["authority"]["decision"] == "user_runtime_unknown_ruling_accepted"
        assert ctx["source"]["content_assertions"] == list(reg.RULING_INSTANCE_IDS)
        for instance_id in reg.RULING_INSTANCE_IDS:
            row = rows[instance_id]
            receipt = _build_ruling_receipt(reg, scratch, ctx, row)
            assert receipt["receipt_id"] == f"{instance_id}.user-ruling.20261026"
            assert receipt["runtime_qualified"] is True
            # The dispatching validator accepts it and binds it to the roster row.
            assert rt.validate_receipt(receipt, roster_row=row)["runtime_qualified"] is True
            assert receipt["formal_gates"] == {"physical_qualified": False,
                                               "rankable": False,
                                               "paper_score_eligible": False}


def test_tampered_ruling_record_fails_receipt_validation(tmp_path):
    scratch, commit = _scratch_repo(tmp_path)
    rows = _rows()
    with _scratch_modules(scratch) as (hist, _rt, reg):
        ctx = reg._ruling_context(commit, RULING_RECORD)
        receipt = _build_ruling_receipt(reg, scratch, ctx, rows["hc-rt-002"])
        (scratch / RULING_RECORD).write_text(_record_text() + "\n篡改追加\n", encoding="utf-8")
        with pytest.raises(hist.HistoricalReceiptError, match="does not match file bytes"):
            hist.validate_historical_receipt(receipt, roster_row=rows["hc-rt-002"], root=scratch)


def test_ruling_commit_without_record_bytes_fails_closed(tmp_path):
    # The commit exists, the record exists, but the commit's blob differs from
    # the working-tree bytes: provenance must be refused, not assumed.
    scratch, commit = _scratch_repo(
        tmp_path, mutate_after_commit=lambda path: path.write_text(
            path.read_text(encoding="utf-8") + "\npost-commit edit\n", encoding="utf-8"))
    rows = _rows()
    with _scratch_modules(scratch) as (hist, _rt, reg):
        ctx = reg._ruling_context(commit, RULING_RECORD)
        with pytest.raises(hist.HistoricalReceiptError, match="source_commit blob does not match"):
            _build_ruling_receipt(reg, scratch, ctx, rows["hc-rt-003"])


@pytest.mark.parametrize("kind", ["missing", "extra", "substituted"])
def test_inexact_ruling_id_list_is_rejected(tmp_path, kind):
    text = _record_text()
    if kind == "missing":
        text = text.replace("hc-rt-024", "")
    elif kind == "extra":
        text = text + "\n| hc-rt-028 | cf2x | 追加 |\n"
    else:
        text = text.replace("hc-rt-008", "hc-rt-001")
    scratch, commit = _scratch_repo(tmp_path, ruling_text=text)
    with _scratch_modules(scratch) as (_hist, _rt, reg):
        with pytest.raises(ValueError, match="does not exactly match the approved twelve"):
            reg._ruling_context(commit, RULING_RECORD)


def test_validator_reports_registration_state_honestly(tmp_path):
    report, promoted = register(ROSTER)
    base_out = tmp_path / "base.validation.json"
    subprocess.run([sys.executable, str(HERE / "validate_runtime_roster.py"),
                    "--roster", str(ROSTER), "--output", str(base_out)],
                   check=True, capture_output=True)
    assert json.loads(base_out.read_text(encoding="utf-8"))["runtime_qualification_performed"] is False
    promoted_path = tmp_path / "promoted.json"
    promoted_path.write_text(json.dumps(promoted, ensure_ascii=False), encoding="utf-8")
    promoted_out = tmp_path / "promoted.validation.json"
    subprocess.run([sys.executable, str(HERE / "validate_runtime_roster.py"),
                    "--roster", str(promoted_path), "--output", str(promoted_out)],
                   check=True, capture_output=True)
    validation = json.loads(promoted_out.read_text(encoding="utf-8"))
    assert validation["status"] == "pass"
    assert validation["runtime_qualification_performed"] is True


def test_full_ruling_registration_end_to_end_27_of_27(tmp_path):
    """Commit the ruling record in a scratch clone, register, validate everything.

    This is the exact production sequence, executed in a throwaway local clone
    (hardlinked, cheap) because the ruling receipt contract demands a real git
    commit containing the ruling record bytes.  The real repository is read
    only.
    """
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("end-to-end ruling registration needs a local git repository")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--local", "--quiet", str(ROOT), str(clone)],
                   check=True, capture_output=True)
    # Overlay the current robot_resume working files: most of them are
    # untracked in the source repository and therefore absent from the clone.
    shutil.copytree(HERE, clone / "benchmark" / "robot_resume", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _git(clone, "add", "-A", "benchmark/robot_resume")
    _git(clone, "-c", "user.email=scratch@test.local", "-c", "user.name=scratch",
         "commit", "-qm", "robot_resume: 2026-10-26 runtime ruling snapshot")
    ruling_commit = _git(clone, "rev-parse", "HEAD").stdout.strip()

    out = tmp_path / "artifacts"
    result = subprocess.run(
        [sys.executable, "benchmark/robot_resume/register_historical_evidence.py",
         "--roster", "benchmark/robot_resume/robot_runtime_roster.v1.json",
         "--output", "benchmark/robot_resume/robot_runtime_roster.registration.v1.json",
         "--receipt-dir", "benchmark/robot_resume/runtime_receipts",
         "--promoted-roster", str(out / "promoted.json"),
         "--ruling-commit", ruling_commit, "--ruling-record", RULING_RECORD],
        cwd=clone, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) == {"robot_count": 27, "qualified_count": 27, "unknown_count": 0}

    report = json.loads((clone / "benchmark" / "robot_resume" /
                         "robot_runtime_roster.registration.v1.json").read_text(encoding="utf-8"))
    assert report["qualified_count"] == 27 and report["unknown_count"] == 0
    assert report["authority"]["decision"] == "user_historical_integration_accepted"
    assert report["ruling"]["authority"]["decision"] == "user_runtime_unknown_ruling_accepted"
    assert report["ruling"]["source_commit"] == ruling_commit
    statuses = [row["status"] for row in report["rows"]]
    assert statuses.count("qualified_historical_integration") == 15
    assert statuses.count("qualified_user_ruling") == 12

    promoted = json.loads((out / "promoted.json").read_text(encoding="utf-8"))
    assert sum(row["runtime_qualified"] for row in promoted["robots"]) == 27
    assert promoted["formal_gates"] == {"runtime_qualified": False, "physical_qualified": False,
                                        "rankable": False, "paper_score_eligible": False}
    assert promoted["promotion"]["ruling_authority"]["decision"] == "user_runtime_unknown_ruling_accepted"

    validation_out = out / "promoted.validation.json"
    subprocess.run([sys.executable, "benchmark/robot_resume/validate_runtime_roster.py",
                    "--roster", str(out / "promoted.json"), "--output", str(validation_out)],
                   cwd=clone, check=True, capture_output=True)
    validation = json.loads(validation_out.read_text(encoding="utf-8"))
    assert validation["status"] == "pass"
    assert validation["runtime_qualification_performed"] is True

    receipt_files = sorted((clone / "benchmark" / "robot_resume" / "runtime_receipts").glob("*.json"))
    assert len(receipt_files) == 27
    rows_by_id = {row["instance_id"]: row for row in promoted["robots"]}
    with _scratch_modules(clone) as (_hist, rt, _reg):
        for path in receipt_files:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            assert rt.validate_receipt(receipt, roster_row=rows_by_id[receipt["instance_id"]])["status"] == "qualified"
