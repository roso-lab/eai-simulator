"""Focused offline checks for template-based family generation (V3 layer).

Uses a synthetic occupancy map so the suite never touches the provider, the
network, Isaac, or ROS. Covers template building, task-spec validation, the
seeded sampler, paired condition expansion, delta rules, and the connectivity
gate including barrier sealing.
"""

import copy
import json
from pathlib import Path

import pytest

from EAI.scenario_spec.geometry import FactoryMap, validate_connectivity
from EAI.scenario_spec.operators import (
    apply_delta,
    expand_family,
    instantiate,
    skeleton_family,
    skeleton_spec,
    task_spec_schema,
    task_spec_manifest,
    validate_task_spec,
)
from EAI.scenario_spec.templates import build_template, zone_anchors
from EAI.scenario_spec.validation import ScenarioError

ROOT = Path(__file__).resolve().parents[3]


def _synthetic_map(tmp_path: Path) -> FactoryMap:
    from PIL import Image

    # Dividing wall with a 2 m doorway (rows 8-11) so west and east connect.
    pixels = bytearray(b"\xff" * (20 * 20))
    for row in range(20):
        if 8 <= row <= 11:
            continue
        for col in (9, 10):
            pixels[row * 20 + col] = 0
    image_path = tmp_path / "map.png"
    Image.frombytes("L", (20, 20), bytes(pixels)).save(image_path)
    yaml_path = tmp_path / "map.yaml"
    yaml_path.write_text(
        "image: map.png\nresolution: 0.5\norigin: [0.0, 0.0, 0.0]\n"
        "negate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n",
        encoding="utf-8",
    )
    return FactoryMap(yaml_path)


def _synthetic_template(tmp_path: Path):
    from EAI.scenario_spec.templates import Zone

    occupancy = _synthetic_map(tmp_path)
    zones = (
        Zone("west", (0.0, 0.0, 4.0, 10.0), "storage"),
        Zone("east", (6.0, 0.0, 10.0, 10.0), "workshop"),
    )
    # scene_key stays "factory": the V2 episode contract currently locks the
    # scene enum, while this suite's geometry gates run on the synthetic map.
    return build_template("synthetic", "factory", zones, occupancy), occupancy


def _spec(template):
    return {
        "skeleton": "TRN-01",
        "robots": [
            {"ref": "carrier", "type": "carter", "zones": ["west"], "attachments": ["z1"]},
            {"ref": "relay", "type": "m20", "zones": ["east"], "attachments": []},
        ],
        "entities": [
            {"ref": "cargo", "asset": "primitive_box", "zones": ["west"],
             "size_m": [0.5, 0.4, 0.4], "mass_kg": 8.0},
        ],
        "targets": [
            {"ref": "delivery", "zones": ["east"], "radius_m": 0.5},
        ],
        "phase_nodes": [
            {"id": "move_to_cargo", "skill": "navigate", "agent_ref": "carrier",
             "target_ref": "cargo", "object_ref": None, "prerequisites": []},
            {"id": "transport", "skill": "transport_object", "agent_ref": "carrier",
             "target_ref": "delivery", "object_ref": "cargo", "prerequisites": ["move_to_cargo"]},
        ],
    }


