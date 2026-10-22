from benchmark.composition_v3 import build_composition_manifest
import math
from benchmark.deployment_gate_v3 import assess_deployment_v3
from benchmark.skill_qualification_v3 import SkillQualificationStatus, SkillQualificationV3, SkillTrack
from benchmark.deployment_gate_v3 import DeploymentGateV3


def manifest(**changes):
    values = dict(
        cwd="/home/pupil/HeteroCoord", protocol_version="protocol.v3", scorebook_digest="score",
        entry_mode="test", actual_import_origins={"EAI": "/eai", "EAI_assets": "/assets", "EAI_hmrs": "/hmrs"},
        asset_roots={"EAI_USD_ROOT": "/usd", "EAI_CONTROLLER_ROOT": "/ctrl", "EAI_NVD_ASSET_ROOT": "/nvd"},
        scene_digest="scene", task_digest="task", robot_digest="robot", controller_digest="controller",
        topology_digest="topology", oracle_id="oracle", skill_id="skill", replay_id="replay", evaluator_id="eval", step_owner="env",
    )
    values.update(changes)
    return build_composition_manifest(**values)


def qualification():
    return SkillQualificationV3(
        "skill", "robot", "controller", SkillTrack.PRIMARY_PHYSICAL, SkillQualificationStatus.QUALIFIED,
        .02, None, ("evidence",), True, True, True, "qualified",
        {"robot_digest": "robot", "controller_digest": "controller"},
    )


def test_deployment_gate_is_not_ready_without_physical_and_asset_provenance():
    result = assess_deployment_v3(manifest(asset_roots={}), ())
    assert result.status == "not_ready"
    assert "asset_roots_missing" in result.reasons
    assert "primary_physical_skill_qualification_missing" in result.reasons


def test_deployment_gate_requires_explicit_shared_transport_parameters():
    result = assess_deployment_v3(manifest(), (qualification(),), transport_policy={"mode": "shared_v3", "bits_per_tick": 0, "max_queue_bits": 1})
    assert result.ready is False
    assert "shared_transport_parameters_invalid" in result.reasons
    ready = assess_deployment_v3(manifest(), (qualification(),), transport_policy={"mode": "shared_v3", "bits_per_tick": 8, "max_queue_bits": 1024})
    assert ready.ready is True
    legacy = assess_deployment_v3(manifest(), (qualification(),), transport_policy={"mode": "legacy"})
    assert legacy.ready is False
    assert "shared_transport_required" in legacy.reasons
    compat = assess_deployment_v3(manifest(), (qualification(),), transport_policy={"mode": "legacy"}, require_shared_transport=False)
    assert compat.ready is True


def test_deployment_gate_binds_qualification_to_current_composition():
    unrelated = SkillQualificationV3(
        "other-skill", "robot", "controller", SkillTrack.PRIMARY_PHYSICAL, SkillQualificationStatus.QUALIFIED,
        .02, None, ("evidence",), True, True, True, "qualified",
        {"robot_digest": "robot", "controller_digest": "controller"},
    )
    result = assess_deployment_v3(manifest(), (unrelated,), transport_policy={"mode": "shared_v3", "bits_per_tick": 8, "max_queue_bits": 1024})
    assert result.ready is False
    assert "primary_physical_skill_qualification_missing" in result.reasons


def test_deployment_gate_rejects_empty_requirements_and_forged_ready_object():
    result = assess_deployment_v3(manifest(), (qualification(),), transport_policy={"mode": "shared_v3", "bits_per_tick": 8, "max_queue_bits": 1024}, required_imports=())
    assert result.ready is False
    assert "actual_import_origins_missing" in result.reasons
    try:
        DeploymentGateV3("ready", True, (), {"x": False}, "shared_v3")
    except ValueError:
        pass
    else:
        raise AssertionError("forged ready gate must be rejected")


def test_manifest_and_skill_timing_reject_nonfinite_or_noninteger_values():
    try:
        build_composition_manifest(cwd="/home/pupil/HeteroCoord", protocol_version="v3", scorebook_digest="s", entry_mode="test", sim_dt_s=math.inf)
    except ValueError:
        pass
    else:
        raise AssertionError("infinite sim_dt_s must be rejected")
    try:
        build_composition_manifest(cwd="/home/pupil/HeteroCoord", protocol_version="v3", scorebook_digest="s", entry_mode="test", control_substeps=1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("noninteger control_substeps must be rejected")
