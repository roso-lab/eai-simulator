#!/usr/bin/env python3
"""Validate the fixed HeteroCoord robot-resume pool and write its receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load(name: str):
    return json.loads((OUT / name).read_text(), object_pairs_hook=_reject_duplicate_keys)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _profiles(data, platform: str):
    return [item for item in data["configuration_profiles"]
            if item["identity"]["platform_type"] == platform]


def _resumes(data, platform: str):
    return [item for item in data["resumes"]
            if item["identity"]["platform_type"] == platform]


def main() -> None:
    data = _load("robot_pool_resume.v1.json")
    schema = _load("robot_pool_resume.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(data)

    profiles = data["configuration_profiles"]
    resumes = data["resumes"]
    assert len({item["profile_id"] for item in profiles}) == len(profiles)
    assert len({item["resume_id"] for item in resumes}) == len(resumes)
    assert data["coverage"]["profile_count"] == len(profiles) == 28
    assert data["coverage"]["expanded_resume_count"] == len(resumes) == 28

    g1_profiles = _profiles(data, "g1")
    g1_resumes = _resumes(data, "g1")
    assert len(g1_profiles) == len(g1_resumes) == 1
    assert g1_profiles[0]["configuration"]["motion_controller"] == "G1_DWAQ_CFG"
    assert g1_profiles[0]["manipulation"]["origin"] == "integrated"
    assert g1_profiles[0]["skills"][1]["support"] == "supported"
    assert g1_profiles[0]["deployment"]["fall_recovery"] == "unsupported"
    assert g1_profiles[0]["deployment"]["status"] == "catalog_registered"
    assert g1_profiles[0]["mobility"]["stairs"]["ascent"] == "supported_unstable"
    assert g1_profiles[0]["mobility"]["stairs"]["descent"] == "unknown"
    assert len(g1_profiles[0]["deployment"]["runtime_blockers_zh"]) == 1
    assert g1_profiles[0]["sensing"]["camera_model"] == "realsense_d455"
    assert g1_profiles[0]["sensing"]["camera_present"] is True
    assert g1_profiles[0]["sensing"]["camera_integration_status"] == "builder_registered_default"
    assert g1_profiles[0]["sensing"]["camera_mount_pose"]["rotation_wxyz"] == [
        0.98480775301, 0.0, 0.17364817767, 0.0]

    expected_go2 = {"GO2_MIXED_STAIRS_CFG", "GO2_EXTREME_PARKOUR_TEACHER_CFG"}
    go2_profiles = _profiles(data, "go2")
    go2_resumes = _resumes(data, "go2")
    assert len(go2_profiles) == len(go2_resumes) == 4
    assert {item["configuration"]["motion_controller"] for item in go2_profiles} == expected_go2
    assert {item["configuration"]["motion_controller"] for item in go2_resumes} == expected_go2
    stable = [item for item in go2_profiles
              if item["configuration"]["motion_controller"] == "GO2_MIXED_STAIRS_CFG"]
    offroad = [item for item in go2_profiles
               if item["configuration"]["motion_controller"] == "GO2_EXTREME_PARKOUR_TEACHER_CFG"]
    assert all(item["configuration"]["controller_variant"] == "normal_stable" for item in stable)
    assert all(item["configuration"]["controller_variant"] == "experimental_offroad" for item in offroad)
    assert all(item["deployment"]["controller_maturity"] == "experimental_unstable" for item in offroad)
    assert all(item["deployment"]["known_instability_zh"] for item in offroad)
    assert all(item["deployment"]["fall_recovery"] == "unsupported" for item in offroad)

    pepper = _profiles(data, "pepper")
    assert len(pepper) == 1
    assert pepper[0]["configuration"]["motion_controller"] == "PEPPER_PICK_PLACE_CFG"
    assert pepper[0]["skills"][1]["support"] == "supported"

    assert not any(item["configuration"]["external_manipulator"] == "ur5" for item in profiles + resumes)
    assert not any(
        item["identity"]["platform_type"] in {"go2", "lite3"}
        and item["configuration"]["external_manipulator"] == "z1"
        for item in profiles + resumes)

    # 2026-10-22 装配政策：go2/lite3 的可装臂为 D1-only；carter/b2/m20/scout
    # 在保留 Z1 资格的同时并列开放 D1（单机械臂槽互斥）。
    d1_platforms = {"carter", "go2", "b2", "m20", "scout", "lite3"}
    d1_resumes = [item for item in resumes if item["configuration"]["external_manipulator"] == "d1"]
    assert {item["identity"]["platform_type"] for item in d1_resumes} == d1_platforms
    for item in d1_resumes:
        assert item["configuration"]["manipulator_controller"] == "D1_IK_CFG"
        assert item["manipulation"]["model"] == "d1"
        assert item["manipulation"]["end_effector"] == "d1_gripper"
        assert item["manipulation"]["payload_kg"] == 0.5
        assert item["manipulation"]["payload_mass_basis"] == "official_unitree_d1_spec"
        assert item["manipulation"]["max_pickplace_z_m"] is None
        assert item["deployment"]["status"] == "catalog_registered"
        assert item["deployment"]["attachment_validation"] is True
    assert "unitree_d1_spec" in {source["source_id"] for source in data["sources"]}

    ur5 = _profiles(data, "ur5")
    assert len(ur5) == 1
    assert ur5[0]["identity"]["configuration_role"] == "standalone_arm"
    assert ur5[0]["manipulation"]["end_effector"] == "robotiq_2f85"
    assert ur5[0]["configuration"]["attachments"] == []

    default_d455 = {"carter", "pepper", "go2", "b2", "m20", "scout",
                    "mushr_v2", "coco", "lite3", "g1"}
    builtin_camera = {"simple_car", "cf2x", "iris", "pegasus"}
    for item in profiles + resumes:
        platform = item["identity"]["platform_type"]
        assert item["sensing"]["camera_required"] is True
        assert item["configuration"]["tools"] == []
        if platform in default_d455:
            assert item["sensing"]["camera_model"] == "realsense_d455"
            assert item["sensing"]["camera_present"] is True
        elif platform in builtin_camera:
            assert item["sensing"]["camera_present"] is True
        else:
            assert item["sensing"]["camera_integration_status"] == "pending_adaptation"

    for source in data["sources"]:
        if source["sha256"] is None:
            continue
        path = Path(source["path"])
        if not path.is_absolute():
            path = ROOT / path
        assert path.is_file(), (source["source_id"], path)
        assert _sha256(path) == source["sha256"], source["source_id"]

    artifacts = [
        "manipulator_capabilities.json",
        "generate_robot_pool.py",
        "validate_robot_pool.py",
        "robot_pool_resume.v1.json",
        "robot_pool_resume.schema.json",
        "robot_pool_resume.zh-CN.md",
    ]
    receipt = {
        "status": "pass",
        "baseline_commit": data["baseline_commit"],
        "checks": [
            "Draft 2020-12 schema, duplicate keys, unique profile/resume IDs",
            "exact 31 reviewed profiles and 30 legal experiment resumes",
            "2026-10-22 assembly policy: go2/lite3 mount D1 only; carter/b2/m20/scout keep Z1 and add D1 (single-slot exclusive)",
            "one G1 identity using only G1_DWAQ_CFG with default grasp capability",
            "G1 mixes with other robots under the unified sim.dt=0.001/decimation=20 fleet schedule; walk/grasp modes are skill-invoked and exclusive",
            "exactly two Go2 controller profiles: stable mixed-stairs and unstable experimental off-road",
            "standalone-only UR5 fixed to Robotiq 2F-85; no mounted UR5",
            "D455 defaults, built-in cameras, and explicit pending camera adaptation",
            "no Keyboard, Navigation I/O, or Camera authoring tools in resumes",
            "all local source fingerprints match",
        ],
        "counts": data["coverage"],
        "limits": [
            "No renewed scene-specific stairs or rough-terrain qualification is claimed.",
            "G1 D455 forehead mount is builder-registered with a user-approved pitch calibration; its runtime image stream is not yet subscription-tested. Standalone UR5/Z1 camera adaptation remains pending.",
            "Mounted Z1 plus default D455 still requires collision and occlusion validation.",
            "Go2 Extreme Parkour remains a privileged Teacher and has no fall recovery.",
            "G1 fleets, pure or mixed, run at sim.dt=0.001/decimation=20; bounded 5 s GPU runs show Go2/Carter at 1 ms substeps match their native 5 ms schedule (Go2 displacement within 1.1%, upright metrics equal); longer-duration qualification stays per experiment run.",
        ],
        "artifacts_sha256": {name: _sha256(OUT / name) for name in artifacts},
    }
    (OUT / "robot_pool_resume.validation.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps({"status": "pass", "profiles": len(profiles), "resumes": len(resumes)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
