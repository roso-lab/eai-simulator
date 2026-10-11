"""Portable sealed collector evidence bound to byte-preserved analysis bundles.

Private snapshots separate mutable input directories from collector validators.
No archived code is executed and no physical endpoint is inferred here.
"""

import hashlib
import json
from pathlib import Path
import platform
import tempfile

from tools.benchmark_branch.store import LEDGERS

from . import ANALYSIS_VERSION
from .bundle import BASE_FILES, EXTRA_FILES, _parse, verify_bundle
from .collector import collector_handoff
from .schema import AnalysisError, _pairs, canonical, digest, integer, obj, read_json, sha, text
from .snapshot import (Budget, SnapshotLimits, copy_file, directory, inventory,
                       parent_directories, publish_directory, read_small, relative_name, write_bytes)


MANIFEST = "evidence_manifest.json"
REQUIRED_RUN_FILES = {name + ".jsonl" for name in LEDGERS} | {"plan.json", "manifest.json"}


def verification_environment() -> dict:
    package = Path(__file__).parent
    sources = {"analysis/" + p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob("*.py"))}
    # Exact validation dependency closure; no simulator, task registry or
    # evaluator implementation is imported/executed from the archive.
    for name in ("store.py", "models.py", "validation.py", "evidence.py", "benchmark_bridge.py"):
        path = package.parent / "benchmark_branch" / name
        sources["collector/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"python": platform.python_version(), "source_files": sources, "code_digest": digest(sources)}


def _allowed_run_file(name: str) -> bool:
    parts = relative_name(name).split("/")
    if name in REQUIRED_RUN_FILES:
        return True
    if len(parts) == 3 and parts[0] == "branches" and parts[2] == "ledger.jsonl":
        return True
    if len(parts) == 2 and parts[0] == "evidence" and parts[1].endswith(".json"):
        sha(parts[1][:-5])
        return True
    return False


def parse_seal(raw: bytes) -> dict[str, str]:
    """Accept only the collector's canonical ledger/evaluator file grammar."""
    files = {}
    for line in raw.decode("ascii").splitlines():
        parts = line.split("  ")
        if len(parts) != 2:
            raise AnalysisError("malformed collector seal entry")
        checksum, name = sha(parts[0]), relative_name(parts[1])
        if name in files or not _allowed_run_file(name):
            raise AnalysisError("duplicate or unsupported collector seal member")
        files[name] = checksum
    if not REQUIRED_RUN_FILES <= set(files):
        raise AnalysisError("collector seal omits required files")
    return files


def _snapshot_bundle(source: str | Path, target: Path, budget: Budget) -> dict[str, dict]:
    with directory(source) as fd:
        raw = read_small(fd, "output_manifest.json", limit=min(8 * 1024 * 1024, budget.limits.max_file_bytes, budget.limits.max_total_bytes))
        manifest = _parse(raw)
        obj(manifest, "schema bundle_type files publishable")
        kind = manifest["bundle_type"]
        if kind not in {"batch", "bound"}:
            raise AnalysisError("raw evidence archives require a bound or batch result bundle")
        if manifest["schema"] != ANALYSIS_VERSION + ".output_manifest" or manifest["publishable"] is not False:
            raise AnalysisError("invalid analysis bundle manifest")
        expected = BASE_FILES | EXTRA_FILES[kind]
        if not isinstance(manifest["files"], dict) or set(manifest["files"]) != expected:
            raise AnalysisError("incorrect analysis bundle member set")
        found, dirs = inventory(fd, budget.limits)
        if found != expected | {"output_manifest.json"} or dirs:
            raise AnalysisError("analysis bundle has missing/extra members")
        files = {"analysis/output_manifest.json": write_bytes(target / "output_manifest.json", raw, budget)}
        for name in sorted(expected):
            files["analysis/" + name] = copy_file(fd, name, target / name, sha(manifest["files"][name]), budget)
        if read_small(fd, "output_manifest.json", limit=len(raw)) != raw:
            raise AnalysisError("analysis bundle changed during snapshot")
        return files


def _handoffs(analysis: Path) -> dict[str, dict]:
    manifest = read_json(analysis / "output_manifest.json")
    if manifest["bundle_type"] == "batch":
        snapshots = read_json(analysis / "sources.json")
        handoffs = [source["handoff"] for source in snapshots["sources"]]
    else:
        handoffs = [read_json(analysis / "handoff.json")]
    runs, seals = {}, set()
    for handoff in handoffs:
        run, seal = text(handoff["run_id"]), sha(handoff["seal_sha256"])
        text(handoff["collector_path"])
        if run in runs or seal in seals:
            raise AnalysisError("duplicate archived run or seal")
        runs[run] = handoff
        seals.add(seal)
    if not runs:
        raise AnalysisError("no observed collector sources to archive")
    return runs


def _snapshot_run(source: str | Path, staging: Path, expected_seals: set[str], budget: Budget) -> tuple[str, dict]:
    with directory(source) as fd:
        raw = read_small(fd, "checksums.sha256", limit=min(8 * 1024 * 1024, budget.limits.max_file_bytes, budget.limits.max_total_bytes - budget.bytes))
        seal = hashlib.sha256(raw).hexdigest()
        if seal not in expected_seals:
            raise AnalysisError("collector seal is not a unique source of this analysis bundle")
        members = parse_seal(raw)
        if len(members) + budget.files + 1 > budget.limits.max_files:
            raise AnalysisError("snapshot total file limit exceeded")
        found, dirs = inventory(fd, budget.limits)
        # Never omit an unsealed mirror/evaluator JSON that the live validator
        # would require. State/worker/validation files are unsealed and omitted.
        discovered = {name for name in found if name.startswith("branches/") and name.endswith("/ledger.jsonl")
                      or name.startswith("evidence/") and name.endswith(".json")}
        if not discovered <= set(members):
            raise AnalysisError("collector has unsealed branch/evaluator evidence")
        prefix = "runs/" + seal + "/"
        files = {prefix + "checksums.sha256": write_bytes(staging / prefix / "checksums.sha256", raw, budget)}
        for name, checksum in sorted(members.items()):
            files[prefix + name] = copy_file(fd, name, staging / prefix / name, checksum, budget)
        if read_small(fd, "checksums.sha256", limit=len(raw)) != raw or inventory(fd, budget.limits) != (found, dirs):
            raise AnalysisError("collector source changed during snapshot")
    return seal, files


def _strict_run_json(root: Path, names: dict) -> None:
    for name in names:
        path = root / name
        if name.endswith(".jsonl"):
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        row = json.loads(line, object_pairs_hook=_pairs)
                        if not isinstance(row, dict):
                            raise AnalysisError("non-object archived ledger row")
                        canonical(row)
        else:
            canonical(read_json(path))


def _validate_snapshot(root: Path, manifest: dict, *, recompute: bool) -> dict:
    obj(manifest, "schema files runs bundle_manifest_sha256 environment qualification publishable")
    if (manifest["schema"] != ANALYSIS_VERSION + ".evidence_archive" or manifest["publishable"] is not False
            or manifest["qualification"] != "sealed_content_consistency_only"):
        raise AnalysisError("invalid archive schema or qualification")
    if canonical(manifest["environment"]) != canonical(verification_environment()):
        raise AnalysisError("archive validation environment differs; use pinned Python and validator sources")
    analysis = root / "analysis"
    if hashlib.sha256((analysis / "output_manifest.json").read_bytes()).hexdigest() != sha(manifest["bundle_manifest_sha256"]):
        raise AnalysisError("archive analysis bundle identity mismatch")
    verification = verify_bundle(analysis, recompute=recompute)
    handoffs = _handoffs(analysis)
    if not isinstance(manifest["runs"], list):
        raise AnalysisError("archive runs must be an array")
    expected = {"analysis/" + p.name for p in analysis.iterdir()}
    seen, file_count = set(), 0
    for entry in manifest["runs"]:
        obj(entry, "run_id seal_sha256")
        run, seal = text(entry["run_id"]), sha(entry["seal_sha256"])
        if run in seen or run not in handoffs or seal != handoffs[run]["seal_sha256"]:
            raise AnalysisError("archive run/source identity mismatch")
        seen.add(run)
        prefix = "runs/" + seal + "/"
        run_root = root / prefix
        seal_bytes = (run_root / "checksums.sha256").read_bytes()
        if hashlib.sha256(seal_bytes).hexdigest() != seal:
            raise AnalysisError("archive run directory/seal mismatch")
        members = parse_seal(seal_bytes)
        expected.update(prefix + name for name in {*members, "checksums.sha256"})
        for name, checksum in members.items():
            if manifest["files"][prefix + name]["sha256"] != checksum:
                raise AnalysisError("archive file index differs from collector seal")
        _strict_run_json(run_root, members)
        actual = collector_handoff(run_root)
        # Location is the sole transport-dependent field. Preserve the original
        # report/snapshots byte-for-byte; normalize only the comparison object.
        actual["collector_path"] = handoffs[run]["collector_path"]
        if canonical(actual) != canonical(handoffs[run]):
            raise AnalysisError("raw collector evidence differs from saved analysis handoff")
        file_count += len(members) + 1
    if seen != set(handoffs) or set(manifest["files"]) != expected:
        raise AnalysisError("archive source/member set is incomplete or contains extras")
    return {"schema": ANALYSIS_VERSION + ".archive_verification", "status": "passed",
            "qualification": "sealed_content_consistency_only", "publishable": False,
            "runs_revalidated": len(seen), "raw_files_checked": file_count,
            "raw_collector_seal_reverified": True, "raw_ledger_structure_revalidated": True,
            "calculation_recomputed": verification["calculation_recomputed"],
            "selection_replayed": verification["selection_replayed"],
            "original_source_paths_required": False, "registration_time_verified": False,
            "evaluator_semantics_verified": False, "collector_authenticity_verified": False,
            "physical_success_verified": False}


def archive_bundle(bundle: str | Path, run_dirs: list[str | Path], output: str | Path, *,
                   limits: SnapshotLimits = SnapshotLimits(), recompute: bool = False) -> dict:
    target = Path(output).absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError("archive output already exists")
    if any(target.resolve().is_relative_to(Path(p).resolve()) for p in [bundle, *run_dirs]):
        raise AnalysisError("archive output cannot be inside its source directories")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".eai-archive-build-", dir=target.parent) as temp:
        staging, budget = Path(temp) / "payload", Budget(limits)
        files = _snapshot_bundle(bundle, staging / "analysis", budget)
        verify_bundle(staging / "analysis")
        handoffs = _handoffs(staging / "analysis")
        available = {item["seal_sha256"] for item in handoffs.values()}
        for path in run_dirs:
            seal, copied = _snapshot_run(path, staging, available, budget)
            available.remove(seal)
            files.update(copied)
        if available:
            raise AnalysisError("missing raw collector source directories")
        manifest = {"schema": ANALYSIS_VERSION + ".evidence_archive", "files": files,
                    "runs": [{"run_id": key, "seal_sha256": h["seal_sha256"]} for key, h in sorted(handoffs.items())],
                    "bundle_manifest_sha256": files["analysis/output_manifest.json"]["sha256"],
                    "environment": verification_environment(), "qualification": "sealed_content_consistency_only", "publishable": False}
        raw_manifest = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        if len(raw_manifest) > 8 * 1024 * 1024:
            raise AnalysisError("snapshot metadata byte limit exceeded")
        write_bytes(staging / MANIFEST, raw_manifest, budget)
        result = _validate_snapshot(staging, manifest, recompute=recompute)
        # One atomic no-replace rename after successful validation. A racing
        # writer's even-empty destination is preserved; no partial target leaks.
        publish_directory(staging, target)
    return {**result, "output": str(target), "copied_bytes": budget.bytes, "copied_files": budget.files}


