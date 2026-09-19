import copy
from dataclasses import dataclass

import pytest

from EAI.scenario_spec.benchmark_physics import benchmark_mount_profile
from EAI.scenario_spec.ideal_grasp import evaluate_grasp
from EAI.scenario_spec.physical_limits import clearance_screen, transport_load_screen, wind_screen
from tools.scenario_generation.physical_evidence import content_hash, verify_calibration
from tools.scenario_generation.robot_dossier import robot_dossier
from tools.scenario_generation.runtime_evidence import merge_runtime_evidence


def carrier(**updates):
    return {"id": "m20_1", "share_kg": 2.0, "base_payload_kg": 40.0,
            "attachment_mass_kg": 22.0, "arm_payload_at_cog_kg": 5.0,
            "tool_mass_kg": 1.0, "normal_force_N": 100.0,
            "friction_coefficient": 0.5, **updates}


def test_unscaled_benchmark_profile_does_not_mutate_simulator_defaults():
    @dataclass(frozen=True)
    class Mount:
        mass_scale: float = 0.03
        visual_scale: float = 0.5
        inertia_scale: float = 0.2
        geometry_scale: float = 0.75
        mount_position: tuple = (0, 0, 0.1)

    original = Mount()
    benchmark = benchmark_mount_profile(original)
    assert original.mass_scale == 0.03
    assert original.visual_scale == 0.5
    assert benchmark.mass_scale == benchmark.visual_scale == benchmark.inertia_scale == benchmark.geometry_scale == 1.0
    assert benchmark.mount_position == original.mount_position


def test_default_manipulator_mount_profiles_are_unscaled():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for relative_path in (
        "source/EAI_assets/EAI_assets/robots/ur5_mount.py",
        "source/EAI_assets/EAI_assets/robots/z1_mount.py",
    ):
        tree = ast.parse((root / relative_path).read_text(encoding="utf-8"))
        scales = [
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "mass_scale"
            and isinstance(keyword.value, ast.Constant)
        ]
        if relative_path.endswith("z1_mount.py"):
            scales.extend(
                node.args[4].value
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Z1MountProfile"
                and len(node.args) > 4
                and isinstance(node.args[4], ast.Constant)
            )
        assert scales
        assert set(scales) == {1.0}


def test_mounted_reset_uses_authored_pose_before_warmup_and_environment_offsets():
    from types import SimpleNamespace
    import torch
    from pxr import Usd, UsdGeom
    from EAI.physics.articulation_reset import capture_root_poses, set_spawn_root_defaults

    stage = Usd.Stage.CreateInMemory()
    root_paths = ["/World/env_0/arm/base", "/World/env_1/arm/base"]
    for path, position in zip(root_paths, [(1, 2, 0.4), (11, 2, 0.4)]):
        UsdGeom.Xform.Define(stage, path).AddTranslateOp().Set(position)
    poses = capture_root_poses(stage, root_paths)
    defaults = torch.full((2, 13), 99.0)
    articulation = SimpleNamespace(root_physx_view=SimpleNamespace(prim_paths=root_paths),
                                   data=SimpleNamespace(default_root_state=defaults))
    origins = torch.tensor([[0, 0, 0], [10, 0, 0]])
    set_spawn_root_defaults(articulation, poses, origins)
    assert torch.allclose(defaults[:, :3], torch.tensor([[1, 2, 0.4], [1, 2, 0.4]]))
    assert torch.equal(defaults[:, 3:7], torch.tensor([[1, 0, 0, 0], [1, 0, 0, 0]]))
    assert torch.count_nonzero(defaults[:, 7:]) == 0
    defaults[:] = 99
    set_spawn_root_defaults(articulation, poses, origins)
    assert torch.count_nonzero(defaults[:, 7:]) == 0
    with pytest.raises(ValueError, match="Missing spawn poses"):
        set_spawn_root_defaults(articulation, {})


def test_arm_limit_blocks_large_base_payload_and_inertia_does_not_become_capacity():
    result = transport_load_screen(8.0, [carrier(share_kg=8.0)])
    assert result["status"] == "rejected"
    assert "m20_1.arm_spare_kg" in result["violations"]
    assert result["margins"]["m20_1.base_spare_kg"] == 10.0


