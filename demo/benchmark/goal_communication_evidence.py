"""Evaluator-owned disk closure for raw communication matrix evidence.

Stage69 validates already assembled rows.  Stage70 added this preceding disk
boundary: freeze the expected matrix before execution, derive one row only
from completed episode files, and independently rebuild every row before
passing it to the Stage69 validator.  Stage71 may execute an evaluator-authored
sealed-v4 terminal-MCR mapping carried by manifest v2.  This module does not
author that mapping, aggregate a communication score, or change any release
gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .communication import validate_communication_report
from .goal_communication_metrics import communication_condition_metrics
from .goal_communication_source_binding import (
    EXPECTED_CELL_FIELDS,
    EXPECTED_MANIFEST_TASK_PERFORMANCE_VERSION,
    build_communication_source_matrix,
    communication_expected_manifest_sha256,
    validate_communication_expected_manifest,
)
from .goal_communication_task_performance import (
    replay_communication_task_performance,
)
from .goal_progress_evidence import replay_progress_evidence
from .goal_scoring_adapter import (
    SCORING_ADAPTER_SCHEMA,
    SCORING_ADAPTER_VERSION,
    build_archive_index,
)
from .provenance import canonical_sha256


EXPECTED_MANIFEST_FILE = "communication_expected_manifest.json"
ROWS_DIRECTORY = "rows"
ROW_SCHEMA = "eai.benchmark.communication_source_row"
ROW_VERSION = 1
ROW_TASK_PERFORMANCE_VERSION = 2
SOURCE_ARCHIVE_SCHEMA = "eai.benchmark.communication_source_archive"
SOURCE_ARCHIVE_VERSION = 1
VERIFICATION_SCHEMA = "eai.benchmark.communication_evidence_verification"
VERIFICATION_VERSION = 1
CONDITION_SPEC_SCHEMA = "eai.benchmark.communication_condition_spec"
CONDITION_SPEC_VERSION = 1
ENDPOINT_POOL_BINDING_SCHEMA = (
    "eai.benchmark.communication_endpoint_pool_binding")
ENDPOINT_POOL_BINDING_VERSION = 1

_RELEASE = {
    "release": False,
    "scoreable": False,
    "execution_validated": False,
    "business_outcomes_validated": False,
}
_REQUIRED_EPISODE_FILES = (
    "episode_manifest.json",
    "public_task.json",
    "goal_session_report.json",
    "communication_transport_report.json",
    "communication_transport_replay.json",
    "communication_tool_report.json",
    "goal_progress_evidence.json",
    "goal_progress_replay.json",
    "result.json",
    "scoring_adapter_metadata.json",
)
_INDEXED_REQUIRED_FILES = frozenset(_REQUIRED_EPISODE_FILES) - {
    "scoring_adapter_metadata.json",
}


def _json_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("evidence must contain canonical finite JSON") from error


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("value must contain canonical finite JSON") from error
    return _sha256_bytes(payload)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must contain a JSON object")
    return deepcopy(dict(value))


def _safe_root(path: str | Path, label: str, *, create: bool = False) -> Path:
    root = Path(path)
    if root.is_symlink():
        raise ValueError(f"{label} must not be a symlink")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"{label} must be a directory")
    return root.resolve()


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path == Path("."):
        raise ValueError(f"{label} must stay below the matrix root")
    return path


def _below(root: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes the matrix root") from error
    return resolved


def _reject_symlink_components(root: Path, path: Path, label: str) -> None:
    """Reject a symlink at any lexical component below ``root``."""
    absolute = path.absolute()
    try:
        relative = absolute.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes the matrix root") from error
    current = root
    for part in relative.parts:
        if part == "..":
            raise ValueError(f"{label} escapes the matrix root")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains a symlink")


def _expected_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(field) for field in EXPECTED_CELL_FIELDS)


def _key_digest(row: Mapping[str, Any]) -> str:
    return canonical_sha256({field: row.get(field)
                             for field in EXPECTED_CELL_FIELDS})


def _producer_manifest_validation(manifest: Mapping[str, Any]) -> str:
    digest = validate_communication_expected_manifest(manifest)
    if manifest.get("archive_schema") != SOURCE_ARCHIVE_SCHEMA:
        raise ValueError("expected manifest archive_schema is not the source archive")
    if manifest.get("archive_version") != SOURCE_ARCHIVE_VERSION:
        raise ValueError("expected manifest archive_version is not supported")
    cells = manifest.get("cells")
    if not isinstance(cells, Sequence) or isinstance(cells, (str, bytes, Mapping)):
        raise ValueError("expected manifest cells must be a sequence")
    episode_ids: set[str] = set()
    episode_paths: set[str] = set()
    for index, raw in enumerate(cells):
        if not isinstance(raw, Mapping):
            raise ValueError(f"expected cell {index} must be an object")
        episode_id = raw.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id.strip():
            raise ValueError(f"expected cell {index} episode_id must be nonempty")
        relpath = _safe_relative(raw.get("episode_relpath"),
                                 f"expected cell {index} episode_relpath")
        if not relpath.parts or relpath.parts[0] != "episodes":
            raise ValueError("expected episode paths must live below episodes/")
        reltext = relpath.as_posix()
        if episode_id in episode_ids or reltext in episode_paths:
            raise ValueError("expected episode_id and episode_relpath must be unique")
        episode_ids.add(episode_id)
        episode_paths.add(reltext)
        spec = raw.get("condition_spec")
        if not isinstance(spec, Mapping):
            raise ValueError(f"expected cell {index} condition_spec must be an object")
        if (spec.get("schema") != CONDITION_SPEC_SCHEMA
                or spec.get("version") != CONDITION_SPEC_VERSION):
            raise ValueError(f"expected cell {index} condition_spec schema is unknown")
        if spec.get("family") != raw.get("family"):
            raise ValueError(f"expected cell {index} condition_spec family conflicts")
        if spec.get("condition_id") != raw.get("condition_id"):
            raise ValueError(f"expected cell {index} condition_spec id conflicts")
        if not isinstance(spec.get("initial_contract"), Mapping):
            raise ValueError(
                f"expected cell {index} condition_spec initial_contract is missing")
        if not isinstance(spec.get("final_contract"), Mapping):
            raise ValueError(
                f"expected cell {index} condition_spec final_contract is missing")
        condition_events = spec.get("condition_events")
        if (not isinstance(condition_events, Sequence)
                or isinstance(condition_events, (str, bytes, Mapping))
                or any(not isinstance(row, Mapping) for row in condition_events)):
            raise ValueError(
                f"expected cell {index} condition_spec events must be objects")
        if canonical_sha256(spec) != raw.get("condition_spec_sha256"):
            raise ValueError(f"expected cell {index} condition_spec digest differs")
    return digest


def freeze_expected_manifest(
    matrix_root: str | Path,
    manifest: Mapping[str, Any],
) -> str:
    """Create the evaluator manifest once, before any matrix artifacts.

    An existing byte-identical manifest is an idempotent confirmation.  A
    different payload, or a nonempty matrix root without a manifest, is
    refused rather than overwritten.
    """
    if not isinstance(manifest, Mapping):
        raise ValueError("expected manifest must be an object")
    digest = _producer_manifest_validation(manifest)
    root_path = Path(matrix_root)
    if root_path.is_symlink():
        raise ValueError("matrix root must not be a symlink")
    if root_path.exists() and not root_path.is_dir():
        raise ValueError("matrix root must be a directory")
    if not root_path.exists():
        root_path.mkdir(parents=True)
    root = root_path.resolve()
    path = root / EXPECTED_MANIFEST_FILE
    payload = _json_bytes(manifest)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise FileExistsError("expected manifest path is not a regular file")
        if path.read_bytes() != payload:
            raise FileExistsError("expected manifest is immutable")
        return digest
    if any(root.iterdir()):
        raise FileExistsError(
            "expected manifest must be frozen before matrix artifacts exist")
    with path.open("xb") as handle:
        handle.write(payload)
    return digest


def _load_manifest(root: Path) -> tuple[dict[str, Any], str]:
    manifest = _load_object(root / EXPECTED_MANIFEST_FILE,
                            "expected manifest")
    return manifest, _producer_manifest_validation(manifest)


def _manifest_cell(
    manifest: Mapping[str, Any], expected_cell: Mapping[str, Any],
    *, require_exact: bool = True,
) -> dict[str, Any]:
    if not isinstance(expected_cell, Mapping):
        raise ValueError("expected_cell must be an object")
    key = _expected_key(expected_cell)
    matches = [dict(row) for row in manifest["cells"]
               if _expected_key(row) == key]
    if len(matches) != 1:
        raise ValueError("expected_cell is not uniquely preregistered")
    cell = matches[0]
    if (require_exact
            and canonical_sha256(cell) != canonical_sha256(dict(expected_cell))):
        raise ValueError("expected_cell differs from the frozen manifest")
    return cell


def _episode_file(root: Path, name: str) -> Path:
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required episode file is missing or unsafe: {name}")
    return path


def _source_inventory(
    episode_root: Path, scoring: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    archive = scoring.get("archive_index")
    files = archive.get("files") if isinstance(archive, Mapping) else None
    if (not isinstance(archive, Mapping)
            or archive.get("schema") != "eai.benchmark.raw_archive_index"
            or archive.get("version") != 1
            or archive.get("raw_only") is not True
            or archive.get("not_protocol_score") is not True
            or not isinstance(files, Mapping)):
        raise ValueError("scoring archive index is missing or unsupported")
    recomputed_index = build_archive_index(episode_root)
    if canonical_sha256(archive) != canonical_sha256(recomputed_index):
        raise ValueError("stored scoring archive index differs from recomputation")
    missing = _INDEXED_REQUIRED_FILES - set(files)
    if missing:
        raise ValueError(
            "scoring archive index lacks required files: " + ", ".join(sorted(missing)))
    inventory: dict[str, dict[str, Any]] = {}
    for name, claim in sorted(files.items()):
        if (not isinstance(name, str) or Path(name).name != name
                or name in {".", ".."}):
            raise ValueError("scoring archive index contains an unsafe filename")
        if not isinstance(claim, Mapping):
            raise ValueError(f"archive claim for {name} must be an object")
        path = _episode_file(episode_root, name)
        actual = {"sha256": _file_sha256(path), "bytes": path.stat().st_size}
        if claim.get("sha256") != actual["sha256"] or claim.get(
                "bytes") != actual["bytes"]:
            raise ValueError(f"scoring archive index differs for {name}")
        inventory[name] = actual
    scoring_path = _episode_file(episode_root, "scoring_adapter_metadata.json")
    inventory[scoring_path.name] = {
        "sha256": _file_sha256(scoring_path),
        "bytes": scoring_path.stat().st_size,
    }
    return dict(sorted(inventory.items()))


def _identity_closure(
    manifest: Mapping[str, Any], cell: Mapping[str, Any],
    episode_root: Path, payloads: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    episode = payloads["episode_manifest.json"]
    public_task = payloads["public_task.json"]
    session = payloads["goal_session_report.json"]
    result = payloads["result.json"]
    scoring = payloads["scoring_adapter_metadata.json"]
    episode_id = cell["episode_id"]
    task_id = cell["task_id"]
    public_digest = canonical_sha256(public_task)
    if (episode.get("schema") != "eai.benchmark.formal_goal_episode"
            or episode.get("version") != 3):
        raise ValueError("episode manifest schema/version is unsupported")
    if (result.get("schema") != "eai.benchmark.formal_goal_episode_result"
            or result.get("version") != 3):
        raise ValueError("episode result schema/version is unsupported")
    if (scoring.get("schema") != SCORING_ADAPTER_SCHEMA
            or scoring.get("version") != SCORING_ADAPTER_VERSION):
        raise ValueError("scoring metadata schema/version is unsupported")
    if manifest.get("contract_sha256") != public_digest:
        raise ValueError("public task differs from frozen contract_sha256")
    if public_task.get("task_id") != task_id:
        raise ValueError("public task_id differs from expected cell")
    for label, payload in (("episode manifest", episode),
                           ("goal session", session),
                           ("result", result), ("scoring metadata", scoring)):
        if payload.get("episode_id") != episode_id:
            raise ValueError(f"{label} episode_id differs from expected cell")
    session_report = session.get("report")
    if (not isinstance(session_report, Mapping)
            or session_report.get("episode_id") != episode_id
            or session_report.get("task_id") != task_id):
        raise ValueError("goal session report identity is incomplete")
    started = [row for row in session_report.get("events", [])
               if isinstance(row, Mapping)
               and row.get("type") == "episode_started"]
    if (len(started) != 1 or started[0].get("sequence") != 0
            or not isinstance(started[0].get("payload"), Mapping)
            or started[0]["payload"].get("public_task_digest") != public_digest):
        raise ValueError("goal session lacks the bound episode_started task digest")
    for label, payload in (("episode manifest", episode),
                           ("result", result), ("scoring metadata", scoring)):
        if payload.get("task_id") != task_id:
            raise ValueError(f"{label} task_id differs from expected cell")
    if episode.get("public_task_sha256") != public_digest:
        raise ValueError("episode manifest public task digest differs")
    if scoring.get("task_contract_sha256") != public_digest:
        raise ValueError("scoring metadata public task digest differs")
    for label, payload in (("episode manifest", episode),
                           ("result", result), ("scoring metadata", scoring)):
        for flag in ("release", "scoreable", "execution_validated",
                     "business_outcomes_validated"):
            if flag in payload and payload.get(flag) is not False:
                raise ValueError(f"{label} {flag} must be false for Stage70")
    return episode_id, public_digest


def _validated_transport(
    manifest: Mapping[str, Any], cell: Mapping[str, Any],
    report: Mapping[str, Any], replay: Mapping[str, Any],
) -> dict[str, Any]:
    recomputed = validate_communication_report(report)
    if recomputed.get("validated") is not True:
        raise ValueError("shared transport report failed independent replay")
    if canonical_sha256(recomputed) != canonical_sha256(replay):
        raise ValueError("stored shared transport replay differs from recomputation")
    if (report.get("schema") != manifest.get("transport_report_schema")
            or report.get("version") != manifest.get("transport_report_version")):
        raise ValueError("shared transport schema/version differs from manifest")
    spec = cell["condition_spec"]
    if canonical_sha256(report.get("initial_contract")) != canonical_sha256(
            spec.get("initial_contract")):
        raise ValueError("shared transport initial contract differs from condition spec")
    if canonical_sha256(report.get("contract")) != canonical_sha256(
            spec.get("final_contract")):
        raise ValueError("shared transport final contract differs from condition spec")
    ledger = report.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("shared transport ledger is missing")
    actual_events = [
        {key: deepcopy(value) for key, value in row.items() if key != "sequence"}
        for row in ledger if isinstance(row, Mapping) and row.get("event") in {
            "channel_condition_changed", "channel_condition_change_rejected"}
    ]
    if canonical_sha256(actual_events) != canonical_sha256(
            spec.get("condition_events")):
        raise ValueError("shared transport condition events differ from condition spec")
    return recomputed


def _endpoint_pool_binding(
    episode_id: str, public_task_sha256: str,
    tool_report: Mapping[str, Any], transport: Mapping[str, Any],
) -> dict[str, Any]:
    if (tool_report.get("schema")
            != "eai.benchmark.goal_communication_tools_report"
            or tool_report.get("version") != 1
            or tool_report.get("episode_id") != episode_id):
        raise ValueError("communication tool report identity/schema is invalid")
    profile = tool_report.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("communication tool profile is missing")
    if (profile.get("schema") != "eai.benchmark.goal_communication_tools"
            or profile.get("version") != 1
            or profile.get("episode_id") != episode_id
            or profile.get("public_task_sha256") != public_task_sha256):
        raise ValueError("communication tool profile identity differs")
    tool_transport = tool_report.get("communication")
    if (not isinstance(tool_transport, Mapping)
            or canonical_sha256(tool_transport) != canonical_sha256(transport)):
        raise ValueError(
            "communication tool report is not bound to the shared transport")
    endpoints = profile.get("endpoints")
    directions = profile.get("allowed_directions")
    initial_contract = transport.get("initial_contract")
    if not all(isinstance(value, Mapping) for value in (
            endpoints, directions, initial_contract)):
        raise ValueError("endpoint/direction/pool initial contract is incomplete")
    pools = initial_contract.get("pools")
    if not isinstance(pools, Mapping) or not pools:
        raise ValueError("shared transport initial contract has no pools")
    if not endpoints or not isinstance(directions.get("table"), list) or not directions[
            "table"]:
        raise ValueError("endpoint/direction binding is empty")
    for flag in ("release_eligible", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if tool_report.get(flag) is not False:
            raise ValueError(f"communication tool report {flag} must be false")
    return {
        "schema": ENDPOINT_POOL_BINDING_SCHEMA,
        "version": ENDPOINT_POOL_BINDING_VERSION,
        "episode_id": episode_id,
        "public_task_sha256": public_task_sha256,
        "endpoints": deepcopy(dict(endpoints)),
        "allowed_directions": deepcopy(dict(directions)),
        "initial_contract": deepcopy(dict(initial_contract)),
    }


def _validated_progress(
    report: Mapping[str, Any], replay: Mapping[str, Any],
    public_task: Mapping[str, Any], episode_id: str,
) -> None:
    recomputed = replay_progress_evidence(
        report, public_task, episode_id=episode_id)
    if canonical_sha256(recomputed) != canonical_sha256(replay):
        raise ValueError("stored progress replay differs from recomputation")
    samples = report.get("samples")
    if (recomputed.get("validated") is not True
            or recomputed.get("status") != "observed"
            or not isinstance(samples, list) or not samples
            or not isinstance(samples[-1], Mapping)
            or samples[-1].get("boundary") != "terminal"):
        raise ValueError("progress evidence lacks a trusted terminal host anchor")


def build_episode_source_row(
    matrix_root: str | Path,
    episode_root: str | Path,
    expected_cell: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild one preregistered row from completed episode files only."""
    root = _safe_root(matrix_root, "matrix root")
    manifest, manifest_digest = _load_manifest(root)
    cell = _manifest_cell(manifest, expected_cell)
    expected_episode = _safe_relative(cell["episode_relpath"],
                                      "expected episode_relpath")
    requested_path = Path(episode_root)
    frozen_path = root / expected_episode
    _reject_symlink_components(root, requested_path, "episode root")
    _reject_symlink_components(root, frozen_path, "expected episode root")
    requested = _below(root, requested_path, "episode root")
    frozen = _below(root, frozen_path, "expected episode root")
    if requested != frozen:
        raise ValueError("episode root differs from preregistered episode_relpath")
    if requested.is_symlink() or not requested.is_dir():
        raise ValueError("episode root must be a regular directory")
    payloads = {name: _load_object(_episode_file(requested, name), name)
                for name in _REQUIRED_EPISODE_FILES}
    episode_id, public_digest = _identity_closure(
        manifest, cell, requested, payloads)
    transport = payloads["communication_transport_report.json"]
    _validated_transport(
        manifest, cell, transport,
        payloads["communication_transport_replay.json"])
    endpoint_binding = _endpoint_pool_binding(
        episode_id, public_digest,
        payloads["communication_tool_report.json"], transport)
    _validated_progress(
        payloads["goal_progress_evidence.json"],
        payloads["goal_progress_replay.json"],
        payloads["public_task.json"], episode_id)
    scoring = payloads["scoring_adapter_metadata.json"]
    inventory = _source_inventory(requested, scoring)
    relative_episode = requested.relative_to(root).as_posix()
    archive = {
        "schema": SOURCE_ARCHIVE_SCHEMA,
        "version": SOURCE_ARCHIVE_VERSION,
        "episode_id": episode_id,
        "task_id": cell["task_id"],
        "episode_relpath": relative_episode,
        "files": inventory,
        "endpoint_pool_binding": endpoint_binding,
        "raw_only": True,
        "not_protocol_score": True,
        **_RELEASE,
    }
    condition_metrics = communication_condition_metrics(
        transport,
        # Progress evidence is replayed above, but Stage70 has no frozen
        # milestone/weight/revocation conversion.  Preserve task M as N/A.
        None,
        condition_id=cell["condition_id"],
        excluded_message_ids=manifest["excluded_message_ids"],
    )
    row_version = ROW_VERSION
    mapping_status = "na_not_frozen_stage70_no_inference"
    task_performance_replay: dict[str, Any] | None = None
    if manifest.get("version") == EXPECTED_MANIFEST_TASK_PERFORMANCE_VERSION:
        task_performance_replay = replay_communication_task_performance(
            manifest["task_performance_mapping"],
            payloads["goal_progress_evidence.json"],
            payloads["public_task.json"],
            episode_id=episode_id,
        )
        condition_metrics["task_performance"] = task_performance_replay
        mapping_status = task_performance_replay["status"]
        row_version = ROW_TASK_PERFORMANCE_VERSION
    bindings = {
        "contract_sha256": manifest["contract_sha256"],
        "expected_manifest_sha256": manifest_digest,
        "episode_id": episode_id,
        **{field: cell[field] for field in EXPECTED_CELL_FIELDS},
        "metric_name": manifest["metric_name"],
        "condition_spec_sha256": cell["condition_spec_sha256"],
        "excluded_message_ids_sha256": _canonical_json_sha256(
            manifest["excluded_message_ids"]),
        "evaluator_ref": manifest["evaluator_ref"],
        "termination_anchor_source": "host_event",
        "communication_report_sha256": inventory[
            "communication_transport_report.json"]["sha256"],
        "progress_source_sha256": inventory[
            "goal_progress_evidence.json"]["sha256"],
        "archive_evidence_ref": relative_episode,
        "archive_sha256": canonical_sha256(archive),
        "archive_schema": SOURCE_ARCHIVE_SCHEMA,
        "archive_version": SOURCE_ARCHIVE_VERSION,
        "transport_report_schema": transport["schema"],
        "transport_report_version": transport["version"],
        "transport_replay_validated": True,
        "shared_transport_runtime_ref": manifest[
            "shared_transport_runtime_ref"],
        "endpoint_pool_binding_sha256": canonical_sha256(endpoint_binding),
        **_RELEASE,
    }
    if task_performance_replay is not None:
        frozen = manifest["task_performance_mapping"]["frozen_contract_v4"]
        bindings.update({
            "task_performance_mapping_sha256": manifest[
                "task_performance_mapping_sha256"],
            "frozen_contract_v4_seal_sha256": frozen["seal_sha256"],
            "task_performance_replay_sha256": canonical_sha256(
                task_performance_replay),
        })
    row = {
        "schema": ROW_SCHEMA,
        "version": row_version,
        "episode_id": episode_id,
        **{field: cell[field] for field in EXPECTED_CELL_FIELDS},
        "metric_name": manifest["metric_name"],
        "condition_metrics": condition_metrics,
        "source_bindings": bindings,
        "source_archive": archive,
        "task_performance_mapping_status": mapping_status,
        "raw_only": True,
        "not_protocol_score": True,
        **_RELEASE,
    }
    if "comparison_level" in cell:
        row["comparison_level"] = cell["comparison_level"]
        bindings["comparison_level"] = cell["comparison_level"]
    return row