def verify_archive(path: str | Path, *, limits: SnapshotLimits = SnapshotLimits(), recompute: bool = False) -> dict:
    # Copy only checksum-matching regular files, then run existing validators on
    # our private immutable snapshot. They never traverse the untrusted source.
    with directory(path) as fd, tempfile.TemporaryDirectory(prefix="eai-archive-verify-") as temp:
        raw = read_small(fd, MANIFEST, limit=min(8 * 1024 * 1024, limits.max_file_bytes, limits.max_total_bytes))
        manifest = _parse(raw)
        obj(manifest, "schema files runs bundle_manifest_sha256 environment qualification publishable")
        if not isinstance(manifest["files"], dict) or not manifest["files"]:
            raise AnalysisError("archive file index must be a nonempty object")
        if len(manifest["files"]) + 1 > limits.max_files:
            raise AnalysisError("snapshot total file limit exceeded")
        for name, entry in manifest["files"].items():
            relative_name(name)
            if not name.startswith(("analysis/", "runs/")):
                raise AnalysisError("unsupported archive member prefix")
            obj(entry, "sha256 bytes")
            sha(entry["sha256"])
            integer(entry["bytes"])
        expected = set(manifest["files"]) | {MANIFEST}
        found, dirs = inventory(fd, limits)
        if found != expected or dirs != parent_directories(expected):
            raise AnalysisError("archive contains missing or extra files/directories")
        staging, budget = Path(temp), Budget(limits)
        write_bytes(staging / MANIFEST, raw, budget)
        for name, entry in sorted(manifest["files"].items()):
            actual = copy_file(fd, name, staging / name, entry["sha256"], budget)
            if actual != entry:
                raise AnalysisError("archive member size mismatch")
        if read_small(fd, MANIFEST, limit=len(raw)) != raw or inventory(fd, limits) != (found, dirs):
            raise AnalysisError("archive changed during snapshot")
        return _validate_snapshot(staging, manifest, recompute=recompute)
