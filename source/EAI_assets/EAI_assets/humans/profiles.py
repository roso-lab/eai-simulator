# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Canonical skeleton joint profiles used by human motion sources."""

from __future__ import annotations

from collections.abc import Mapping


SMPLX_70_JOINTS = (
    "pelvis",
    "pelvis/left_hip",
    "pelvis/left_hip/left_knee",
    "pelvis/left_hip/left_knee/left_ankle",
    "pelvis/left_hip/left_knee/left_ankle/left_foot",
    "pelvis/left_hip/left_knee/left_ankle/left_foot/left_foot_end",
    "pelvis/right_hip",
    "pelvis/right_hip/right_knee",
    "pelvis/right_hip/right_knee/right_ankle",
    "pelvis/right_hip/right_knee/right_ankle/right_foot",
    "pelvis/right_hip/right_knee/right_ankle/right_foot/right_foot_end",
    "pelvis/spine1",
    "pelvis/spine1/spine2",
    "pelvis/spine1/spine2/spine3",
    "pelvis/spine1/spine2/spine3/neck",
    "pelvis/spine1/spine2/spine3/neck/head",
    "pelvis/spine1/spine2/spine3/neck/head/jaw",
    "pelvis/spine1/spine2/spine3/neck/head/jaw/jaw_end",
    "pelvis/spine1/spine2/spine3/neck/head/left_eye_smplhf",
    "pelvis/spine1/spine2/spine3/neck/head/left_eye_smplhf/left_eye_smplhf_end",
    "pelvis/spine1/spine2/spine3/neck/head/right_eye_smplhf",
    "pelvis/spine1/spine2/spine3/neck/head/right_eye_smplhf/right_eye_smplhf_end",
    "pelvis/spine1/spine2/spine3/left_collar",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_index1",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_index1/left_index2",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_index1/left_index2/left_index3",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_index1/left_index2/left_index3/left_index3_end",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_middle1",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_middle1/left_middle2",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_middle1/left_middle2/left_middle3",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_middle1/left_middle2/left_middle3/left_middle3_end",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_pinky1",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_pinky1/left_pinky2",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_pinky1/left_pinky2/left_pinky3",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_pinky1/left_pinky2/left_pinky3/left_pinky3_end",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_ring1",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_ring1/left_ring2",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_ring1/left_ring2/left_ring3",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_ring1/left_ring2/left_ring3/left_ring3_end",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_thumb1",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_thumb1/left_thumb2",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_thumb1/left_thumb2/left_thumb3",
    "pelvis/spine1/spine2/spine3/left_collar/left_shoulder/left_elbow/left_wrist/left_thumb1/left_thumb2/left_thumb3/left_thumb3_end",
    "pelvis/spine1/spine2/spine3/right_collar",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_index1",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_index1/right_index2",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_index1/right_index2/right_index3",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_index1/right_index2/right_index3/right_index3_end",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_middle1",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_middle1/right_middle2",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_middle1/right_middle2/right_middle3",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_middle1/right_middle2/right_middle3/right_middle3_end",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_pinky1",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_pinky1/right_pinky2",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_pinky1/right_pinky2/right_pinky3",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_pinky1/right_pinky2/right_pinky3/right_pinky3_end",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_ring1",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_ring1/right_ring2",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_ring1/right_ring2/right_ring3",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_ring1/right_ring2/right_ring3/right_ring3_end",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_thumb1",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_thumb1/right_thumb2",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_thumb1/right_thumb2/right_thumb3",
    "pelvis/spine1/spine2/spine3/right_collar/right_shoulder/right_elbow/right_wrist/right_thumb1/right_thumb2/right_thumb3/right_thumb3_end",
)

CANONICAL_PROFILES: dict[str, tuple[str, ...]] = {
    "smplx_70": SMPLX_70_JOINTS,
}

# ---------------------------------------------------------------------------
# Cross-profile joint aliases.
#
# The RPM skeletons use Mixamo-style leaf names ("hip", "upperleg_l") while
# the SMPL-X family (smplx_70 and its leaf-trimmed subset synbody_55) uses
# SMPL-X names ("pelvis", "left_hip").  Both families share the same chain
# topology for the mapped region, so a whole relative joint path can be
# translated by rewriting every leaf segment.  Names that are identical in
# both families (neck, head, jaw, jaw_end) are listed explicitly so callers
# can detect fallback mistakes in tests.
# ---------------------------------------------------------------------------

