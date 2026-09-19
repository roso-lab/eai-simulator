"""Small local prepare fixtures exercise city intake without USD/ROS/Isaac."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from demo.benchmark.goal_city_intake import load_city_goal_candidate, validate_city_goal_candidate
from demo.benchmark.goal_driven_contract import GOAL_CONTRACT_VERSION, build_goal_participant_api
from demo.benchmark.goal_driven_fixtures import execution_contract, robot_physical_profile
from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark.contracts import build_episode_binding, episode_parameter_names


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xfe")) + chunk(b"IEND", b""))


def _public():
    outcomes = ["incident_assessed", "exposed_people_accounted_for", "secondary_traffic_protected", "response_access_preserved"]
    return {
        "schema": "eai.benchmark.goal_task_spec", "version": GOAL_CONTRACT_VERSION,
        "task_id": "CITY-TEST-01", "evaluation_track": "goal_driven_team_planning_v1",
        "mission": {"scene_key": "city", "goal": "Protect people and following traffic", "context": "Urban incident response"},
        "required_outcomes": [{"id": name, "description": name, "acceptance": "Independent current evidence",
                               "evidence_expectations": ["measured_state"]} for name in outcomes],
        "candidate_robot_pool": [
            {"id": "ground_1", "type": "carter", "mobility": "ground",
             "public_capabilities": ["ground_navigation", "relay"], "interfaces": ["event_bus"],
             "physical_profile": robot_physical_profile("carter")},
            {"id": "aerial_1", "type": "iris", "mobility": "aerial",
             "public_capabilities": ["aerial_navigation", "observe", "relay"], "interfaces": ["camera", "event_bus"],
             "physical_profile": robot_physical_profile("iris")},
        ],
        "skill_catalog_version": "eai.skills.v2", "execution_contract": execution_contract(),
        "public_world": {
            "regions": [{"id": "road_surface", "kind": "road_network", "public_description": "Road network",
                         "access_modes": ["ground_navigation", "aerial_navigation"], "resource_capacity": 2}],
            "objects": [], "vertical_links": [], "public_notes": "Static prior; current actors require observations",
            "routes": [{"id": "road_link", "from_region_id": "road_surface", "to_region_id": "road_surface",
                        "access_modes": ["ground_navigation"], "length_m": 1.0, "clearance_m": [4.0, 4.0],
                        "max_step_height_m": 0.0, "resource_id": "road", "capacity": 1}],
        },
        "business_constraints": ["Requests do not establish successful outcomes"],
        "budgets": {"simulation_s": 180.0, "planning_s": 30.0, "message_bytes": 8192,
                    "model_tokens": 12000, "max_active_agents": 2},
        "experiment_factor": {"id": "communication_quality", "level": "immediate", "public_rule": "Only delivered evidence",
                              "frozen_inputs": ["scene_layout", "incident_instance", "actor_initial_states",
                                                "candidate_robot_pool", "budgets", "model_budget"]},
    }


def _rehash(root):
    manifest_path = root / "integration_manifest.json"
    manifest = _read(manifest_path)
    manifest["artifact_sha256"] = {str(path.relative_to(root)): _hash(path) for path in sorted(root.rglob("*"))
                                   if path.is_file() and path != manifest_path}
    _write_json(manifest_path, manifest)


def _mutate(root, name, operation):
    path = root / name
    value = _read(path)
    operation(value)
    _write_json(path, value)
    if name != "integration_manifest.json":
        _rehash(root)


def _rebind_public(root):
    public = _read(root / "release/public_task.json")
    digest = canonical_sha256(public)
    _write_json(root / "release/participant_api.json", build_goal_participant_api(public))
    _mutate(root, "authoring/private.json", lambda row: row.update(
        public_task_sha256=digest, outcome_evidence_requirements=deepcopy(public["required_outcomes"])))
    _mutate(root, "integration_manifest.json", lambda row: row.update(public_task_sha256=digest))
    _rehash(root)


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "prepared"
    sources = tmp_path / "city_assets"
    sources.mkdir()
    for name in ("city_layout.json", "city_scenario.json", "city_static.usd", "building.usd"):
        (sources / name).write_text("local test source: " + name, encoding="utf-8")
    public = _public()
    assembly_regions = [
        {"id": "assembly_candidate_001", "kind": "assembly_candidate",
         "public_description": "候选集合位置", "access_modes": ["ground_navigation"], "resource_capacity": 1},
        {"id": "assembly_candidate_002", "kind": "assembly_candidate",
         "public_description": "候选集合位置", "access_modes": ["ground_navigation"], "resource_capacity": 1},
    ]
    public["public_world"]["regions"] = public["public_world"]["regions"] + assembly_regions
    semantics = {
        "schema": "eai.city.semantic_map", "version": 1, "coordinate_frame": "city_world",
        "meters_per_unit": 1.0, "up_axis": "Z", "regions": deepcopy(public["public_world"]["regions"]),
        "bindings": [{"id": "road_surface", "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
                     {"id": "assembly_candidate_001", "candidate_radius_m": 0.75,
                      "geometry": {"type": "Point", "coordinates": [10.0, 6.0]}},
                     {"id": "assembly_candidate_002", "candidate_radius_m": 0.75,
                      "geometry": {"type": "Point", "coordinates": [10.0, 7.0]}}],
        "static_obstacles": [], "vehicle_routes": [],
        "pedestrian_routes": [{"id": f"p{index}", "points": [[float(index), 6.0], [float(index) + 3.0, 6.0]]}
                              for index in range(4)],
        "surface_heights_m": {"sidewalks": 0.3, "ground": 0.22}, "scope": "static_prior_only",
    }
    metadata = {
        "schema": "eai.city.static_map", "version": 1, "coordinate_frame": "city_world",
        "meters_per_unit": 1.0, "up_axis": "Z", "resolution_m": 0.25,
        "origin_xyz_m": [0.0, 0.0, 0.0], "width": 1, "height": 1,
        "status": "static_geometry_candidate", "navigation_validated": False,
    }
    required = []
    for name, public_skill, outcome in (("guide_people", "guide_people", "exposed_people_accounted_for"),
                                        ("control_traffic", "control_following_traffic", "secondary_traffic_protected"),
                                        ("report_incident", "report_incident", "response_access_preserved")):
        required.append({"id": name, "status": "public_skill_registered_runtime_pending",
                         "participant_operation": name if name != "control_traffic" else "control_following_traffic",
                         "required_public_skill": public_skill, "arguments": ["current_evidence"],
                         "preconditions": ["fresh_evidence"], "effects": ["service_request"],
                         "failure_codes": ["unavailable"], "evidence_types": ["independent_receipt"],
                         "cancel_semantics": "safe_hold", "timeout_semantics": "bounded", "owner": "local_adapter",
                         "outcome_ids": [outcome]})
    private = {
        "schema": "eai.city.authoring_private", "version": 1, "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "source_provenance": {
            "source_sha256": {name: _hash(sources / name) for name in ("city_layout.json", "city_scenario.json", "city_static.usd")},
            "composed_usd_layers_sha256": {str(sources / name): _hash(sources / name) for name in ("city_static.usd", "building.usd")},
            "declared_layout_digest": "not-a-certified-digest", "layout_digest_status": "unverified_upstream_algorithm",
        },
        "static_prim_bindings": [{"id": "building_1", "runtime_prim_path": "/World/City/Building_1"}],
        "incident_template": {"seed": 7, "selected_target": None, "visibility": "author_only"},
        "response_template": {"people": [{"person_id": "secret_affected_person_1", "start_xyz": [1.0, 2.0, 0.0]}]},
        "outcome_evidence_requirements": deepcopy(public["required_outcomes"]),
        "full_mission_witnesses": [], "required_services": required,
        "episode_binding": build_episode_binding(semantics),
        "private_factor_design": {"communication": "freeze content, vary delivery", "frozen": ["map", "actor_states"]},
        "required_episode_parameters": episode_parameter_names(),
    }
    review = {"schema": "eai.city.authoring_review", "version": 1, "public_contract_validated": True,
              "authoring_status": "blocked", "execution_validated": False, "scoreable": False,
              "valid_full_mission_plan_count": 0,
              "blockers": ["observed_actor_reference_extension", "two_causally_complete_feasible_plans"]}
    navigation = {"schema": "eai.city.navigation_handoff", "version": 1, "ground_map": "release/maps/occupancy.yaml",
                  "coordinate_frame": "city_world", "aerial_static_bounds": "release/maps/semantic_map.json:static_obstacles",
                  "start_reviews": [{"agent_id": row["id"], "physical_spawn_validated": False}
                                    for row in public["candidate_robot_pool"]], "navigation_ready": False}
    manifest = {
        "schema": "eai.city.integration_manifest", "version": 1, "scene_key": "city",
        "task_family": "urban_incident_response", "selection_scaffold_scene_key": "modern_house_nvd",
        "package_kind": "preparation_draft_not_goal_bundle", "release_status": "authoring_draft",
        "execution_validated": False, "scoreable": False,
        "public_task_sha256": canonical_sha256(public), "public_map_semantics_sha256": canonical_sha256(semantics),
        "public_inputs": ["release/public_task.json", "release/participant_api.json", "release/maps/"],
        "private_inputs": ["authoring/private.json", "authoring/review.json"],
        "response_services": "demo.city_benchmark.response.CityResponseServices",
        "following_traffic_runtime": "demo.city_benchmark.traffic.TrafficControlRuntime",
    }
    for name, value in {"release/public_task.json": public, "release/participant_api.json": build_goal_participant_api(public),
                        "release/maps/semantic_map.json": semantics, "release/maps/map_metadata.json": metadata,
                        "authoring/private.json": private, "authoring/review.json": review,
                        "authoring/navigation_config.json": navigation, "integration_manifest.json": manifest}.items():
        _write_json(root / name, value)
    for name in ("occupancy.png", "map_preview.png"):
        (root / "release/maps" / name).write_bytes(_png())
    (root / "release/maps/occupancy.yaml").write_text(
        "image: occupancy.png\nresolution: 0.25\norigin: [0.0, 0.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n",
        encoding="utf-8")
    _rehash(root)
    return root


def test_candidate_load_and_readonly_audit_preserve_draft_boundary(package):
    before = {p: p.read_bytes() for p in package.rglob("*") if p.is_file()}
    candidate = load_city_goal_candidate(package)
    assert candidate["kind"] == "preparation_draft_not_goal_bundle"
    assert candidate["public_task"]["mission"]["scene_key"] == "city"
    assert candidate["manifest"]["selection_scaffold_scene_key"] == "modern_house_nvd"
    assert candidate["intake_review"]["artifact_count"] == 10
    assert candidate["intake_review"]["authoring_status"] == "blocked"
    assert candidate["intake_review"]["execution_status"] == "external_pending"
    assert candidate["intake_review"]["scoreable"] is False
    assert "hidden_evaluator" not in candidate
    assert "witness_plans" not in candidate
    assert validate_city_goal_candidate(candidate)["validated"]
    assert {p: p.read_bytes() for p in before} == before


def test_required_services_are_registered_but_runtime_pending(package):
    report = load_city_goal_candidate(package)["service_review"]
    assert report["missing_service_ids"] == []
    assert report["registered_shared_skill_ids"] == [
        "control_following_traffic", "guide_people", "report_incident"]
    assert report["registered_service_ids"] == [
        "control_traffic", "guide_people", "report_incident"]
    assert report["declared_backend_entrypoints"]
    assert report["shared_service_review"]["proposed_services"] == ["control_traffic"]
    assert report["local_backend_is_shared_skill"] is False
    assert report["execution_validated"] is False


def test_source_verification_is_explicit_local_byte_check(package):
    unchecked = load_city_goal_candidate(package)["source_binding"]
    assert unchecked["checked_file_count"] == 0
    checked = load_city_goal_candidate(package, verify_sources=True)["source_binding"]
    assert checked["checked_file_count"] == 4
    assert checked["status"] == "listed_local_bytes_verified"
    assert checked["immutable_provider_verified"] is False
    assert checked["usd_dependency_closure_verified"] is False


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink"])
def test_optional_source_verification_rejects_bad_local_binding(package, mutation):
    source = package.parent / "city_assets/building.usd"
    if mutation == "changed":
        source.write_bytes(b"changed")
    elif mutation == "missing":
        source.unlink()
    else:
        target = source.with_name("target.usd")
        source.rename(target)
        source.symlink_to(target)
    assert load_city_goal_candidate(package)["source_binding"]["verification_requested"] is False
    with pytest.raises(ValueError, match="source SHA256 mismatch|unavailable|symbolic link"):
        load_city_goal_candidate(package, verify_sources=True)


@pytest.mark.parametrize("path", ["release/public_task.json", "release/participant_api.json",
                                  "release/maps/occupancy.png", "release/maps/occupancy.yaml",
                                  "release/maps/map_metadata.json", "release/maps/semantic_map.json",
                                  "release/maps/map_preview.png", "authoring/private.json",
                                  "authoring/review.json", "authoring/navigation_config.json"])
def test_all_declared_artifacts_are_required(package, path):
    (package / path).unlink()
    with pytest.raises(ValueError, match="missing artifact"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("path", ["release/hidden_evaluator.json", "authoring/extra.json", "unlisted.bin"])
def test_unlisted_or_extra_files_are_rejected(package, path):
    (package / path).write_bytes(b"extra")
    with pytest.raises(ValueError, match="unexpected artifact"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("path", ["../outside.json", "/tmp/outside.json", "release/../outside.json",
                                  "release//public_task.json", "release/./public_task.json",
                                  "release\\public_task.json", "file:///tmp/outside.json"])
def test_manifest_paths_cannot_traverse_or_alias(package, path):
    _mutate(package, "integration_manifest.json", lambda row: row["artifact_sha256"].update({path: "0" * 64}))
    with pytest.raises(ValueError, match="unsafe relative"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("mutation", ["missing", "extra", "bad_hash"])
def test_manifest_hash_inventory_is_exact(package, mutation):
    def change(row):
        hashes = row["artifact_sha256"]
        if mutation == "missing":
            hashes.pop("authoring/private.json")
        elif mutation == "extra":
            hashes["authoring/not_present.json"] = "0" * 64
        else:
            hashes["authoring/private.json"] = "incorrect"
    _mutate(package, "integration_manifest.json", change)
    with pytest.raises(ValueError, match="inventory|SHA256 digest"):
        load_city_goal_candidate(package)


def test_byte_hash_is_independent_from_canonical_json_hash(package):
    path = package / "release/public_task.json"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact SHA256 mismatch"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("location,key", [("integration_manifest.json", "public_task_sha256"),
                                          ("integration_manifest.json", "public_map_semantics_sha256"),
                                          ("authoring/private.json", "public_task_sha256")])
def test_canonical_bindings_checked_after_file_integrity(package, location, key):
    _mutate(package, location, lambda row: row.update({key: "0" * 64}))
    with pytest.raises(ValueError, match="canonical"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("kind", ["file", "directory", "package_root", "manifest"])
def test_symbolic_links_cannot_bypass_package_boundary(package, kind):
    if kind == "package_root":
        alias = package.with_name("alias")
        alias.symlink_to(package, target_is_directory=True)
        package = alias
    else:
        path = package / {"file": "release/maps/occupancy.png", "directory": "release/maps",
                          "manifest": "integration_manifest.json"}[kind]
        external = package.parent / ("external-" + path.name)
        path.rename(external)
        path.symlink_to(external, target_is_directory=kind == "directory")
    with pytest.raises(ValueError, match="symbolic link"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("location", ["manifest", "public"])
def test_builder_scaffold_is_not_city_task_identity(package, location):
    if location == "manifest":
        _mutate(package, "integration_manifest.json", lambda row: row.update(scene_key="modern_house_nvd"))
    else:
        _mutate(package, "release/public_task.json", lambda row: row["mission"].update(scene_key="modern_house_nvd"))
        _rebind_public(package)
    with pytest.raises(ValueError, match="scaffold"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("change", ["version", "catalog", "wording"])
def test_stale_api_is_rejected_without_repair(package, change):
    def stale(api):
        if change == "version":
            api["version"] = 1
        elif change == "catalog":
            api["skill_catalog"]["skills"]["observe"]["effects"] = ["obsolete_effect"]
        else:
            api["plan_contract"]["runtime_state"] = "obsolete but structurally valid runtime policy"
    _mutate(package, "release/participant_api.json", stale)
    before = (package / "release/participant_api.json").read_bytes()
    with pytest.raises(ValueError, match="stale or invalid.*rerun city prepare"):
        load_city_goal_candidate(package)
    assert (package / "release/participant_api.json").read_bytes() == before


@pytest.mark.parametrize("leak", ["actor", "path", "authoring_file", "prim_path"])
def test_private_identifiers_and_paths_never_enter_public_payload(package, leak):
    token = {"actor": "secret_affected_person_1", "path": str(package.parent / "city_assets/building.usd"),
             "authoring_file": "authoring/private.json", "prim_path": "/World/City/Building_1"}[leak]
    _mutate(package, "release/public_task.json", lambda row: row["public_world"].update(public_notes=token))
    _rebind_public(package)
    with pytest.raises(ValueError, match="private path or actor"):
        load_city_goal_candidate(package)


@pytest.mark.parametrize("location,key,value", [
    ("integration_manifest.json", "scoreable", True), ("authoring/review.json", "authoring_status", "passed"),
    ("authoring/review.json", "execution_validated", True), ("authoring/navigation_config.json", "navigation_ready", True),
    ("authoring/private.json", "full_mission_witnesses", [{"fake": True}]),
])
def test_draft_cannot_be_promoted_by_editing_flags(package, location, key, value):
    _mutate(package, location, lambda row: row.update({key: value}))
    with pytest.raises(ValueError, match="claim|blocked|readiness|witnesses"):
        load_city_goal_candidate(package)


def test_manifest_cannot_publish_private_inputs(package):
    _mutate(package, "integration_manifest.json", lambda row: row["public_inputs"].append("authoring/private.json"))
    with pytest.raises(ValueError, match="public/private"):
        load_city_goal_candidate(package)


def test_duplicate_json_keys_are_rejected(package):
    path = package / "integration_manifest.json"
    path.write_text('{"schema":"first","schema":"eai.city.integration_manifest"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON"):
        load_city_goal_candidate(package)


def test_yaml_image_cannot_reference_an_external_file(package):
    path = package / "release/maps/occupancy.yaml"
    path.write_text(path.read_text().replace("image: occupancy.png", "image: ../../authoring/private.json"))
    _rehash(package)
    with pytest.raises(ValueError, match="packaged map"):
        load_city_goal_candidate(package)


def test_factor_inputs_freeze_business_parameters_without_fabricating_treatments(package):
    original = load_city_goal_candidate(package)
    factor = original["factor_candidate_inputs"]
    assert factor["visibility"] == "author_only"
    assert factor["hidden_treatments_instantiated"] is False
    assert factor["factor_effect_validated"] is False
    assert "assembly_capacity" in factor["episode_parameters_to_bind"]
    _mutate(package, "release/public_task.json", lambda row: row["budgets"].update(model_tokens=10000))
    _rebind_public(package)
    changed = load_city_goal_candidate(package)
    assert factor["frozen_input_sha256"] != changed["factor_candidate_inputs"]["frozen_input_sha256"]


def test_readonly_validator_rechecks_disk_and_cached_flags(package):
    candidate = load_city_goal_candidate(package)
    forged = deepcopy(candidate)
    forged["intake_review"]["scoreable"] = True
    with pytest.raises(ValueError, match="stale or differs"):
        validate_city_goal_candidate(forged)
    (package / "release/maps/occupancy.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="artifact SHA256 mismatch"):
        validate_city_goal_candidate(candidate)


def test_required_city_service_cannot_be_deleted_to_hide_a_gap(package):
    _mutate(package, "authoring/private.json", lambda row: row["required_services"].pop(0))
    with pytest.raises(ValueError, match="retain guidance, traffic and reporting"):
        load_city_goal_candidate(package)


def test_public_maps_cannot_embed_private_actor_state(package):
    _mutate(package, "release/maps/semantic_map.json", lambda row: row.update(
        response_template={"person_id": "secret_affected_person_1"}))
    semantics = _read(package / "release/maps/semantic_map.json")
    _mutate(package, "integration_manifest.json", lambda row: row.update(public_map_semantics_sha256=canonical_sha256(semantics)))
    with pytest.raises(ValueError, match="extra fields"):
        load_city_goal_candidate(package)


def test_source_relative_paths_cannot_escape_the_declared_root(package):
    _mutate(package, "authoring/private.json", lambda row: row["source_provenance"]["source_sha256"].update(
        {"../outside.usd": "0" * 64}))
    with pytest.raises(ValueError, match="unsafe relative"):
        load_city_goal_candidate(package)


def test_composed_layer_root_hash_must_agree_with_top_level_source(package):
    def change(private):
        layers = private["source_provenance"]["composed_usd_layers_sha256"]
        root = next(name for name in layers if Path(name).name == "city_static.usd")
        layers[root] = "0" * 64
    _mutate(package, "authoring/private.json", change)
    with pytest.raises(ValueError, match="matching composed city_static"):
        load_city_goal_candidate(package)


def test_episode_binding_is_reviewed_against_the_packaged_semantics(package):
    candidate = load_city_goal_candidate(package)
    review = candidate["episode_binding_review"]
    assert review["validated"] is True
    assert review["bound_person_count"] == 4
    assert review["runtime_admission_pending"] is True and review["execution_validated"] is False
    assert candidate["intake_review"]["episode_parameters_bound"] is True
    binding = candidate["private_authoring"]["episode_binding"]
    assert sorted(binding["affected_person_roster"][0]["evacuation_route"][-1][:2]) == sorted(
        next(row for row in _read(package / "release/maps/semantic_map.json")["bindings"]
             if row["id"] == binding["affected_person_roster"][0]["assembly_region_id"]
             )["geometry"]["coordinates"])


@pytest.mark.parametrize("mutation", [
    lambda b: b["affected_person_roster"].pop(),
    lambda b: b["affected_person_roster"][0].update(assembly_region_id="road_surface"),
    lambda b: b["assembly_capacity"][0].update(capacity_people=len(b["assembly_capacity"][0]["bound_person_ids"]) + 1),
    lambda b: b["response_service_endpoint"].update(receipt_deadline_s=0),
    lambda b: b["dynamic_hazard_boundaries"].update(exclusion_half_extents_m=[-4.0, 4.0]),
])
def test_tampered_episode_binding_fails_the_intake(package, mutation):
    def change(private):
        mutation(private["episode_binding"])
    _mutate(package, "authoring/private.json", change)
    with pytest.raises(ValueError, match="stale or invalid city episode binding"):
        load_city_goal_candidate(package)


def test_episode_binding_person_ids_stay_private(package):
    binding = _read(package / "authoring/private.json")["episode_binding"]
    token = binding["affected_person_roster"][0]["person_id"]
    _mutate(package, "release/public_task.json", lambda row: row["public_world"].update(public_notes=token))
    _rebind_public(package)
    with pytest.raises(ValueError, match="private path or actor"):
        load_city_goal_candidate(package)


def test_episode_binding_is_a_frozen_factor_input(package):
    original = load_city_goal_candidate(package)["factor_candidate_inputs"]

    def rotate(private):
        private["episode_binding"]["seed"] = 8
    _mutate(package, "authoring/private.json", rotate)
    changed = load_city_goal_candidate(package)["factor_candidate_inputs"]
    assert original["frozen_input_sha256"] != changed["frozen_input_sha256"]
    assert original["episode_parameters_to_bind"] == episode_parameter_names()


def test_absent_episode_binding_still_loads_as_an_unbound_draft(package):
    _mutate(package, "authoring/private.json", lambda row: row.update(episode_binding=None))
    candidate = load_city_goal_candidate(package)
    assert candidate["episode_binding_review"] is None
    assert candidate["intake_review"]["episode_parameters_bound"] is False
    assert candidate["intake_review"]["authoring_status"] == "blocked"


def test_preparation_drafts_still_reject_embedded_witnesses_with_builder_guidance(package):
    _mutate(package, "authoring/private.json", lambda row: row.update(
        full_mission_witnesses=[{"id": "forged_witness"}]))
    with pytest.raises(ValueError, match="declaration-level"):
        load_city_goal_candidate(package)
