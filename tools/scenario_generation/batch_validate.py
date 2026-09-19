"""Batch-validate an instance pool: generate families, run gates, settle each.

Each instance goes through the offline gate stack (fast, in-process) and then
a headless Isaac settle simulation (slow, one subprocess per instance). The
pool directory accumulates the instance files, settle reports, and a
`pool.json` manifest recording every label earned:

- ``placement_validated`` -- offline schema/semantic/geometry/connectivity/
  oracle/evaluation gates passed;
- ``spawn_validated``     -- additionally settled in Isaac with zero commands
  and stayed within the drift tolerance.

Resume-safe: instances already recorded as spawn_validated are skipped when
the same pool directory is reused.
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    import common
else:
    from . import common

if str(common.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(common.REPO_ROOT))

POOL_SCHEMA = 1


def load_pool(pool_dir: Path) -> dict:
    manifest = pool_dir / "pool.json"
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8"))
    return {"schema": POOL_SCHEMA, "pool": pool_dir.name, "instances": []}


def save_pool(pool_dir: Path, pool: dict) -> None:
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / "pool.json").write_text(
        json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert_instance(pool: dict, record: dict) -> None:
    instances = pool["instances"]
    for index, existing in enumerate(instances):
        if existing["id"] == record["id"]:
            instances[index] = record
            return
    instances.append(record)


def run_settle_subprocess(instance_path: Path, report_path: Path, *, steps, timeout_s, device):
    cmd = [
        sys.executable, "-u", str(common.REPO_ROOT / "tools" / "scenario_generation" / "settle_smoke.py"),
        str(instance_path), "--steps", str(steps), "--headless", "--device", device,
        "--report", str(report_path),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            cmd, cwd=str(common.REPO_ROOT), capture_output=True, text=True, timeout=timeout_s)
        outcome = {"returncode": completed.returncode,
                   "stderr_tail": completed.stderr.strip()[-400:]}
    except subprocess.TimeoutExpired:
        outcome = {"returncode": "timeout", "stderr_tail": f"settle exceeded {timeout_s}s"}
    outcome["duration_s"] = round(time.monotonic() - started, 1)
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True, help="Pool directory (created if absent)")
    parser.add_argument("--skeleton", help="Recipe family to generate per seed")
    parser.add_argument("--arm", default="z1", choices=["z1", "ur5"],
                        help="TRN-01/SAR-01 manipulator variant (ur5 = m20 carrier)")
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 23, 31])
    parser.add_argument("--instances", nargs="+",
                        help="Existing instance JSON files (e.g. LLM proposals) to gate and settle into the pool")
    parser.add_argument("--steps", type=int, default=50, help="Settle steps per instance")
    parser.add_argument("--settle-timeout", type=float, default=1200, help="Per-instance settle timeout (s)")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--payload-policy",
                        help="Complete JSON/file mapping robot types to experimental payload capacities")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate and gate-check only; no Isaac settle")
    parser.add_argument("--list", action="store_true",
                        help="List existing pool instances and exit")
    parser.add_argument("--require-team-necessary", action="store_true",
                        help="Reject instances for which capability/payload set cover admits a single robot")
    parser.add_argument("--scene-key", choices=["factory", "warehouse_nvd", "modern_house_nvd"],
                        help="Scene key; inferred from the first external instance when omitted")
    args = parser.parse_args(argv)
    if not args.skeleton and not args.instances and not args.list:
        parser.error("one of --skeleton, --instances, or --list is required")

    from EAI.scenario_spec import load_scenario
    from EAI.scenario_spec.geometry import provider_map
    from EAI.scenario_spec.operators import (
        diff_fields,
        expand_family,
        layout_signature,
        run_gates,
        skeleton_family,
    )
    from EAI.scenario_spec.oracle import minimum_team_certificate
    from EAI.scenario_spec.templates import template_for
    from tools.scenario_generation.validate_factory import _load_payload_policy

    payload_policy = _load_payload_policy(args.payload_policy)

    pool_dir = Path(args.pool)
    instances_dir = pool_dir / "instances"
    reports_dir = pool_dir / "settle"
    pool = load_pool(pool_dir)
    pool.setdefault("schema", POOL_SCHEMA)
    pool["settle_policy"] = {"steps": args.steps, "device": args.device,
                             "drift_tol_m": 0.5, "timeout_s": args.settle_timeout}
    from tools.scenario_generation.combination_profile import payload_policy_hash
    from tools.scenario_generation.robot_dossier import all_dossiers_snapshot_hash
    pool["robot_dossier_snapshot_sha256"] = all_dossiers_snapshot_hash()
    pool["payload_policy_hash"] = payload_policy_hash(payload_policy) if payload_policy else None
    pool["payload_policy"] = payload_policy
    definitions = pool.setdefault("labels_definition", {})
    definitions.update({
        "placement_validated": "offline gates: schema, semantic, geometry, connectivity, oracle, evaluation",
        "spawn_validated": "placement_validated plus headless Isaac settle within drift tolerance",
        "single_robot_feasible": "capability/payload set cover contains a one-robot solution",
        "team_necessary": "capability/payload set cover proves minimum_team_size > 1",
    })

    done = 0
    skipped = 0
    failed = 0

    if args.list:
        for item in pool.get("instances", []):
            print(f"{item['id']}\t{','.join(item.get('labels', [])) or '-'}\t"
                  f"sig={item.get('layout_signature', '-')}\t"
                  f"pair_diff={item.get('pair_diff') or '-'}")
        print(f"[batch] pool={pool_dir} records={len(pool.get('instances', []))}")
        return 0

    scene_key = args.scene_key or "factory"
    if args.instances and not args.scene_key:
        observed = {json.loads(Path(raw).read_text(encoding="utf-8")).get("scene_key", "factory")
                    for raw in args.instances}
        if len(observed) != 1:
            raise ValueError(f"external instances must share one scene_key, got {sorted(observed)}")
        scene_key = observed.pop()
    occupancy = provider_map(scene_key=scene_key)
    template = template_for(scene_key, occupancy)
    pool["scene_key"] = scene_key
    pool["template"] = {"key": template.key, "map_sha256": template.map_sha256}

    def settle_and_record(entry, instance_path, report_path):
        """Shared settle path; returns True when spawn_validated."""
        if args.dry_run:
            upsert_instance(pool, entry)
            save_pool(pool_dir, pool)
            return True
        print(f"[batch] {entry['id']}: settle start ({args.steps} steps, timeout {args.settle_timeout}s)")
        outcome = run_settle_subprocess(instance_path, report_path, steps=args.steps,
                                        timeout_s=args.settle_timeout, device=args.device)
        entry["settle"] = outcome
        # A report file is only trustworthy when this run actually completed;
        # otherwise a stale report from an earlier run could whitewash a
        # timeout into spawn_validated.
        if outcome["returncode"] != 0:
            report_path.unlink(missing_ok=True)
            entry["settle"]["status"] = f"exit_{outcome['returncode']}"
            upsert_instance(pool, entry)
            save_pool(pool_dir, pool)
            print(f"[batch] {entry['id']}: settle FAILED (exit {outcome['returncode']})")
            return False
        settle_report = {}
        if report_path.exists():
            try:
                settle_report = json.loads(report_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                settle_report = {}
        entry["settle"]["status"] = settle_report.get("status", "no_report")
        if settle_report.get("status") == "spawn_validated":
            from tools.scenario_generation.runtime_evidence import import_settle_report
            try:
                entry["runtime_evidence"] = import_settle_report(
                    settle_report,
                    scenario_sha256=hashlib.sha256(instance_path.read_bytes()).hexdigest(),
                    robot_dossier_snapshot_sha256=pool["robot_dossier_snapshot_sha256"],
                )
            except (TypeError, ValueError) as exc:
                entry["settle"]["status"] = "evidence_rejected"
                entry["settle"]["evidence_error"] = str(exc)
                upsert_instance(pool, entry)
                save_pool(pool_dir, pool)
                print(f"[batch] {entry['id']}: settle evidence REJECTED ({exc})")
                return False
            entry["labels"].append("spawn_validated")
            entry["settle"]["max_drift_m"] = max(
                (item.get("drift_m", 0.0) for item in settle_report.get("entities", {}).values()),
                default=None)
            upsert_instance(pool, entry)
            save_pool(pool_dir, pool)
            print(f"[batch] {entry['id']}: spawn_validated "
                  f"(max drift {entry['settle']['max_drift_m']} m, {outcome['duration_s']}s)")
            return True
        upsert_instance(pool, entry)
        save_pool(pool_dir, pool)
        print(f"[batch] {entry['id']}: settle FAILED ({settle_report.get('status', outcome['returncode'])})")
        return False

    def check_duplicate_layout(entry):
        signature = entry.get("layout_signature")
        if not signature:
            return
        for other in pool["instances"]:
            same_family = (other.get("family_id") is not None
                           and other.get("family_id") == entry.get("family_id"))
            if other["id"] != entry["id"] and other.get("layout_signature") == signature \
                    and not same_family:
                print(f"[batch] WARNING: {entry['id']} duplicates layout of {other['id']} "
                      f"(signature {signature})")

    def attach_collaboration_evidence(entry, data):
        certificate = minimum_team_certificate(data, payload_policy=payload_policy)
        entry["collaboration_certificate"] = certificate
        label = "team_necessary" if certificate["forced_cooperation"] else "single_robot_feasible"
        entry["labels"].append(label)
        return certificate["forced_cooperation"]

    # Channel 1: existing instance files (e.g. LLM proposals).
    if args.instances:
        instances_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        for raw in args.instances:
            source = Path(raw)
            try:
                data = run_gates(
                    load_scenario(str(source)), occupancy,
                    payload_policy=payload_policy, template=template)
            except Exception as exc:
                failed += 1
                print(f"[batch] {source.name}: gates FAILED: {exc}")
                continue
            instance_id = f"ext_{data['task_name']}"
            instance_path = instances_dir / f"{instance_id}.json"
            report_path = reports_dir / f"{instance_id}.report.json"
            # The freshly gated source always wins over a stale pool copy.
            instance_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
            existing = next((item for item in pool["instances"] if item["id"] == instance_id), None)
            if existing and "spawn_validated" in existing.get("labels", []) \
                    and existing.get("layout_signature") == layout_signature(data):
                skipped += 1
                print(f"[batch] {instance_id}: already spawn_validated, skipping")
                continue
            entry = {
                "id": instance_id, "source": str(source), "family_id": None,
                "seed": data.get("seed"), "skeleton": "external",
                "file": str(instance_path.relative_to(pool_dir)),
                "labels": ["placement_validated"], "task_name": data["task_name"],
                 "robot_dossier_snapshot_sha256": pool["robot_dossier_snapshot_sha256"],
                 "payload_policy_hash": pool["payload_policy_hash"],
                "layout_signature": layout_signature(data),
            }
            team_necessary = attach_collaboration_evidence(entry, data)
            if args.require_team_necessary and not team_necessary:
                failed += 1
                print(f"[batch] {instance_id}: collaboration gate FAILED: single robot feasible")
                continue
            check_duplicate_layout(entry)
            if settle_and_record(entry, instance_path, report_path):
                done += 1
            else:
                failed += 1

    # Channel 2: skeleton families across seeds.
    for seed in args.seeds:
        family_id = f"{args.skeleton.lower().replace('-', '')}_s{seed}_{args.arm}" if args.skeleton else None
        if not family_id:
            break
        # Pool-aware resampling: the sampler's anchor space is small, so
        # different seeds can produce identical layouts. Retry with a
        # deterministic derived seed until the family's layout signature is
        # new for this pool (bounded), and record the effective seed.
        existing_sigs = {item.get("layout_signature") for item in pool["instances"]
                         if item.get("layout_signature")}
        effective_seed = seed
        record = instances = None
        last_error = None
        for attempt in range(8):
            effective_seed = seed + 1009 * attempt
            family = skeleton_family(args.skeleton, family_id=family_id, seed=effective_seed, arm=args.arm)
            try:
                record, instances = expand_family(
                    family, template, occupancy, payload_policy=payload_policy)
            except Exception as exc:
                last_error = exc
                continue
            first_sig = layout_signature(next(iter(instances.values())))
            if first_sig not in existing_sigs or attempt == 7:
                if first_sig in existing_sigs:
                    print(f"[batch] {family_id}: layout still duplicated after {attempt + 1} attempts")
                break
        if record is None:
            upsert_instance(pool, {"id": family_id, "family_id": family_id, "seed": seed,
                                   "skeleton": args.skeleton, "labels": [],
                                   "gates_error": str(last_error)})
            save_pool(pool_dir, pool)
            failed += 1
            print(f"[batch] {family_id}: generation/gates FAILED: {last_error}")
            continue
        if effective_seed != seed:
            print(f"[batch] {family_id}: resampled seed {seed} -> {effective_seed} for layout uniqueness")

        # Pairing audit: conditions of one family must differ only in the
        # declared delta fields; record the observed diff set per instance.
        condition_ids = list(instances)
        pair_diff = None
        if len(condition_ids) > 1:
            base_id = condition_ids[0]
            pair_diff = {
                other: diff_fields(instances[base_id], instances[other])
                for other in condition_ids[1:]
            }

        instances_dir.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        family_file = pool_dir / f"{family_id}.family.json"
        if not family_file.exists():
            family_file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")

        for condition_id, data in instances.items():
            instance_id = f"{family_id}_{condition_id}"
            instance_path = instances_dir / f"{instance_id}.json"
            report_path = reports_dir / f"{instance_id}.report.json"
            # The freshly generated instance always wins over a stale copy.
            instance_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
            report_path.unlink(missing_ok=True)

            existing = next((item for item in pool["instances"] if item["id"] == instance_id), None)
            if existing and "spawn_validated" in existing.get("labels", []):
                skipped += 1
                print(f"[batch] {instance_id}: already spawn_validated, skipping")
                continue

            entry = {
                "id": instance_id, "family_id": family_id, "seed": effective_seed,
                "condition": condition_id, "skeleton": args.skeleton, "arm": args.arm,
                "file": str(instance_path.relative_to(pool_dir)),
                "labels": ["placement_validated"],
                "task_name": data["task_name"],
                 "robot_dossier_snapshot_sha256": pool["robot_dossier_snapshot_sha256"],
                 "payload_policy_hash": pool["payload_policy_hash"],
                "layout_signature": layout_signature(data),
            }
            team_necessary = attach_collaboration_evidence(entry, data)
            if args.require_team_necessary and not team_necessary:
                failed += 1
                print(f"[batch] {instance_id}: collaboration gate FAILED: single robot feasible")
                continue
            if pair_diff is not None:
                entry["pair_diff"] = pair_diff.get(condition_id)
            check_duplicate_layout(entry)
            if args.dry_run:
                upsert_instance(pool, entry)
                save_pool(pool_dir, pool)
                done += 1
                print(f"[batch] {instance_id}: placement_validated (dry-run)")
                continue
            if settle_and_record(entry, instance_path, report_path):
                done += 1
            else:
                failed += 1

    save_pool(pool_dir, pool)
    print(f"[batch] pool={pool_dir} done={done} skipped={skipped} failed={failed} "
          f"total_records={len(pool['instances'])}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