def test_load_sharing_requires_each_arm_and_explicit_mass_balance():
    result = transport_load_screen(8.0, [carrier(id="first", share_kg=4.0),
                                        carrier(id="second", share_kg=4.0)])
    assert result["status"] == "rejected"
    assert "first.retention_spare_N" in result["violations"]
    assert transport_load_screen(8.0, [carrier(share_kg=2.0)])["status"] == "rejected"


def test_unknown_force_does_not_pass_and_acceleration_changes_load():
    assert transport_load_screen(2.0, [carrier(normal_force_N=None)])["status"] == "unknown"
    assert transport_load_screen(2.0, [carrier()])["status"] == "conditional_pass"
    assert transport_load_screen(2.0, [carrier()], vertical_acceleration_m_s2=10)["status"] == "rejected"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1])
def test_physical_quantities_reject_invalid_numbers(value):
    with pytest.raises(ValueError):
        transport_load_screen(2.0, [carrier(normal_force_N=value)])


def test_ideal_grasp_opt_in_checks_actual_load_path_without_claiming_physics():
    robot = {"id": "m20_1", "type": "m20", "attachments": [{"type": "ur5"}]}
    policy = {"capacity_kg": 40.0, "grasp_envelope_m": [1, 1, 1],
              "physical_limits": {"carriers": [carrier(share_kg=8.0)]}}
    result = evaluate_grasp(robot, {"id": "box", "mass_kg": 8, "size_m": [0.2]*3}, policy)
    assert result["success"] is False
    assert result["physical_grasp"] is False
    assert result["details"]["physical_screen"]["status"] == "rejected"


def test_clearance_requires_error_budget_and_wind_requires_aerodynamics():
    assert clearance_screen(measured_clearance_m=0.3)["status"] == "unknown"
    assert clearance_screen(measured_clearance_m=0.3, required_margin_m=0.2,
                            geometry_error_m=0.05, tracking_error_m=0.1,
                            sampling_error_m=0.02)["status"] == "rejected"
    assert wind_screen(mass_kg=0.0282, wind_speed_m_s=2)["status"] == "unknown"
    params = dict(mass_kg=1, thrust_limit_N=12, tilt_limit_rad=0.5, drag_coefficient=1,
                  projected_area_m2=0.2, air_density_kg_m3=1.225)
    assert wind_screen(**params, wind_speed_m_s=1)["status"] == "conditional_pass"
    assert wind_screen(**params, wind_speed_m_s=20)["status"] == "rejected"


def report_fixture():
    trials = []
    for repeat in range(3):
        for control in ("close", "open_control"):
            samples = []
            for phase_index, phase in enumerate(("close", "lift", "transfer", "hold", "release")):
                for step in range(60):
                    retained = control == "close" and phase != "release"
                    raised = retained and phase_index >= 1
                    moved = retained and phase_index >= 2
                    samples.append({"time_s": (len(samples) + 1) / 60,
                                    "phase": phase,
                                    "object_position_m": [0, 0.1 if moved else 0, 0.3 if raised else 0.1],
                                    "stator_position_m": [0, 0.1 if phase_index >= 2 else 0, 0.3],
                                    "object_velocity_m_s": [0, 0, 0],
                                    "jaw_contact_force_N": [10, 10] if retained else [0, 0],
                                    "support_contact_force_N": 0 if retained else 1.96,
                                    "gripper_joint_rad": -0.1 if retained else -0.7})
                    samples[-1]["maximum_penetration_m"] = 0.0005
            trials.append({"mass_kg": 0.2, "runtime_mass_kg": 0.2, "repeat": repeat, "control": control,
                           "initial_object_position_m": [0, 0, 0.1], "samples": samples})
    return {"schema_id": "eai.physical_calibration", "schema_version": "1.0",
            "experiment": "z1_prepositioned_grasp", "subject": "z1_standalone_unscaled",
            "domain": "simulation", "runner_sha256": "a"*64,
            "source_asset_layers": [{"path": "synthetic_test.usda", "sha256": "b"*64}],
            "authored_arm_inertials": {"link": {"mass_kg": 2, "inertia_kg_m2": [1,0,0,0,1,0,0,0,1]}},
            "arm_runtime_masses_kg": {"world": 1, "link": 2},
            "arm_runtime_inertias_kg_m2": {"link": [1,0,0,0,1,0,0,0,1]},
            "fixed_dummy_bodies": ["world"],
            "protocol": {"scripted_payload_motion": False, "fixed_attachment": False,
                         "payload_gravity_enabled": True, "payload_collision_enabled": True,
                         "position_ik": True, "orientation_ik": True, "drive_type": "force",
                         "mass_scale": 1.0, "gravity_m_s2": 9.81, "dt_s": 1/60, "repeats": 3},
            "trials": trials}


