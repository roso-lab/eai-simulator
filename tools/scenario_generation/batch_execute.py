"""Run scenario-executor rollouts and retain per-instance evidence in a pool.

This tool is intentionally separate from ``batch_validate.py``.  Validation
labels say a frozen instance passed a gate; execution evidence says exactly
which executor, mode, input bytes, timeout, and report produced a result.
Only a report whose own ``execution_validation.validated`` is true earns an
execution-tier label.  Failed and timed-out attempts remain in the manifest.
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

if __package__ in (None, ""):
    import common
    from execute_episode import execution_contract
    from project_provenance import (
        canonical_sha256, capture_request, execution_code_provenance, file_sha256,
        runtime_options, validate_execution_provenance,
    )
else:
    from . import common
    from .execute_episode import execution_contract
    from .project_provenance import (
        canonical_sha256, capture_request, execution_code_provenance, file_sha256,
        runtime_options, validate_execution_provenance,
    )


EXECUTION_TIERS = {
    "navigation_execution_validated",
    "kinematic_execution_validated",
}
PROTOCOL_SCHEMA = 2


def load_pool(pool_dir: Path) -> dict:
    manifest = pool_dir / "pool.json"
    if not manifest.is_file():
        raise ValueError(f"pool manifest does not exist: {manifest}")
    return json.loads(manifest.read_text(encoding="utf-8"))


def save_pool(pool_dir: Path, pool: dict) -> None:
    (pool_dir / "pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_protocol_selection(protocol_path: Path, split_name: str) -> tuple[dict[str, str], dict]:
    """Read one frozen split and return ``instance_id -> expected input hash``.

    Batch execution uses this rather than a caller-supplied ID list so a
    changed instance or an accidental development/test mix cannot silently
    receive evidence under the frozen protocol.
    """
    try:
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"benchmark protocol does not exist: {protocol_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid benchmark protocol JSON: {protocol_path}") from exc
    if not isinstance(protocol, dict) or protocol.get("schema") != PROTOCOL_SCHEMA:
        raise ValueError(
            f"unsupported benchmark protocol schema: {protocol.get('schema') if isinstance(protocol, dict) else None!r}"
        )
    if split_name not in {"development", "test", "all"}:
        raise ValueError(f"unsupported benchmark split: {split_name!r}")
    split = protocol.get("split")
    instances = protocol.get("instances")
    if not isinstance(split, dict) or not isinstance(instances, list):
        raise ValueError("benchmark protocol is missing split or instances")
    group_key = f"{split_name}_groups"
    if split_name == "all":
        groups = split.get("development_groups", []) + split.get("test_groups", [])
    else:
        groups = split.get(group_key, [])
    if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get("id"), str)
                                           for group in groups):
        raise ValueError(f"benchmark protocol has malformed {group_key}")
    selected_groups = {group["id"] for group in groups}
    selected: dict[str, str] = {}
    for item in instances:
        if not isinstance(item, dict) or item.get("group_id") not in selected_groups:
            continue
        identifier, input_hash = item.get("id"), item.get("scenario_sha256")
        if not isinstance(identifier, str) or not isinstance(input_hash, str) or len(input_hash) != 64:
            raise ValueError("benchmark protocol has malformed instance provenance")
        if identifier in selected:
            raise ValueError(f"benchmark protocol duplicates instance id: {identifier}")
        selected[identifier] = input_hash
    if not selected:
        raise ValueError(f"benchmark split {split_name!r} contains no instances")
    return selected, {
        "path": str(protocol_path.resolve()),
        "schema": PROTOCOL_SCHEMA,
        "split": split_name,
        "frozen_at_utc": protocol.get("frozen_at_utc"),
        "execution_sources": protocol.get("execution_sources"),
    }


def validated_tier(report: dict | None) -> str | None:
    if not isinstance(report, dict):
        return None
    validation = report.get("execution_validation")
    if not isinstance(validation, dict) or validation.get("validated") is not True:
        return None
    tier = validation.get("tier")
    return tier if tier in EXECUTION_TIERS else None


def preflight_executor(data: dict, *, transport_mode: str) -> tuple[dict | None, str | None]:
    """Classify executor support without launching Isaac.

    Unsupported phase skills and an unavailable transport mode are harness
    limitations, not episode failures.  They receive durable provenance but do
    not enter the runtime success-rate denominator.
    """
    try:
        return execution_contract(data, transport_mode=transport_mode), None
    except ValueError as exc:
        return None, str(exc)


def resolve_transport_mode(data: dict, requested_mode: str) -> str:
    """Choose the non-physical transport opt-in only for transport DAGs."""
    if requested_mode != "auto":
        return requested_mode
    nodes = data.get("phase_graph", {}).get("nodes", [])
    return "kinematic" if any(node.get("skill_key") in {"transport_object", "joint_transport"}
                               for node in nodes) else "none"


def _attempt_is_on_or_after(attempt: dict, cutoff: str | None) -> bool:
    if not isinstance(cutoff, str) or not isinstance(attempt.get("attempted_at"), str):
        return False
    try:
        attempted_at = datetime.fromisoformat(attempt["attempted_at"].replace("Z", "+00:00"))
        cutoff_at = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (attempted_at.tzinfo is not None and cutoff_at.tzinfo is not None
            and attempted_at >= cutoff_at)


def should_skip_matching_success(
    attempt: dict,
    *,
    scenario_sha256: str,
    transport_mode: str,
    protocol_context: dict | None,
    requested_options: dict | None = None,
) -> bool:
    """Reuse evidence only if its mass contract, mode and protocol cutoff match."""
    from EAI.scenario_spec.benchmark_physics import has_current_mass_audit
    if not has_current_mass_audit(attempt.get("report_body")):
        return False
    if attempt.get("scenario_sha256") != scenario_sha256 or not validated_attempt_tier(attempt):
        return False
    config = attempt.get("config")
    if not isinstance(config, dict) or config.get("transport_mode") != transport_mode:
        return False
    if protocol_context and config.get("code_provenance") != protocol_context.get("execution_sources"):
        return False
    try:
        validate_execution_provenance(
            attempt, expected_options=requested_options or runtime_options(transport_mode=transport_mode),
            check_runtime_files=True)
    except (ValueError, TypeError):
        return False
    return not (protocol_context and protocol_context.get("split") == "test"
                and not _attempt_is_on_or_after(attempt, protocol_context.get("frozen_at_utc")))


def append_execution_result(record: dict, result: dict) -> bool:
    """Append immutable attempt evidence and update the compatibility summary.

    Returns whether this particular attempt earned an execution-tier label.
    """
    record.setdefault("execution_history", []).append(result)
    record["execution"] = result
    tier = validated_attempt_tier(result)
    if tier is None:
        return False
    labels = record.setdefault("labels", [])
    if tier not in labels:
        labels.append(tier)
    return True


def validated_attempt_tier(attempt):
    from EAI.scenario_spec.benchmark_physics import has_current_mass_audit

    if (attempt.get("status") != "completed" or attempt.get("outcome", {}).get("returncode") != 0
            or not has_current_mass_audit(attempt.get("report_body"))):
        return None
    try:
        validate_execution_provenance(attempt)
    except (ValueError, TypeError):
        return None
    return validated_tier(attempt.get("report_body"))


def run_executor(instance_path: Path, report_path: Path, *, steps: int, timeout_s: float,
                 device: str, transport_mode: str, request_path: Path,
                 render_period_s: float = 0.0, wall_timeout_s: float = 0.0,
                 archive_camera_frames: bool = False, inventory_label_id: int | None = None,
                 inventory_label_state: str = "visible", inventory_dispatch_mode: str = "none",
                 inventory_message_delay_s: float = 60.0,
                 inventory_dispatch_policy: str = "immediate",
                 inventory_dispatch_wait_s: float = 65.0) -> tuple[dict, dict | None]:
    # ``run_executor`` is also imported directly by historical callers.  Keep
    # the unavailable-backend check inside this public boundary so a direct
    # call cannot delete an existing report or start a subprocess.  The
    # current backend is absent independently of scenario contents; a future
    # versioned backend must replace this probe with its real contract load.
    execution_contract({}, transport_mode=transport_mode)
    report_path.unlink(missing_ok=True)
    command = [
        sys.executable, "-u", str(common.REPO_ROOT / "tools" / "scenario_generation" / "execute_episode.py"),
        str(instance_path), "--device", device, "--report", str(report_path),
        "--execution-request", str(request_path), "--render-period-s", str(render_period_s),
        "--wall-timeout-s", str(wall_timeout_s),
    ]
    if steps:
        command.extend(["--steps", str(steps)])
    if transport_mode == "kinematic":
        command.extend(["--transport-mode", "kinematic"])
    if archive_camera_frames:
        command.append("--archive-camera-frames")
    if inventory_label_id is not None:
        command.extend(["--inventory-label-id", str(inventory_label_id),
                        "--inventory-label-state", inventory_label_state])
    command.extend(["--inventory-dispatch-mode", inventory_dispatch_mode,
                    "--inventory-message-delay-s", str(inventory_message_delay_s),
                    "--inventory-dispatch-policy", inventory_dispatch_policy,
                    "--inventory-dispatch-wait-s", str(inventory_dispatch_wait_s)])
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, cwd=str(common.REPO_ROOT), capture_output=True, text=True, timeout=timeout_s)
        stdout_text, stderr_text = completed.stdout, completed.stderr
        outcome = {
            "returncode": completed.returncode,
            "stderr_tail": completed.stderr.strip()[-800:],
            "stdout_tail": completed.stdout.strip()[-800:],
        }
    except subprocess.TimeoutExpired as exc:
        stdout_text = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr_text = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        outcome = {
            "returncode": "timeout",
            "stderr_tail": (exc.stderr or "")[-800:] if isinstance(exc.stderr, str) else "",
            "stdout_tail": (exc.stdout or "")[-800:] if isinstance(exc.stdout, str) else "",
        }
    logs = {}
    for stream_name, contents in (("stdout", stdout_text), ("stderr", stderr_text)):
        log_path = report_path.with_suffix(f".{stream_name}.log")
        log_path.write_text(contents, encoding="utf-8")
        logs[stream_name] = {"path": str(log_path), "sha256": file_sha256(log_path)}
    outcome["logs"] = logs
    outcome["duration_s"] = round(time.monotonic() - started, 2)

    report = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            outcome["report_parse_error"] = True
    return outcome, report


def _record_attempt(*, instance_id: str, scenario_sha256: str,
                    robot_dossier_snapshot_sha256: str | None = None,
                    payload_policy_hash: str | None = None, report_path: Path,
                    transport_mode: str, steps: int, device: str, timeout_s: float,
                    outcome: dict, report: dict | None, protocol_context: dict | None = None,
                    request: dict | None = None) -> dict:
    status = ("not_executable" if outcome["returncode"] == "not_executable"
              else "completed" if outcome["returncode"] == 0 and validated_tier(report)
              else "failed")
    config = {
        "executor": "tools/scenario_generation/execute_episode.py",
        "transport_mode": transport_mode,
        "steps": steps or "auto",
        "device": device,
        "timeout_s": timeout_s,
        "code_provenance": request.get("code_provenance") if request else None,
        "request": request,
    }
    if protocol_context is not None:
        config["benchmark_protocol"] = protocol_context
    attempt = {
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        # Captured immediately before the subprocess starts, so this is the
        # frozen input that the executor actually received.
        "scenario_sha256": scenario_sha256,
        "robot_dossier_snapshot_sha256": robot_dossier_snapshot_sha256,
        "payload_policy_hash": payload_policy_hash,
        "report_path": str(report_path),
        "config": config,
        "outcome": outcome,
        "report_body": report,
        "report_sha256": canonical_sha256(report) if report is not None else None,
    }
    if status == "completed" and validated_attempt_tier(attempt) is None:
        attempt["status"] = "failed"
    return attempt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, help="Existing pool directory containing pool.json")
    parser.add_argument("--ids", nargs="*", help="Instance IDs to run (default: all spawn-validated instances)")
    parser.add_argument("--protocol", help="Frozen benchmark-protocol JSON; selects a declared split by hash")
    parser.add_argument("--split", choices=["development", "test", "all"],
                        help="Required with --protocol; explicit because test execution is consequential")
    parser.add_argument("--transport-mode", choices=["none", "kinematic", "auto"], default="none",
                        help="auto: none for navigation-only DAGs and kinematic only for transport DAGs")
    parser.add_argument("--steps", type=int, default=0, help="Executor step limit (0: its deterministic auto budget)")
    parser.add_argument("--timeout", type=float, default=3600, help="Per-instance execution timeout in seconds")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--render-period-s", type=float, default=0.0)
    parser.add_argument("--wall-timeout-s", type=float, default=0.0)
    parser.add_argument("--archive-camera-frames", action="store_true")
    parser.add_argument("--inventory-label-id", type=int, choices=range(50))
    parser.add_argument("--inventory-label-state", choices=["visible", "covered", "absent"], default="visible")
    parser.add_argument("--rerun", action="store_true", help="Run again even when the same frozen input already has success evidence")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if any(not math.isfinite(value) or value < 0 for value in (args.render_period_s, args.wall_timeout_s)):
        parser.error("render period and wall timeout must be finite and nonnegative")

    pool_dir = Path(args.pool).resolve()
    pool = load_pool(pool_dir)
    frozen_hashes: dict[str, str] = {}
    protocol_context = None
    if args.protocol:
        if args.ids is not None:
            parser.error("--ids cannot be combined with --protocol; use the protocol's declared split")
        if args.split is None:
            parser.error("--protocol requires --split development, test, or all")
        try:
            frozen_hashes, protocol_context = load_protocol_selection(Path(args.protocol), args.split)
        except ValueError as exc:
            parser.error(str(exc))
        requested = set(frozen_hashes)
    else:
        if args.split is not None:
            parser.error("--split requires --protocol")
        requested = set(args.ids or [])
    known_ids = {record["id"] for record in pool.get("instances", [])}
    unknown = sorted(requested - known_ids)
    if unknown:
        parser.error(f"unknown instance IDs: {', '.join(unknown)}")

    reports_dir = pool_dir / "execution"
    reports_dir.mkdir(parents=True, exist_ok=True)
    attempted = succeeded = skipped = hash_mismatches = not_executable = 0
    for record in pool.get("instances", []):
        if requested and record["id"] not in requested:
            continue
        if "spawn_validated" not in record.get("labels", []):
            skipped += 1
            print(f"[execute] {record['id']}: skipped (not spawn_validated)")
            continue
        instance_path = pool_dir / record["file"]
        if not instance_path.is_file():
            skipped += 1
            print(f"[execute] {record['id']}: skipped (missing {instance_path})")
            continue
        input_hash = file_sha256(instance_path)
        expected_hash = frozen_hashes.get(record["id"])
        if expected_hash is not None and input_hash != expected_hash:
            hash_mismatches += 1
            print(f"[execute] {record['id']}: skipped (does not match frozen protocol input hash)")
            continue
        from EAI.scenario_spec import load_scenario

        data = load_scenario(str(instance_path))
        transport_mode = resolve_transport_mode(data, args.transport_mode)
        options = runtime_options(steps=args.steps, device=args.device, transport_mode=transport_mode,
                                  render_period_s=args.render_period_s, wall_timeout_s=args.wall_timeout_s,
                                  archive_camera_frames=args.archive_camera_frames,
                                  inventory_label_id=args.inventory_label_id, inventory_label_state=args.inventory_label_state)
        prior = record.get("execution") or {}
        if not args.rerun and should_skip_matching_success(
                prior, scenario_sha256=input_hash, transport_mode=transport_mode,
                protocol_context=protocol_context, requested_options=options):
            skipped += 1
            print(f"[execute] {record['id']}: already has matching execution evidence")
            continue

        report_path = reports_dir / f"{record['id']}.{uuid4().hex}.report.json"
        request = capture_request(instance_path, options)
        if protocol_context is not None and request["code_provenance"] != protocol_context["execution_sources"]:
            hash_mismatches += 1
            print(f"[execute] {record['id']}: skipped (execution sources differ from frozen protocol)")
            continue
        request_path = report_path.with_suffix(".request.json")
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        contract, unsupported_reason = preflight_executor(
            data, transport_mode=transport_mode
        )
        if unsupported_reason is not None:
            report = {
                "status": "not_executable",
                "execution_contract": None,
                "execution_validation": {
                    "tier": "not_executable_by_current_harness",
                    "validated": False,
                    "reason": unsupported_reason,
                },
            }
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            outcome = {
                "returncode": "not_executable",
                "stderr_tail": "",
                "stdout_tail": unsupported_reason[-800:],
                "duration_s": 0.0,
            }
            attempt = _record_attempt(
                instance_id=record["id"], scenario_sha256=input_hash, report_path=report_path,
                robot_dossier_snapshot_sha256=record.get(
                    "robot_dossier_snapshot_sha256", pool.get("robot_dossier_snapshot_sha256")),
                payload_policy_hash=record.get("payload_policy_hash", pool.get("payload_policy_hash")),
                transport_mode=transport_mode, steps=args.steps, device=args.device,
                timeout_s=args.timeout, outcome=outcome, report=report,
                request=request,
                protocol_context=({**protocol_context, "frozen_scenario_sha256": expected_hash}
                                  if protocol_context is not None else None),
            )
            append_execution_result(record, attempt)
            save_pool(pool_dir, pool)
            not_executable += 1
            print(f"[execute] {record['id']}: not executable by current harness ({unsupported_reason})")
            continue
        print(f"[execute] {record['id']}: start (mode={transport_mode}, timeout={args.timeout}s)")
        outcome, report = run_executor(instance_path, report_path, steps=args.steps,
                                       timeout_s=args.timeout, device=args.device,
                                       transport_mode=transport_mode, request_path=request_path,
                                       render_period_s=args.render_period_s, wall_timeout_s=args.wall_timeout_s,
                                       archive_camera_frames=args.archive_camera_frames,
                                       inventory_label_id=args.inventory_label_id, inventory_label_state=args.inventory_label_state)
        attempt = _record_attempt(instance_id=record["id"], scenario_sha256=input_hash,
                                  robot_dossier_snapshot_sha256=record.get(
                                      "robot_dossier_snapshot_sha256", pool.get("robot_dossier_snapshot_sha256")),
                                  payload_policy_hash=record.get(
                                      "payload_policy_hash", pool.get("payload_policy_hash")),
                                  report_path=report_path, transport_mode=transport_mode,
                                  steps=args.steps, device=args.device, timeout_s=args.timeout,
                                  outcome=outcome, report=report,
                                  request=request,
                                  protocol_context=({**protocol_context, "frozen_scenario_sha256": expected_hash}
                                                    if protocol_context is not None else None))
        earned = append_execution_result(record, attempt)
        save_pool(pool_dir, pool)
        attempted += 1
        if earned:
            succeeded += 1
            print(f"[execute] {record['id']}: {validated_tier(report)}")
        else:
            print(f"[execute] {record['id']}: failed (exit {outcome['returncode']})")

    print(f"[execute] pool={pool_dir} attempted={attempted} succeeded={succeeded} "
          f"not_executable={not_executable} skipped={skipped} hash_mismatches={hash_mismatches}")
    if hash_mismatches:
        return 2
    return 0 if attempted and succeeded == attempted and not not_executable else (0 if attempted == 0 and not not_executable else 1)


if __name__ == "__main__":
    raise SystemExit(main())
