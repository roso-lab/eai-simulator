"""Author-only scene-binding inventory for the three existing goal task families.

API: ``build_goal_scene_binding_inventory(scene_key, verify_sources=True)``,
``validate_goal_scene_binding_inventory(inventory)`` and
``write_goal_scene_binding_inventory(inventory, path)``. Validation rebuilds
from current local sources; a digest or caller-supplied 'measured' value is
never sufficient evidence. The writer refuses to overwrite an existing file.

CLI (env_isaaclab; no Isaac/ROS, downloads, or asset writes)::

    python -m demo.benchmark.goal_scene_binding audit --output-dir tmp/new_inventory
    python -m demo.benchmark.goal_scene_binding verify tmp/new_inventory/factory.json

``verify_sources=False`` still reads/hashes authoring declarations, but skips
USD/raster inspection. True adds standalone OpenUSD *single-layer* inspection
and occupancy-raster samples at declared legacy target coordinates. Local mesh
point bounds are in their authored coordinate frames, not composed world
bounds. No reference/payload composition, collision, mass calibration, robot
configuration certification, route clearance or execution is established.

Candidate associations below are explicit author-review suggestions. They do
not rename existing scene regions or assert geometric/semantic equivalence.
Missing suggested records remain gaps. Existing robot type matches are only
configuration candidates, and cannot instantiate two goal members from one
scenario robot. Source locations/hashes and legacy authoring details make the
whole report author-only. It is never a public task or a release certificate.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .goal_driven_contract import validate_goal_task_spec
from .goal_driven_tasks import build_goal_task_spec
from .provenance import canonical_sha256


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")
_TEMPLATES = "source/EAI/EAI/scenario_spec/templates.py"
_PROPS = "source/EAI_assets/EAI_assets/scenario_props.json"
_SCENES = {
    "factory": ("factory_benchmark", "inspection_dispatch_01.json", "FACTORY_TEMPLATE_ZONES"),
    "warehouse_nvd": ("warehouse_benchmark", "business_logistics_01.json", "WAREHOUSE_NVD_ZONES"),
    "modern_house_nvd": ("modern_house_benchmark", "home_recovery_01.json", "MODERN_HOUSE_ZONES"),
}
_OBJECT_HINTS = {
    "warehouse_nvd": {"order_crate_01": ("oversized_crate_proxy", "wooden_crate_a"),
                      "order_carton_01": ("priority_carton_proxy", "cardbox_c")},
    "modern_house_nvd": {"living_armchair_01": ("armchair_proxy", "mh_armchair"),
                         "resident_bookstack_01": ("keepsake_box_proxy", "mh_bookstack")},
}
# An explicitly listed related record is a lead for review, never an alias.
_REGION_HINTS = {
    "factory": {
        "factory_overview": [("target", "inspection_site")],
        "assembly_cell": [("target", "assembly_site"), ("zone", "assembly_east")],
        "stair_approach": [("stair", "main_stair")],
        "mezzanine_workcell": [("zone", "mezzanine_south"), ("vertical_target", "mezzanine_stair_exit")],
    },
    "warehouse_nvd": {
        "rack_overview": [("target", "rack_scan_zone")],
        "rack_aisle_a": [("target", "aisle_entry_zone"), ("zone", "storage_south")],
        "rack_aisle_b": [("zone", "storage_south")],
        "consolidation_area": [("target", "consolidation_zone")],
        "outbound_dock": [("target", "outbound_dock_zone"), ("zone", "dock_west")],
    },
    "modern_house_nvd": {
        "room_overview": [("target", "entry_scan_zone"), ("target", "furniture_review_zone")],
        "entry_hall": [("target", "entry_scan_zone"), ("zone", "hall_center")],
        "furniture_side": [("target", "furniture_access_zone")],
        "living_area": [("zone", "living_west"), ("target", "room_review_zone")],
        "furniture_parking": [("target", "furniture_parking_zone")],
        "keepsake_staging": [("target", "keepsake_staging_zone")],
        "resident_path": [("zone", "hall_center")],
        "house_standby_a": [("target", "primary_standby_zone")],
        "house_standby_b": [("target", "reviewer_standby_zone")],
    },
}
_REQUIREMENTS = {
    "objects": ["unique scene instance and business identity", "world pose and initial-region membership",
                "configured mass/inertia versus experimental mass", "native geometry and collision envelope",
                "authored grasp frames, reach and tool compatibility", "object-specific business evidence producer"],
    "regions": ["reviewed world-frame polygon/volume and floor", "goal meaning and acceptance boundary",
                "robot access and support surfaces", "observation visibility where required",
                "operation capacity and physical occupancy"],
    "routes": ["both endpoints bound to reviewed goal regions", "directed continuous world-frame path",
               "USD-aligned body/loaded-envelope clearance", "support/step-height and aerial volume checks",
               "shared resource identity and concurrency", "robot-specific execution evidence"],
    "robots": ["distinct deployed instance for each selected goal member", "controller/sensor/tool assembly",
               "measured body envelope and configured payload limits", "fixed-grasp interfaces and reachable frames",
               "skill backend conformance, including safe failure", "current scene spawn and team lifecycle binding"],
    "vertical_links": ["measured entry/exit support surfaces", "continuous stair-to-platform connectivity",
                       "tread/step/width measurements", "unloaded and loaded robot-specific traversal evidence"],
}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate structured-data key: {key}")
        result[key] = value
    return result


def _json(raw: bytes) -> Any:
    def reject(value):
        raise ValueError(f"non-finite JSON value: {value}")
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=reject)


def _yaml(raw: bytes) -> dict[str, Any]:
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        loader.flatten_mapping(node)
        return _unique((loader.construct_object(k, deep=True), loader.construct_object(v, deep=True))
                       for k, v in node.value)

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    value = yaml.load(raw, Loader=UniqueLoader)
    if not isinstance(value, dict):
        raise ValueError("map YAML must be a mapping")
    canonical_sha256(value)
    return value


def _relative(text: str) -> Path:
    if not isinstance(text, str) or not text:
        raise ValueError("expected a relative source path")
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or ":" in text or "\\" in text:
        raise ValueError("source declaration must use a local relative path")
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class _Sources:
    def __init__(self):
        self.rows: dict[str, dict[str, Any]] = {}

    def add(self, identifier: str, path: Path) -> bytes | None:
        path = path.absolute()
        raw = path.read_bytes() if path.is_file() else None
        row = {"id": identifier, "path": str(path), "resolved_path": str(path.resolve()),
               "status": "present" if raw is not None else "missing",
               "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
               "size_bytes": len(raw) if raw is not None else None}
        if identifier in self.rows and self.rows[identifier] != row:
            raise ValueError(f"source changed while inventory was read: {identifier}")
        self.rows[identifier] = row
        return raw

    def ref(self, identifier: str, selector: str) -> dict[str, Any]:
        return {"source_id": identifier, "source_sha256": self.rows[identifier]["sha256"], "selector": selector}

    def confirm(self) -> None:
        for identifier, row in self.rows.items():
            path = Path(row["path"])
            present = path.is_file()
            if (present != (row["status"] == "present") or str(path.resolve()) != row["resolved_path"]
                    or present and _digest(path) != row["sha256"]):
                raise ValueError(f"source changed while inventory was read: {identifier}")


def _assignments(raw: bytes | None) -> dict[str, ast.AST]:
    if raw is None:
        return {}
    result = {}
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result[target.id] = node.value
    return result


def _constants(raw: bytes | None) -> dict[str, Any]:
    values = {}
    for name, node in _assignments(raw).items():
        try:
            values[name] = ast.literal_eval(node)
        except (ValueError, TypeError):
            pass  # No imports, calls, or arbitrary Python evaluation.
    return values


def _template_rows(raw: bytes | None, name: str, constructor: str) -> list[dict[str, Any]]:
    value = _assignments(raw).get(name)
    if value is None:
        return []
    if not isinstance(value, (ast.Tuple, ast.List)):
        raise ValueError(f"{name} must be a literal sequence of {constructor} declarations")
    fields = {"Zone": ["id", "aabb_xy", "kind", "level"],
              "StairLink": ["id", "aabb_xy", "low_z", "high_z", "kind"]}[constructor]
    result = []
    for node in value.elts:
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != constructor:
            raise ValueError(f"unsupported template expression in {name}")
        if len(node.args) > len(fields):
            raise ValueError("unexpected template constructor arguments")
        row = dict(zip(fields, [ast.literal_eval(arg) for arg in node.args]))
        for keyword in node.keywords:
            key = "aabb_xy" if keyword.arg == "aabb" else keyword.arg
            if key not in fields or key in row:
                raise ValueError("unexpected or duplicate template keyword")
            row[key] = ast.literal_eval(keyword.value)
        if constructor == "Zone":
            row.setdefault("level", 0)
        if set(row) != set(fields):
            raise ValueError("incomplete template declaration")
        result.append(json.loads(json.dumps(row, allow_nan=False)))
    return result


def _index(rows: Any, label: str, key: str = "id") -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]
                or row[key] in result):
            raise ValueError(f"{label} requires unique nonempty {key}s")
        result[row[key]] = row
    return result


def _local_usd_root() -> Path:
    # Reuse the resolver's read-only path policy; never call ensure/download.
    from EAI_assets.asset_resolver import usd_root
    return usd_root()


def _usd_layer_evidence(path: Path, source_ref: dict[str, Any], *, prim_paths=()) -> dict[str, Any]:
    """Read one authored layer only. Do not resolve references or stage physics."""
    base = {"kind": "authored_usd_layer", "source": source_ref, "path": str(path.absolute()),
            "validated": False, "composition_resolved": False, "physical_bound": False}
    if not path.is_file():
        return {**base, "status": "missing"}
    try:
        from pxr import Sdf
    except ImportError:
        return {**base, "status": "openusd_unavailable"}
    try:
        layer = Sdf.Layer.OpenAsAnonymous(str(path))
        if layer is None:
            return {**base, "status": "unreadable_layer"}
        prims = []
        meshes = []

        def visit(prim_path):
            prim = layer.GetObjectAtPath(prim_path)
            if not isinstance(prim, Sdf.PrimSpec):
                return
            prims.append(str(prim_path))
            points = prim.attributes.get("points")
            if prim.typeName != "Mesh" or points is None or points.default is None:
                return
            values = points.default
            if not len(values):
                return
            low = [min(float(v[i]) for v in values) for i in range(3)]
            high = [max(float(v[i]) for v in values) for i in range(3)]
            if not all(math.isfinite(v) for v in low + high):
                raise ValueError("non-finite authored mesh points")
            meshes.append({"prim_path": str(prim_path), "vertex_count": len(values),
                           "local_point_bounds": {"min": low, "max": high},
                           "basis": "computed_from_authored_default_points_in_mesh_local_units"})

        layer.Traverse("/", visit)
        metadata = {key: layer.pseudoRoot.GetInfo(key) if layer.pseudoRoot.HasInfo(key) else None
                    for key in ("metersPerUnit", "upAxis")}
        return {**base, "status": "inspected", "validated": True,
                "default_prim": layer.defaultPrim, "authored_metadata": metadata,
                "authored_prim_count": len(prims), "local_mesh_geometry": sorted(meshes, key=lambda r: r["prim_path"]),
                "requested_prims": [{"prim_path": p, "authored_in_this_layer": p in prims}
                                    for p in sorted(set(prim_paths))],
                "limits": ["no_composed_world_transforms_or_reference_closure",
                           "no_spawned_instance_identity_or_physical_mass_measurement",
                           "no_collision_support_visibility_or_route_certification"]}
    except Exception as exc:
        # A missing/corrupt optional asset is an inventory finding, not success.
        return {**base, "status": "inspection_failed", "error_type": type(exc).__name__}


def _map_evidence(sources: _Sources, path: Path, targets: Mapping[str, Any], verify: bool) -> dict[str, Any]:
    raw = sources.add("scene_map_yaml", path)
    evidence = {"kind": "occupancy_product", "source": sources.ref("scene_map_yaml", "$"),
                "validated": False, "scene_revision_alignment_validated": False, "route_clearance_validated": False}
    if raw is None:
        return {**evidence, "status": "missing"}
    declaration = _yaml(raw)
    evidence["declaration"] = declaration
    image_name = declaration.get("image")
    image_path = path.parent / _relative(image_name)
    image_raw = sources.add("scene_map_image", image_path)
    evidence["image_source"] = sources.ref("scene_map_image", "$binary")
    if not verify or image_raw is None:
        return {**evidence, "status": "not_inspected" if not verify else "missing_image"}
    from io import BytesIO
    from PIL import Image

    resolution, origin = declaration.get("resolution"), declaration.get("origin")
    if (isinstance(resolution, bool) or not isinstance(resolution, (int, float)) or not math.isfinite(resolution)
            or resolution <= 0 or not isinstance(origin, list) or len(origin) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in origin)):
        raise ValueError("invalid map resolution/origin")
    free, occupied = declaration.get("free_thresh"), declaration.get("occupied_thresh")
    if (any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (free, occupied))
            or not 0 <= free < occupied <= 1 or declaration.get("negate") not in (0, 1)):
        raise ValueError("invalid occupancy map thresholds")
    with Image.open(BytesIO(image_raw)) as original:
        raster = original.convert("L")
    samples = []
    cosine, sine = math.cos(origin[2]), math.sin(origin[2])
    for tid, target in sorted(targets.items()):
        x, y = target["position"][:2]
        dx, dy = x - origin[0], y - origin[1]
        col = math.floor((cosine * dx + sine * dy) / resolution)
        row = raster.height - 1 - math.floor((-sine * dx + cosine * dy) / resolution)
        state, pixel = "out_of_bounds", None
        if 0 <= col < raster.width and 0 <= row < raster.height:
            pixel = raster.getpixel((col, row))
            probability = pixel / 255 if declaration["negate"] else (255 - pixel) / 255
            state = "occupied" if probability > occupied else "free" if probability < free else "unknown"
        samples.append({"target_id": tid, "declared_world_xy_m": [x, y], "image_cell_col_row": [col, row],
                        "pixel": pixel, "point_occupancy": state})
    return {**evidence, "status": "inspected", "validated": True,
            "image_size_px": [raster.width, raster.height], "declared_target_samples": samples,
            "limits": ["sampled_raster_at_legacy_declared_point_not_goal_region",
                       "single_point_not_robot_footprint_loaded_clearance_or_continuous_path",
                       "map_to_current_usd_revision_not_established"]}


def _candidate(sources: _Sources, kind: str, identifier: str, source_id: str, selector: str,
               declaration: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {"candidate_id": f"{kind}:{identifier}", "kind": kind, "id": identifier,
            "source": sources.ref(source_id, selector), "declaration": deepcopy(dict(declaration)),
            "basis": "existing_authoring_declaration", "relation": reason,
            "identity_equivalent": False, "physical_bound": False}


def _entry(kind: str, public: Mapping[str, Any]) -> dict[str, Any]:
    return {"public_id": public["id"], "public_declaration": deepcopy(dict(public)),
            "parameter_basis": "benchmark_experimental_definition_not_scene_measurement",
            "binding_status": "unbound", "physical_bound": False,
            "requirements": list(_REQUIREMENTS[kind]), "candidates": [],
            "gaps": ["reviewed_identity_and_geometry_binding_missing"], "differences": []}


def _asset_files(prop: Mapping[str, Any], usd_root: Path) -> list[tuple[Path, str]]:
    relative = _relative(prop["usd_rel"])
    paths: dict[str, tuple[Path, str]] = {}
    configured = os.environ.get("EAI_NVD_ASSET_ROOT")
    if configured:
        exact = Path(configured).expanduser().absolute() / relative
        paths[str(exact)] = (exact, "configured_registry_path")
    # Inspection-only discovery: exact suffix below local package directories.
    # Different package directory spelling never becomes a runtime resolver alias.
    base = usd_root / "assets/nvd"
    if len(relative.parts) > 1 and base.is_dir():
        suffix = Path(*relative.parts[1:])
        for package in sorted(base.iterdir()):
            candidate = package / suffix
            if package.is_dir() and candidate.is_file():
                paths.setdefault(str(candidate.absolute()), (candidate, "local_package_suffix_candidate_not_runtime_resolution"))
    return list(paths.values())


def build_goal_scene_binding_inventory(scene_key: str, *, verify_sources: bool = True) -> dict[str, Any]:
    """Build a current, author-only inventory; no mapping or measurement input is trusted."""
    if scene_key not in _SCENES:
        raise ValueError(f"unsupported goal binding scene: {scene_key}")
    if type(verify_sources) is not bool:
        raise ValueError("verify_sources must be boolean")
    spec = build_goal_task_spec(scene_key)
    validate_goal_task_spec(spec)
    sources = _Sources()
    for name, relative in {
        "inventory_implementation": "demo/benchmark/goal_scene_binding.py",
        "goal_tasks": "demo/benchmark/goal_driven_tasks.py",
        "goal_fixtures": "demo/benchmark/goal_driven_fixtures.py",
        "goal_contract": "demo/benchmark/goal_driven_contract.py",
        "goal_constraints": "demo/benchmark/goal_constraints.py",
        "skill_ontology": "demo/benchmark/skill_ontology.py",
        "asset_registry_implementation": "source/EAI_assets/EAI_assets/scenario_assets.py",
        "asset_path_policy": "source/EAI_assets/EAI_assets/asset_resolver.py",
        "scene_asset_config": f"source/EAI_assets/EAI_assets/scene/{scene_key}.py",
    }.items():
        sources.add(name, REPO_ROOT / relative)
    module, scenario_name, zone_constant = _SCENES[scene_key]
    config_raw = sources.add("demo_config", REPO_ROOT / f"demo/{module}/config.py")
    config = _constants(config_raw)
    scenario_raw = sources.add("scenario", REPO_ROOT / f"demo/{module}/scenarios/{scenario_name}")
    scenario = _json(scenario_raw) if scenario_raw is not None else {}
    if scenario and scenario.get("scene_key") != scene_key:
        raise ValueError("scenario scene_key does not match the goal scene")
    if config and config.get("SCENE_KEY") != scene_key:
        raise ValueError("config scene_key does not match the goal scene")
    template_raw = sources.add("templates", REPO_ROOT / _TEMPLATES)
    zones = _index(_template_rows(template_raw, zone_constant, "Zone"), "zones")
    composition = scenario.get("composition", {})
    targets = _index(composition.get("targets", []), "scenario targets")
    entities = _index(composition.get("entities", []), "scenario entities")
    robots = _index(scenario.get("robots", []), "scenario robots")
    props_raw = sources.add("props", REPO_ROOT / _PROPS)
    props = _index(_json(props_raw).get("props", []) if props_raw is not None else [], "props", "key")
    catalog: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, records, sid, prefix in (
        ("target", targets, "scenario", "$.composition.targets"),
        ("entity", entities, "scenario", "$.composition.entities"),
        ("robot", robots, "scenario", "$.robots"),
        ("zone", zones, "templates", zone_constant),
    ):
        for identifier, row in records.items():
            catalog[(kind, identifier)] = _candidate(sources, kind, identifier, sid,
                f"{prefix}[id={identifier}]", row, "related existing record; requires explicit goal-specific review")
    prim_bindings = {}
    vertical_targets = {}
    if scene_key == "factory":
        bindings_raw = sources.add("factory_prim_bindings", REPO_ROOT / "demo/factory_benchmark/prim_bindings.py")
        prim_bindings = _constants(bindings_raw).get("FACTORY_PRIM_BINDINGS", {})
        vertical_raw = sources.add("vertical_scenario", REPO_ROOT / "demo/factory_benchmark/scenarios/vertical_stair_01.json")
        vertical = _json(vertical_raw) if vertical_raw is not None else {}
        vertical_targets = _index(vertical.get("composition", {}).get("targets", []), "vertical targets")
        for identifier, row in vertical_targets.items():
            catalog[("vertical_target", identifier)] = _candidate(sources, "vertical_target", identifier,
                "vertical_scenario", f"$.composition.targets[id={identifier}]", row,
                "legacy stair diagnostic target; support height/connectivity not established")
        for row in _template_rows(template_raw, "FACTORY_STAIRS", "StairLink"):
            catalog[("stair", row["id"])] = _candidate(sources, "stair", row["id"], "templates",
                f"FACTORY_STAIRS[id={row['id']}]", row,
                "historical template AABB declaration; high_z is not verified walkable surface height")
        for identifier, row in prim_bindings.items():
            catalog[("prim", identifier)] = _candidate(sources, "prim", identifier, "factory_prim_bindings",
                f"FACTORY_PRIM_BINDINGS[{identifier}]", row, "named native geometry, not a business object identity")

    inventory = {kind: [] for kind in _REQUIREMENTS}
    local_evidence = []
    usd_root = _local_usd_root().absolute()
    if "USD_RELATIVE_PATH" in config:
        path = usd_root / _relative(config["USD_RELATIVE_PATH"])
        sources.add("scene_usd", path)
        if verify_sources:
            local_evidence.append(_usd_layer_evidence(path, sources.ref("scene_usd", "$layer"),
                prim_paths=[r["prim_path"] for r in prim_bindings.values()]))
    if "MAP_RELATIVE_PATH" in config:
        local_evidence.append(_map_evidence(sources, usd_root / _relative(config["MAP_RELATIVE_PATH"]),
                                           {**targets, **vertical_targets}, verify_sources))

    for obj in spec["public_world"]["objects"]:
        entry = _entry("objects", obj)
        hint = _OBJECT_HINTS.get(scene_key, {}).get(obj["id"])
        if hint:
            entity_id, asset_key = hint
            candidate = catalog.get(("entity", entity_id))
            if candidate and candidate["declaration"].get("asset_key") == asset_key:
                entry["candidates"].append(deepcopy(candidate))
                declaration = candidate["declaration"]
                for label, experiment, scene_value in (
                    ("mass_kg", obj["physical_properties"]["mass_kg"], declaration.get("physics", {}).get("mass_kg")),
                    ("size_m", obj["physical_properties"]["size_m"], declaration.get("size_m")),
                ):
                    if experiment != scene_value:
                        entry["differences"].append({"field": label, "experimental_value": experiment,
                            "scenario_declared_value": scene_value, "measured_value": None,
                            "source": candidate["source"]})
            else:
                entry["gaps"].append(f"expected_legacy_entity_or_asset_key_missing:{entity_id}:{asset_key}")
            if asset_key in props:
                prop = props[asset_key]
                entry["candidates"].append(_candidate(sources, "prop_asset", asset_key, "props",
                    f"$.props[key={asset_key}]", prop, "native asset declaration, not an instantiated goal object"))
                paths = _asset_files(prop, usd_root)
                if not paths:
                    entry["gaps"].append("native_prop_file_not_located")
                for index, (path, discovery) in enumerate(paths):
                    sid = f"native_prop:{asset_key}:{index}"
                    sources.add(sid, path)
                    if verify_sources:
                        proof = _usd_layer_evidence(path, sources.ref(sid, "$layer"))
                        proof.update({"asset_key": asset_key, "discovery": discovery,
                                      "runtime_registry_resolution_verified": False})
                        local_evidence.append(proof)
                if not os.environ.get("EAI_NVD_ASSET_ROOT"):
                    entry["gaps"].append("runtime_prop_registry_root_unset_despite_possible_local_files")
            else:
                entry["gaps"].append(f"prop_declaration_missing:{asset_key}")
        elif scene_key == "factory" and obj["id"] == "production_anomaly_marker":
            candidate = catalog.get(("prim", "inspection_target_box"))
            if candidate:
                entry["candidates"].append(deepcopy(candidate))
            entry["gaps"].append("legacy_inventory_box_is_not_the_production_anomaly_record")
        else:
            entry["gaps"].append("no_reviewed_candidate_for_this_public_object")
        entry["gaps"].extend(["scene_instance_not_authored_for_goal_identity",
                              "experimental_mass_size_and_grasp_frames_not_bound_to_spawn_configuration"])
        inventory["objects"].append(entry)

    for region in spec["public_world"]["regions"]:
        entry = _entry("regions", region)
        for key in _REGION_HINTS.get(scene_key, {}).get(region["id"], []):
            if key in catalog:
                entry["candidates"].append(deepcopy(catalog[key]))
            else:
                entry["gaps"].append(f"suggested_source_record_missing:{key[0]}:{key[1]}")
        if not entry["candidates"]:
            entry["gaps"].append("no_explicit_existing_region_candidate")
        entry["gaps"].append("point_radius_or_template_aabb_does_not_define_goal_region_volume")
        if "aerial_navigation" in region["access_modes"]:
            entry["gaps"].append("goal_observation_volume_visibility_not_bound")
        zone_candidates = [r for r in entry["candidates"] if r["kind"] == "zone"]
        for target in (r for r in entry["candidates"] if r["kind"] in {"target", "vertical_target"}):
            for zone in zone_candidates:
                x, y = target["declaration"]["position"][:2]
                a, b, c, d = zone["declaration"]["aabb_xy"]
                if not a <= x <= c or not b <= y <= d:
                    entry["differences"].append({"kind": "legacy_target_outside_suggested_template_zone",
                        "target": target["candidate_id"], "zone": zone["candidate_id"],
                        "basis": "comparison_of_declarations_not_measurement"})
        inventory["regions"].append(entry)

    region_candidates = {r["public_id"]: [c["candidate_id"] for c in r["candidates"]]
                         for r in inventory["regions"]}
    for route in spec["public_world"]["routes"]:
        entry = _entry("routes", route)
        entry["endpoint_candidate_refs"] = {
            "from": region_candidates[route["from_region_id"]], "to": region_candidates[route["to_region_id"]]}
        entry["gaps"].extend(["no_goal_specific_directed_path_polyline",
                              "declared_clearance_length_step_and_capacity_are_not_map_or_usd_measurements",
                              "endpoint_candidates_do_not_establish_a_route"])
        inventory["routes"].append(entry)
    for robot in spec["candidate_robot_pool"]:
        entry = _entry("robots", robot)
        for rid, candidate in robots.items():
            if candidate.get("type") == robot["type"]:
                row = deepcopy(catalog[("robot", rid)])
                row["relation"] = "same robot type only; identity, attachments, skills and numerical profile unbound"
                entry["candidates"].append(row)
        entry["gaps"].extend(["experimental_profile_and_skill_configuration_not_certified",
                              "scenario_attachments_do_not_prove_fixed_grasp_or_support_socket_interfaces"])
        count = sum(r["type"] == robot["type"] for r in spec["candidate_robot_pool"])
        if len(entry["candidates"]) < count:
            entry["gaps"].append("scenario_has_fewer_distinct_instances_than_public_candidate_pool_of_this_type")
        inventory["robots"].append(entry)
    for link in spec["public_world"]["vertical_links"]:
        entry = _entry("vertical_links", link)
        for key in (("stair", link["id"]), ("prim", link["id"])):
            if key in catalog:
                entry["candidates"].append(deepcopy(catalog[key]))
        entry["gaps"].extend(["stair_top_aabb_is_not_exit_support_height",
                              "open_public_stair_requires_measured_continuous_scene_connection"])
        inventory["vertical_links"].append(entry)

    sources.confirm()
    result = {
        "schema": "eai.benchmark.goal_scene_binding_inventory", "version": 1, "visibility": "author_only",
        "scene_key": scene_key, "task_id": spec["task_id"], "public_task_sha256": canonical_sha256(spec),
        "source_verification_requested": verify_sources,
        "source_context": {"usd_root": str(usd_root), "nvd_registry_root": os.environ.get("EAI_NVD_ASSET_ROOT")},
        "sources": sorted(sources.rows.values(), key=lambda r: r["id"]),
        "existing_scene_declarations": {"config_literals": config, "template_zones": list(zones.values()),
                                        "scenario_name": scenario.get("task_name"),
                                        "basis": "authoring_declarations_not_current_physical_measurements"},
        "inventory": inventory, "local_evidence": local_evidence,
        "review": {"status": "blocked", "physical_bound": False, "scene_binding_validated": False,
                   "execution_validated": False, "scoreable": False,
                   "public_item_counts": {kind: len(rows) for kind, rows in inventory.items()},
                   "local_evidence_checks_passed": sum(e["validated"] is True for e in local_evidence),
                   "missing_sources": sorted(r["id"] for r in sources.rows.values() if r["status"] == "missing"),
                   "blockers": ["goal_to_scene_instance_and_region_identity_review",
                                "experimental_parameters_to_deployment_configuration_binding",
                                "composed_usd_map_revision_alignment_and_measured_route_envelopes",
                                "skill_backend_business_evidence_and_reference_execution"]},
    }
    return {**result, "sha256": canonical_sha256(result)}


def validate_goal_scene_binding_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read sources and regenerate every claim; reject stale or edited reports."""
    if not isinstance(inventory, Mapping):
        raise ValueError("binding inventory must be an object")
    if inventory.get("scene_key") not in SCENES or type(inventory.get("source_verification_requested")) is not bool:
        raise ValueError("binding inventory needs a supported scene and explicit verification mode")
    expected = build_goal_scene_binding_inventory(inventory["scene_key"],
                                                  verify_sources=inventory["source_verification_requested"])
    if inventory.get("sources") != expected["sources"]:
        raise ValueError("binding inventory sources are stale, missing, or changed")
    if canonical_sha256(dict(inventory)) != canonical_sha256(expected):
        raise ValueError("binding inventory claims/coverage differ from current source-derived evidence")
    return {"validated": True, "scope": "author_inventory_consistency_only", "sha256": expected["sha256"],
            "scene_binding_validated": False, "execution_validated": False, "scoreable": False}


