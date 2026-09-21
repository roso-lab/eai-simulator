"""F-VERT-01 authoring, private truth and unverified route-intent boundaries."""

from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from EAI.scenario_spec import load_scenario
from EAI.scenario_spec.geometry import ground_surface_exclusions
from EAI.scenario_spec.schema import scenario_schema
from demo.benchmark.challenge_contract import (
    validate_baseline_manifest, validate_hidden_evaluator, validate_participant_api,
    validate_public_task_spec, write_challenge_bundle,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.benchmark.semantic_contract import objective_semantics_for
from demo.factory_benchmark.vertical_challenge import (
    DEFAULT_SCENARIO, PROBE_SCOPE, compile_challenge, main, validate_route_intent,
)


def _write_scenario(tmp_path, scenario):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return path


def _action(action_type="navigate_goal"):
    payload = {"node_id": "stair_access_check", "agent_id": "go2_1"}
    payload.update({"duration_s": 1.0} if action_type == "wait" else
                   {"target_ref": "mezzanine_stair_exit"})
    return {"schema": "eai.benchmark.participant_action", "version": 1,
            "action_id": "intent-1", "submitted_at_s": 0.0,
            "type": action_type, "payload": payload}


def _static_measurement(tmp_path, *, flight_id="flight_02", support_status="measured"):
    path = tmp_path / "stair_measurement.json"
    data = {
        "schema": "eai.factory_stair_static_measurement", "version": 1,
        "status": "measured", "prim_path": "/World/SM_StairsSmall",
        "provenance": {
            "status": "measured",
            "root_layer": {"status": "measured", "sha256": "a" * 64},
            "dependency_layers": [{"status": "measured", "identifier": "stairs.usd",
                                   "sha256": "b" * 64}],
        },
        "treads": {"flights": [{
            "flight_id": flight_id, "status": "measured",
            "candidate_count": 2, "travel_direction_world_xy": [0.0, -1.0],
            "treads": [{"support_z_m": 0.2}, {"support_z_m": 1.55}],
        }]},
        "support_regions": {"by_flight": [{
            "flight_id": flight_id, "status": support_status,
            "start": {"status": support_status}, "end": {"status": support_status},
        }]},
        "net_width_m": {"status": "measured_from_visible_tread_faces", "value_m": 2.6},
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_candidate_loads_generic_v2_and_opens_explicit_stair_without_proving_geometry():
    raw = json.loads(DEFAULT_SCENARIO.read_text(encoding="utf-8"))
    Draft202012Validator(scenario_schema()).validate(raw)
    scenario = load_scenario(DEFAULT_SCENARIO)
    assert scenario["scene_key"] == "factory"
    assert scenario["benchmark_task_id"] == "F-VERT-01"
    assert scenario["robots"][0]["controller"]["cfg"] == "GO2_MIXED_STAIRS_CONTINUED_CFG"
    node = scenario["phase_graph"]["nodes"][0]
    assert node["skill_key"] == "navigate"
    assert node["vertical"]["link_id"] == "main_stair"
    assert node["vertical"]["kind"] == "stair"
    assert node["vertical"]["support_probe"] == "required"
    assert ground_surface_exclusions(scenario) == []
    assert objective_semantics_for("factory", node)["scope"] == "vertical_probe"
    declaration = scenario["semantic_task"]["objective_semantics"]
    assert declaration["execution_validated"] is False
    assert declaration["release_status"] == "pending"
    geometry = declaration["geometry_declaration"]
    assert {geometry[key]["status"] for key in ("approach", "start", "goal")} == {"unknown"}
    assert geometry["goal"]["measured_support_xyz_m"] is None
    assert geometry["goal"]["candidate_support_xyz_m"][2] == 1.57


def test_vertical_ground_approach_can_reach_wide_robot_pre_entry_stance():
    """The 2-D approach must not double-inflate the separate stair envelope."""
    from EAI.scenario_spec.geometry import provider_map
    from EAI.scenario_spec.templates import factory_template
    from demo.benchmark.runtime import build_plan

    scenario = load_scenario(DEFAULT_SCENARIO)
    scenario["benchmark_task_id"] = "F-INS-01"
    scenario["robots"][0].update({
        "id": "b2_1", "type": "b2",
        "controller": {"mode": "default", "cfg": "B2_VELOCITY_RSL_CFG"},
        "spawn_pose": {"position": [1.5, -0.35, 0.58],
                        "rotation": [0.7071067811865476, 0, 0, -0.7071067811865476]},
    })
    node = scenario["phase_graph"]["nodes"][0]
    node["preferred_agent"] = "b2_1"
    node.pop("vertical", None)
    scenario["composition"]["targets"] = [{
        "id": "__vertical_ground_approach__",
        "position": [1.5, -0.35, 0.0], "radius_m": 0.35,
    }]
    node["target_ref"] = "__vertical_ground_approach__"
    occupancy = provider_map(scene_key="factory")
    plan = build_plan(scenario, occupancy, factory_template(occupancy),
                      ground_tracking_reserve_m=0.0)
    assert plan[0]["agent"] == "b2_1"
    assert plan[0]["waypoints"]


def test_bundle_reuses_contracts_and_remains_nonexecutable_and_unscored():
    bundle = compile_challenge()
    public = bundle["public_task"]
    assert validate_public_task_spec(public)["validated"]
    for key, validator in (("participant_api", validate_participant_api),
                           ("hidden_evaluator", validate_hidden_evaluator),
                           ("baseline_manifest", validate_baseline_manifest)):
        assert validator(bundle[key], task_spec=public)["validated"]
        assert bundle[key]["public_task_sha256"] == canonical_sha256(public)
        assert bundle[key]["execution_validated"] is False
    assert public["scene"]["scenario_sha256"] == file_sha256(DEFAULT_SCENARIO)
    assert public["scene"]["asset_sha256"] is None
    assert public["release_status"] == "pending"
    assert public["execution_validated"] is False
    assert public["probe_scope"] == PROBE_SCOPE
    assert public["release_gate"]["executable"] is False
    assert public["release_gate"]["scoreable"] is False
    assert public["scoring"]["missing_evidence_result"] == "unknown"
    assert public["participant"]["allowed_action_types"] == ["navigate_goal", "wait"]
    assert bundle["participant_api"]["implementation_status"]["executed_action_types"] == []
    hidden = bundle["hidden_evaluator"]
    assert hidden["support_truth"]["status"] == "unknown"
    assert hidden["support_truth"]["calibration_id"] is None
    reference = hidden["reference_physical_acceptance"]
    assert reference["status"] == "pending"
    assert reference["record"] is None
    assert reference["reference_trajectory"] is None
    assert reference["candidate_report_is_acceptance"] is False
    assert reference["acceptance_evaluator"] is None
    encoded = json.dumps([bundle[key] for key in ("public_task", "participant_api", "baseline_manifest")])
    for private in ("preferred_agent", "support_truth", "reference_trajectory", "calibration_id",
                    "candidate_prim_bindings", "scenario_snapshot"):
        assert private not in encoded


def test_private_measurement_selects_flight_02_and_binds_layer_hashes_without_physics_claim(tmp_path):
    measurement = _static_measurement(tmp_path)
    bundle = compile_challenge(stair_measurement_path=measurement)
    public = bundle["public_task"]
    hidden = bundle["hidden_evaluator"]
    static = hidden["support_truth"]["static_geometry"]

    assert public["scene"]["asset_sha256"] == "a" * 64
    assert public["scene"]["asset_binding_status"] == \
        "static_geometry_hash_bound_physics_unvalidated"
    assert public["release_gate"]["executable"] is False
    assert public["release_gate"]["scoreable"] is False
    assert "physx_support_and_connectivity" in public["release_gate"]["blockers"]
    assert static["selected_flight_id"] == "flight_02"
    assert static["root_layer_sha256"] == "a" * 64
    assert static["dependency_layers"][0]["sha256"] == "b" * 64
    assert static["physical_support_validated"] is False
    assert static["connectivity_validated"] is False
    assert static["traversal_validated"] is False
    assert hidden["support_truth"]["status"] == "unknown"
    assert "flight_02" not in json.dumps([
        bundle["public_task"], bundle["participant_api"], bundle["baseline_manifest"]])


@pytest.mark.parametrize("flight_id,support_status,match", [
    ("flight_01", "measured", "flight_02"),
    ("flight_02", "partial", "authored start and end support"),
])
def test_private_measurement_rejects_wrong_flight_or_incomplete_support(
        tmp_path, flight_id, support_status, match):
    measurement = _static_measurement(
        tmp_path, flight_id=flight_id, support_status=support_status)
    with pytest.raises(ValueError, match=match):
        compile_challenge(stair_measurement_path=measurement)


def test_private_semantic_extensions_are_retained_only_in_evaluator(tmp_path):
    scenario = load_scenario(DEFAULT_SCENARIO)
    objective = scenario["semantic_task"]["objective_semantics"]
    objective["support_truth"] = {"private_note": "PRIVATE_CONTACT_SENTINEL"}
    objective["reference_trajectory"] = ["PRIVATE_ROUTE_SENTINEL"]
    objective["success"] = True
    objective["geometry_declaration"]["goal"]["private_measurement"] = "PRIVATE_HEIGHT_SENTINEL"
    scenario["semantic_task"]["team_lifecycle"]["hidden_schedule"] = "PRIVATE_SCHEDULE_SENTINEL"
    bundle = compile_challenge(_write_scenario(tmp_path, scenario))
    published = json.dumps([bundle[key] for key in ("public_task", "participant_api", "baseline_manifest")])
    assert "SENTINEL" not in published
    assert '"success": true' not in published
    assert "PRIVATE_CONTACT_SENTINEL" in json.dumps(bundle["hidden_evaluator"])
    assert bundle["public_task"]["release_status"] == "pending"


@pytest.mark.parametrize("change,match", [
    ("task_id", "Factory F-VERT-01"),
    ("link", "main_stair requires"),
    ("support", "support_probe=required"),
    ("execution_claim", "execution_validated=false"),
    ("release_claim", "release_status=pending"),
    ("measured_claim", "unknown geometry"),
    ("null_measurement_missing", "unknown geometry"),
    ("approach_mismatch", "approach candidates"),
    ("start_mismatch", "start candidate"),
    ("entry_mismatch", "surface-entry candidate"),
    ("goal_mismatch", "goal candidate"),
    ("lifecycle_enabled", "variations are disabled"),
    ("fault", "interventions"),
])
def test_compiler_rejects_misbound_or_promoted_candidates(tmp_path, change, match):
    scenario = load_scenario(DEFAULT_SCENARIO)
    node = scenario["phase_graph"]["nodes"][0]
    declaration = scenario["semantic_task"]["objective_semantics"]
    geometry = declaration["geometry_declaration"]
    if change == "task_id":
        scenario["benchmark_task_id"] = "F-INS-01"
        scenario["robots"][0]["controller"]["cfg"] = "GO2_VELOCITY_RSL_CFG"
    elif change == "link":
        node["vertical"]["link_id"] = "west_ramp"
    elif change == "support":
        node["vertical"]["support_probe"] = "validated"
    elif change == "execution_claim":
        declaration["execution_validated"] = True
    elif change == "release_claim":
        declaration["release_status"] = "ready"
    elif change == "measured_claim":
        geometry["goal"]["status"] = "measured"
    elif change == "null_measurement_missing":
        del geometry["start"]["measured_support_xyz_m"]
    elif change == "approach_mismatch":
        geometry["approach"]["candidate_waypoints_xy_m"] = []
    elif change == "start_mismatch":
        geometry["start"]["candidate_root_xyz_m"][0] += 1.0
    elif change == "entry_mismatch":
        geometry["start"]["candidate_surface_entry_xy_m"] = [0.0, 0.0]
    elif change == "goal_mismatch":
        geometry["goal"]["candidate_support_xyz_m"][2] = 2.57
    elif change == "lifecycle_enabled":
        scenario["semantic_task"]["team_lifecycle"]["enabled_events"] = ["member_join"]
    elif change == "fault":
        scenario["fault_events"] = [{
            "id": "controller_change",
            "kind": "capability_change",
            "time_s": 1.0,
            "agent": "go2_1",
            "capability_declaration": {"locomotion": "degraded"},
            "capability_version": 2,
        }]
    with pytest.raises(ValueError, match=match):
        compile_challenge(_write_scenario(tmp_path, scenario))


@pytest.mark.parametrize("action_type", ["navigate_goal", "wait"])
def test_intents_validate_without_executing_or_promoting_release(action_type):
    bundle = compile_challenge()
    before = deepcopy(bundle)
    action = _action(action_type)
    result = validate_route_intent(action, api=bundle["participant_api"], task_spec=bundle["public_task"])
    assert result == action
    assert result is not action
    assert bundle == before
    assert "success" not in result


@pytest.mark.parametrize("change,match", [
    ("null_agent", "explicit agent_id"),
    ("unknown_agent", "unknown agent"),
    ("unknown_node", "unknown objective"),
    ("unknown_target", "unknown target"),
    ("pose_write", "undeclared fields"),
    ("success", "undeclared fields"),
    ("envelope_success", "envelope fields"),
    ("wait_timeout", "exceeds the objective timeout"),
    ("allocate", "not allowed"),
])
def test_intent_rejects_out_of_scope_control_and_success_claims(change, match):
    bundle = compile_challenge()
    action = _action()
    if change == "null_agent":
        action["payload"]["agent_id"] = None
    elif change == "unknown_agent":
        action["payload"]["agent_id"] = "carter_1"
    elif change == "unknown_node":
        action["payload"]["node_id"] = "inspect"
    elif change == "unknown_target":
        action["payload"]["target_ref"] = "shortcut"
    elif change == "pose_write":
        action["payload"]["position"] = [1.5, -7.5, 1.97]
    elif change == "success":
        action["payload"]["success"] = True
    elif change == "envelope_success":
        action["success"] = True
    elif change == "wait_timeout":
        action = _action("wait")
        action["payload"]["duration_s"] = 151.0
    elif change == "allocate":
        action["type"] = "allocate"
    with pytest.raises(ValueError, match=match):
        validate_route_intent(action, api=bundle["participant_api"], task_spec=bundle["public_task"])


def test_cli_writes_separate_hash_bound_artifacts_and_refuses_overwrite(tmp_path, capsys):
    output = tmp_path / "candidate"
    assert main(["--output-dir", str(output)]) == 0
    assert "pending; execution_validated=false" in capsys.readouterr().out
    assert {p.name for p in (output / "release").iterdir()} == {
        "public_task.json", "participant_api.json", "baseline_manifest.json"}
    assert {p.name for p in (output / "evaluator").iterdir()} == {"hidden_evaluator.json"}
    written = json.loads((output / "release/public_task.json").read_text())
    assert written == compile_challenge()["public_task"]
    with pytest.raises(FileExistsError):
        main(["--output-dir", str(output)])
    bundle = compile_challenge()
    bundle["public_task"]["targets"][0]["position"][0] += 1.0
    with pytest.raises(ValueError, match="not bound"):
        write_challenge_bundle(bundle, tmp_path / "tampered")
    assert not (tmp_path / "tampered").exists()