def test_template_zones_have_anchors_and_stable_ids(tmp_path: Path) -> None:
    template, _ = _synthetic_template(tmp_path)
    west = zone_anchors(template, "west")
    east = zone_anchors(template, "east")
    assert west and east
    assert {anchor.id for anchor in west}.isdisjoint({anchor.id for anchor in east})
    rebuilt, _ = _synthetic_template(tmp_path)
    assert [a.id for a in template.anchors] == [a.id for a in rebuilt.anchors]


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("duplicate_ref", lambda s: s["entities"].append(copy.deepcopy(s["entities"][0]))),
        ("unknown_zone", lambda s: s["robots"][0].__setitem__("zones", ["atlantis"])),
        ("unknown_asset", lambda s: s["entities"][0].__setitem__("asset", "unobtainium")),
        ("bad_size", lambda s: s["entities"][0].__setitem__("size_m", [9.0, 0.4, 0.4])),
        ("mass_on_static", lambda s: s["entities"].append(
            {"ref": "beam", "asset": "safety_beam", "zones": ["west"],
             "size_m": [0.3, 2.0, 0.45], "mass_kg": 5.0, "mode": "static"})),
        ("unknown_agent", lambda s: s["phase_nodes"][0].__setitem__("agent_ref", "ghost")),
        ("missing_manipulation", lambda s: s["robots"][0].__setitem__("attachments", [])),
        ("object_required", lambda s: s["phase_nodes"][1].__setitem__("object_ref", None)),
        ("unsupported_attachment", lambda s: s["robots"][0].__setitem__("attachments", ["ur5", "z1"])),
    ],
)
def test_task_spec_is_rejected(tmp_path, name, mutate) -> None:
    template, _ = _synthetic_template(tmp_path)
    spec = _spec(template)
    mutate(spec)
    with pytest.raises(ScenarioError):
        validate_task_spec(spec, template)


