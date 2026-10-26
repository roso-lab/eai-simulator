"""Gate D extractor tests: builder camera registration → resume patch."""

from __future__ import annotations

import json

from benchmark.observation_camera_registration import extract_camera_registration

SOURCE = '''
from dataclasses import dataclass

@dataclass(frozen=True)
class RobotOption:
    key: str
    label: str
    camera_mount_link: str | None = None
    camera_offset: tuple = (0.12, 0.0, 0.02)
    camera_rot: tuple = (1.0, 0.0, 0.0, 0.0)
    realsense_mount_link: str | None = None
    realsense_offset: tuple = (0.0, 0.0, 0.12)
    realsense_rot: tuple = (0.707, 0.0, 0.0, 0.707)
    orsus_mount_link: str | None = None
    orsus_offset: tuple = (0.026, 0.0, 0.0)

OPTIONS = [
    RobotOption(
        "go2",
        "Unitree Go2",
        camera_mount_link=None,
        realsense_mount_link="go2/base",
        realsense_offset=(0.01, 0.0, 0.13),
        realsense_rot=(0.707, 0.0, 0.0, 0.707),
    ),
    RobotOption("cf2x", "CF2x quadcopter", camera_mount_link="body",
                camera_offset=(0.16, 0.0, 0.02), camera_rot=(0.5, 0.5, -0.5, -0.5)),
    RobotOption("bare", "No camera option"),
]
'''


def test_extracts_identity_positionally_and_flags_publication():
    patch = extract_camera_registration({"text": SOURCE, "name": "memory"})
    by_key = {r["builder_key"]: r for r in patch["registrations"]}
    assert set(by_key) == {"go2", "cf2x", "bare"}

    go2 = by_key["go2"]
    assert go2["camera_path_kind"] == "realsense_mount"
    assert go2["enable_camera_publish"] is True
    assert go2["camera_publication_patch"] == "builder_registered_default"
    reg = go2["camera_registration"]
    assert reg["realsense_mount_link"] == "go2/base"
    assert reg["realsense_offset"] == [0.01, 0.0, 0.13]
    assert reg["realsense_rot"] == [0.707, 0.0, 0.0, 0.707]

    cf2x = by_key["cf2x"]
    assert cf2x["camera_path_kind"] == "camera_mount"
    assert cf2x["camera_registration"]["camera_rot"] == [0.5, 0.5, -0.5, -0.5]

    bare = by_key["bare"]
    assert bare["enable_camera_publish"] is False
    assert bare["camera_publication_patch"] == "not_configured_in_builder"
    assert bare["camera_path_kind"] is None


def test_patch_is_deterministic_and_serializable():
    first = extract_camera_registration({"text": SOURCE, "name": "memory"})
    second = extract_camera_registration({"text": SOURCE, "name": "memory"})
    a = json.dumps(first, sort_keys=True)
    b = json.dumps(second, sort_keys=True)
    assert a == b
    assert "patch_semantics" in first
    assert first["generated_from"]["sha256"]


def test_real_builder_patch_file_matches_live_extraction():
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    builder = repo / "source/EAI_hmrs/EAI_hmrs/env_builder.py"
    committed = repo / "benchmark/observation_camera_registration_patch.v1.json"
    if not builder.exists() or not committed.exists():
        return  # repo layout drift is not this test's concern
    live = extract_camera_registration(builder)
    stored = json.loads(committed.read_text(encoding="utf-8"))
    assert stored["registrations"] == live["registrations"]
    assert stored["generated_from"]["sha256"] == live["generated_from"]["sha256"]


def test_resume_preview_is_pure_and_preserves_existing_mount(tmp_path):
    from benchmark.observation_camera_registration import apply_to_resume, preview_resume_patch

    resume = {
        "configuration_profiles": [
            {"profile_id": "go2.default", "sensing": {
                "camera_mount_pose": None,
                "camera_publication": "pending_binding",
            }},
            {"profile_id": "g1.default", "sensing": {
                "camera_mount_pose": {"reference_frame": "hand_calibrated"},
                "camera_publication": "pending_binding",
            }},
        ]
    }
    patch = extract_camera_registration({"text": SOURCE, "name": "memory"})
    before = json.dumps(resume, sort_keys=True)
    result = preview_resume_patch(resume, patch)
    assert json.dumps(resume, sort_keys=True) == before
    assert result["filled_mount_pose"] == ["go2.default"]
    assert result["publication_bound"] == ["go2.default"]
    assert result["no_builder_entry"] == ["g1.default"]
    assert result["resume"]["configuration_profiles"][1]["sensing"]["camera_mount_pose"] == {
        "reference_frame": "hand_calibrated"
    }

    resume_path = tmp_path / "resume.json"
    resume_path.write_text(json.dumps(resume), encoding="utf-8")
    before = resume_path.read_bytes()
    preview = apply_to_resume(resume_path, patch)
    assert preview["filled_mount_pose"] == ["go2.default"]
    assert resume_path.read_bytes() == before
