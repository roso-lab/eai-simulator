#!/usr/bin/env python3
"""CPU-only validation for the v3.1 runtime roster."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Keep the CPU audit runnable in the minimal worker image.
    Draft202012Validator = None

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(name: str):
    return json.loads((OUT / name).read_text())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=OUT / "robot_runtime_roster.v1.json",
                        help="roster file to validate; defaults to the checked-in v1 roster")
    parser.add_argument("--output", type=Path, default=OUT / "robot_runtime_roster.validation.json",
                        help="where to write the validation receipt")
    args = parser.parse_args(argv)
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    schema = _load("robot_runtime_roster.schema.json")
    if Draft202012Validator is not None:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(roster)
    else:
        # The structural assertions below are the required fallback check; a
        # full Draft 2020-12 pass is used automatically when jsonschema exists.
        assert schema["$id"].endswith("robot_runtime_roster.v1.json")
        assert schema["properties"]["robots"]["maxItems"] == 27
    robots = roster["robots"]
    assert len(robots) == 27
    assert len({r["instance_id"] for r in robots}) == 27
    assert len({r["source_resume_id"] for r in robots}) == 27
    assert sum(r["admission_class"] == "pool" for r in robots) == 25
    assert sum(r["admission_class"] == "independent_arm_immediate" for r in robots) == 2
    forbidden = {"carter.CARTER_DIFF_CFG.v0002", "m20.M20_ROUGH_RSL_CFG.v0002"}
    assert forbidden.isdisjoint({r["source_resume_id"] for r in robots})
    assert all(type(r["runtime_qualified"]) is bool for r in robots)
    for row in robots:
        if row["runtime_qualified"]:
            assert isinstance(row["runtime_qualification_receipt"], dict)
            from runtime_qualification_receipt import validate_receipt
            validate_receipt(row["runtime_qualification_receipt"], roster_row=row)
        else:
            assert row["runtime_qualification_receipt"] is None or isinstance(row["runtime_qualification_receipt"], dict)
    restrictions = {code: 0 for code in roster["restriction_codes"]}
    for r in robots:
        for code in r["restrictions"]:
            restrictions[code] += 1
        assert len(r["asset_binding"]["asset_digest"]) == 64
        assert len(r["capability_binding"]["capability_digest"]) == 64
        assert len(r["initial_state_binding"]["initial_state_digest"]) == 64
        for asset in r["asset_binding"]["asset_sources"]:
            path = ROOT / asset["path"]
            assert path.is_file(), path
            assert _sha256(path) == asset["sha256"], asset["path"]
    assert restrictions == {
        "GO2_OFFROAD_MOTION_UNSTABLE": 2,
        "G1_WALK_GRASP_MODE_EXCLUSIVE_AND_STAIRS_UNSTABLE": 1,
        "CF2X_NO_PICK_PLACE_ADAPTER": 1,
        "LITE3_STAIRS_UNSUPPORTED": 2,
    }
    assert roster["formal_gates"] == {"runtime_qualified": False, "physical_qualified": False, "rankable": False, "paper_score_eligible": False}
    # Honest reporting: the flag reflects the roster being validated, not this
    # CPU validator.  A promoted roster with embedded receipts (historical or
    # user-ruling registration) has qualification evidence registered; the base
    # roster has none.
    qualified_count = sum(1 for row in robots if row["runtime_qualified"])
    receipt = {"status": "pass", "scope": "cpu_roster_only", "robots": 27,
               "pool": 25, "independent_arm_immediate": 2,
               "excluded_absent": sorted(forbidden), "restriction_counts": restrictions,
               "runtime_qualification_performed": qualified_count > 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
