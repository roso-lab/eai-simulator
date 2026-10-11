"""Strict sealed collector snapshots shared by ingestion and portable archives."""

import hashlib
import json
from pathlib import Path

from tools.benchmark_branch.store import LEDGERS

from .schema import AnalysisError, _pairs, canonical, read_json, sha
from .snapshot import Budget, copy_file, directory, inventory, read_small, relative_name, write_bytes


REQUIRED_RUN_FILES = {name + ".jsonl" for name in LEDGERS} | {"plan.json", "manifest.json"}


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


def snapshot_run(source: str | Path, staging: Path, expected_seals: set[str] | None,
                 budget: Budget) -> tuple[str, dict]:
    """Copy exact sealed bytes below staging/runs/<seal>, without live validation."""
    with directory(source) as fd:
        raw = read_small(fd, "checksums.sha256", limit=min(8 * 1024 * 1024, budget.limits.max_file_bytes,
                                                         budget.limits.max_total_bytes - budget.bytes))
        seal = hashlib.sha256(raw).hexdigest()
        if expected_seals is not None and seal not in expected_seals:
            raise AnalysisError("collector seal is not a unique source of this analysis bundle")
        members = parse_seal(raw)
        if len(members) + budget.files + 1 > budget.limits.max_files:
            raise AnalysisError("snapshot total file limit exceeded")
        found, dirs = inventory(fd, budget.limits)
        # State/worker/validation files are unsealed convenience outputs. Mirrors
        # and evaluator JSON, however, must never disappear from validation.
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


def strict_run_json(root: Path, names: dict) -> None:
    """Validate all raw JSON, including branch mirrors and evaluator artifacts."""
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
