"""Adversarial author-inventory checks; no Isaac/ROS or production asset writes.

Source mutations below affect isolated copies only. Tiny USD/raster fixtures
are parser evidence, never physical validation or an executed benchmark.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from demo.benchmark import goal_scene_binding as binding
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.provenance import canonical_sha256


_REAL_ROOT = binding.REPO_ROOT
_SCENARIO = {
    "factory": "demo/factory_benchmark/scenarios/inspection_dispatch_01.json",
    "warehouse_nvd": "demo/warehouse_benchmark/scenarios/business_logistics_01.json",
    "modern_house_nvd": "demo/modern_house_benchmark/scenarios/home_recovery_01.json",
}
_TEMPLATES = "source/EAI/EAI/scenario_spec/templates.py"
_PROPS = "source/EAI_assets/EAI_assets/scenario_props.json"


@pytest.fixture
def local_sources(tmp_path, monkeypatch):
    """Real declarations, separate writable copies, initially missing assets."""
    root = tmp_path / "source_copy"
    files = [
        "demo/benchmark/goal_scene_binding.py", "demo/benchmark/goal_driven_tasks.py",
        "demo/benchmark/goal_driven_fixtures.py", "demo/benchmark/goal_driven_contract.py",
        "demo/benchmark/goal_constraints.py", "demo/benchmark/skill_ontology.py",
        "source/EAI_assets/EAI_assets/scenario_assets.py",
        "source/EAI_assets/EAI_assets/asset_resolver.py", _TEMPLATES, _PROPS,
        "demo/factory_benchmark/prim_bindings.py",
        "demo/factory_benchmark/scenarios/vertical_stair_01.json",
        *_SCENARIO.values(),
    ]
    for scene, module in (("factory", "factory_benchmark"),
                          ("warehouse_nvd", "warehouse_benchmark"),
                          ("modern_house_nvd", "modern_house_benchmark")):
        files.extend([f"demo/{module}/config.py", f"source/EAI_assets/EAI_assets/scene/{scene}.py"])
    for relative in files:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REAL_ROOT / relative, destination)
    usd = root / "usd"
    usd.mkdir()
    monkeypatch.setattr(binding, "REPO_ROOT", root)
    monkeypatch.setattr(binding, "_local_usd_root", lambda: usd)
    monkeypatch.delenv("EAI_NVD_ASSET_ROOT", raising=False)
    return root


def _build(scene="warehouse_nvd", *, verify=True):
    return binding.build_goal_scene_binding_inventory(scene, verify_sources=verify)


def _row(report, kind, identifier):
    return next(row for row in report["inventory"][kind] if row["public_id"] == identifier)


def _source(report, identifier):
    return next(row for row in report["sources"] if row["id"] == identifier)


def _save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _rehash(report):
    report["sha256"] = canonical_sha256({key: value for key, value in report.items() if key != "sha256"})
    return report


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd"])
def test_all_public_items_have_requirements_and_gaps_without_binding_claim(local_sources, scene):
    report = _build(scene)
    public = build_goal_task_spec(scene)
    assert report["visibility"] == "author_only"
    assert report["public_task_sha256"] == canonical_sha256(public)
    for kind, rows in report["inventory"].items():
        expected = public["candidate_robot_pool"] if kind == "robots" else public["public_world"][kind]
        assert {row["public_id"] for row in rows} == {row["id"] for row in expected}
        for row in rows:
            assert row["requirements"] and row["gaps"]
            assert row["physical_bound"] is False and row["binding_status"] == "unbound"
            assert "not_scene_measurement" in row["parameter_basis"]
            assert all(c["identity_equivalent"] is False and c["physical_bound"] is False
                       for c in row["candidates"])
    review = binding.validate_goal_scene_binding_inventory(report)
    assert review["validated"] is True
    assert review["scene_binding_validated"] is review["execution_validated"] is review["scoreable"] is False
    assert report["review"]["status"] == "blocked"


@pytest.mark.parametrize("scene,object_id,experimental,declared", [
    ("warehouse_nvd", "order_crate_01", 12.0, 60.0),
    ("warehouse_nvd", "order_carton_01", 3.0, 8.0),
    ("modern_house_nvd", "living_armchair_01", 14.0, 28.0),
    ("modern_house_nvd", "resident_bookstack_01", 2.0, 2.5),
])
def test_scene_mass_is_declared_and_compared_not_mislabelled_measured(
        local_sources, scene, object_id, experimental, declared):
    row = _row(_build(scene), "objects", object_id)
    difference = next(item for item in row["differences"] if item["field"] == "mass_kg")
    assert difference["experimental_value"] == experimental
    assert difference["scenario_declared_value"] == declared
    assert difference["measured_value"] is None
    assert row["physical_bound"] is False


def test_every_candidate_links_to_exact_current_source_bytes(local_sources):
    report = _build()
    sources = {row["id"]: row for row in report["sources"]}
    for source in sources.values():
        if source["status"] == "present":
            raw = Path(source["path"]).read_bytes()
            assert source["sha256"] == hashlib.sha256(raw).hexdigest()
            assert source["size_bytes"] == len(raw)
        else:
            assert source["sha256"] is None and source["size_bytes"] is None
    for rows in report["inventory"].values():
        for row in rows:
            for candidate in row["candidates"]:
                reference = candidate["source"]
                assert reference["source_sha256"] == sources[reference["source_id"]]["sha256"]
                assert reference["selector"]


def test_factory_native_inspection_box_is_not_a_production_marker_or_service_cart(local_sources):
    report = _build("factory")
    marker = _row(report, "objects", "production_anomaly_marker")
    cart = _row(report, "objects", "maintenance_service_cart")
    assert marker["candidates"][0]["id"] == "inspection_target_box"
    assert "legacy_inventory_box_is_not_the_production_anomaly_record" in marker["gaps"]
    assert cart["candidates"] == []
    assert "no_reviewed_candidate_for_this_public_object" in cart["gaps"]
    stair = _row(report, "vertical_links", "main_stair")
    assert {c["kind"] for c in stair["candidates"]} == {"stair", "prim"}
    assert "stair_top_aabb_is_not_exit_support_height" in stair["gaps"]
    assert stair["physical_bound"] is False


def test_legacy_target_zone_disagreement_and_route_gaps_are_visible(local_sources):
    report = _build("modern_house_nvd")
    region = _row(report, "regions", "living_area")
    assert any(d["kind"] == "legacy_target_outside_suggested_template_zone" for d in region["differences"])
    for route in report["inventory"]["routes"]:
        assert route["candidates"] == []
        assert "endpoint_candidates_do_not_establish_a_route" in route["gaps"]
    ground = _row(report, "robots", "house_ground_3")
    assert [c["id"] for c in ground["candidates"]] == ["m20_1"]
    assert "scenario_has_fewer_distinct_instances_than_public_candidate_pool_of_this_type" in ground["gaps"]


def test_renamed_goal_object_does_not_gain_binding_from_matching_legacy_name(local_sources, monkeypatch):
    public = build_goal_task_spec("warehouse_nvd")
    public["public_world"]["objects"][0]["id"] = "oversized_crate_proxy"
    monkeypatch.setattr(binding, "build_goal_task_spec", lambda scene: deepcopy(public))
    report = _build()
    row = _row(report, "objects", "oversized_crate_proxy")
    assert row["candidates"] == []
    assert "no_reviewed_candidate_for_this_public_object" in row["gaps"]


def test_renamed_goal_region_does_not_alias_existing_target(local_sources, monkeypatch):
    public = build_goal_task_spec("warehouse_nvd")
    old, new = "consolidation_area", "consolidation_zone"
    for region in public["public_world"]["regions"]:
        if region["id"] == old:
            region["id"] = new
    for route in public["public_world"]["routes"]:
        for field in ("from_region_id", "to_region_id"):
            if route[field] == old:
                route[field] = new
    monkeypatch.setattr(binding, "build_goal_task_spec", lambda scene: deepcopy(public))
    row = _row(_build(), "regions", new)
    assert row["candidates"] == []
    assert "no_explicit_existing_region_candidate" in row["gaps"]


@pytest.mark.parametrize("mutation", ["remove", "rename", "wrong_asset"])
def test_missing_or_renamed_scenario_entity_remains_a_gap(local_sources, mutation):
    path = local_sources / _SCENARIO["warehouse_nvd"]
    scenario = json.loads(path.read_text())
    entities = scenario["composition"]["entities"]
    if mutation == "remove":
        entities.pop(0)
    elif mutation == "rename":
        entities[0]["id"] = "order_crate_01"  # Equality to goal name proves nothing.
    else:
        entities[0]["asset_key"] = "cardbox_c"
    _save(path, scenario)
    row = _row(_build(), "objects", "order_crate_01")
    assert all(c["kind"] != "entity" for c in row["candidates"])
    assert "expected_legacy_entity_or_asset_key_missing:oversized_crate_proxy:wooden_crate_a" in row["gaps"]
    assert row["physical_bound"] is False


def test_missing_prop_declaration_and_assets_are_not_replaced_by_fake_geometry(local_sources):
    path = local_sources / _PROPS
    props = json.loads(path.read_text())
    props["props"] = [p for p in props["props"] if p["key"] != "wooden_crate_a"]
    _save(path, props)
    report = _build()
    assert "prop_declaration_missing:wooden_crate_a" in _row(report, "objects", "order_crate_01")["gaps"]
    assert _source(report, "scene_usd")["status"] == "missing"
    assert not any(e["validated"] for e in report["local_evidence"])
    assert report["review"]["scene_binding_validated"] is False


@pytest.mark.parametrize("change", ["modify", "delete"])
def test_stale_or_missing_source_rejected_even_if_claims_would_be_identical(local_sources, change):
    report = _build()
    path = local_sources / _TEMPLATES
    if change == "modify":
        path.write_text(path.read_text() + "\n# isolated source revision\n")
    else:
        path.unlink()
    with pytest.raises(ValueError, match="sources are stale, missing, or changed"):
        binding.validate_goal_scene_binding_inventory(report)
    current = _build()
    assert binding.validate_goal_scene_binding_inventory(current)["validated"]
    assert current["review"]["scene_binding_validated"] is False


@pytest.mark.parametrize("forgery", ["physical_bound", "scoreable", "measured_mass", "drop_object", "rename_object", "hash_only"])
def test_rehashed_forged_claims_do_not_pass_source_derived_validation(local_sources, forgery):
    report = _build()
    if forgery == "physical_bound":
        report["inventory"]["objects"][0]["physical_bound"] = True
    elif forgery == "scoreable":
        report["review"]["scoreable"] = True
    elif forgery == "measured_mass":
        report["inventory"]["objects"][0]["differences"][0]["measured_value"] = 12.0
    elif forgery == "drop_object":
        report["inventory"]["objects"].pop()
        report["review"]["public_item_counts"]["objects"] -= 1
    elif forgery == "rename_object":
        report["inventory"]["objects"][0]["public_id"] = "oversized_crate_proxy"
    else:
        report["local_evidence"].append({"kind": "measured_geometry", "validated": True,
                                          "source_sha256": _source(report, "scenario")["sha256"]})
    _rehash(report)
    with pytest.raises(ValueError, match="claims/coverage"):
        binding.validate_goal_scene_binding_inventory(report)


def test_arbitrary_measured_labels_inside_author_source_cannot_promote_physical_binding(local_sources):
    path = local_sources / _SCENARIO["warehouse_nvd"]
    scenario = json.loads(path.read_text())
    scenario["composition"]["entities"][0]["physics"].update({
        "measured_mass_kg": 12.0, "measurement_verified": True, "physical_bound": True})
    _save(path, scenario)
    report = _build()
    row = _row(report, "objects", "order_crate_01")
    assert row["candidates"][0]["basis"] == "existing_authoring_declaration"
    assert all(d["measured_value"] is None for d in row["differences"])
    assert row["physical_bound"] is report["review"]["physical_bound"] is False
    assert binding.validate_goal_scene_binding_inventory(report)["scene_binding_validated"] is False


def test_tampered_source_hash_rejected_even_with_recomputed_inventory_digest(local_sources):
    report = _build()
    _source(report, "scenario")["sha256"] = "a" * 64
    _rehash(report)
    with pytest.raises(ValueError, match="sources are stale"):
        binding.validate_goal_scene_binding_inventory(report)


def _native_fixture(root):
    Usd = pytest.importorskip("pxr.Usd")
    UsdGeom = pytest.importorskip("pxr.UsdGeom")
    path = (root / "usd/assets/nvd/test_package/Assets/XR/Stages/Indoor/Warehouse/Containers/Wooden/WoodenCrate_A1.usd")
    path.parent.mkdir(parents=True)
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    UsdGeom.SetStageUpAxis(stage, "Z")
    base = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(base.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/World/TestMesh")
    mesh.CreatePointsAttr().Set([(10, 20, 0), (30, 20, 10), (10, 60, 0)])
    mesh.CreateFaceVertexCountsAttr().Set([3])
    mesh.CreateFaceVertexIndicesAttr().Set([0, 1, 2])
    stage.GetRootLayer().Save()
    return path


def test_native_layer_geometry_is_hash_bound_local_evidence_only(local_sources):
    path = _native_fixture(local_sources)
    report = _build()
    evidence = next(e for e in report["local_evidence"] if e.get("asset_key") == "wooden_crate_a")
    assert evidence["validated"] is True
    assert evidence["source"]["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert evidence["authored_metadata"] == {"metersPerUnit": 0.01, "upAxis": "Z"}
    assert evidence["local_mesh_geometry"][0]["local_point_bounds"] == {"min": [10.0, 20.0, 0.0], "max": [30.0, 60.0, 10.0]}
    assert evidence["composition_resolved"] is evidence["physical_bound"] is False
    assert evidence["runtime_registry_resolution_verified"] is False
    assert report["review"]["scene_binding_validated"] is False
    assert binding.validate_goal_scene_binding_inventory(report)["validated"]
    path.unlink()
    with pytest.raises(ValueError, match="sources are stale"):
        binding.validate_goal_scene_binding_inventory(report)


def test_declarations_only_mode_does_not_claim_usd_inspection(local_sources):
    _native_fixture(local_sources)
    report = _build(verify=False)
    assert not any(e["kind"] == "authored_usd_layer" for e in report["local_evidence"])
    assert report["review"]["local_evidence_checks_passed"] == 0
    assert report["review"]["scene_binding_validated"] is False


def test_map_pixel_sample_is_not_goal_region_or_route_clearance(local_sources):
    Image = pytest.importorskip("PIL.Image")
    import yaml

    directory = local_sources / "usd/scene/warehouse_nvd"
    directory.mkdir(parents=True)
    image_path = directory / "ground_map_0.6m.png"
    Image.new("L", (10, 10), 255).save(image_path)
    (directory / "ground_map_0.6m.yaml").write_text(yaml.safe_dump({
        "image": image_path.name, "resolution": 10.0, "origin": [-50.0, -50.0, 0.0],
        "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196}))
    report = _build()
    evidence = next(e for e in report["local_evidence"] if e["kind"] == "occupancy_product")
    assert evidence["validated"] and evidence["image_size_px"] == [10, 10]
    assert all(sample["point_occupancy"] == "free" for sample in evidence["declared_target_samples"])
    assert evidence["route_clearance_validated"] is evidence["scene_revision_alignment_validated"] is False
    Image.new("L", (10, 10), 0).save(image_path)
    with pytest.raises(ValueError, match="sources are stale"):
        binding.validate_goal_scene_binding_inventory(report)


def test_writer_roundtrip_and_no_overwrite_even_when_content_is_valid(local_sources, tmp_path):
    report = _build()
    destination = tmp_path / "reports/warehouse.json"
    assert binding.write_goal_scene_binding_inventory(report, destination) == destination
    assert json.loads(destination.read_text()) == report
    before = destination.read_bytes()
    with pytest.raises(FileExistsError):
        binding.write_goal_scene_binding_inventory(report, destination)
    assert destination.read_bytes() == before
    assert list(destination.parent.iterdir()) == [destination]


def test_writer_refuses_existing_symlink_without_touching_target(local_sources, tmp_path):
    report = _build()
    target = tmp_path / "keep.txt"
    target.write_text("keep this file")
    link = tmp_path / "report.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        binding.write_goal_scene_binding_inventory(report, link)
    assert target.read_text() == "keep this file" and link.is_symlink()


def test_invalid_report_is_rejected_before_output_is_created(local_sources, tmp_path):
    report = _build()
    report["review"]["scene_binding_validated"] = True
    _rehash(report)
    target = tmp_path / "must_not_be_created/report.json"
    with pytest.raises(ValueError, match="claims/coverage"):
        binding.write_goal_scene_binding_inventory(report, target)
    assert not target.parent.exists()


def test_cli_three_scene_roundtrip_and_existing_directory_rejection(local_sources, tmp_path, capsys):
    output = tmp_path / "audit_output"
    assert binding.main(["audit", "--output-dir", str(output), "--no-verify-sources"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["inventory_validated"] and summary["scene_binding_validated"] is False
    assert {p.name for p in output.iterdir()} == {"factory.json", "warehouse_nvd.json", "modern_house_nvd.json"}
    for report in sorted(output.iterdir()):
        assert binding.main(["verify", str(report)]) == 0
        assert json.loads(capsys.readouterr().out)["scoreable"] is False
    with pytest.raises(FileExistsError):
        binding.main(["audit", "--output-dir", str(output)])
