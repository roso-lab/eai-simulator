"""Task-specific suitability gates over robot dossiers.

Suitability is a declared/static compatibility result. It is not a runtime
success claim and cannot upgrade a dossier's evidence tier.
"""
from __future__ import annotations

from typing import Any

from tools.scenario_generation.robot_dossier import validate_dossier

_EVIDENCE_ORDER = {f"E{i}": i for i in range(8)}

SCENE_REQUIREMENTS = {
    "factory": {"domain": "ground", "needs_navigation": True},
    "warehouse_nvd": {"domain": "ground", "needs_navigation": True},
    "modern_house_nvd": {"domain": "ground", "needs_navigation": True},
}


def evaluate_suitability(dossier: dict[str, Any], scene_key: str, *, requires_manipulation: bool = False,
                         requires_aerial_scan: bool = False, requires_sensor: str | None = None,
                         required_evidence_tier: str = "E1") -> dict[str, Any]:
    if scene_key not in SCENE_REQUIREMENTS:
        raise ValueError(f"unsupported benchmark scene {scene_key!r}")
    if required_evidence_tier not in _EVIDENCE_ORDER:
        raise ValueError(f"invalid required evidence tier {required_evidence_tier!r}")
    reasons: list[str] = []
    dossier_errors = validate_dossier(dossier)
    if dossier_errors:
        reasons.extend(f"invalid dossier: {error}" for error in dossier_errors)
    evidence = {item.get("evidence_id"): item for item in dossier.get("evidence", ())
                if isinstance(item, dict)}
    passing_tier = max((_EVIDENCE_ORDER.get(item.get("tier"), -1) for item in evidence.values()
                        if item.get("result") == "pass"), default=-1)
    if passing_tier < _EVIDENCE_ORDER[required_evidence_tier]:
        reasons.append(f"passing evidence is below required {required_evidence_tier}")
    robot_key = dossier.get("robot_key")
    try:
        from EAI.hmrs_env.env_diy.catalog import validate_attachment_types
        validate_attachment_types(robot_key, dossier.get("selected_attachments", ()))
    except (TypeError, ValueError) as exc:
        reasons.append(f"attachment compatibility failed: {exc}")
    capabilities = dossier.get("capabilities", {})
    morphology = dossier.get("morphology", {})
    sensing = dossier.get("sensing", {}).get("capabilities", {})
    domain = morphology.get("domain")
    if domain != SCENE_REQUIREMENTS[scene_key]["domain"] and not requires_aerial_scan:
        reasons.append(f"scene requires {SCENE_REQUIREMENTS[scene_key]['domain']} domain")
    def claim_true(key: str, label: str) -> bool:
        claim = capabilities.get(key, {})
        if claim.get("value") is not True or claim.get("status") == "unknown":
            reasons.append(label)
            return False
        return True
    claim_true("navigation", "navigation capability is not declared")
    if requires_manipulation:
        claim_true("manipulation", "manipulation capability is not declared")
        physics = dossier.get("physics", {})
        required_physics = (
            ("mass_kg", "base mass is not physically sourced"),
            ("center_of_mass_m", "base center of mass is not physically sourced"),
            ("inertia_kg_m2", "base inertia is not physically sourced"),
        )
        for field, label in required_physics:
            claim = physics.get(field, {})
            if not isinstance(claim, dict) or claim.get("value") is None or claim.get("status") == "unknown":
                reasons.append(label)
        for field, label in (
            ("support_geometry_status", "support geometry is not validated"),
            ("aggregate_com_inertia_status", "mounted aggregate COM/inertia is not validated"),
            ("installation_geometry_status", "installation geometry is not physically sourced"),
        ):
            if physics.get(field) not in {"measured", "derived", "validated"}:
                reasons.append(label)
        for attachment in dossier.get("manipulation", {}).get("attachments", ()):
            if not isinstance(attachment, dict):
                continue
            if attachment.get("mount_position_m") is None:
                reasons.append("manipulator mount position is unknown")
            if attachment.get("physical_grasp_status") == "unknown":
                reasons.append(f"{attachment.get('type', 'manipulator')} grasp physics is unknown")
        try:
            from tools.scenario_generation.arm_deployment_evidence import (
                PROFILE_MANIPULATORS,
                deployment_evidence_for_configuration,
                dynamic_manipulation_admitted,
            )
            manipulators = [str(item).strip().lower()
                            for item in dossier.get("selected_attachments", ())
                            if str(item).strip().lower() in PROFILE_MANIPULATORS]
            attachment_evidence = {
                item.get("type"): item.get("deployment_evidence")
                for item in dossier.get("manipulation", {}).get("attachments", ())
                if isinstance(item, dict)
            }
            if not manipulators:
                reasons.append("no declared manipulator deployment")
            for manipulator in manipulators:
                deployment = attachment_evidence.get(manipulator)
                if not isinstance(deployment, dict):
                    deployment = deployment_evidence_for_configuration(robot_key, manipulator)
                if not dynamic_manipulation_admitted(deployment):
                    status = deployment["mount_evidence"]["status"]
                    reason = deployment["dynamic_manipulation_admission"]["reason"]
                    reasons.append(f"{manipulator} dynamic deployment is {status}: {reason}")
        except (TypeError, ValueError) as exc:
            reasons.append(f"dynamic manipulator gate failed: {exc}")
    if requires_aerial_scan:
        claim_true("aerial_scan", "aerial_scan capability is not declared for this robot")
    if requires_sensor:
        claim = sensing.get(requires_sensor, {})
        if claim.get("value") is not True or claim.get("status") == "unknown":
            reasons.append(f"sensor capability {requires_sensor!r} is not declared")
    status = "eligible" if not reasons else "ineligible"
    return {"scene_key": scene_key, "robot_key": robot_key, "status": status, "reasons": reasons,
            "evidence_tier": f"E{passing_tier}" if passing_tier >= 0 else "E0",
            "required_evidence_tier": required_evidence_tier, "runtime_validated": False}


__all__ = ["SCENE_REQUIREMENTS", "evaluate_suitability"]