def write_goal_scene_binding_inventory(inventory: Mapping[str, Any], path: str | Path) -> Path:
    """Validate and atomically create one report; never replace existing output."""
    validate_goal_scene_binding_inventory(inventory)
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(dict(inventory), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    staging = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            staging = Path(stream.name)
            stream.write(content)
        os.link(staging, path)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("--scene", choices=SCENES, action="append")
    audit.add_argument("--output-dir", required=True)
    audit.add_argument("--no-verify-sources", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("report")
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(json.dumps(validate_goal_scene_binding_inventory(_json(Path(args.report).read_bytes())), sort_keys=True))
        return 0
    scenes = list(dict.fromkeys(args.scene or SCENES))
    destination = Path(args.output_dir).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"inventory output directory already exists: {destination}")
    reports = [build_goal_scene_binding_inventory(scene, verify_sources=not args.no_verify_sources) for scene in scenes]
    destination.mkdir(parents=True, exist_ok=False)
    for report in reports:
        write_goal_scene_binding_inventory(report, destination / f"{report['scene_key']}.json")
    print(json.dumps({"output_dir": str(destination), "scenes": scenes, "inventory_validated": True,
                      "scene_binding_validated": False, "execution_validated": False, "scoreable": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_goal_scene_binding_inventory", "validate_goal_scene_binding_inventory",
           "write_goal_scene_binding_inventory"]
