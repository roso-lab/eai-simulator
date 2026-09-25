"""Raw-only cost evidence extracted from a Goal episode archive.

This module deliberately joins only trusted host-side records that already
exist in an episode archive.  It does not estimate physics time, participant
compute, or communication traffic from action counts.  Missing or unverified
sources remain ``None`` so the sidecar cannot turn an evidence gap into a
zero-cost claim. V3 cost evidence preserves communication byte-accounting
semantics; numeric totals remain scheduler evidence and never imply total
physical completed-byte progress.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "eai.benchmark.goal_raw_cost_evidence"
VERSION = 3
COST_SOURCE_BINDING_SCHEMA = "eai.benchmark.goal_cost_source_bindings"
COST_SOURCE_BINDING_VERSION = 1
COST_SOURCE_BINDING_FILE = "goal_cost_source_bindings.json"
_SESSION_SOURCE = "goal_session_report.json"
_ACTIVATION_SOURCE = "team_usage.json"
_COMMUNICATION_SOURCES = (
    "pose_tools_report.json", "pose_communication_replay.json",
    "camera_tools_report.json", "camera_communication_replay.json",
    "communication_report.json", "communication_replay.json",
    "communication.json", "communication_transport_report.json",
    "communication_transport_replay.json",
)
_COST_SOURCE_FILES = (_SESSION_SOURCE, _ACTIVATION_SOURCE, *_COMMUNICATION_SOURCES)
_SCHEDULER_CHARGED_BYTES = "scheduler_charged_bytes"
_LEGACY_UNSPECIFIED = "legacy_unspecified"
_ACCOUNTING_UNAVAILABLE = "unavailable"


def _finite_number(value: Any, *, nonnegative: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return not nonnegative or value >= 0


def _read_json(root: Path, name: str) -> Any:
    path = root / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _report_task_bound(report: Mapping[str, Any], expected: str) -> bool:
    """Require every available trusted task binding to agree with expected."""
    values: list[Any] = []
    if "public_task_sha256" in report:
        values.append(report.get("public_task_sha256"))
    events = report.get("events")
    if isinstance(events, list):
        for event in events:
            if (isinstance(event, Mapping) and event.get("type") == "episode_started"
                    and isinstance(event.get("payload"), Mapping)
                    and "public_task_digest" in event["payload"]):
                values.append(event["payload"].get("public_task_digest"))
    gate = report.get("deployment_assessment")
    if isinstance(gate, Mapping):
        if "public_task_sha256" in gate:
            values.append(gate.get("public_task_sha256"))
        history = gate.get("history")
        if isinstance(history, list):
            for row in history:
                if isinstance(row, Mapping) and "public_task_sha256" in row:
                    values.append(row.get("public_task_sha256"))
    return bool(values) and all(isinstance(value, str) and value == expected
                                for value in values)


def build_cost_source_bindings(
    output_root: str | Path, *, episode_id: str, task_sha256: str,
) -> dict[str, Any]:
    """Seal the runner-produced raw cost inputs without creating cost facts."""
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError("episode_id must be a nonempty string")
    if not _valid_sha256(task_sha256):
        raise ValueError("task_sha256 must be a lowercase SHA-256 digest")
    root = Path(output_root).absolute()
    wrapper = _read_json(root, _SESSION_SOURCE)
    if (not isinstance(wrapper, Mapping) or wrapper.get("episode_id") != episode_id
            or not isinstance(wrapper.get("report"), Mapping)
            or not _report_task_bound(wrapper["report"], task_sha256)):
        raise ValueError("goal session wrapper does not match the cost binding identity")
    source_files = {
        name: {"sha256": _sha256_file(root / name)}
        for name in _COST_SOURCE_FILES if (root / name).is_file()
    }
    session_sha256 = source_files[_SESSION_SOURCE]["sha256"]
    return {
        "schema": COST_SOURCE_BINDING_SCHEMA,
        "version": COST_SOURCE_BINDING_VERSION,
        "episode_id": episode_id,
        "public_task_sha256": task_sha256,
        "session_wrapper": {
            "filename": _SESSION_SOURCE, "sha256": session_sha256,
        },
        "source_files": source_files,
    }


def _binding_context(root: Path, *, episode_id: str | None,
                     task_sha256: str | None) -> dict[str, Any]:
    if episode_id is None and task_sha256 is None:
        return {"mode": "legacy_unbound", "status": "legacy_unbound",
                "expected_episode_id": None, "expected_public_task_sha256": None,
                "file_statuses": {}, "quality_failures": []}
    if episode_id is not None and task_sha256 is None:
        return {"mode": "legacy_episode_only", "status": "legacy_episode_only",
                "expected_episode_id": episode_id, "expected_public_task_sha256": None,
                "file_statuses": {}, "quality_failures": []}
    if (not isinstance(episode_id, str) or not episode_id.strip()
            or not _valid_sha256(task_sha256)):
        return {"mode": "formal", "status": "invalid_expected_identity",
                "expected_episode_id": episode_id,
                "expected_public_task_sha256": task_sha256,
                "file_statuses": {},
                "quality_failures": ["expected_episode_and_task_identity_required"]}

    binding_path = root / COST_SOURCE_BINDING_FILE
    payload = _read_json(root, COST_SOURCE_BINDING_FILE)
    base = {"mode": "formal", "expected_episode_id": episode_id,
            "expected_public_task_sha256": task_sha256,
            "file_statuses": {}, "quality_failures": []}
    if payload is None and not binding_path.is_file():
        return {**base, "status": "binding_missing",
                "quality_failures": ["cost_source_binding_missing"]}
    if (not isinstance(payload, Mapping)
            or set(payload) != {
                "schema", "version", "episode_id", "public_task_sha256",
                "session_wrapper", "source_files",
            }
            or payload.get("schema") != COST_SOURCE_BINDING_SCHEMA
            or type(payload.get("version")) is not int
            or payload["version"] != COST_SOURCE_BINDING_VERSION
            or payload.get("episode_id") != episode_id
            or payload.get("public_task_sha256") != task_sha256
            or not isinstance(payload.get("session_wrapper"), Mapping)
            or set(payload["session_wrapper"]) != {"filename", "sha256"}
            or payload["session_wrapper"].get("filename") != _SESSION_SOURCE
            or not _valid_sha256(payload["session_wrapper"].get("sha256"))
            or not isinstance(payload.get("source_files"), Mapping)
            or not set(payload["source_files"]).issubset(_COST_SOURCE_FILES)
            or any(not isinstance(entry, Mapping) or set(entry) != {"sha256"}
                   for entry in payload["source_files"].values())):
        return {**base, "status": "binding_invalid",
                "quality_failures": ["cost_source_binding_invalid"]}

    statuses: dict[str, str] = {}
    failures: list[str] = []
    source_files = payload["source_files"]
    for name in _COST_SOURCE_FILES:
        entry = source_files.get(name)
        path = root / name
        if name not in source_files:
            statuses[name] = "unbound" if path.is_file() else "not_produced"
            if path.is_file():
                failures.append(f"source_unbound:{name}")
            continue
        if not isinstance(entry, Mapping) or not _valid_sha256(entry.get("sha256")):
            statuses[name] = "binding_entry_invalid"
            failures.append(f"source_binding_entry_invalid:{name}")
            continue
        if not path.is_file():
            statuses[name] = "missing"
            failures.append(f"source_missing:{name}")
            continue
        try:
            matches = _sha256_file(path) == entry["sha256"]
        except OSError:
            matches = False
        statuses[name] = "verified" if matches else "hash_mismatch"
        if not matches:
            failures.append(f"source_hash_mismatch:{name}")
    session_entry = source_files.get(_SESSION_SOURCE)
    if (not isinstance(session_entry, Mapping)
            or session_entry.get("sha256") != payload["session_wrapper"]["sha256"]):
        statuses[_SESSION_SOURCE] = "hash_mismatch"
        failures.append("session_wrapper_hash_mismatch")
    try:
        binding_sha256 = _sha256_file(binding_path)
    except OSError:
        return {**base, "status": "binding_invalid",
                "quality_failures": ["cost_source_binding_unreadable"]}
    return {**base, "status": "verified" if not failures else "partial",
            "file_statuses": statuses,
            "quality_failures": sorted(set(failures)),
            "binding_sha256": binding_sha256}


def _file_binding_status(context: Mapping[str, Any], name: str) -> str:
    mode = context["mode"]
    if mode in {"legacy_unbound", "legacy_episode_only"}:
        return mode
    if context["status"] in {"binding_missing", "binding_invalid",
                             "invalid_expected_identity"}:
        return context["status"]
    return context["file_statuses"].get(name, "unbound")


def _session_report(
    root: Path, supplied: Mapping[str, Any] | None, *,
    context: Mapping[str, Any], episode_id: str | None, task_sha256: str | None,
) -> tuple[Mapping[str, Any] | None, str]:
    disk_payload = _read_json(root, _SESSION_SOURCE)
    mode = context["mode"]
    if mode == "legacy_unbound":
        payload = supplied if supplied is not None else disk_payload
        wrapped = payload.get("report") if isinstance(payload, Mapping) else None
        report = wrapped if isinstance(wrapped, Mapping) else payload
        return (report if isinstance(report, Mapping) else None, mode)
    if mode == "legacy_episode_only":
        payload = supplied if supplied is not None else disk_payload
        if not isinstance(payload, Mapping):
            return None, "source_missing"
        wrapped = payload.get("report")
        if isinstance(wrapped, Mapping):
            recorded = payload.get("episode_id")
            if recorded is not None and recorded != episode_id:
                return None, "episode_mismatch"
            return wrapped, mode
        # A bare supplied report remains an explicitly unbound legacy input.
        return payload, mode

    file_status = _file_binding_status(context, _SESSION_SOURCE)
    if file_status != "verified" or not isinstance(disk_payload, Mapping):
        return None, file_status
    disk_report = disk_payload.get("report")
    if (disk_payload.get("episode_id") != episode_id
            or not isinstance(disk_report, Mapping)
            or not _report_task_bound(disk_report, str(task_sha256))):
        return None, "identity_mismatch"
    if supplied is not None:
        if (not isinstance(supplied, Mapping)
                or supplied.get("episode_id") != episode_id
                or not isinstance(supplied.get("report"), Mapping)
                or not _report_task_bound(supplied["report"], str(task_sha256))
                or _canonical_digest(supplied) != _canonical_digest(disk_payload)):
            return None, "supplied_report_mismatch"
    return disk_report, "verified"


def _accepted_actions(report: Mapping[str, Any] | None) -> tuple[dict[str, int], int | None]:
    if not isinstance(report, Mapping):
        return {}, None
    rows = report.get("actions")
    if not isinstance(rows, list):
        return {}, None
    counts: Counter[str] = Counter()
    for row in rows:
        # GoalSession.actions is the trusted accepted-action ledger.  Keep a
        # defensive check so malformed archives do not become evidence.
        if isinstance(row, Mapping) and isinstance(row.get("type"), str) and row["type"]:
            counts[row["type"]] += 1
    return dict(sorted(counts.items())), sum(counts.values())


def _host_clock(report: Mapping[str, Any] | None, *, binding_status: str) -> dict[str, Any]:
    source = "goal_host_receipt_clock"
    if not isinstance(report, Mapping):
        return {"source": source, "start_s": None, "end_s": None,
                "elapsed_s": None, "valid": False,
                "binding_status": binding_status}
    end = report.get("current_time_s")
    if not _finite_number(end, nonnegative=True):
        return {"source": source, "start_s": None, "end_s": None,
                "elapsed_s": None, "valid": False,
                "binding_status": binding_status}
    # GoalSession starts its trusted receipt clock at zero.  This is a ledger
    # clock bound, not a claim about wall-clock or physics elapsed time.
    return {"source": source, "start_s": 0.0, "end_s": float(end),
            "elapsed_s": float(end), "valid": True,
            "binding_status": binding_status}


def _activation_proxy(root: Path, *, context: Mapping[str, Any]) -> dict[str, Any]:
    binding_status = _file_binding_status(context, _ACTIVATION_SOURCE)
    formal = context["mode"] == "formal"
    if formal and binding_status != "verified":
        return {"value": None, "source": None, "is_proxy": True,
                "valid": False, "binding_status": binding_status}
    usage = _read_json(root, "team_usage.json")
    if not isinstance(usage, Mapping):
        return {"value": None, "source": None, "is_proxy": True,
                "valid": False, "binding_status": binding_status}
    value = usage.get("activation_cost_proxy")
    source = usage.get("activation_cost_source")
    valid = _finite_number(value, nonnegative=True) and isinstance(source, str) and bool(source)
    return {"value": float(value) if valid else None,
            "source": source if valid else None, "is_proxy": True,
            "valid": valid, "binding_status": binding_status}


def _explicit_compute_cost(
    report: Mapping[str, Any] | None, *, binding_status: str,
) -> dict[str, Any]:
    """Read only explicitly host-recorded planning costs.

    No current GoalSession producer records these fields, so normal episodes
    intentionally report ``None``.  The names are reserved for a future
    trusted host scheduler source and are not populated from participant trace.
    """
    values: dict[str, float | None] = {}
    for key in ("planning_compute_cost_s", "replanning_compute_cost_s"):
        value = report.get(key) if isinstance(report, Mapping) else None
        values[key] = float(value) if _finite_number(value, nonnegative=True) else None
    total = (values["planning_compute_cost_s"] + values["replanning_compute_cost_s"]
             if values["planning_compute_cost_s"] is not None
             and values["replanning_compute_cost_s"] is not None else None)
    recorded = any(value is not None for value in values.values())
    return {"planning_s": values["planning_compute_cost_s"],
            "replanning_s": values["replanning_compute_cost_s"],
            "total_s": total,
            "source": "trusted_host_scheduler" if recorded else None,
            "valid": recorded,
            "binding_status": binding_status}


def _canonical_digest(value: Any) -> str:
    """Return a stable identity for deduplicating mirrored archive files."""
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _communication_bytes(
    root: Path, *, episode_id: str | None = None,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Sum bytes only from communication reports with replay proof.

    Shared mode has one authoritative transport and never falls back to tool
    mirrors. Legacy tool reports retain their existing replay-sidecar contract.
    """
    sources = (
        ("pose_tools_report.json", "pose_communication_replay.json"),
        ("camera_tools_report.json", "camera_communication_replay.json"),
        ("communication_report.json", "communication_replay.json"),
        ("communication.json", "communication_replay.json"),
    )
    shared_source = ("communication_transport_report.json", "communication_transport_replay.json")
    formal = context["mode"] == "formal"

    def produced_or_bound(name: str) -> bool:
        if (root / name).exists():
            return True
        return formal and _file_binding_status(context, name) != "not_produced"

    shared = any(produced_or_bound(name) for name in shared_source)
    if shared:
        sources = (shared_source,)
    total = 0
    by_pool: Counter[str] = Counter()
    by_direction: Counter[str] = Counter()
    reliable_sources = 0
    invalid_sources = 0
    direction_complete = True
    seen_archives: set[tuple[str, str]] = set()
    binding_files: dict[str, str] = {}
    binding_failed = False
    accounting_rows: list[dict[str, Any] | None] = []
    for report_name, replay_name in sources:
        report_path = root / report_name
        report_status = (_file_binding_status(context, report_name)
                         if formal else None)
        pair_present = ((root / report_name).exists()
                        or formal and report_status != "not_produced")
        if not pair_present:
            continue
        if formal:
            pair_statuses = {
                name: _file_binding_status(context, name)
                for name in (report_name, replay_name)
            }
            binding_files.update(pair_statuses)
            if any(status != "verified" for status in pair_statuses.values()):
                invalid_sources += 1
                binding_failed = True
                continue
        if not report_path.is_file():
            if shared:
                invalid_sources += 1
            continue
        report_payload = _read_json(root, report_name)
        replay = _read_json(root, replay_name)
        communication = (report_payload.get("communication")
                         if isinstance(report_payload, Mapping) else None)
        if communication is None and isinstance(report_payload, Mapping):
            # A generic archive may already be the communication report.
            communication = report_payload if report_payload.get("schema") == "eai.benchmark.communication" else None
        if not isinstance(communication, Mapping):
            invalid_sources += 1
            continue
        # Tool reports are episode-owned.  Older fixture reports may omit the
        # wrapper, so absence remains compatible; an explicit mismatch is a
        # hard evidence failure and must never be silently joined.
        recorded_episode = report_payload.get("episode_id") if isinstance(report_payload, Mapping) else None
        if episode_id is not None and recorded_episode is not None and recorded_episode != episode_id:
            invalid_sources += 1
            continue
        if not isinstance(replay, Mapping) or replay.get("validated") is not True:
            invalid_sources += 1
            continue
        communication_version = communication.get("version")
        current_version = (
            type(communication_version) is int and communication_version == 4
        )
        legacy_version = "version" not in communication or (
            type(communication_version) is int
            and communication_version in {1, 2, 3}
        )
        if not current_version and not legacy_version:
            invalid_sources += 1
            continue
        if shared or current_version:
            from .communication import validate_communication_report

            # Recompute shared reports and every v4 independent report. A
            # self-asserted replay sidecar cannot authorize v4 byte semantics.
            if validate_communication_report(communication).get("validated") is not True:
                invalid_sources += 1
                continue
        elif "byte_accounting" in communication:
            # Historical report versions cannot acquire v4 semantics through
            # a forged downgrade.
            invalid_sources += 1
            continue
        archive_key = (_canonical_digest(communication), _canonical_digest(replay))
        if archive_key in seen_archives:
            # A runner may expose the same transport under a generic filename
            # and a tool-specific filename.  Count that transport once.
            continue
        seen_archives.add(archive_key)
        ledger = communication.get("ledger")
        if not isinstance(ledger, list):
            invalid_sources += 1
            continue
        source_total = 0
        source_valid = True
        for event in ledger:
            if not isinstance(event, Mapping) or event.get("event") != "bytes_served":
                continue
            value = event.get("bytes")
            if not _finite_number(value, nonnegative=True):
                source_valid = False
                break
            source_total += int(value)
            pool = event.get("pool")
            direction = event.get("direction")
            if isinstance(pool, str) and pool:
                by_pool[pool] += int(value)
                if not (isinstance(direction, str) and direction):
                    # Totals and pool attribution remain valid, but a
                    # direction breakdown must fail closed when any pooled
                    # transmission lacks its publication direction.
                    direction_complete = False
            if isinstance(direction, str) and direction:
                by_direction[direction] += int(value)
        if not source_valid:
            invalid_sources += 1
            continue
        total += source_total
        accounting_rows.append(
            deepcopy(communication["byte_accounting"])
            if current_version else None)
        reliable_sources += 1
    reliable = reliable_sources > 0 and invalid_sources == 0
    exact_accounting = (
        deepcopy(accounting_rows[0])
        if reliable and accounting_rows and accounting_rows[0] is not None
        and all(row == accounting_rows[0] for row in accounting_rows)
        else None
    )
    total_interpretation = (
        _SCHEDULER_CHARGED_BYTES if exact_accounting is not None
        else _LEGACY_UNSPECIFIED if reliable
        else _ACCOUNTING_UNAVAILABLE
    )
    if formal:
        binding_status = ("source_binding_failed" if binding_failed else
                          "verified" if binding_files else "not_produced")
    else:
        # Both legacy modes lack task-bound file hashes. Keep the communication
        # section stable for old producer/consumer pairs that compare it as a
        # whole; the top-level source_binding still distinguishes episode-only
        # calls and preserves their explicit episode mismatch check.
        binding_status = "legacy_unbound"
    return {
        "total_bytes": total if reliable else None,
        "byte_accounting": exact_accounting,
        "total_bytes_interpretation": total_interpretation,
        "total_physical_progress": False,
        "by_pool_bytes": dict(sorted(by_pool.items())) if reliable else None,
        "by_direction_bytes": (dict(sorted(by_direction.items()))
                               if reliable and direction_complete else None),
        "direction_complete": direction_complete if reliable else False,
        "reliable_source_count": reliable_sources,
        "invalid_source_count": invalid_sources,
        "source": "validated_communication_replay" if reliable else None,
        "valid": reliable,
        "aggregation": "shared_transport_counted_once" if shared else "legacy_distinct_reports",
        "binding_status": binding_status,
        "binding_file_statuses": dict(sorted(binding_files.items())),
    }