def verify(report):
    return verify_calibration(report, expected_report_sha256=content_hash(report),
                              expected_runner_sha256="a"*64)


def test_physical_verifier_binds_component_scope_and_raw_report():
    report = report_fixture()
    result = verify(report)
    assert result["tier"] == "E7"
    assert result["scope"] == "standalone_prepositioned_grasp_lift_transfer_release"
    assert result["validated_masses_kg"] == [0.2]
    with pytest.raises(ValueError, match="hash"):
        verify_calibration(report, expected_report_sha256="c"*64, expected_runner_sha256="a"*64)


@pytest.mark.parametrize("field,value", [("scripted_payload_motion", True), ("fixed_attachment", True),
                                         ("mass_scale", 0.1), ("repeats", 1),
                                         ("payload_gravity_enabled", False)])
def test_physical_verifier_rejects_simulation_shortcuts(field, value):
    report = report_fixture()
    report["protocol"][field] = value
    with pytest.raises(ValueError):
        verify(report)


def test_contact_observation_alone_and_successful_open_control_do_not_certify_grasp():
    report = report_fixture()
    for trial in report["trials"]:
        for sample in trial["samples"]:
            sample["object_position_m"][2] = 0.1
    assert verify(report)["validated"] is False
    report = report_fixture()
    for index in (1, 3, 5):
        report["trials"][index]["samples"] = copy.deepcopy(report["trials"][index-1]["samples"])
    assert verify(report)["validated"] is False


def test_verifier_rejects_missing_control_and_gaps():
    report = report_fixture()
    report["trials"].pop()
    with pytest.raises(ValueError, match="missing"):
        verify(report)
    report = report_fixture()
    report["trials"][0]["samples"].pop(10)
    with pytest.raises(ValueError, match="timestamps"):
        verify(report)


def test_verifier_rejects_mass_and_inertia_scaling_even_when_protocol_claims_unscaled():
    for field in ("arm_runtime_masses_kg", "arm_runtime_inertias_kg_m2"):
        report = report_fixture()
        if field == "arm_runtime_masses_kg":
            report[field]["link"] = 0.2
        else:
            report[field]["link"] = [0.1,0,0,0,0.1,0,0,0,0.1]
        with pytest.raises(ValueError, match="unscaled source"):
            verify(report)


def test_successful_motion_with_deep_penetration_is_rejected():
    report = report_fixture()
    report["trials"][0]["samples"][150]["maximum_penetration_m"] = 0.01
    result = verify(report)
    assert result["validated"] is False
    assert result["mass_results"][0]["close_trials"][0]["checks"]["penetration"] is False


def test_self_declared_e7_cannot_promote_robot():
    with pytest.raises(ValueError, match="configuration-specific"):
        merge_runtime_evidence(robot_dossier("m20"),
            {"evidence_id": "forged", "tier": "E7", "result": "pass", "validation_status": "physical_validated"})


def test_legacy_scaled_execution_is_not_reused_as_current_benchmark_evidence():
    from tools.scenario_generation.batch_execute import should_skip_matching_success
    attempt = {"scenario_sha256": "a"*64, "config": {"transport_mode": "kinematic"},
               "report_body": {"execution_validation": {"tier": "kinematic_execution_validated", "validated": True}}}
    assert not should_skip_matching_success(attempt, scenario_sha256="a"*64,
                                            transport_mode="kinematic", protocol_context=None)


def test_manufacturer_variants_are_not_silently_applied_to_assets():
    dossier = robot_dossier("go2", attachments=["z1"])
    references = dossier["payload"]["manufacturer_references"]
    assert {record["payload_kg"] for record in references} == {7.0, 8.0}
    assert all(record["asset_variant_match"] == "unverified" for record in references)
    assert dossier["payload"]["capacity_kg"]["value"] is None
    assert dossier["payload"]["benchmark_mount_mass_scale"] == 1.0
    assert dossier["manipulation"]["attachments"][0]["mount_mass_scale"] == 1.0
    assert robot_dossier("go2", attachments=["z1"], physics_context="simulator")["payload"]["mount_mass_scale"]["z1"]["value"] == 1.0
