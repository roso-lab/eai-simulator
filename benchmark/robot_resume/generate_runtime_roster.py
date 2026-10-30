#!/usr/bin/env python3
"""Build the v3.1 runtime robot roster from the reviewed resume catalog.

This is a CPU-only admission-material generator.  It deliberately does not
probe Isaac, CUDA, or a simulator: ``runtime_qualified`` stays false until a
runtime receipt exists.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RESUME_PATH = OUT / "robot_pool_resume.v1.json"

ASSET_MODULES = {
    "carter": "source/EAI_assets/EAI_assets/robots/carter.py",
    "simple_car": "source/EAI_assets/EAI_assets/robots/simple_car.py",
    "pepper": "source/EAI_assets/EAI_assets/robots/pepper.py",
    "go2": "source/EAI_assets/EAI_assets/robots/go2.py",
    "b2": "source/EAI_assets/EAI_assets/robots/b2.py",
    "m20": "source/EAI_assets/EAI_assets/robots/unitree.py",
    "scout": "source/EAI_assets/EAI_assets/robots/scout.py",
    "mushr_v2": "source/EAI_assets/EAI_assets/robots/mushr_v2.py",
    "g1": "source/EAI_assets/EAI_assets/robots/humanoid.py",
    "cf2x": "source/EAI_assets/EAI_assets/robots/cf2x.py",
    "iris": "source/EAI_assets/EAI_assets/robots/pegasus.py",
    "pegasus": "source/EAI_assets/EAI_assets/robots/pegasus.py",
    "lite3": "source/EAI_assets/EAI_assets/robots/unitree.py",
    "coco": "source/EAI_assets/EAI_assets/robots/coco.py",
    "microduck": "source/EAI_assets/EAI_assets/robots/microduck.py",
    "ur5": "source/EAI_assets/EAI_assets/robots/ur5_mount.py",
    "z1": "source/EAI_assets/EAI_assets/robots/z1_mount.py",
}
ARM_MODULES = {
    "z1": "source/EAI_assets/EAI_assets/robots/z1_mount.py",
    "d1": "source/EAI_assets/EAI_assets/robots/d1_mount.py",
    "ur5": "source/EAI_assets/EAI_assets/robots/ur5_mount.py",
}
SKILL_VERSIONS = {
    "eai.navigation.node_route": {"provider_version": "2.0", "schema_version": "simulator.skill.v1"},
    "eai.manipulation.pick_place": {"provider_version": "2.0.0", "schema_version": "simulator.skill.v1"},
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _digest(value: Any) -> str:
    return _sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def _file_digest(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _source_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["source_id"]: item for item in data["sources"]}


def _source_record(relative: str) -> dict[str, str]:
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": relative, "sha256": _file_digest(path)}


def _restriction_codes(rec: dict[str, Any]) -> list[str]:
    host = rec["identity"]["platform_type"]
    controller = rec["configuration"]["motion_controller"] or ""
    codes: list[str] = []
    if host == "go2" and controller == "GO2_EXTREME_PARKOUR_TEACHER_CFG":
        codes.append("GO2_OFFROAD_MOTION_UNSTABLE")
    if host == "g1":
        codes.append("G1_WALK_GRASP_MODE_EXCLUSIVE_AND_STAIRS_UNSTABLE")
    if host == "cf2x":
        codes.append("CF2X_NO_PICK_PLACE_ADAPTER")
    if host == "lite3":
        codes.append("LITE3_STAIRS_UNSUPPORTED")
    return codes


def _selected(rec: dict[str, Any]) -> bool:
    host = rec["identity"]["platform_type"]
    arm = rec["configuration"]["external_manipulator"]
    role = rec["identity"]["configuration_role"]
    # The v3.1 decision has no Carter+Z1 or M20+Z1 tail, including no
    # deprecated record.  B2+Z1 and Scout+Z1 are policy-promoted directly.
    if (host, arm) in {("carter", "z1"), ("m20", "z1")}:
        return False
    if rec["deployment"]["status"] == "catalog_registered":
        return True
    return (host, role) in {("b2", "mobile_platform"), ("scout", "mobile_platform"),
                            ("ur5", "standalone_arm"), ("z1", "standalone_arm")}


def _asset_sources(rec: dict[str, Any]) -> list[dict[str, str]]:
    host = rec["identity"]["platform_type"]
    arm = rec["configuration"]["external_manipulator"]
    paths = [ASSET_MODULES[host]]
    if arm in ARM_MODULES and ARM_MODULES[arm] not in paths:
        paths.append(ARM_MODULES[arm])
    return [_source_record(path) for path in paths]


def _skill_bindings(rec: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for skill in rec["skills"]:
        if skill["support"] not in {"supported", "supported_unstable"}:
            continue
        skill_id = skill["skill_id"]
        version = SKILL_VERSIONS.get(skill_id)
        if version is None:
            continue
        result.append({"skill_id": skill_id, **version, "backend": skill.get("backend")})
    return result


def _roster_record(rec: dict[str, Any], ordinal: int) -> dict[str, Any]:
    identity = rec["identity"]
    config = rec["configuration"]
    asset_sources = _asset_sources(rec)
    skill_bindings = _skill_bindings(rec)
    capability_input = {
        "source_resume_id": rec["resume_id"],
        "motion_controller": config["motion_controller"],
        "manipulator_controller": config["manipulator_controller"],
        "external_manipulator": config["external_manipulator"],
        "skills": skill_bindings,
        "skill_fingerprint_policy": "skill_change_invalidates_capability_and_runtime_receipt",
    }
    initial_state = {
        "schema_version": "heterocoord.robot.initial_state.v1",
        "source": "catalog_default_spawn_state",
        # Keep the digest configuration-bound even while the instance-level
        # USD/prim spawn pose is pending.  A single generic zero pose would
        # make every robot look interchangeable in the admission audit.
        "platform_type": identity["platform_type"],
        "motion_controller": config["motion_controller"],
        "external_manipulator": config["external_manipulator"],
        "configuration_role": identity["configuration_role"],
        "pose": {"position_m": [0.0, 0.0, 0.0], "orientation_wxyz": [1.0, 0.0, 0.0, 0.0]},
        "joint_state": "controller_default",
        "binding_status": "pending_instance_binding",
    }
    restrictions = _restriction_codes(rec)
    role = identity["configuration_role"]
    admission_class = "independent_arm_immediate" if role == "standalone_arm" else "pool"
    return {
        "instance_id": f"hc-rt-{ordinal:03d}",
        "source_resume_id": rec["resume_id"],
        "profile_id": rec["profile_id"],
        "admission_class": admission_class,
        "platform_type": identity["platform_type"],
        "configuration_role": role,
        "asset_binding": {
            "asset_sources": asset_sources,
            "asset_digest": _digest(asset_sources),
        },
        "capability_binding": {
            "skill_bindings": skill_bindings,
            "capability_digest": _digest(capability_input),
            "source_input": capability_input,
        },
        "initial_state_binding": {
            "initial_state": initial_state,
            "initial_state_digest": _digest(initial_state),
        },
        "restrictions": restrictions,
        "restriction_notes_zh": [
            rec["special_notes_zh"][0] if rec["special_notes_zh"] else "按裁决政策及该配置简历执行限制；运行时验证须逐台留证。"
        ],
        "runtime_qualified": False,
        "runtime_qualification_receipt": None,
        "qualification_policy": "no_receipt_no_true",
    }


def build() -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(RESUME_PATH.read_text())
    selected = [rec for rec in data["resumes"] if _selected(rec)]
    if len(selected) != 27:
        raise RuntimeError(f"v3.1 selection expected 27, got {len(selected)}")
    forbidden = [rec["resume_id"] for rec in selected if rec["configuration"]["external_manipulator"] == "z1" and rec["identity"]["platform_type"] in {"carter", "m20"}]
    if forbidden:
        raise RuntimeError(f"forbidden variants selected: {forbidden}")
    roster = [_roster_record(rec, i) for i, rec in enumerate(selected, 1)]
    sources = [{"source_id": "pool_policy", "path": "benchmark/robot_resume/POOL_ELIGIBILITY_REVIEW_20261026.zh-CN.md",
                "sha256": _file_digest(OUT / "POOL_ELIGIBILITY_REVIEW_20261026.zh-CN.md")},
               {"source_id": "resume_catalog", "path": str(RESUME_PATH.relative_to(ROOT)), "sha256": _file_digest(RESUME_PATH)}]
    doc = {
        "schema_id": "heterocoord.robot_runtime_roster",
        "schema_version": "1.0.0",
        "policy": "装配政策 v3.1；2026-10-26",
        "formal_gates": {"runtime_qualified": False, "physical_qualified": False, "rankable": False, "paper_score_eligible": False},
        "membership_counts": {"total": 27, "pool": sum(r["admission_class"] == "pool" for r in roster),
                              "independent_arm_immediate": sum(r["admission_class"] == "independent_arm_immediate" for r in roster)},
        "excluded_resume_ids": ["carter.CARTER_DIFF_CFG.v0002", "m20.M20_ROUGH_RSL_CFG.v0002"],
        "shared_runtime_condition": "Isaac qualification is queued one process at a time; no receipt is produced by this CPU artifact.",
        "restriction_codes": {
            "GO2_OFFROAD_MOTION_UNSTABLE": "Go2 越野版运动不稳定",
            "G1_WALK_GRASP_MODE_EXCLUSIVE_AND_STAIRS_UNSTABLE": "G1 行走/抓取模式互斥且楼梯上行不稳定、下行未验证",
            "CF2X_NO_PICK_PLACE_ADAPTER": "CF2X 当前无抓放适配器，仅限非抓放任务",
            "LITE3_STAIRS_UNSUPPORTED": "Lite3 不能上下楼",
        },
        "sources": sources,
        "robots": roster,
    }
    audit = []
    for r in roster:
        audit.append({"instance_id": r["instance_id"], "source_resume_id": r["source_resume_id"],
                      "profile_id": r["profile_id"], "asset_digest": r["asset_binding"]["asset_digest"],
                      "capability_digest": r["capability_binding"]["capability_digest"],
                      "initial_state_digest": r["initial_state_binding"]["initial_state_digest"],
                      "restriction_codes": r["restrictions"], "runtime_qualified": r["runtime_qualified"]})
    return doc, audit, selected


def main() -> None:
    doc, audit, _ = build()
    (OUT / "robot_runtime_roster.v1.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    (OUT / "robot_runtime_roster.asset_binding_audit.json").write_text(json.dumps({"status": "draft_cpu_only", "rows": audit}, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 机器人池运行时名册资产绑定对账（v3.1）", "", "CPU 前置产物；未执行 Isaac/GPU，所有 `runtime_qualified` 保持 false。", "", "|实例|来源简历|asset_digest|capability_digest|initial_state_digest|限制|", "|---|---|---|---|---|---|"]
    for row in audit:
        lines.append(f"|{row['instance_id']}|{row['source_resume_id']}|`{row['asset_digest'][:16]}…`|`{row['capability_digest'][:16]}…`|`{row['initial_state_digest'][:16]}…`|{', '.join(row['restriction_codes']) or '—'}|")
    lines += ["", "排除项：`carter.CARTER_DIFF_CFG.v0002`、`m20.M20_ROUGH_RSL_CFG.v0002`；无 deprecated 尾巴。", "", "独立 UR5/Z1 以 `independent_arm_immediate` 登记，但仍需逐台运行时收据才能置真。"]
    (OUT / "robot_runtime_roster.asset_binding_audit.zh-CN.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"status": "pass", "robots": len(doc["robots"]), "pool": doc["membership_counts"]["pool"], "independent_arm_immediate": doc["membership_counts"]["independent_arm_immediate"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
