"""Configuration-bound manipulator deployment evidence for benchmark profiles.

Stationary mount fixtures, component manipulation evidence, dynamic mobile
manipulation, and hardware claims are deliberately represented as separate
fields. No field in this module produces a normalized capability score.
"""
from __future__ import annotations

from typing import Any


ENV_DIY_MANIPULATOR_VARIANTS = ("ur5", "ur5_bare", "z1")
CANDIDATE_ONLY_MANIPULATORS = ("kinova_gen3",)
PROFILE_MANIPULATORS = ENV_DIY_MANIPULATOR_VARIANTS + CANDIDATE_ONLY_MANIPULATORS

_ASSEMBLIES = {
    "ur5": {"assembly": "ur5_with_robotiq_2f85", "end_effector": "robotiq_2f85"},
    "ur5_bare": {"assembly": "ur5_bare", "end_effector": None},
    "z1": {"assembly": "z1_with_integrated_gripper", "end_effector": "z1_gripper"},
    "kinova_gen3": {"assembly": "kinova_gen3_bare", "end_effector": None},
}

_KINOVA_RUNTIME_ARTIFACTS = {
    "scout": "tmp/kinova_interface_v2_20260916/scout_root_corrected/report.json",
    "b2": "tmp/kinova_interface_v2_20260916/b2/report.json",
    "m20": "tmp/mount_validation_20260916/m20_kinova_gen3/report.json",
    "carter": "tmp/mount_validation_20260916/carter_kinova_gen3/report.json",
    "go2": "tmp/mount_validation_20260916/go2_kinova_gen3/report.json",
    "lite3": "tmp/mount_validation_20260916/lite3_kinova_gen3/report.json",
}

_CARTER_MASS_WARNING = (
    "Carter's simulator mass is wheel-heavy (40 kg of the declared 54 kg is in "
    "the two drive wheels), so stationary mount passes are optimistic and do not "
    "establish hardware stability or safety margin."
)

_CANDIDATE_MOUNT_EVIDENCE = {
    "scout": ("conditional", "Unscaled Gen3 passed the stationary arm-motion and ground-reaction fixture; Scout still had negative/degenerate contact-envelope samples, and locomotion manipulation is unverified."),
    "b2": ("conditional", "Unscaled Gen3 passed the stationary arm-motion and ground-reaction fixture; locomotion manipulation is unverified."),
    "m20": ("conditional", "Unscaled Gen3 passed the stationary arm-motion, ground-reaction, tilt, and drift fixture; locomotion manipulation is unverified."),
    "carter": ("conditional", "Unscaled Gen3 passed the stationary fixture, but Carter's simulator mass is concentrated unrealistically in its drive wheels, making the result optimistic; locomotion manipulation is unverified."),
    "go2": ("rejected", "The bare host passed the stationary fixture, but the unscaled Gen3 configuration failed tilt, drift, and persistent-support gates at the tested mount."),
    "lite3": ("rejected", "The loaded configuration overturned at the tested TORSO mount; the bare fixture was already abnormal, so this is fixture-specific and not a hardware capacity claim."),
}


def _component_physical_manipulation(manipulator: str) -> dict[str, Any]:
    if manipulator != "ur5":
        return {
            "status": "unknown" if manipulator != "ur5_bare" else "not_applicable",
            "tier": None,
            "scope": None,
            "applicable_to_mobile_configuration": False,
        }
    return {
        "status": "available_component_only",
        "tier": "E7",
        "scope": {
            "base": "fixed",
            "manipulator": "ur5",
            "end_effector": "robotiq_2f85",
            "fixture": "contact_grasp_lift_move_release",
            "passed_test_masses_kg": [0.2, 1.0, 2.0],
            "failed_fixture_mass_kg": 3.0,
        },
        "applicable_to_mobile_configuration": False,
        "reason": (
            "The E7 record is limited to the fixed-base component fixture and "
            "cannot certify any mobile host, rated payload, or hardware grasp force."
        ),
        "source_refs": [
            "tools/scenario_generation/ARM_DEPLOYMENT_ROADMAP.zh-CN.md",
            "tmp/ur5_e7_component_summary.json",
        ],
        "runtime_artifact_policy": "tmp artifact is untracked and must be retained with the run archive",
    }