def build_raw_cost_evidence(
    output_root: str | Path,
    *,
    trusted_report: Mapping[str, Any] | None = None,
    episode_id: str | None = None,
    task_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a stable, raw-only declaration/coordination cost archive.

    The function is intentionally sidecar-only: callers may persist the
    returned mapping, but it never changes episode validity or scoreability.
    """
    root = Path(output_root).absolute()
    context = _binding_context(root, episode_id=episode_id, task_sha256=task_sha256)
    report, session_binding_status = _session_report(
        root, trusted_report, context=context, episode_id=episode_id,
        task_sha256=task_sha256)
    action_counts, action_total = _accepted_actions(report)
    clock = _host_clock(report, binding_status=session_binding_status)
    communication = _communication_bytes(
        root, episode_id=episode_id, context=context)
    activation = _activation_proxy(root, context=context)
    compute = _explicit_compute_cost(
        report, binding_status=session_binding_status)
    validity = {
        "raw_only": True,
        "not_protocol_score": True,
        "clock_valid": clock["valid"],
        "accepted_action_ledger_valid": action_total is not None,
        "communication_ledger_reliable": communication["valid"],
        "activation_cost_proxy_valid": activation["valid"],
        "planning_compute_cost_recorded": compute["valid"],
        "scoreable": False,
        "execution_validated": False,
        "business_outcomes_validated": False,
    }
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "raw_only": True,
        "not_protocol_score": True,
        "source_binding": dict(context),
        "clock_source": clock["source"],
        "clock_valid": clock["valid"],
        "clock": clock,
        "accepted_actions": {
            "counts_by_type": action_counts,
            "total_count": action_total,
            "source": "goal_session.actions",
            "binding_status": session_binding_status,
        },
        "communication": communication,
        "activation_cost_proxy": activation,
        "planning_compute_cost": compute,
        "validity": validity,
    }


def build_cost_evidence(
    output_root: str | Path,
    *,
    trusted_report: Mapping[str, Any] | None = None,
    episode_id: str | None = None,
    task_sha256: str | None = None,
) -> dict[str, Any]:
    """Stable short alias for consumers assembling a scoring sidecar."""
    return build_raw_cost_evidence(output_root, trusted_report=trusted_report,
                                   episode_id=episode_id,
                                   task_sha256=task_sha256)


__all__ = [
    "COST_SOURCE_BINDING_FILE", "COST_SOURCE_BINDING_SCHEMA",
    "COST_SOURCE_BINDING_VERSION", "SCHEMA", "VERSION",
    "build_cost_evidence", "build_cost_source_bindings",
    "build_raw_cost_evidence",
]