def write_episode_source_row(
    matrix_root: str | Path,
    row: Mapping[str, Any],
) -> Path:
    """Persist one row with exclusive-create duplicate semantics."""
    root = _safe_root(matrix_root, "matrix root")
    if not isinstance(row, Mapping):
        raise ValueError("source row must be an object")
    manifest, _ = _load_manifest(root)
    expected_version = (ROW_TASK_PERFORMANCE_VERSION
                        if manifest.get("version")
                        == EXPECTED_MANIFEST_TASK_PERFORMANCE_VERSION
                        else ROW_VERSION)
    if (row.get("schema") != ROW_SCHEMA
            or row.get("version") != expected_version):
        raise ValueError("source row schema/version is unsupported")
    cell = _manifest_cell(manifest, row, require_exact=False)
    rebuilt = build_episode_source_row(
        root, root / cell["episode_relpath"], cell)
    if canonical_sha256(rebuilt) != canonical_sha256(dict(row)):
        raise ValueError("source row differs from disk recomputation")
    rows = root / ROWS_DIRECTORY
    if rows.is_symlink():
        raise ValueError("rows directory must not be a symlink")
    rows.mkdir(exist_ok=True)
    filename = _key_digest(row) + ".json"
    path = rows / filename
    with path.open("xb") as handle:
        handle.write(_json_bytes(row))
    return path


