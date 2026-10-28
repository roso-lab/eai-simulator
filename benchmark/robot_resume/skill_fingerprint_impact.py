#!/usr/bin/env python3
"""Audit the blast radius of a skill-provider fingerprint/version change.

The command is intentionally non-mutating.  A changed skill invalidates the
old capability digest and any runtime receipt for every roster entry that
binds that skill; callers must recompute and rerun qualification explicitly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

OUT = Path(__file__).resolve().parent


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_changes(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        skill, separator, version = value.partition("=")
        if not separator or not skill or not version:
            raise ValueError(f"skill change must be SKILL_ID=VERSION: {value!r}")
        if skill in result and result[skill] != version:
            raise ValueError(f"conflicting versions for {skill}")
        result[skill] = version
    return result


def affected_entries(roster: Mapping[str, Any], changes: Mapping[str, str]) -> list[dict[str, Any]]:
    if not changes:
        return []
    affected = []
    for robot in roster["robots"]:
        old = {item["skill_id"]: item["provider_version"] for item in robot["capability_binding"]["skill_bindings"]}
        changed = {skill: {"old_provider_version": old.get(skill), "new_provider_version": version}
                   for skill, version in changes.items() if skill in old and old[skill] != version}
        if not changed:
            continue
        affected.append({
            "instance_id": robot["instance_id"],
            "source_resume_id": robot["source_resume_id"],
            "changed_skills": changed,
            "old_capability_digest": robot["capability_binding"]["capability_digest"],
            "actions": ["recompute_capability_digest", "invalidate_previous_runtime_receipt", "rerun_runtime_qualification"],
            "runtime_qualified_after_change": False,
        })
    return affected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roster", type=Path, default=OUT / "robot_runtime_roster.v1.json")
    parser.add_argument("--skill", action="append", default=[], help="SKILL_ID=NEW_PROVIDER_VERSION")
    args = parser.parse_args()
    changes = parse_changes(args.skill)
    roster = json.loads(args.roster.read_text())
    result = {"status": "pass", "changes": changes, "affected_count": 0,
              "revalidation_required": False, "affected_entries": []}
    result["affected_entries"] = affected_entries(roster, changes)
    result["affected_count"] = len(result["affected_entries"])
    result["revalidation_required"] = bool(result["affected_entries"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
