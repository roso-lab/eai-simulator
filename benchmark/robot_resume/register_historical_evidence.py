#!/usr/bin/env python3
"""Register explicitly reviewed historical integration evidence.

Unlike the retired keyword inventory, this module has an exact instance keyed
matrix.  A historical receipt is emitted only when an actual recorded result
supports that platform/configuration.  Merely having a runnable smoke script is
not evidence; a commit's recorded validation result is accepted under the
user's 2026-10-26 decision.  Unmatched entries remain false and are listed.

Opt-in ``--ruling-commit``/``--ruling-record`` additionally registers the
twelve instances the user approved by final ruling on 2026-10-26 without an
exact historical record.  The ruling record itself is then the sole receipt
source; no platform artifact is invented.  Without both parameters the twelve
instances stay unknown (fail-closed) exactly as before.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

try:
    from .historical_runtime_receipt import make_historical_receipt
except ImportError:
    from historical_runtime_receipt import make_historical_receipt

ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = {
    "decision": "user_historical_integration_accepted",
    "decision_date": "2026-10-26",
    "scope": "existing smoke, calibration, diagnostic and integration results may establish roster runtime qualification; formal physical/ranking/paper gates stay false",
}
RULING_AUTHORITY = {
    "decision": "user_runtime_unknown_ruling_accepted",
    "decision_date": "2026-10-26",
    "scope": "user final ruling grants runtime qualification only for the twelve listed instances; no platform artifact is claimed and formal physical/ranking/paper gates stay false",
}
# The exact instance list approved by RUNTIME_RULING_20261026.zh-CN.md.  The
# ruling path refuses to run unless the roster's unknown set equals this list.
RULING_INSTANCE_IDS = ("hc-rt-002", "hc-rt-003", "hc-rt-008", "hc-rt-011",
                       "hc-rt-013", "hc-rt-015", "hc-rt-016", "hc-rt-017",
                       "hc-rt-019", "hc-rt-022", "hc-rt-023", "hc-rt-024")
RULING_RECORD_LOCATOR = "「批准名单（12 台，逐字 instance_id）」表格：全部批准 runtime qualification"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source(path: str, commit: str, locator: str, assertions: tuple[str, ...]) -> dict[str, Any]:
    target = ROOT / path
    if not target.is_file():
        raise ValueError(f"reviewed historical source disappeared: {path}")
    return {"path": path, "sha256": _sha(target), "source_commit": commit,
            "record_locator": locator, "content_assertions": list(assertions)}


def _record(source_specs: list[tuple[str, str, str, tuple[str, ...]]], detail: str) -> dict[str, Any]:
    sources = [_source(*spec) for spec in source_specs]
    refs = [str(index) for index in range(len(sources))]
    checks = {
        "integration_record": {"status": "passed", "source_refs": refs,
                               "details": {"recorded": detail}},
        "functional_record": {"status": "passed", "source_refs": refs,
                              "details": {"recorded": detail}},
        "configuration_binding": {"status": "passed", "source_refs": refs,
                                  "details": {"binding": "exact instance/platform/configuration scope"}},
    }
    return {"sources": sources, "checks": checks}


def _ruling_context(ruling_commit: str, ruling_record: str) -> dict[str, Any]:
    """Evidence context whose sole source is the committed user ruling record.

    Fail-closed by construction: the record must live under the repository
    root, and every ``hc-rt-###`` id it mentions must be exactly the approved
    twelve -- a missing, renamed or extra id refuses registration outright.
    """
    record = Path(ruling_record)
    if not record.is_absolute():
        record = ROOT / ruling_record
    try:
        relative = record.resolve().relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"ruling record escapes the repository root: {ruling_record}") from exc
    if not record.is_file() or record.is_symlink():
        raise ValueError(f"ruling record is not a regular file: {ruling_record}")
    text = record.read_text(encoding="utf-8", errors="replace")
    listed = frozenset(re.findall(r"hc-rt-\d{3}", text))
    expected = frozenset(RULING_INSTANCE_IDS)
    if listed != expected:
        raise ValueError("ruling record instance list does not exactly match the approved twelve; "
                         f"missing={sorted(expected - listed)}, unexpected={sorted(listed - expected)}")
    source = _source(relative.as_posix(), ruling_commit, RULING_RECORD_LOCATOR, RULING_INSTANCE_IDS)
    refs = ["0"]
    checks = {
        "integration_record": {"status": "passed", "source_refs": refs,
                               "details": {"basis": "user final ruling 2026-10-26",
                                           "recorded": "no recorded integration result; user ruling accepted in lieu"}},
        "functional_record": {"status": "passed", "source_refs": refs,
                              "details": {"basis": "user final ruling 2026-10-26",
                                          "recorded": "no platform run claimed; the ruling record is the sole source"}},
        "configuration_binding": {"status": "passed", "source_refs": refs,
                                  "details": {"binding": "exact instance id enumerated verbatim in the ruling record"}},
    }
    return {"source": source, "checks": checks, "authority": RULING_AUTHORITY}


def evidence_matrix() -> dict[str, dict[str, Any]]:
    """Reviewed evidence matrix.  An absent instance is deliberately unknown."""

    arm = ("tools/scenario_generation/ARM_DEPLOYMENT_ROADMAP.zh-CN.md",
           "c840ac1a35a901a998f197d60ad9ff00de4afc1d",
           "runtime mount/base matrices, lines 115-180")
    unified = ("tools/validation/UNIFIED_PICK_PLACE_VALIDATION.zh-CN.md",
               "44d46681b1ef9f4cffcb67b44fdb34bd5c3c22a2",
               "Isaac continuous ten-trial result table and model scope")
    d1_commit = "3c8c9006636c7529a875bbd68171fb653082dd1b"
    return {
        "hc-rt-001": _record([
            (*arm, ("carter | 裸 | pass", "倾角 0.002 rad")),
        ], "Carter bare-host runtime diagnostic passed in the mount matrix."),
        "hc-rt-004": _record([
            (*unified, ("Pepper、G1 各完成", "Pepper | 10/10 | 10/10")),
        ], "Pepper completed ten pick/place trials with observed terminal states."),
        "hc-rt-005": _record([
            ("tools/topology_authoring/MULTILEVEL_VALIDATION.md",
             "c840ac1a35a901a998f197d60ad9ff00de4afc1d",
             "Go2 go2-01/go2-02 physical validation row and reproduction command",
             ("GO2_MIXED_STAIRS_CFG", "两次 physical_pass=true", "进程 exit 0")),
        ], "Go2 mixed-stairs configuration completed two physical-pass stair runs."),
        "hc-rt-006": _record([
            ("tools/validation/mobile_d1_mount_smoke.py", d1_commit,
             "commit 3c8c9006 recorded mount smoke 14/14 for the Stage-4 Go2+D1 fixture",
             ("Stage-4 physics smoke test", "Unitree Go2", "mounted D1 arm")),
            ("source/EAI_assets/EAI_assets/robots/d1_mount.py", d1_commit,
             "D1_MOUNT_PROFILES exact go2 mount binding",
             ("D1_MOUNT_PROFILES", 'D1MountProfile("go2", "base"')),
        ], "Go2+D1 mount smoke recorded 14/14 for the exact host/arm profile."),
        "hc-rt-007": _record([
            ("tools/locomotion_deployment/go2_play_result.json",
             "e8f97448a55feef76d9ec1ed993f01695580efc6",
             "actual repository USD physics playback result",
             ('"robot": "go2"', '"steps": 250', '"finite": true')),
            ("tools/locomotion_deployment/isaac_import_verification.json",
             "e8f97448a55feef76d9ec1ed993f01695580efc6",
             "Isaac import verification for the exact controller",
             ("GO2_EXTREME_PARKOUR_TEACHER_CFG", '"passed": true')),
        ], "Go2 Extreme controller/import and bounded real-USD playback passed."),
        "hc-rt-009": _record([
            (*arm, ("B2 的空载和 Z1 挂载静止站姿", "B2 | 74.5803 / 96.4992 | pass | pass")),
        ], "B2 bare-host and mounted stationary diagnostics are recorded as passed."),
        "hc-rt-010": _record([
            (*arm, ("B2 的空载和 Z1 挂载静止站姿", "B2 | `conditional`", "已完成未缩放静止摆臂测试")),
        ], "B2+Z1 unscaled stationary arm/stance record passed with limitations."),
        "hc-rt-012": _record([
            (*arm, ("m20 | 裸 | pass", "倾角 0.001")),
        ], "M20 bare-host runtime diagnostic passed in the recorded matrix."),
        "hc-rt-014": _record([
            (*arm, ("Scout | 52.0000 / 73.9189 | pass | pass", "Scout 接触报告")),
        ], "Scout runtime diagnostics recorded passing motion/support checks."),
        "hc-rt-018": _record([
            (*unified, ("G1 | 10/10 | 10/10", "最终修正后已重新完成 G1 十轮")),
            ("tools/locomotion_deployment/g1_play_result.json",
             "e8f97448a55feef76d9ec1ed993f01695580efc6",
             "actual repository USD physics playback result",
             ('"robot": "g1"', '"steps": 250', '"finite": true')),
        ], "G1 has ten-trial manipulation and bounded locomotion records."),
        "hc-rt-020": _record([
            (*unified, ("PegasusIris", "PegasusIris | 10/10 | 10/10")),
        ], "Pegasus Iris completed ten pick/place integration trials."),
        "hc-rt-021": _record([
            (*unified, ("PegasusX4", "PegasusX4 | 10/10 | 10/10")),
        ], "Pegasus X4 completed ten pick/place integration trials."),
        "hc-rt-025": _record([
            (*unified, ("UR5 | 10/10 | 10/10", "固定基座")),
        ], "Standalone fixed-base UR5 completed ten pick/place trials."),
        "hc-rt-026": _record([
            (*unified, ("Z1 | 10/10 | 10/10", "固定基座")),
        ], "Standalone fixed-base Z1 completed ten pick/place trials."),
        "hc-rt-027": _record([
            ("tools/validation/mobile_microduck_smoke.py",
             "f51164de6b01016c88a368ec5bdf55aa6962cb5b",
             "commit f51164de recorded EAI-session smoke 12/12",
             ("Stage-5 smoke", "MicroDuck", "stand/walk/turn", "SUMMARY")),
        ], "MicroDuck commit records EAI-session smoke 12/12 for stand/walk/turn."),
    }


def _scope(row: Mapping[str, Any]) -> dict[str, Any]:
    source = row["capability_binding"]["source_input"]
    return {"platform_type": row["platform_type"],
            "configuration_role": row["configuration_role"],
            "motion_controller": source.get("motion_controller"),
            "manipulator_controller": source.get("manipulator_controller"),
            "external_manipulator": source.get("external_manipulator"),
            "limitations": list(row.get("restrictions", []))}


def register(roster_path: Path, *, receipt_dir: Path | None = None,
             promoted_roster_path: Path | None = None,
             ruling_commit: str | None = None,
             ruling_record: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if (ruling_commit is None) != (ruling_record is None):
        raise ValueError("user-ruling registration requires both ruling_commit and ruling_record")
    ruling = _ruling_context(ruling_commit, ruling_record) if ruling_commit is not None else None
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    if roster.get("schema_id") != "heterocoord.robot_runtime_roster" or len(roster.get("robots", [])) != 27:
        raise ValueError("historical registration requires the exact 27-entry runtime roster")
    matrix = evidence_matrix()
    if ruling is not None:
        unknown_ids = {row["instance_id"] for row in roster["robots"]} - set(matrix)
        if unknown_ids != frozenset(RULING_INSTANCE_IDS):
            raise ValueError("user-ruling registration requires the roster unknown set to be "
                             f"exactly the approved twelve; got {sorted(unknown_ids)}")
    promoted = json.loads(json.dumps(roster, ensure_ascii=False))
    rows = []
    for row in promoted["robots"]:
        evidence = matrix.get(row["instance_id"])
        if evidence is None and ruling is not None:
            receipt = make_historical_receipt(
                roster_row=row, receipt_id=f"{row['instance_id']}.user-ruling.20261026",
                authority=ruling["authority"], platform_scope=_scope(row),
                sources=[ruling["source"]], checks=ruling["checks"], root=ROOT)
            row["runtime_qualified"] = True
            row["runtime_qualification_receipt"] = receipt
            if receipt_dir is not None:
                receipt_dir.mkdir(parents=True, exist_ok=True)
                (receipt_dir / f"{row['instance_id']}.json").write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            rows.append({"instance_id": row["instance_id"], "source_resume_id": row["source_resume_id"],
                         "status": "qualified_user_ruling", "runtime_qualified": True,
                         "receipt_digest": receipt["receipt_digest"]})
            continue
        if evidence is None:
            row["runtime_qualified"] = False
            row["runtime_qualification_receipt"] = None
            rows.append({"instance_id": row["instance_id"], "source_resume_id": row["source_resume_id"],
                         "status": "unknown_no_exact_historical_record", "runtime_qualified": False,
                         "reason": "no reviewed result supports this exact platform/configuration"})
            continue
        receipt = make_historical_receipt(
            roster_row=row, receipt_id=f"{row['instance_id']}.historical.20261026",
            authority=AUTHORITY, platform_scope=_scope(row), sources=evidence["sources"],
            checks=evidence["checks"], root=ROOT)
        row["runtime_qualified"] = True
        row["runtime_qualification_receipt"] = receipt
        if receipt_dir is not None:
            receipt_dir.mkdir(parents=True, exist_ok=True)
            (receipt_dir / f"{row['instance_id']}.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows.append({"instance_id": row["instance_id"], "source_resume_id": row["source_resume_id"],
                     "status": "qualified_historical_integration", "runtime_qualified": True,
                     "receipt_digest": receipt["receipt_digest"]})
    qualified = sum(item["runtime_qualified"] for item in rows)
    report = {"schema": "heterocoord.historical_runtime_evidence_registration.v1",
              "authority": AUTHORITY, "source_roster": str(roster_path),
              "source_roster_sha256": _sha(roster_path), "robot_count": len(rows),
              "qualified_count": qualified, "unknown_count": len(rows) - qualified,
              "rows": rows,
              "formal_gates": {"physical_qualified": False, "rankable": False,
                               "paper_score_eligible": False}}
    promotion_authority = dict(AUTHORITY)
    if ruling is not None:
        report["ruling"] = {
            "authority": RULING_AUTHORITY,
            "record_path": ruling["source"]["path"],
            "record_sha256": ruling["source"]["sha256"],
            "source_commit": ruling["source"]["source_commit"],
            "instance_ids": list(RULING_INSTANCE_IDS),
            "qualified_count": sum(item["status"] == "qualified_user_ruling" for item in rows),
        }
    promoted["promotion"] = {
        "schema": "heterocoord.robot_runtime_roster_historical_promotion.v1",
        "authority": promotion_authority, "qualified_count": qualified,
        "unknown_count": len(rows) - qualified,
        "registration_digest": hashlib.sha256(json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    if ruling is not None:
        promoted["promotion"]["ruling_authority"] = dict(RULING_AUTHORITY)
    if promoted_roster_path is not None:
        promoted_roster_path.parent.mkdir(parents=True, exist_ok=True)
        promoted_roster_path.write_text(json.dumps(promoted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report, promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path)
    parser.add_argument("--promoted-roster", type=Path)
    parser.add_argument("--ruling-commit", help="git commit holding the ruling record bytes; enables the twelve-instance user-ruling path")
    parser.add_argument("--ruling-record", help="repository-relative path of the 2026-10-26 ruling record")
    args = parser.parse_args(argv)
    if (args.ruling_commit is None) != (args.ruling_record is None):
        parser.error("--ruling-commit and --ruling-record must be passed together")
    report, _ = register(args.roster.resolve(), receipt_dir=args.receipt_dir,
                         promoted_roster_path=args.promoted_roster,
                         ruling_commit=args.ruling_commit, ruling_record=args.ruling_record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("robot_count", "qualified_count", "unknown_count")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