def _force_rejected(row: Mapping[str, Any]) -> dict[str, Any]:
    rejected = deepcopy(dict(row))
    bindings = rejected.get("source_bindings")
    bindings = dict(bindings) if isinstance(bindings, Mapping) else {}
    bindings["transport_replay_validated"] = False
    rejected["source_bindings"] = bindings
    # A source failure invalidates every stored derived measurement.  Keeping
    # an old zero here would conflate a once-valid zero-message report with a
    # currently missing/tampered report.
    rejected["condition_metrics"] = None
    return rejected


def verify_communication_evidence_archive(
    matrix_root: str | Path,
) -> dict[str, Any]:
    """Pure read-only rebuild of every row followed by Stage69 validation."""
    root = _safe_root(matrix_root, "matrix root")
    manifest, manifest_digest = _load_manifest(root)
    expected_row_version = (ROW_TASK_PERFORMANCE_VERSION
                            if manifest.get("version")
                            == EXPECTED_MANIFEST_TASK_PERFORMANCE_VERSION
                            else ROW_VERSION)
    expected = {_expected_key(cell): dict(cell) for cell in manifest["cells"]}
    rows_dir = root / ROWS_DIRECTORY
    errors: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    verified = 0
    candidates: list[Path] = []
    if rows_dir.exists():
        if rows_dir.is_symlink() or not rows_dir.is_dir():
            raise ValueError("rows path must be a regular directory")
        candidates = sorted(rows_dir.iterdir(), key=lambda item: item.name)
    for path in candidates:
        if path.is_symlink() or not path.is_file() or path.suffix != ".json":
            errors.append({"row_file": path.name,
                           "reason": "unsafe_or_unknown_row_entry"})
            continue
        try:
            row = _load_object(path, f"row {path.name}")
        except ValueError as error:
            errors.append({"row_file": path.name,
                           "reason": f"row_unreadable:{error}"})
            continue
        key = _expected_key(row)
        cell = expected.get(key)
        failure: str | None = None
        if path.name != _key_digest(row) + ".json":
            failure = "row_filename_not_canonical"
        elif (row.get("schema") != ROW_SCHEMA
              or row.get("version") != expected_row_version):
            failure = "row_schema_or_version_unknown"
        elif cell is None:
            failure = "row_cell_not_preregistered"
        else:
            try:
                rebuilt = build_episode_source_row(
                    root, root / cell["episode_relpath"], cell)
                if canonical_sha256(rebuilt) != canonical_sha256(row):
                    failure = "row_differs_from_disk_recomputation"
            except (OSError, ValueError, KeyError, TypeError) as error:
                failure = f"row_disk_recomputation_failed:{type(error).__name__}:{error}"
        if failure is None:
            observed.append(row)
            verified += 1
        else:
            errors.append({"row_file": path.name, "reason": failure})
            errors[-1]["row_sha256"] = canonical_sha256(row)
            bindings = row.get("source_bindings")
            errors[-1]["archive_evidence_ref"] = (
                bindings.get("archive_evidence_ref")
                if isinstance(bindings, Mapping) else None)
            observed.append(_force_rejected(row))
    source_matrix = build_communication_source_matrix(manifest, observed)
    return {
        "schema": VERIFICATION_SCHEMA,
        "version": VERIFICATION_VERSION,
        "status": (source_matrix["status"] if not errors
                   else "raw_only_source_failures"),
        "dimension_status": source_matrix["dimension_status"],
        "formal_scoring_wired": False,
        "not_protocol_score": True,
        "expected_manifest_sha256": manifest_digest,
        "row_file_count": len(candidates),
        "verified_row_count": verified,
        "invalid_row_count": len(errors),
        "errors": errors,
        "source_matrix": source_matrix,
        "verifier_writes_archive": False,
        **_RELEASE,
    }


__all__ = [
    "CONDITION_SPEC_SCHEMA", "CONDITION_SPEC_VERSION",
    "ENDPOINT_POOL_BINDING_SCHEMA", "ENDPOINT_POOL_BINDING_VERSION",
    "EXPECTED_MANIFEST_FILE", "ROWS_DIRECTORY", "ROW_SCHEMA", "ROW_VERSION",
    "ROW_TASK_PERFORMANCE_VERSION",
    "SOURCE_ARCHIVE_SCHEMA", "SOURCE_ARCHIVE_VERSION",
    "VERIFICATION_SCHEMA", "VERIFICATION_VERSION",
    "build_episode_source_row", "freeze_expected_manifest",
    "verify_communication_evidence_archive", "write_episode_source_row",
]