def test_sampler_is_deterministic_and_passes_gates(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    spec = _spec(template)
    first = instantiate(spec, template, occupancy, seed=5)
    second = instantiate(spec, template, occupancy, seed=5)
    assert first == second
    other = instantiate(spec, template, occupancy, seed=6)
    assert json.dumps(first, sort_keys=True) != json.dumps(other, sort_keys=True)
    assert first["robots"][0]["id"] == "carter_1"
    assert first["robots"][1]["id"] == "m20_1"
    assert first["robots"][0]["spawn_pose"]["position"][2] == 0.2
    assert first["composition"]["entities"][0]["spawn_pose"]["position"][2] == pytest.approx(0.2)


def test_family_conditions_are_paired_by_delta(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    family = {
        "family_id": "syn-pair", "seed": 9, "scientific_target": "H2=mass",
        "base_spec": _spec(template),
        "conditions": [
            {"id": "low", "level": "low",
             "delta": [{"op": "set_mass", "ref": "cargo", "mass_kg": 8.0}]},
            {"id": "high", "level": "high",
             "delta": [{"op": "set_mass", "ref": "cargo", "mass_kg": 30.0}]},
        ],
    }
    record, instances = expand_family(family, template, occupancy)
    low, high = instances["low"], instances["high"]
    low["composition"]["entities"][0]["physics"]["mass_kg"] = 30.0
    low["task_name"] = high["task_name"]
    assert low == high
    assert record["family_id"] == "syn_pair"
    assert [c["id"] for c in record["conditions"]] == ["low", "high"]


def test_apply_delta_rules(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=3)
    with pytest.raises(ScenarioError):
        apply_delta(data, {"op": "grow_arm", "ref": "cargo"})
    with pytest.raises(ScenarioError):
        apply_delta(data, {"op": "set_mass", "ref": "ghost", "mass_kg": 5.0})
    with pytest.raises(ScenarioError):
        apply_delta(data, {"op": "set_mass", "ref": "cargo", "mass_kg": 500.0})
    changed = apply_delta(copy.deepcopy(data), {"op": "set_mass", "ref": "cargo", "mass_kg": 30.0})
    assert changed["composition"]["entities"][0]["physics"]["mass_kg"] == 30.0


def test_connectivity_rejects_sealed_corridor(tmp_path: Path) -> None:
    template, occupancy = _synthetic_template(tmp_path)
    data = instantiate(_spec(template), template, occupancy, seed=4)
    assert validate_connectivity(data, occupancy)["status"] == "connectivity_validated"

    # A static wall spanning the map's only gap seals west from east.
    sealed = copy.deepcopy(data)
    sealed["composition"]["entities"].append({
        "id": "seal", "asset_key": "safety_beam",
        "size_m": [0.6, 10.0, 0.45],
        "spawn_pose": {"position": [5.0, 5.0, 0.225], "rotation": [1.0, 0.0, 0.0, 0.0]},
        "physics": {"mode": "static", "mass_kg": None, "static_friction": 0.8,
                    "dynamic_friction": 0.6, "restitution": 0.05},
    })
    with pytest.raises(ScenarioError):
        validate_connectivity(sealed, occupancy)


def _factory_or_skip():
    from EAI.scenario_spec.geometry import provider_map
    from EAI.scenario_spec.templates import factory_template

    try:
        occupancy = provider_map()
        return factory_template(occupancy), occupancy
    except OSError:
        pytest.skip("factory occupancy map not cached locally")


@pytest.mark.parametrize("skeleton", ["TRN-01", "SAR-01", "COV-03"])
def test_skeleton_recipes_compile(skeleton) -> None:
    template, _ = _factory_or_skip()
    validate_task_spec(skeleton_spec(skeleton), template)


def test_trn01_family_expands_paired_on_factory_template() -> None:
    template, occupancy = _factory_or_skip()
    family = skeleton_family("TRN-01", family_id="trn test", seed=11)
    record, instances = expand_family(family, template, occupancy)
    low, high = instances["low"], instances["high"]
    assert low["robots"] == high["robots"]
    assert low["composition"]["entities"][0]["spawn_pose"] == high["composition"]["entities"][0]["spawn_pose"]
    assert low["composition"]["entities"][0]["physics"]["mass_kg"] == 8.0
    assert high["composition"]["entities"][0]["physics"]["mass_kg"] == 30.0
    assert record["family_id"] == "trn_test"


def test_trn01_is_an_honest_single_carrier_baseline_and_policy_is_enforced() -> None:
    template, occupancy = _factory_or_skip()
    family = skeleton_family("TRN-01", family_id="payload_gate", seed=11)
    assert len(family["base_spec"]["robots"]) == 1
    with pytest.raises(ScenarioError, match="cannot carry"):
        expand_family(
            family, template, occupancy,
            payload_policy={"carter": 15.0, "m20": 15.0},
        )


def test_task_spec_schema_and_manifest_have_no_paths(tmp_path: Path) -> None:
    template, _ = _synthetic_template(tmp_path)
    schema = task_spec_schema(template)
    assert schema["properties"]["robots"]["items"]["properties"]["zones"]["items"]["enum"] == ["west", "east"]
    view = task_spec_manifest(template)
    rendered = json.dumps(view)
    assert ".usd" not in rendered and ".py" not in rendered
    assert view["template_key"] == "synthetic"
    assert set(view["constraints"])


def test_layout_signature_and_pair_diff(tmp_path: Path) -> None:
    from EAI.scenario_spec.operators import diff_fields, instantiate, layout_signature
    from copy import deepcopy

    template, occupancy = _synthetic_template(tmp_path)
    base = instantiate(_spec(template), template, occupancy, seed=5)
    twin = deepcopy(base)
    twin["composition"]["entities"][0]["physics"]["mass_kg"] = 30.0
    twin["task_name"] = "other"
    assert layout_signature(base) == layout_signature(twin)  # mass/name ignored
    assert diff_fields(base, twin) == ["$.composition.entities[0].physics.mass_kg", "$.task_name"]

    other_seed = instantiate(_spec(template), template, occupancy, seed=6)
    assert layout_signature(base) != layout_signature(other_seed)


def test_batch_pool_roundtrip(tmp_path: Path) -> None:
    import sys as _sys

    root = str(ROOT)
    if root not in _sys.path:
        _sys.path.insert(0, root)
    from tools.scenario_generation.batch_validate import load_pool, save_pool, upsert_instance

    pool_dir = tmp_path / "pool"
    pool = load_pool(pool_dir)
    upsert_instance(pool, {"id": "a", "labels": ["placement_validated"]})
    upsert_instance(pool, {"id": "a", "labels": ["placement_validated", "spawn_validated"]})
    upsert_instance(pool, {"id": "b", "labels": ["placement_validated"]})
    save_pool(pool_dir, pool)
    reloaded = load_pool(pool_dir)
    assert [item["id"] for item in reloaded["instances"]] == ["a", "b"]
    assert "spawn_validated" in reloaded["instances"][0]["labels"]


def test_nvd_prop_registry_both_modes(monkeypatch) -> None:
    """无 NVD root 时注册表退化为 3 个程序化资产；有 root 时 nvd_ props 出现。"""
    import importlib

    import os

    from EAI_assets import scenario_assets as sa

    monkeypatch.delenv("EAI_NVD_ASSET_ROOT", raising=False)
    reloaded = importlib.reload(sa)
    assert set(reloaded.ASSETS) == {"primitive_box", "safety_beam", "goal_marker"}

    monkeypatch.setenv("EAI_NVD_ASSET_ROOT", "/home/pupil/下载/NVD_assets_sanitized")
    reloaded = importlib.reload(sa)
    nvd_keys = [k for k in reloaded.ASSETS if k.startswith("nvd_")]
    if Path("/home/pupil/下载/NVD_assets_sanitized").is_dir():
        assert len(nvd_keys) == 8
        cone = reloaded.ASSETS["nvd_trafficcone_a01"]
        assert "dynamic" in cone.modes and "visual" in cone.modes
        assert cone.suggested_mass_kg and cone.suggested_mass_kg > 0
    else:
        assert nvd_keys == []
    importlib.reload(sa)  # restore env-independent state for other tests


def test_mezzanine_platform_on_factory_template() -> None:
    """用户确认的二层平台（/World/wall/wall_06，顶面 1.57m）在模板与采样中可用。"""
    template, occupancy = _factory_or_skip()
    from EAI.scenario_spec.templates import zone_anchors

    anchors = zone_anchors(template, "mezzanine_south")
    assert len(anchors) >= 4
    level = next(z for z in template.zones if z.id == "mezzanine_south").level
    assert template.levels[level].floor_z == 1.57
    ramp = next(z for z in template.zones if z.id == "ramp_top_west")
    assert template.levels[ramp.level].floor_z == 0.85
    for anchor in anchors:
        assert 0.77 <= anchor.x <= 2.67 and -9.31 <= anchor.y <= -7.31

    from EAI.scenario_spec.operators import skeleton_spec

    spec = skeleton_spec("TRN-MEZ", arm="ur5")
    mezz = [z for r in spec["robots"] + spec["entities"] + spec["targets"]
            for z in r["zones"] if "mezzanine" in z]
    assert mezz, "TRN-MEZ must reference the mezzanine zone"


def test_asm_and_ramp_recipe_predicates() -> None:
    template, occupancy = _factory_or_skip()
    from EAI.scenario_spec.evaluation import derive_success_predicates
    from EAI.scenario_spec.operators import expand_family, skeleton_family

    ramp = expand_family(skeleton_family("TRN-RAMP", family_id="r", seed=17), template, occupancy)
    preds = derive_success_predicates(ramp[1]["base"])
    assert {"fn": "robot_visited_zone", "args": ["go2_1", "ramp_top"]} in preds

    asm3 = expand_family(skeleton_family("ASM-03", family_id="a3", seed=17, arm="z1"),
                         template, occupancy)
    assert derive_success_predicates(asm3[1]["base"]) == [
        {"fn": "all_at_zone", "args": [["module_1", "module_2", "module_3"], "line_cell", 3]}]


def test_trn03_has_explicit_staging_and_sender_clearance() -> None:
    from EAI.scenario_spec.operators import skeleton_spec

    spec = skeleton_spec("TRN-03", arm="ur5")
    targets = {target["ref"]: target for target in spec["targets"]}
    assert targets["handoff"]["zones"] == ["yard_south"]
    assert targets["receiver_staging"]["zones"] == ["assembly_east"]
    assert targets["sender_clear"]["zones"] == ["storage_west"]

    nodes = {node["id"]: node for node in spec["phase_nodes"]}
    assert nodes["leg_a_clear_handoff"]["prerequisites"] == ["leg_a_carry"]
    assert nodes["leg_b_carry"]["prerequisites"] == [
        "leg_a_clear_handoff", "leg_b_to_staging"]