def deployment_evidence_for_configuration(robot_key: str, manipulator: str) -> dict[str, Any]:
    """Return one exact host/manipulator evidence record without score inference."""
    from EAI.hmrs_env.env_diy import catalog

    host = catalog.canonical_robot_type(robot_key)
    if host not in catalog.ROBOT_KEYS:
        raise ValueError(f"unknown connected robot type {robot_key!r}")
    arm = str(manipulator).strip().lower()
    if arm not in PROFILE_MANIPULATORS:
        raise ValueError(f"unsupported manipulator evidence type {manipulator!r}")

    entry = catalog.attachment_catalog().get(arm)
    registered_for_host = bool(entry and entry.supports(host))
    catalog_state = (
        "registered_for_host" if registered_for_host
        else "candidate_only" if arm in CANDIDATE_ONLY_MANIPULATORS
        else "not_registered_for_host"
    )
    deployment = (_CANDIDATE_MOUNT_EVIDENCE.get(host)
                  if arm == "kinova_gen3" else
                  catalog.manipulator_deployment_status(host, arm))
    mount_status, reason = deployment or (
        "unknown", "No host-specific unscaled stationary mount evidence is available."
    )
    fixture_status = {
        "conditional": "passed_with_limitations",
        "rejected": "failed",
        "unknown": "not_run_or_not_retained",
        "allowed": "passed",
    }.get(mount_status, "unknown")
    if mount_status == "rejected":
        dynamic_reason = "The tested stationary mount fixture rejected this configuration."
    elif mount_status == "unknown":
        dynamic_reason = "Host-specific mount evidence is missing."
    else:
        dynamic_reason = (
            "Stationary mount evidence does not establish locomotion, contact manipulation, "
            "load dynamics, or configuration-level E7 evidence."
        )

    warnings = []
    if host == "carter" and mount_status != "unknown":
        warnings.append(_CARTER_MASS_WARNING)
    source_refs = [
        "source/EAI/EAI/hmrs_env/env_diy/catalog.py",
        "tools/scenario_generation/ARM_DEPLOYMENT_ROADMAP.zh-CN.md",
    ]
    runtime_artifact = _KINOVA_RUNTIME_ARTIFACTS.get(host) if arm == "kinova_gen3" else None
    return {
        "host_robot_key": host,
        "manipulator": arm,
        **_ASSEMBLIES[arm],
        "manipulator_controller_cfg": entry.controller_cfg if entry else None,
        "catalog_state": catalog_state,
        "registered_for_host": registered_for_host,
        "selectable_in_env_diy": registered_for_host and mount_status != "rejected",
        "mount_evidence": {
            "status": mount_status,
            "reason": reason,
            "evidence_class": "stationary_mobile_mount_fixture",
            "fixture_status": fixture_status,
            "fixture": {
                "protocol": "mobile_arm_mount_smoke_v2",
                "duration_steps": 3600,
                "mass_policy": "unscaled_asset_mass_and_inertia",
                "locomotion_exercised": False,
                "object_grasp_exercised": False,
                "hardware_exercised": False,
            } if mount_status != "unknown" else None,
            "source_refs": source_refs,
            "runtime_artifact": ({
                "path": runtime_artifact,
                "repository_tracked": False,
                "required_for_catalog_registration": False,
            } if runtime_artifact else None),
        },
        "dynamic_manipulation_admission": {
            "status": "blocked",
            "reason": dynamic_reason,
            "requires": "configuration_specific_dynamic_mobile_manipulation_evidence",
        },
        "mobile_configuration_e7": {
            "status": "unknown",
            "evidence_id": None,
            "reason": "No physical manipulation E7 record is bound to this mobile configuration.",
        },
        "component_physical_manipulation": _component_physical_manipulation(arm),
        "claim_boundaries": {
            key: {"status": "unknown", "value": None}
            for key in (
                "rated_payload_kg",
                "hardware_grasp_force_n",
                "collision_safety_margin",
                "wind_resistance",
                "load_dynamics",
            )
        },
        "warnings": warnings,
    }


def deployment_evidence_for_robot(robot_key: str) -> dict[str, Any]:
    """Return selectable and retained candidate evidence for one robot type."""
    return {
        "env_diy_variants": [
            deployment_evidence_for_configuration(robot_key, arm)
            for arm in ENV_DIY_MANIPULATOR_VARIANTS
        ],
        "candidate_only": [
            deployment_evidence_for_configuration(robot_key, arm)
            for arm in CANDIDATE_ONLY_MANIPULATORS
        ],
        "policy": {
            "radar_score_inference": "forbidden",
            "component_e7_transfer_to_mobile": "forbidden",
            "stationary_fixture_transfer_to_dynamic": "forbidden",
        },
    }


def dynamic_manipulation_admitted(evidence: dict[str, Any]) -> bool:
    """Admit only an explicit configuration-level dynamic result, never static E7."""
    return (
        evidence.get("dynamic_manipulation_admission", {}).get("status") == "allowed"
        and evidence.get("mobile_configuration_e7", {}).get("status") == "passed"
    )


__all__ = [
    "CANDIDATE_ONLY_MANIPULATORS",
    "ENV_DIY_MANIPULATOR_VARIANTS",
    "PROFILE_MANIPULATORS",
    "deployment_evidence_for_configuration",
    "deployment_evidence_for_robot",
    "dynamic_manipulation_admitted",
]