#: rpm leaf name -> SMPL-X family leaf name, for every joint both families
#: share.  High-risk semantic pairs are annotated: the SMPL "hip" joint sits
#: at the thigh root, "ankle" names the foot's parent joint and "foot" the
#: ball, while rpm shifts those roles one segment down the chain.
_RPM_TO_SMPLX_LEAF_ALIASES: dict[str, str] = {
    # Torso and head.  hip is the rpm root joint; the SMPL root is pelvis.
    "hip": "pelvis",
    "spine_01": "spine1",
    "spine_02": "spine2",
    "spine_03": "spine3",
    "neck": "neck",
    "head": "head",
    "jaw": "jaw",
    "jaw_end": "jaw_end",
    # Eyes.
    "eye_l": "left_eye_smplhf",
    "eye_end_l": "left_eye_smplhf_end",
    "eye_r": "right_eye_smplhf",
    "eye_end_r": "right_eye_smplhf_end",
    # Shoulders and arms: rpm names the bone segments below each joint,
    # while the SMPL family names the joints themselves.
    "shoulder_l": "left_collar",
    "upperarm_l": "left_shoulder",
    "lowerarm_l": "left_elbow",
    "hand_l": "left_wrist",
    "shoulder_r": "right_collar",
    "upperarm_r": "right_shoulder",
    "lowerarm_r": "right_elbow",
    "hand_r": "right_wrist",
    # Left fingers (thumb, index, middle, ring, pinky; 3 phalanges + end).
    "thumb_01_l": "left_thumb1",
    "thumb_02_l": "left_thumb2",
    "thumb_03_l": "left_thumb3",
    "thumb_end_l": "left_thumb3_end",
    "index_01_l": "left_index1",
    "index_02_l": "left_index2",
    "index_03_l": "left_index3",
    "index_end_l": "left_index3_end",
    "middle_01_l": "left_middle1",
    "middle_02_l": "left_middle2",
    "middle_03_l": "left_middle3",
    "middle_end_l": "left_middle3_end",
    "ring_01_l": "left_ring1",
    "ring_02_l": "left_ring2",
    "ring_03_l": "left_ring3",
    "ring_end_l": "left_ring3_end",
    "pinky_01_l": "left_pinky1",
    "pinky_02_l": "left_pinky2",
    "pinky_03_l": "left_pinky3",
    "pinky_end_l": "left_pinky3_end",
    # Right fingers.
    "thumb_01_r": "right_thumb1",
    "thumb_02_r": "right_thumb2",
    "thumb_03_r": "right_thumb3",
    "thumb_end_r": "right_thumb3_end",
    "index_01_r": "right_index1",
    "index_02_r": "right_index2",
    "index_03_r": "right_index3",
    "index_end_r": "right_index3_end",
    "middle_01_r": "right_middle1",
    "middle_02_r": "right_middle2",
    "middle_03_r": "right_middle3",
    "middle_end_r": "right_middle3_end",
    "ring_01_r": "right_ring1",
    "ring_02_r": "right_ring2",
    "ring_03_r": "right_ring3",
    "ring_end_r": "right_ring3_end",
    "pinky_01_r": "right_pinky1",
    "pinky_02_r": "right_pinky2",
    "pinky_03_r": "right_pinky3",
    "pinky_end_r": "right_pinky3_end",
    # Legs: the rpm chain inserts a "ball" level between foot and toe, so
    # rpm "foot" pairs with SMPL "ankle" and rpm "ball" with SMPL "foot".
    "upperleg_l": "left_hip",
    "lowerleg_l": "left_knee",
    "foot_l": "left_ankle",
    "ball_l": "left_foot",
    "foot_end_l": "left_foot_end",
    "upperleg_r": "right_hip",
    "lowerleg_r": "right_knee",
    "foot_r": "right_ankle",
    "ball_r": "right_foot",
    "foot_end_r": "right_foot_end",
}

_SMPLX_TO_RPM_LEAF_ALIASES: dict[str, str] = {
    smplx: rpm for rpm, smplx in _RPM_TO_SMPLX_LEAF_ALIASES.items()
}


def _translate_path(path: str, table: dict[str, str]) -> str:
    """Rewrite every leaf segment of a relative joint path via ``table``."""
    return "/".join(table.get(part, part) for part in path.split("/"))


#: Full relative-path aliases from SMPL-X family joints to rpm_87 joints.
#: Every value is distinct, so the reverse table is well defined.
SMPLX_70_TO_RPM_87_JOINT_ALIASES: dict[str, str] = {
    joint: _translate_path(joint, _SMPLX_TO_RPM_LEAF_ALIASES)
    for joint in SMPLX_70_JOINTS
}

#: Full relative-path aliases from rpm_87 joints to SMPL-X family joints.
RPM_87_TO_SMPLX_70_JOINT_ALIASES: dict[str, str] = {
    rpm: smplx for smplx, rpm in SMPLX_70_TO_RPM_87_JOINT_ALIASES.items()
}

_SMPLX_FAMILY_PROFILES = frozenset({"smplx_70", "synbody_55"})


def joint_aliases_for(
    source_profile: str,
    target_profile: str,
) -> Mapping[str, str] | None:
    """Return source->target joint aliases for cross-family pairs, else None.

    Same-profile pairs and pairs inside the SMPL-X family share joint names
    and need no translation.  Unknown profile combinations raise ValueError
    so cache building fails loudly instead of silently retargeting by name.
    """
    if source_profile == target_profile:
        return None
    if source_profile == "rpm_87" and target_profile in _SMPLX_FAMILY_PROFILES:
        return RPM_87_TO_SMPLX_70_JOINT_ALIASES
    if target_profile == "rpm_87" and source_profile in _SMPLX_FAMILY_PROFILES:
        return SMPLX_70_TO_RPM_87_JOINT_ALIASES
    if {source_profile, target_profile} <= _SMPLX_FAMILY_PROFILES:
        return None
    raise ValueError(
        f"unsupported profile pair: {source_profile} -> {target_profile}"
    )


#: Motion source profiles each asset skeleton profile can retarget.  All
#: three articulated profiles accept both motion families through the joint
#: aliases above; rigid assets cannot play skeletal actions at all.
_COMPATIBLE_ARTICULATED_PROFILES = frozenset({"smplx_70", "synbody_55", "rpm_87"})

COMPATIBLE_MOTION_PROFILES: dict[str, frozenset[str]] = {
    "smplx_70": _COMPATIBLE_ARTICULATED_PROFILES,
    "synbody_55": _COMPATIBLE_ARTICULATED_PROFILES,
    "rpm_87": _COMPATIBLE_ARTICULATED_PROFILES,
    "rigid_1": frozenset(),
}


__all__ = [
    "CANONICAL_PROFILES",
    "COMPATIBLE_MOTION_PROFILES",
    "RPM_87_TO_SMPLX_70_JOINT_ALIASES",
    "SMPLX_70_JOINTS",
    "SMPLX_70_TO_RPM_87_JOINT_ALIASES",
    "joint_aliases_for",
]
