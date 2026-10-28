"""Physically audit a semantic navigation map with one catalog robot.

The dependency-light ``--check`` path validates the selected map and writes a
deterministic test plan.  A normal run starts one headless Isaac session,
executes every applicable case, records collision evidence and writes Markdown,
JSON, trajectories and MP4 evidence below ``~/tmp``.  Invoke from the project
root with ``python tools/semantic_map_traversability.py --robot ... --scene ...``.

All route selection and route progression use the project's sole navigation
Skill, ``algorithm.skills.navigation`` / ``eai.navigation.node_route``.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import sys
from typing import Any, Mapping, Sequence
import unicodedata
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from algorithm.skills.navigation import (  # noqa: E402
    NODE_ROUTE_SKILL_ID,
    FrozenNavigationTopology,
    NavigationObservation,
    NavigationPose,
    NavigationProvider,
    aerial_navigation_profile,
    ground_navigation_profile,
    route_graph_digest,
    select_node_route,
    validate_topology,
)


DEFAULT_ASSET_ROOT = REPO_ROOT / "usd"
BEIJING = ZoneInfo("Asia/Shanghai")
NAVIGATION_SKILL_MODULE = "algorithm.skills.navigation"
AERIAL_ROBOTS = frozenset({"cf2x", "iris", "pegasus"})
ACKERMANN_ROBOTS = frozenset({"mushr_v2", "coco"})
LEGGED_ROBOTS = frozenset({"go2", "b2", "m20", "lite3", "g1", "microduck"})
KNOWN_ROBOTS = frozenset({
    "carter", "simple_car", "pepper", "go2", "b2", "m20", "scout",
    "mushr_v2", "g1", "cf2x", "iris", "pegasus", "lite3", "coco",
    "microduck",
})
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 15
ARRIVAL_RADIUS_M = 0.30
STOP_LINEAR_M_S = 0.04
STOP_ANGULAR_RAD_S = 0.08
ROTATION_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 10.0
ROTATION_TOLERANCE_RAD = math.radians(5.0)
ROTATION_DRIFT_M = 0.30
GROUND_VERTICAL_DEVIATION_M = 0.75
GROUND_ENDPOINT_VERTICAL_ERROR_M = 0.45
RAYCAST_PROXY_HALF_WIDTH_M = 1.0


class InputError(ValueError):
    """The requested robot, scene or map cannot define a valid audit."""


class InfrastructureError(RuntimeError):
    """Isaac, measurement or recording infrastructure failed."""


@dataclass(frozen=True)
class HostNavigationTarget:
    """Controller-facing view of one node target emitted by the navigation Skill."""

    position_m: tuple[float, float, float]
    heading_rad: float | None = None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    for name in ("detach", "cpu"):
        method = getattr(value, name, None)
        if callable(method):
            try:
                value = method()
            except Exception:
                pass
    method = getattr(value, "tolist", None)
    if callable(method):
        return _jsonable(method())
    if hasattr(value, "to_mapping"):
        return _jsonable(value.to_mapping())
    return str(value)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    """Return a readable, path-safe component without losing CJK names."""
    normalized = unicodedata.normalize("NFKC", str(value)).strip()
    output: list[str] = []
    previous_separator = False
    for character in normalized:
        if character.isalnum() or character in "-_.":
            output.append(character)
            previous_separator = False
        elif not previous_separator:
            output.append("_")
            previous_separator = True
    result = "".join(output).strip("._-")
    return result[:80] or "unnamed"


def create_output_dir(
    robot: str,
    scene: str,
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    root = (root or (Path.home() / "tmp")).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    base = f"{now:%Y%m%d_%H%M%S}_{safe_name(robot)}_{safe_name(scene)}"
    for suffix in range(10000):
        candidate = root / (base if suffix == 0 else f"{base}_{suffix:02d}")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        (candidate / "videos").mkdir()
        (candidate / "trajectories").mkdir()
        return candidate
    raise InfrastructureError("cannot allocate a unique output directory")


def canonical_robot(robot: str) -> str:
    key = str(robot).strip().lower()
    if key == "m20":
        return "m20"
    if key not in KNOWN_ROBOTS:
        raise InputError(
            f"unknown robot {robot!r}; expected one of: {', '.join(sorted(KNOWN_ROBOTS))}"
        )
    return key


def navigation_domain(robot: str) -> str:
    return "aerial" if canonical_robot(robot) in AERIAL_ROBOTS else "ground"


def controller_capabilities(robot: str, domain: str) -> tuple[str, ...]:
    key = canonical_robot(robot)
    if domain == "aerial" or key in LEGGED_ROBOTS:
        return ("ramp", "stairs", "walk")
    return ("ramp", "walk")


def rotation_policy(robot: str, domain: str) -> str:
    if domain == "aerial":
        return "not_applicable"
    if canonical_robot(robot) in ACKERMANN_ROBOTS:
        return "unsupported"
    return "applicable"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"JSON root must be an object: {path}")
    return value


def resolve_scene_project(scene: str, asset_root: Path = DEFAULT_ASSET_ROOT) -> Path:
    """Resolve a project by exact scene_key or exact scene directory name."""
    scene_root = asset_root.expanduser().resolve() / "scene"
    if not scene_root.is_dir():
        raise InputError(f"scene asset root does not exist: {scene_root}")
    exact_key: list[Path] = []
    exact_dir: list[Path] = []
    for project_file in sorted(scene_root.glob("*/*/semantic_map/project.json")):
        project = _read_json(project_file)
        if project.get("scene_key") == scene:
            exact_key.append(project_file.parent)
        if project_file.parents[1].name == scene:
            exact_dir.append(project_file.parent)
    matches = exact_key or exact_dir
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise InputError(
            f"expected one semantic-map project for {scene!r}, found {len(matches)}"
        )
    return matches[0]


def _scene_usd(project: Mapping[str, Any], project_dir: Path, asset_root: Path) -> Path:
    provenance = project.get("provenance", {})
    candidates = [provenance]
    if isinstance(provenance, Mapping):
        candidates.extend(
            row for row in (
                provenance.get("semantic-footprints.json"),
                provenance.get("semantic-manifest.json"),
            ) if isinstance(row, Mapping)
        )
    source = next((row.get("source_usd") for row in candidates if row.get("source_usd")), None)
    if isinstance(source, str):
        path = (asset_root.resolve() / source).resolve()
        if path.is_relative_to(asset_root.resolve()) and path.is_file():
            return path
    roots = sorted(
        path for path in project_dir.parent.iterdir()
        if path.is_file() and path.suffix.lower() in {".usd", ".usda", ".usdc"}
    )
    if len(roots) == 1:
        return roots[0].resolve()
    raise InputError("semantic project has no unique, in-tree source USD binding")


@dataclass(frozen=True)
class MapInputs:
    project_dir: Path
    current_dir: Path
    project: dict[str, Any]
    topology: dict[str, Any]
    topology_path: Path
    semantic_points: list[dict[str, Any]]
    scene_key: str
    scene_usd: Path
    domain: str
    input_hashes: dict[str, str]


def load_map_inputs(
    robot: str,
    scene: str,
    asset_root: Path = DEFAULT_ASSET_ROOT,
) -> MapInputs:
    domain = navigation_domain(robot)
    project_dir = resolve_scene_project(scene, asset_root)
    project_path = project_dir / "project.json"
    project = _read_json(project_path)
    revision = project.get("current_revision", "current")
    if revision != "current":
        raise InputError("only atomic current semantic-map revisions are supported")
    current = project_dir / "current"
    products = project.get("navigation_products", {})
    filename = products.get(domain) if isinstance(products, Mapping) else None
    filename = filename or ("topology-aerial.json" if domain == "aerial" else "topology.json")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise InputError(f"invalid {domain} navigation product path")
    topology_path = current / filename
    if not topology_path.is_file():
        raise InputError(f"selected {domain} navigation product does not exist: {topology_path}")
    topology = _read_json(topology_path)
    if topology.get("domain", domain) != domain:
        raise InputError(f"selected product is not a {domain} navigation network")
    errors = validate_topology(topology, metric="xyz" if domain == "aerial" else "xy")
    if errors:
        raise InputError("invalid navigation product: " + "; ".join(errors))
    if not topology.get("nodes"):
        raise InputError(f"selected {domain} navigation network has no nodes")
    semantic_file = current / str(project.get("semantic_point_product", "semantic-points.json"))
    semantic_points: list[dict[str, Any]] = []
    if semantic_file.is_file():
        semantic_doc = _read_json(semantic_file)
        rows = semantic_doc.get("points", [])
        if not isinstance(rows, list):
            raise InputError("semantic-points.json must contain a points array")
        semantic_points = deepcopy(rows)
    scene_usd = _scene_usd(project, project_dir, asset_root.expanduser().resolve())
    hashed = sorted(
        {scene_usd.resolve()} | {
            path.resolve() for path in project_dir.rglob("*") if path.is_file()
        },
        key=str,
    )
    return MapInputs(
        project_dir.resolve(), current.resolve(), project, topology, topology_path.resolve(),
        semantic_points, str(project.get("scene_key") or topology.get("scene_key") or scene),
        scene_usd, domain, {str(path.resolve()): _sha256(path) for path in hashed},
    )


def edge_allowed(edge: Mapping[str, Any], capabilities: Sequence[str]) -> bool:
    required = {str(edge.get("traversal_mode", "walk"))}
    required.update(str(value) for value in edge.get("required_controller_capabilities", ()))
    return required.issubset(set(capabilities))


def edge_cases(
    topology: Mapping[str, Any], capabilities: Sequence[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for edge in topology.get("edges", ()):
        base = {
            "edge_id": edge["id"],
            "traversal_mode": edge.get("traversal_mode", "walk"),
            "required_controller_capabilities": list(
                edge.get("required_controller_capabilities", ())
            ),
            "navigation_skill_id": NODE_ROUTE_SKILL_ID,
        }
        allowed = edge_allowed(edge, capabilities)
        if not allowed:
            excluded.append({**base, "reason": "controller_capability_mismatch"})
        status = "planned" if allowed else "unsupported"
        reason = {} if allowed else {"reason": "controller_capability_mismatch"}
        cases.append({**base, "case_id": f"edge:{edge['id']}:forward",
                      "origin": edge["from"], "target": edge["to"],
                      "status": status, **reason})
        if not edge.get("directed", False):
            cases.append({**base, "case_id": f"edge:{edge['id']}:reverse",
                          "origin": edge["to"], "target": edge["from"],
                          "status": status, **reason})
    return cases, excluded


def restricted_edge_topology(
    topology: Mapping[str, Any], case: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a two-node, one-edge graph that cannot silently route around a failure."""
    nodes = {row["id"]: row for row in topology.get("nodes", ())}
    edges = {row["id"]: row for row in topology.get("edges", ())}
    edge = deepcopy(edges[case["edge_id"]])
    edge["from"] = case["origin"]
    edge["to"] = case["target"]
    edge["directed"] = True
    result = deepcopy(dict(topology))
    result["nodes"] = [deepcopy(nodes[case["origin"]]), deepcopy(nodes[case["target"]])]
    result["edges"] = [edge]
    result["semantics"] = None
    return result


def _metric(left: Sequence[float], right: Sequence[float], domain: str) -> float:
    return math.dist(left, right) if domain == "aerial" else math.dist(left[:2], right[:2])


def navigation_skill_profile(
    topology: Mapping[str, Any],
    capabilities: Sequence[str],
    *,
    arrival_radius_m: float = ARRIVAL_RADIUS_M,
):
    """Build the profile consumed by the project's sole navigation Skill."""
    common = {
        "arrival_radius_m": arrival_radius_m,
        "controller_capabilities": tuple(capabilities),
        "stop_linear_speed_m_s": STOP_LINEAR_M_S,
        "stop_angular_speed_rad_s": STOP_ANGULAR_RAD_S,
    }
    if str(topology.get("domain", "ground")) == "aerial":
        return aerial_navigation_profile(**common)
    return ground_navigation_profile(supports_final_heading=False, **common)


def _skill_document(topology: Mapping[str, Any]) -> dict[str, Any]:
    """Fill only legacy fixture defaults before handing a document to the Skill.

    Runtime map loading remains strict.  This small adapter keeps the pure plan
    helpers useful for old, dependency-free fixtures that predate the shared
    topology envelope; the actual route algorithm still runs in the project
    navigation Skill and performs its normal validation.
    """
    document = deepcopy(dict(topology))
    document.setdefault("schema", "eai.navigation.topology")
    document.setdefault("version", 1)
    document.setdefault("coordinate_frame", "scene_world")
    document.setdefault("meters_per_unit", 1.0)
    document.setdefault("up_axis", "Z")
    if "levels" not in document:
        level_ids = sorted({
            row.get("level_id") for row in document.get("nodes", ())
            if isinstance(row, Mapping) and row.get("level_id") is not None
        })
        document["levels"] = [
            {
                "id": level_id,
                "label": str(level_id),
                "support_z_m": min(
                    float(row["position_xyz"][2])
                    for row in document.get("nodes", ())
                    if row.get("level_id") == level_id
                ),
            }
            for level_id in level_ids
        ]
    semantics = document.get("semantics")
    if not isinstance(semantics, Mapping) or semantics.get("schema") != "eai.scene.semantics":
        document["semantics"] = None
    return document


def connectivity_and_routes(
    topology: Mapping[str, Any],
    capabilities: Sequence[str],
    *,
    seed: int,
    route_count: int = 10,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    skill_topology = _skill_document(topology)
    nodes = {row["id"]: row for row in skill_topology.get("nodes", ())}
    profile = navigation_skill_profile(skill_topology, capabilities)
    candidates: list[dict[str, Any]] = []
    for origin in sorted(nodes):
        pose = NavigationPose(tuple(nodes[origin]["position_xyz"]), 0.0)
        for target in sorted(nodes):
            if target == origin:
                continue
            selected = select_node_route(
                skill_topology,
                pose,
                profile=profile,
                target_ref=target,
                origin_ref=origin,
                controller_capabilities=tuple(capabilities),
                target_node_ids=(target,),
                origin_node_ids=(origin,),
                target_is_semantic=False,
                origin_is_semantic=False,
            )
            if not selected.get("admitted"):
                continue
            candidates.append({
                "origin": origin,
                "target": target,
                "route_length_m": float(selected["route_length_m"]),
                "node_ids": list(selected["topology_node_ids"]),
                "edge_ids": list(selected["topology_edge_ids"]),
                "navigation_skill_id": NODE_ROUTE_SKILL_ID,
            })

    # Weak connectivity is reporting metadata.  Derive it from routes admitted
    # by the navigation Skill so capability filtering and direction semantics
    # cannot drift from executable planning.
    parent = {node_id: node_id for node_id in nodes}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(first: str, second: str) -> None:
        first_root, second_root = find(first), find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    for row in candidates:
        union(str(row["origin"]), str(row["target"]))
    grouped: dict[str, list[str]] = {}
    for node_id in sorted(nodes):
        grouped.setdefault(find(node_id), []).append(node_id)
    components = sorted(grouped.values(), key=lambda row: (-len(row), row))

    candidates.sort(key=lambda row: (-row["route_length_m"], row["origin"], row["target"]))
    quartile_count = math.ceil(len(candidates) * 0.25) if candidates else 0
    longest = candidates[:quartile_count]
    rng = random.Random(seed)
    selected = rng.sample(longest, min(route_count, len(longest))) if longest else []
    selected.sort(key=lambda row: (-row["route_length_m"], row["origin"], row["target"]))
    for index, row in enumerate(selected, 1):
        row["case_id"] = f"long-route:{index:02d}"
        row["case_type"] = "long_route"
    total_ordered = len(nodes) * max(0, len(nodes) - 1)
    level_by_node = {key: value.get("level_id") for key, value in nodes.items()}
    connectivity = {
        "weak_component_count": len(components),
        "weak_components": [
            {
                "node_count": len(component),
                "node_ids": component,
                "level_ids": sorted({level_by_node[node] for node in component
                                     if level_by_node[node] is not None}),
            }
            for component in components
        ],
        "reachable_ordered_pair_count": len(candidates),
        "unreachable_ordered_pair_count": total_ordered - len(candidates),
        "longest_quartile_candidate_count": len(longest),
        "requested_long_route_count": route_count,
        "selected_long_route_count": len(selected),
        "candidate_shortage": max(0, route_count - len(selected)),
    }
    return connectivity, selected


def build_test_plan(inputs: MapInputs, robot: str, seed: int) -> dict[str, Any]:
    capabilities = controller_capabilities(robot, inputs.domain)
    policy = rotation_policy(robot, inputs.domain)
    rotations = [
        {
            "case_id": f"rotation:{row['id']}",
            "node_id": row["id"],
            "status": (
                "planned" if policy == "applicable"
                else "unsupported" if policy == "unsupported"
                else "not_applicable"
            ),
            **(
                {"reason": "controller_cannot_rotate_in_place"}
                if policy == "unsupported"
                else {"reason": "aerial_robot_rotation_not_applicable"}
                if policy == "not_applicable"
                else {}
            ),
        }
        for row in inputs.topology["nodes"]
    ]
    edges, excluded_edges = edge_cases(inputs.topology, capabilities)
    connectivity, routes = connectivity_and_routes(
        inputs.topology, capabilities, seed=seed, route_count=10
    )
    for row in routes:
        row["status"] = "planned"
    return {
        "mode": "standard",
        "domain": inputs.domain,
        "metric": "xyz" if inputs.domain == "aerial" else "xy",
        "navigation_skill": {
            "module": NAVIGATION_SKILL_MODULE,
            "skill_id": NODE_ROUTE_SKILL_ID,
        },
        "controller_capabilities": list(capabilities),
        "rotation_policy": policy,
        "rotations": rotations,
        "edges": edges,
        "excluded_edges": excluded_edges,
        "long_routes": routes,
        "connectivity": connectivity,
    }


def parse_stress_entries(
    values: Sequence[str] | None,
    topology: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Parse exact node IDs and comma-separated node pairs in input order."""
    if values is None:
        raise InputError("--StressT was not provided")
    if not values:
        raise InputError("--StressT requires at least one node or node pair")
    node_ids = {
        str(row["id"]) for row in topology.get("nodes", ())
        if isinstance(row, Mapping) and "id" in row
    }
    parsed: list[dict[str, Any]] = []
    for index, raw_value in enumerate(values, 1):
        raw = str(raw_value)
        if not raw.strip():
            raise InputError(f"--StressT entry {index} is empty")
        parts = [part.strip() for part in raw.split(",")]
        if any(not part for part in parts):
            raise InputError(f"--StressT entry {index} contains an empty node ID: {raw!r}")
        if len(parts) not in {1, 2}:
            raise InputError(
                f"--StressT entry {index} must contain one node or one node pair: {raw!r}"
            )
        unknown = [node_id for node_id in parts if node_id not in node_ids]
        if unknown:
            raise InputError(
                f"--StressT entry {index} contains unknown node ID(s): "
                + ", ".join(repr(value) for value in unknown)
            )
        if len(parts) == 2 and parts[0] == parts[1]:
            raise InputError(f"--StressT entry {index} has identical endpoints: {parts[0]!r}")
        parsed.append({
            "input_index": index,
            "raw_entry": raw,
            "entry_type": "node" if len(parts) == 1 else "pair",
            "node_ids": parts,
        })
    return parsed


def _stress_route(
    topology: Mapping[str, Any],
    capabilities: Sequence[str],
    origin: str,
    target: str,
) -> dict[str, Any]:
    """Plan one stress leg exclusively through the public navigation Skill."""
    skill_topology = _skill_document(topology)
    nodes = {row["id"]: row for row in skill_topology.get("nodes", ())}
    profile = navigation_skill_profile(skill_topology, capabilities)
    selected = select_node_route(
        skill_topology,
        NavigationPose(tuple(nodes[origin]["position_xyz"]), 0.0),
        profile=profile,
        target_ref=target,
        origin_ref=origin,
        controller_capabilities=tuple(capabilities),
        target_node_ids=(target,),
        origin_node_ids=(origin,),
        target_is_semantic=False,
        origin_is_semantic=False,
    )
    if not selected.get("admitted"):
        return {
            "origin": origin,
            "target": target,
            "admitted": False,
            "reason": str(selected.get("reason") or "route_unreachable"),
            "navigation_skill_id": NODE_ROUTE_SKILL_ID,
        }
    return {
        "origin": origin,
        "target": target,
        "admitted": True,
        "route_length_m": float(selected["route_length_m"]),
        "node_ids": list(selected["topology_node_ids"]),
        "edge_ids": list(selected["topology_edge_ids"]),
        "navigation_skill_id": NODE_ROUTE_SKILL_ID,
    }


def build_stress_plan(
    inputs: MapInputs,
    robot: str,
    raw_entries: Sequence[str] | None,
) -> dict[str, Any]:
    """Build ordered three-turn and two-round-trip stress batches."""
    entries = parse_stress_entries(raw_entries, inputs.topology)
    capabilities = controller_capabilities(robot, inputs.domain)
    policy = rotation_policy(robot, inputs.domain)
    connectivity, _unused = connectivity_and_routes(
        inputs.topology, capabilities, seed=0, route_count=0
    )
    connectivity["candidate_shortage"] = 0
    connectivity["requested_long_route_count"] = 0
    connectivity["selected_long_route_count"] = 0

    rotations: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    for entry in entries:
        input_index = int(entry["input_index"])
        batch_id = f"stress:{input_index:03d}"
        if entry["entry_type"] == "node":
            node_id = entry["node_ids"][0]
            status = (
                "planned" if policy == "applicable"
                else "unsupported" if policy == "unsupported"
                else "not_applicable"
            )
            reason = (
                "controller_cannot_rotate_in_place" if status == "unsupported"
                else "aerial_robot_rotation_not_applicable" if status == "not_applicable"
                else ""
            )
            case = {
                "case_id": f"{batch_id}:rotation",
                "batch_id": batch_id,
                "input_index": input_index,
                "raw_entry": entry["raw_entry"],
                "node_id": node_id,
                "case_type": "stress_rotation",
                "target_turns": 3,
                "target_yaw_rad": 6.0 * math.pi,
                "timeout_s": 3.0 * ROTATION_TIMEOUT_S,
                "keep_video": True,
                "status": status,
                **({"reason": reason} if reason else {}),
            }
            rotations.append(case)
            batches.append({
                **entry,
                "batch_id": batch_id,
                "status": status,
                "case_ids": [case["case_id"]],
                "target_turns": 3,
                "target_yaw_rad": 6.0 * math.pi,
                **({"reason": reason} if reason else {}),
            })
            continue

        first, second = entry["node_ids"]
        forward = _stress_route(inputs.topology, capabilities, first, second)
        reverse = _stress_route(inputs.topology, capabilities, second, first)
        admitted = bool(forward["admitted"] and reverse["admitted"])
        batch = {
            **entry,
            "batch_id": batch_id,
            "status": "planned" if admitted else "unreachable",
            "directions": [forward, reverse],
            "case_ids": [],
            "round_trip_count": 2,
        }
        if not admitted:
            batch["reason"] = "stress_pair_not_bidirectionally_reachable"
            batch["unreachable_directions"] = [
                {
                    "origin": row["origin"], "target": row["target"],
                    "reason": row.get("reason", "route_unreachable"),
                }
                for row in (forward, reverse) if not row["admitted"]
            ]
            batches.append(batch)
            continue

        direction_rows = (forward, reverse, forward, reverse)
        for leg_index, route in enumerate(direction_rows, 1):
            round_trip = (leg_index + 1) // 2
            case = {
                "case_id": f"{batch_id}:leg:{leg_index:02d}",
                "batch_id": batch_id,
                "input_index": input_index,
                "raw_entry": entry["raw_entry"],
                "case_type": "stress_navigation",
                "leg_index": leg_index,
                "round_trip": round_trip,
                "direction": f"{route['origin']}->{route['target']}",
                "origin": route["origin"],
                "target": route["target"],
                "route_length_m": route["route_length_m"],
                "node_ids": deepcopy(route["node_ids"]),
                "edge_ids": deepcopy(route["edge_ids"]),
                "navigation_skill_id": NODE_ROUTE_SKILL_ID,
                "keep_video": True,
                "status": "planned",
            }
            routes.append(case)
            batch["case_ids"].append(case["case_id"])
        batches.append(batch)

    return {
        "mode": "stress",
        "domain": inputs.domain,
        "metric": "xyz" if inputs.domain == "aerial" else "xy",
        "navigation_skill": {
            "module": NAVIGATION_SKILL_MODULE,
            "skill_id": NODE_ROUTE_SKILL_ID,
        },
        "controller_capabilities": list(capabilities),
        "rotation_policy": policy,
        "stress_entries": [str(value) for value in raw_entries or ()],
        "stress_batches": batches,
        "rotations": rotations,
        "edges": [],
        "excluded_edges": [],
        "long_routes": routes,
        "connectivity": connectivity,
    }


def normalise_angle(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def yaw_from_wxyz(quaternion: Sequence[float]) -> float:
    w, x, y, z = (float(value) for value in quaternion)
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def upright_cosine(quaternion: Sequence[float]) -> float:
    if len(quaternion) != 4 or not all(math.isfinite(float(value)) for value in quaternion):
        raise ValueError("posture requires a finite wxyz quaternion")
    w, x, y, z = (float(value) for value in quaternion)
    norm = w * w + x * x + y * y + z * z
    if norm <= 1e-12:
        raise ValueError("posture quaternion must be nonzero")
    return 1.0 - 2.0 * (x * x + y * y) / norm


SUPPORT_BODY_TOKENS = (
    "wheel", "foot", "toe", "ankle",
)


def active_physx_contact_point(point: Mapping[str, Any]) -> bool:
    """Reject PhysX contact-offset proximity rows that carry no contact force."""
    force = point.get("force_n")
    separation = point.get("separation_m")
    if force is None and separation is None:
        return True
    try:
        force_active = force is not None and math.isfinite(float(force)) \
            and abs(float(force)) > 1e-9
        penetration_active = separation is not None and math.isfinite(float(separation)) \
            and float(separation) <= 0.0
    except (TypeError, ValueError):
        return False
    return force_active or penetration_active


def is_support_contact(
    *,
    domain: str,
    robot_collider: str,
    points: Sequence[Mapping[str, Any]],
    support_z_m: float,
    height_tolerance_m: float = 0.12,
) -> bool:
    """Classify a support-limb contact without trusting a whole mesh as floor.

    The caller supplies the route-interpolated support height, so feet and
    wheels remain valid while climbing authored stairs/ramps without treating
    a horizontal furniture surface elsewhere as floor.  Contacts made by the
    chassis or against a wall/furniture side still fail.
    """
    if domain != "ground" or not points:
        return False
    lowered = robot_collider.casefold()
    if not any(token in lowered for token in SUPPORT_BODY_TOKENS):
        return False
    for point in points:
        position = point.get("position")
        normal = point.get("normal")
        if not isinstance(position, (list, tuple)) or len(position) != 3:
            return False
        if not isinstance(normal, (list, tuple)) or len(normal) != 3:
            return False
        if abs(float(position[2]) - float(support_z_m)) > height_tolerance_m:
            return False
        if abs(float(normal[2])) < 0.65:
            return False
    return True


def classify_contact(
    *,
    domain: str,
    robot_root: str,
    actor_paths: Sequence[str],
    collider_paths: Sequence[str],
    points: Sequence[Mapping[str, Any]],
    support_z_m: float,
) -> str:
    robot_sides = [
        index for index in (0, 1)
        if actor_paths[index] == robot_root or actor_paths[index].startswith(robot_root + "/")
        or collider_paths[index] == robot_root or collider_paths[index].startswith(robot_root + "/")
    ]
    if not robot_sides:
        return "unrelated"
    if len(robot_sides) == 2:
        return "self"
    side = robot_sides[0]
    if is_support_contact(
        domain=domain,
        robot_collider=collider_paths[side],
        points=points,
        support_z_m=support_z_m,
    ):
        return "support"
    return "obstacle"


def _initial_report(args: argparse.Namespace, inputs: MapInputs, plan: dict[str, Any], output: Path) -> dict[str, Any]:
    return {
        "schema": "eai.semantic_map.traversability_report",
        "version": 1,
        "status": "check_only" if args.check else "running",
        "mode": plan.get("mode", "standard"),
        "started_at": datetime.now(BEIJING).isoformat(),
        "output_dir": str(output),
        "robot": canonical_robot(args.robot),
        "scene": inputs.scene_key,
        "requested_scene": args.scene,
        "domain": inputs.domain,
        "seed": args.seed,
        "device": args.device,
        "headless": True,
        "navigation_skill": {
            "module": NAVIGATION_SKILL_MODULE,
            "skill_id": NODE_ROUTE_SKILL_ID,
        },
        "map": {
            "project_dir": str(inputs.project_dir),
            "topology_path": str(inputs.topology_path),
            "scene_usd": str(inputs.scene_usd),
            "node_count": len(inputs.topology.get("nodes", ())),
            "edge_count": len(inputs.topology.get("edges", ())),
            "input_hashes_before": inputs.input_hashes,
        },
        "thresholds": {
            "arrival_radius_m": ARRIVAL_RADIUS_M,
            "stop_linear_speed_m_s": STOP_LINEAR_M_S,
            "stop_angular_speed_rad_s": STOP_ANGULAR_RAD_S,
            "rotation_timeout_s": ROTATION_TIMEOUT_S,
            "stop_timeout_s": STOP_TIMEOUT_S,
            "rotation_tolerance_deg": math.degrees(ROTATION_TOLERANCE_RAD),
            "rotation_drift_m": ROTATION_DRIFT_M,
            "ground_vertical_deviation_m": GROUND_VERTICAL_DEVIATION_M,
            "ground_endpoint_vertical_error_m": GROUND_ENDPOINT_VERTICAL_ERROR_M,
        },
        "plan": plan,
        "results": {"rotations": [], "edges": [], "long_routes": []},
        "stress_entries": deepcopy(plan.get("stress_entries", [])),
        "stress_batches": [],
        "summary": {},
        "errors": [],
    }


def _case_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    statuses = [str(row.get("status", "unknown")) for row in rows]
    not_applicable = {"unsupported", "not_applicable"}
    return {
        "planned": sum(value not in not_applicable for value in statuses),
        "executed": sum(value in {"passed", "failed"} for value in statuses),
        "passed": statuses.count("passed"),
        "failed": statuses.count("failed"),
        "unsupported": statuses.count("unsupported"),
        "not_run": statuses.count("not_run") + statuses.count("planned"),
    }


def update_summary(report: dict[str, Any]) -> None:
    result = report["results"]
    groups = {
        "rotations": _case_summary(result["rotations"]),
        "edges": _case_summary(result["edges"]),
        "long_routes": _case_summary(result["long_routes"]),
    }
    applicable = sum(row["planned"] for row in groups.values())
    executed = sum(row["executed"] for row in groups.values())
    passed = sum(row["passed"] for row in groups.values())
    stress_batches = report.get("stress_batches", [])
    report["summary"] = {
        **groups,
        "applicable_planned": applicable,
        "executed": executed,
        "passed": passed,
        "pass_rate": None if executed == 0 else passed / executed,
        "coverage_rate": None if applicable == 0 else executed / applicable,
        "obstacle_collision_count": sum(
            len(row.get("collisions", ()))
            for group in result.values() for row in group
        ),
        "candidate_shortage": report["plan"]["connectivity"]["candidate_shortage"],
        "stress_batch_count": len(stress_batches),
        "stress_unreachable_batch_count": sum(
            row.get("status") == "unreachable" for row in stress_batches
        ),
    }


def _markdown_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[tuple[str, str]]) -> list[str]:
    output = ["| " + " | ".join(label for _key, label in columns) + " |",
              "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        cells = []
        for key, _label in columns:
            value = row.get(key, "")
            if key == "video" and value:
                value = f"[视频]({value})"
            elif key == "video":
                value = ""
            cells.append(str(value).replace("|", "\\|").replace("\n", " "))
        output.append("| " + " | ".join(cells) + " |")
    return output


def write_markdown(report: Mapping[str, Any], path: Path) -> None:
    summary = report.get("summary", {})
    pass_rate = summary.get("pass_rate")
    coverage_rate = summary.get("coverage_rate")
    artifact_lines = []
    if (path.parent / "test-plan.json").is_file():
        artifact_lines.append("- 测试清单：[test-plan.json](test-plan.json)")
    if (path.parent / "test-results.json").is_file():
        artifact_lines.append("- 逐项结果：[test-results.json](test-results.json)")
    lines = [
        "# 语义地图可通行性报告", "",
        f"- 状态：`{report.get('status')}`",
        f"- 模式：`{report.get('mode', 'standard')}`",
        f"- 机器人：`{report.get('robot')}`",
        f"- 场景：`{report.get('scene')}`",
        f"- 导航网络：`{report.get('domain')}`",
        f"- 导航 Skill：`{report.get('navigation_skill', {}).get('skill_id', '')}` "
        f"(`{report.get('navigation_skill', {}).get('module', '')}`)",
        f"- 随机种子：`{report.get('seed')}`", "",
        ("- 物理测试：未执行（`--check` 仅验证地图并生成清单）"
         if report.get("status") == "check_only" else "- 物理测试：已执行"),
        f"- 节点旋转策略：`{report.get('plan', {}).get('rotation_policy', '')}`",
        *artifact_lines, "",
        "## 汇总", "",
        "| 项目 | 计划 | 执行 | 通过 | 失败 | 不支持 | 未执行 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (("rotations", "节点旋转"), ("edges", "边导航"), ("long_routes", "长距离路线")):
        row = summary.get(key, {})
        lines.append(
            f"| {label} | {row.get('planned', 0)} | {row.get('executed', 0)} | "
            f"{row.get('passed', 0)} | {row.get('failed', 0)} | "
            f"{row.get('unsupported', 0)} | {row.get('not_run', 0)} |"
        )
    lines.extend([
        "",
        f"- 已执行用例通过率：{'未执行' if pass_rate is None else f'{pass_rate:.1%}'}",
        f"- 适用用例覆盖率：{'不适用' if coverage_rate is None else f'{coverage_rate:.1%}'}",
        f"- 障碍物碰撞事件：{summary.get('obstacle_collision_count', 0)}",
    ])
    connectivity = report.get("plan", {}).get("connectivity", {})
    lines.extend([
        "", "## 网络连通性", "",
        f"- 弱连通分量：{connectivity.get('weak_component_count', 0)}",
        f"- 合法可达有序点对：{connectivity.get('reachable_ordered_pair_count', 0)}",
        f"- 隔离或单向不可达点对：{connectivity.get('unreachable_ordered_pair_count', 0)}",
        f"- 长距离候选不足：{connectivity.get('candidate_shortage', 0)}",
        "",
    ])
    if report.get("mode") == "stress":
        raw_entries = report.get("stress_entries", [])
        lines.extend([
            "## 压力测试批次", "",
            f"- 原始条目（按输入顺序）：`{raw_entries}`",
            "- 单节点旋转目标：3 圈（1080°）",
            "- 节点对目标：A→B→A→B→A，仅首段前重置", "",
        ])
        batch_rows = report.get("stress_batches", []) or report.get("plan", {}).get(
            "stress_batches", []
        )
        if batch_rows:
            lines.extend(_markdown_table(batch_rows, (
                ("batch_id", "批次"), ("input_index", "输入序号"),
                ("raw_entry", "原始条目"), ("entry_type", "类型"),
                ("status", "状态"), ("reason", "原因"),
            )))
            lines.append("")
    for key, label in (("rotations", "节点旋转"), ("edges", "边导航"), ("long_routes", "长距离路线")):
        rows = report.get("results", {}).get(key, [])
        if not rows:
            continue
        lines.extend([f"## {label}", ""])
        lines.extend(_markdown_table(rows, (
            ("case_id", "用例"), ("status", "状态"), ("duration_s", "仿真秒"),
            ("reason", "原因"), ("video", "录像"),
        )))
        lines.append("")
    errors = report.get("errors", [])
    if errors:
        lines.extend(["## 错误", ""])
        lines.extend(f"- `{row.get('type', 'Error')}`：{row.get('message', '')}" for row in errors)
        lines.append("")
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(path)


def persist_report(report: dict[str, Any], output: Path) -> None:
    update_summary(report)
    _write_json(output / "report.json", report)
    _write_json(output / "test-results.json", {
        "status": report.get("status"),
        "summary": report.get("summary", {}),
        "results": report.get("results", {}),
        "stress_batches": report.get("stress_batches", []),
    })
    write_markdown(report, output / "report.md")


def _prepare_kit_numpy() -> None:
    """Prefer Kit's NumPy ABI before importing packages that pull in NumPy."""
    from importlib.util import find_spec

    spec = find_spec("isaacsim")
    if spec is None or not spec.submodule_search_locations:
        return
    for root in spec.submodule_search_locations:
        archives = sorted((Path(root) / "kit/data/Kit/Isaac-Sim").glob(
            "*/exts/*/omni.kit.pip_archive*/pip_prebundle"
        ))
        for archive in reversed(archives):
            if not (archive / "numpy/__init__.py").is_file():
                continue
            if "numpy" in sys.modules:
                raise InfrastructureError(
                    "NumPy was imported before Kit; run this diagnostic as a standalone process"
                )
            sys.path.insert(0, str(archive))
            return


def _level_supports(topology: Mapping[str, Any]) -> dict[str, float]:
    return {
        str(row["id"]): float(row["support_z_m"])
        for row in topology.get("levels", ())
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        and isinstance(row.get("support_z_m"), (int, float))
    }


def node_support_z(node: Mapping[str, Any], topology: Mapping[str, Any]) -> float:
    level = node.get("level_id")
    supports = _level_supports(topology)
    if level in supports:
        return supports[str(level)]
    return float(node["position_xyz"][2])


def route_support_z(
    position_xy: Sequence[float],
    node_ids: Sequence[str],
    nodes: Mapping[str, Mapping[str, Any]],
    topology: Mapping[str, Any],
) -> float:
    """Interpolate the authored support height on the nearest route segment."""
    route = [nodes[node_id] for node_id in node_ids]
    if not route:
        raise ValueError("support route must contain at least one node")
    if len(route) == 1:
        return node_support_z(route[0], topology)
    px, py = float(position_xy[0]), float(position_xy[1])
    best: tuple[float, float] | None = None
    for first, second in zip(route, route[1:]):
        ax, ay = (float(value) for value in first["position_xyz"][:2])
        bx, by = (float(value) for value in second["position_xyz"][:2])
        dx, dy = bx - ax, by - ay
        squared = dx * dx + dy * dy
        ratio = 0.0 if squared <= 1e-12 else max(
            0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / squared)
        )
        nearest_x, nearest_y = ax + ratio * dx, ay + ratio * dy
        distance_squared = (px - nearest_x) ** 2 + (py - nearest_y) ** 2
        first_z = node_support_z(first, topology)
        support = first_z + ratio * (node_support_z(second, topology) - first_z)
        candidate = (distance_squared, support)
        if best is None or candidate[0] < best[0]:
            best = candidate
    assert best is not None
    return best[1]


def raycast_proxy_geometry(
    topology: Mapping[str, Any],
    *,
    half_width_m: float = RAYCAST_PROXY_HALF_WIDTH_M,
) -> tuple[list[tuple[float, float, float]], list[int], list[int]]:
    """Build a non-colliding route-surface mesh for legged height scanners.

    The mesh follows node/edge heights rather than covering a whole level, so
    an upper floor cannot shadow lower-floor or staircase readings.  Stair
    edges are represented as horizontal treads; other height-changing edges
    are represented as ramps.  It is sensing geometry only and never receives
    collision APIs.
    """
    if not math.isfinite(half_width_m) or half_width_m <= 0:
        raise ValueError("raycast proxy half-width must be positive")
    nodes = {row["id"]: row for row in topology.get("nodes", ())}
    points: list[tuple[float, float, float]] = []
    counts: list[int] = []
    indices: list[int] = []

    def quad(corners: Sequence[Sequence[float]]) -> None:
        offset = len(points)
        points.extend(tuple(float(value) for value in corner) for corner in corners)
        counts.extend((3, 3))
        indices.extend((offset, offset + 1, offset + 2,
                        offset, offset + 2, offset + 3))

    for node in nodes.values():
        x, y = (float(value) for value in node["position_xyz"][:2])
        z = node_support_z(node, topology)
        quad(((x - half_width_m, y - half_width_m, z),
              (x + half_width_m, y - half_width_m, z),
              (x + half_width_m, y + half_width_m, z),
              (x - half_width_m, y + half_width_m, z)))

    for edge in topology.get("edges", ()):
        first, second = nodes[edge["from"]], nodes[edge["to"]]
        ax, ay = (float(value) for value in first["position_xyz"][:2])
        bx, by = (float(value) for value in second["position_xyz"][:2])
        first_z, second_z = node_support_z(first, topology), node_support_z(second, topology)
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        offset_x, offset_y = -dy / length * half_width_m, dx / length * half_width_m
        steps = (
            max(1, math.ceil(abs(second_z - first_z) / 0.18))
            if edge.get("traversal_mode") == "stairs" else 1
        )
        for index in range(steps):
            start_ratio, end_ratio = index / steps, (index + 1) / steps
            start_x, start_y = ax + dx * start_ratio, ay + dy * start_ratio
            end_x, end_y = ax + dx * end_ratio, ay + dy * end_ratio
            if edge.get("traversal_mode") == "stairs":
                # Select the tread at the segment's destination height when
                # ascending and at its source height when descending.
                height_ratio = end_ratio if second_z >= first_z else start_ratio
                start_z = end_z = first_z + (second_z - first_z) * height_ratio
            else:
                start_z = first_z + (second_z - first_z) * start_ratio
                end_z = first_z + (second_z - first_z) * end_ratio
            quad(((start_x - offset_x, start_y - offset_y, start_z),
                  (end_x - offset_x, end_y - offset_y, end_z),
                  (end_x + offset_x, end_y + offset_y, end_z),
                  (start_x + offset_x, start_y + offset_y, start_z)))
    return points, counts, indices


def _scene_hook(inputs: MapInputs, first_node: Mapping[str, Any]):
    """Replace the DIY plane with the exact USD bound to the map project."""
    def configure(cfg: Any) -> None:
        import isaaclab.sim as sim_utils
        from isaaclab.assets import AssetBaseCfg

        usd = sim_utils.UsdFileCfg(usd_path=str(inputs.scene_usd))
        original_spawn = usd.func

        def spawn(path, spawn_cfg, translation=None, orientation=None, **kwargs):
            prim = original_spawn(
                path, spawn_cfg, translation=translation, orientation=orientation, **kwargs
            )
            from pxr import Usd, UsdGeom, UsdPhysics

            for child in Usd.PrimRange(prim):
                if child.IsA(UsdPhysics.Scene):
                    child.SetActive(False)

            # Mixed-terrain legged controllers require one ray-cast mesh.
            # This non-colliding route proxy supplies controller height
            # observations; physical contacts still come only from the USD.
            points, counts, indices = raycast_proxy_geometry(inputs.topology)
            if points:
                mesh = UsdGeom.Mesh.Define(prim.GetStage(), "/World/TraversabilityRaycastSurface")
                mesh.CreatePointsAttr(points)
                mesh.CreateFaceVertexCountsAttr(counts)
                mesh.CreateFaceVertexIndicesAttr(indices)
                mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
            return prim

        usd.func = spawn
        scene_type = type(cfg.scene)
        annotations = getattr(scene_type, "__annotations__", {})
        annotations["world"] = AssetBaseCfg
        cfg.scene.world = AssetBaseCfg(prim_path="/World/TraversabilityScene", spawn=usd)
        if hasattr(cfg.scene, "terrain"):
            cfg.scene.terrain = None

        agents = list(getattr(cfg, "possible_agents", ()))
        if not agents:
            raise InfrastructureError("environment configuration has no robot agent")
        robot_cfg = getattr(cfg.scene, agents[0])
        robot_spawn = getattr(robot_cfg, "spawn", None)
        if robot_spawn is None or not hasattr(robot_spawn, "activate_contact_sensors"):
            raise InfrastructureError(
                "robot asset does not expose PhysX contact-report activation"
            )
        robot_spawn.activate_contact_sensors = True
        original_z = float(robot_cfg.init_state.pos[2])
        position = tuple(float(value) for value in first_node["position_xyz"])
        spawn_z = position[2] if inputs.domain == "aerial" else node_support_z(
            first_node, inputs.topology
        ) + original_z
        robot_cfg.init_state.pos = (position[0], position[1], spawn_z)

        scanner = getattr(cfg.scene, agents[0] + "_height_scanner", None)
        if scanner is not None:
            scanner.mesh_prim_paths = ["/World/TraversabilityRaycastSurface"]

        # Large authored scenes can exceed training-oriented default buffers.
        cfg.sim.physx.gpu_max_rigid_contact_count = max(
            int(cfg.sim.physx.gpu_max_rigid_contact_count), 2**18
        )
        cfg.sim.physx.gpu_found_lost_pairs_capacity = max(
            int(cfg.sim.physx.gpu_found_lost_pairs_capacity), 2**18
        )
        cfg.sim.physx.gpu_collision_stack_size = max(
            int(cfg.sim.physx.gpu_collision_stack_size), 2**24
        )

    return configure


class PhysxCollisionMonitor:
    """Collect every robot/external contact at each real physics substep."""

    def __init__(
        self,
        robot_root: str,
        body_paths: Sequence[str],
        domain: str,
        simulation: Any,
        physics_dt: float,
    ):
        import carb.settings
        import omni.physics.tensors
        import omni.usd
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysxSchema, Usd, UsdPhysics

        self.robot_root = robot_root.rstrip("/")
        self.domain = domain
        self.support_z_m = 0.0
        self.active = False
        self.events: list[dict[str, Any]] = []
        self.support_contact_count = 0
        self.self_contact_count = 0
        self.tensor_error: str | None = None
        self.tensor_sample_count = 0
        self.support_height_reader: Any = None
        self.callback_error: str | None = None
        self.callback_invocation_count = 0
        self.contact_header_count = 0
        self.unrelated_contact_count = 0
        self.inactive_proximity_count = 0
        stage = omni.usd.get_context().get_stage()
        root = stage.GetPrimAtPath(self.robot_root)
        if not root.IsValid():
            raise InfrastructureError(f"robot root prim does not exist: {self.robot_root}")
        reporters = 0
        covered_paths: list[str] = []
        for path in sorted(set(str(value) for value in body_paths)):
            if path != self.robot_root and not path.startswith(self.robot_root + "/"):
                raise InfrastructureError(
                    f"articulation body is outside robot root: {path}"
                )
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid() or not prim.HasAPI(UsdPhysics.RigidBodyAPI):
                raise InfrastructureError(f"robot body is not a rigid body: {path}")
            rigid = UsdPhysics.RigidBodyAPI(prim)
            if rigid.GetRigidBodyEnabledAttr().Get() is False:
                continue
            if not prim.HasAPI(PhysxSchema.PhysxContactReportAPI):
                if prim.IsInstanceProxy():
                    raise InfrastructureError(
                        f"instance-proxy rigid body lacks contact reporting: {prim.GetPath()}"
                    )
                PhysxSchema.PhysxContactReportAPI.Apply(prim)
            PhysxSchema.PhysxContactReportAPI(prim).CreateThresholdAttr().Set(0.0)
            reporters += 1
            covered_paths.append(path)
        if reporters == 0:
            raise InfrastructureError("robot has no rigid bodies for contact reporting")
        self.reporter_count = reporters
        self.reporter_paths = tuple(covered_paths)
        filters: set[str] = set()
        for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
            if not prim.HasAPI(UsdPhysics.CollisionAPI):
                continue
            if UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False:
                continue
            owner = prim
            while owner.IsValid() and not owner.HasAPI(UsdPhysics.RigidBodyAPI):
                owner = owner.GetParent()
            external_path = str(owner.GetPath()) if owner.IsValid() else str(prim.GetPath())
            if (external_path == self.robot_root
                    or external_path.startswith(self.robot_root + "/")):
                continue
            filters.add(external_path)
        if not filters:
            raise InfrastructureError("scene has no external collision filters")
        self.filter_paths = tuple(sorted(filters))
        self.physics_dt = float(physics_dt)
        if not math.isfinite(self.physics_dt) or self.physics_dt <= 0:
            raise InfrastructureError("invalid physics timestep for contact measurement")
        self.settings = carb.settings.get_settings()
        self.previous_disable = self.settings.get_as_bool("/physics/disableContactProcessing")
        self.settings.set_bool("/physics/disableContactProcessing", False)
        interface = get_physx_simulation_interface()
        interface.flush_changes()
        simulation.forward()
        self.simulation_view = omni.physics.tensors.create_simulation_view("torch")
        self.contact_view = self.simulation_view.create_rigid_contact_view(
            list(self.reporter_paths),
            [list(self.filter_paths) for _path in self.reporter_paths],
            max_contact_data_count=32768,
        )
        if (self.contact_view is None
                or set(self.contact_view.sensor_paths) != set(self.reporter_paths)
                or self.contact_view.sensor_count != len(self.reporter_paths)
                or self.contact_view.filter_count != len(self.filter_paths)):
            raise InfrastructureError("PhysX tensor contact view has incomplete coverage")
        self.sensor_paths = tuple(str(path) for path in self.contact_view.sensor_paths)
        self.subscription = interface.subscribe_full_contact_report_events(self._contacts)
        if self.subscription is None:
            raise InfrastructureError("PhysX contact report subscription is unavailable")
        self.simulation = simulation
        self.original_step = simulation.step
        self.owned_step = vars(simulation).get("step")

        def measured_step(*args: Any, **kwargs: Any) -> Any:
            result = self.original_step(*args, **kwargs)
            if self.active:
                self.sample()
            return result

        self.measured_step = measured_step
        simulation.step = measured_step

    def begin(self, support_z_m: float) -> None:
        self.support_z_m = float(support_z_m)
        self.events.clear()
        self.support_contact_count = 0
        self.self_contact_count = 0
        self.tensor_error = None
        self.tensor_sample_count = 0
        self.callback_error = None
        self.callback_invocation_count = 0
        self.contact_header_count = 0
        self.unrelated_contact_count = 0
        self.inactive_proximity_count = 0
        self.active = True

    def bind_support_height_reader(self, reader: Any) -> None:
        if not callable(reader):
            raise TypeError("support-height reader must be callable")
        self.support_height_reader = reader

    def end(self) -> dict[str, Any]:
        self.active = False
        error = self.tensor_error
        if error is None and self.tensor_sample_count == 0:
            error = "PhysX tensor contact view produced no physical-substep samples"
        return {
            "available": error is None,
            "error": error,
            "obstacle_contacts": deepcopy(self.events),
            "support_contact_count": self.support_contact_count,
            "self_contact_count": self.self_contact_count,
            "reporter_count": self.reporter_count,
            "reporter_paths": list(self.reporter_paths),
            "external_filter_count": len(self.filter_paths),
            "tensor_sample_count": self.tensor_sample_count,
            "callback_invocation_count": self.callback_invocation_count,
            "callback_error": self.callback_error,
            "contact_header_count": self.contact_header_count,
            "unrelated_contact_count": self.unrelated_contact_count,
            "inactive_proximity_count": self.inactive_proximity_count,
        }

    @property
    def measurement_error(self) -> str | None:
        return self.tensor_error

    def sample(self) -> None:
        """Read the tensor view immediately after one physics substep."""
        if not self.active or self.tensor_error is not None:
            return
        try:
            if self.domain == "ground":
                if self.support_height_reader is None:
                    raise ValueError("ground contact measurement lacks support-height reader")
                support_z = float(self.support_height_reader())
                if not math.isfinite(support_z):
                    raise ValueError("support-height reader returned a nonfinite value")
                self.support_z_m = support_z
            forces, positions, normals, separations, counts, starts = (
                self.contact_view.get_contact_data(self.physics_dt)
            )
            counts_host = counts.detach().cpu()
            starts_host = starts.detach().cpu()
            total = int(counts_host.sum().item())
            if total >= int(self.contact_view.max_contact_data_count):
                raise ValueError("PhysX contact buffer capacity reached")
            self.tensor_sample_count += 1
            for sensor_index, filter_index in counts_host.nonzero().tolist():
                count = int(counts_host[sensor_index, filter_index].item())
                start = int(starts_host[sensor_index, filter_index].item())
                if start < 0 or count < 0 or start + count > int(
                    self.contact_view.max_contact_data_count
                ):
                    raise ValueError("invalid PhysX tensor contact range")
                point_rows = positions[start:start + count].detach().cpu().tolist()
                normal_rows = normals[start:start + count].detach().cpu().tolist()
                force_rows = forces[start:start + count].detach().cpu().reshape(-1).tolist()
                separation_rows = (
                    separations[start:start + count].detach().cpu().reshape(-1).tolist()
                )
                points = [
                    {
                        "position": [float(value) for value in point],
                        "normal": [float(value) for value in normal],
                        "force_n": float(force),
                        "separation_m": float(separation),
                    }
                    for point, normal, force, separation in zip(
                        point_rows, normal_rows, force_rows, separation_rows
                    )
                ]
                points = [point for point in points if active_physx_contact_point(point)]
                if not points:
                    self.inactive_proximity_count += 1
                    continue
                sensor_path = self.sensor_paths[sensor_index]
                filter_path = self.filter_paths[filter_index]
                if is_support_contact(
                    domain=self.domain,
                    robot_collider=sensor_path,
                    points=points,
                    support_z_m=self.support_z_m,
                ):
                    self.support_contact_count += 1
                elif len(self.events) < 256:
                    self.events.append({
                        "source": "physx_tensor_contact_view",
                        "robot_body": sensor_path,
                        "external_collider": filter_path,
                        "points": points[:16],
                    })
        except Exception as exc:
            self.tensor_error = f"{type(exc).__name__}: {exc}"

    def _contacts(self, headers: Any, data: Any, friction: Any) -> None:
        del friction
        if not self.active:
            return
        try:
            self.callback_invocation_count += 1
            self.contact_header_count += len(headers)
            for header in headers:
                start, count = int(header.contact_data_offset), int(header.num_contact_data)
                if start < 0 or count < 0 or start + count > len(data):
                    raise ValueError("invalid PhysX contact data range")
        except Exception as exc:
            self.callback_error = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        self.active = False
        self.subscription = None
        if vars(self.simulation).get("step") is self.measured_step:
            if self.owned_step is None:
                del vars(self.simulation)["step"]
            else:
                self.simulation.step = self.owned_step
        self.contact_view = None
        self.simulation_view = None
        self.settings.set_bool("/physics/disableContactProcessing", self.previous_disable)


class CaseVideoRecorder:
    """One off-screen H.264 writer driven only by caller-owned physics ticks."""

    def __init__(self, stage: Any, simulation: Any, videos_dir: Path):
        import omni.replicator.core as rep
        from pxr import Gf, Usd, UsdGeom

        self.rep = rep
        self.stage = stage
        self.simulation = simulation
        self.videos_dir = videos_dir
        self.camera_path = "/World/SemanticMapTraversability/Camera"
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Scope.Define(stage, "/World/SemanticMapTraversability")
            self.camera = UsdGeom.Camera.Define(stage, self.camera_path)
            self.camera.CreateFocalLengthAttr(24.0)
            self.camera.CreateClippingRangeAttr(Gf.Vec2f(0.03, 10000.0))
            self.transform = self.camera.AddTransformOp()
        self.product = rep.create.render_product(self.camera_path, (VIDEO_WIDTH, VIDEO_HEIGHT))
        self.rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        self.rgb.attach(self.product)
        self.writer = None
        self.temporary: Path | None = None
        self.final: Path | None = None
        self.last_capture_time = -math.inf
        self.label = ""
        self.error: str | None = None

    def _set_camera(self, pose: Mapping[str, Any]) -> None:
        from pxr import Gf

        position = pose["position_m"]
        yaw = float(pose["yaw_rad"])
        distance = 2.8 if pose.get("domain") == "aerial" else 2.2
        elevation = 1.5 if pose.get("domain") == "aerial" else 1.15
        eye = (
            position[0] - distance * math.cos(yaw),
            position[1] - distance * math.sin(yaw),
            position[2] + elevation,
        )
        target = (position[0], position[1], position[2] + 0.20)
        self.transform.Set(Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1)
        ).GetInverse())

    def start(self, case_id: str, pose: Mapping[str, Any]) -> None:
        import imageio.v2 as imageio

        slug = safe_name(case_id)
        self.temporary = self.videos_dir / f"._recording_{slug}.mp4"
        self.final = self.videos_dir / f"{slug}.mp4"
        self.writer = imageio.get_writer(
            str(self.temporary), fps=VIDEO_FPS, codec="libx264", quality=7,
            macro_block_size=1,
        )
        self.label = case_id
        self.last_capture_time = -math.inf
        self.error = None
        self._set_camera(pose)

    def capture(self, simulation_time_s: float, pose: Mapping[str, Any], *, force: bool = False) -> None:
        if self.writer is None or self.error is not None:
            return
        if not force and simulation_time_s - self.last_capture_time < 1.0 / VIDEO_FPS - 1e-6:
            return
        try:
            import numpy as np
            from PIL import Image, ImageDraw, ImageFont

            before = float(self.simulation.current_time)
            self._set_camera(pose)
            self.simulation.render()
            if float(self.simulation.current_time) != before:
                raise RuntimeError("video render advanced physics outside the control loop")
            data = self.rgb.get_data()
            if getattr(data, "size", 0) == 0:
                raise RuntimeError("RGB annotator returned no frame")
            picture = Image.fromarray(np.asarray(data)[:, :, :3].astype("uint8"))
            draw = ImageDraw.Draw(picture)
            font_path = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
            font = ImageFont.truetype(str(font_path), 24) if font_path.is_file() else ImageFont.load_default()
            draw.rectangle((0, 0, VIDEO_WIDTH, 52), fill=(15, 20, 28))
            draw.text((18, 12), f"{self.label}  t={simulation_time_s:.2f}s", font=font, fill="white")
            self.writer.append_data(np.asarray(picture))
            self.last_capture_time = simulation_time_s
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def finish(self, *, keep: bool) -> tuple[str | None, str | None]:
        if self.writer is None:
            return None, self.error
        try:
            self.writer.close()
        except Exception as exc:
            self.error = self.error or f"{type(exc).__name__}: {exc}"
        finally:
            self.writer = None
        if self.temporary is None or self.final is None:
            return None, self.error
        if not self.temporary.is_file() or self.temporary.stat().st_size == 0:
            self.error = self.error or "encoder produced no MP4 data"
            return None, self.error
        if self.error is not None:
            keep = True
        if keep and self.temporary.exists():
            self.temporary.replace(self.final)
            return "videos/" + self.final.name, self.error
        if self.temporary.exists():
            self.temporary.unlink()
        return None, self.error

    def close(self) -> None:
        if self.writer is not None:
            self.finish(keep=True)
        try:
            self.rgb.detach(self.product)
            self.product.destroy()
        except Exception:
            pass


def _default_controller_cfg(robot: str) -> str:
    source_roots = (REPO_ROOT / "source/EAI", REPO_ROOT / "source/EAI_hmrs")
    for root in reversed(source_roots):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    from EAI.hmrs_env.env_diy.catalog import default_controller_cfg

    return str(default_controller_cfg(canonical_robot(robot)))


def _selection(robot: str, controller_cfg: str) -> dict[str, Any]:
    return {
        "version": 1,
        "task_name": "semantic_map_traversability",
        "scene_key": "plane",
        "robots": [{
            "type": canonical_robot(robot),
            "controller": {"cfg": controller_cfg, "mode": "default"},
            "attachments": [],
            "visual": {},
        }],
    }


def controller_uses_goal_position(controller: Any) -> bool:
    """Mirror the simulator's catalog-controller command classification."""
    return (
        getattr(controller, "command_name", None) == "goal_position"
        or getattr(controller, "robot_type", None) in {"Quadcopter", "M20Nav"}
    )


class PhysicalRunner:
    def __init__(
        self,
        *,
        args: argparse.Namespace,
        inputs: MapInputs,
        plan: dict[str, Any],
        report: dict[str, Any],
        output: Path,
        session: Any,
    ):
        import omni.usd
        import torch
        from EAI.controllers.base import normalize_controller_entry

        self.args = args
        self.inputs = inputs
        self.plan = plan
        self.report = report
        self.output = output
        self.session = session
        self.env = session.base_env
        self.app = session.simulation_app
        self.torch = torch
        if len(session.possible_agents) != 1:
            raise InfrastructureError("audit session must contain exactly one robot")
        self.robot_id = session.possible_agents[0]
        self.robot = self.env.scene[self.robot_id]
        entry = session.env_cfg.controllers[self.robot_id]
        self.controller, _aux = normalize_controller_entry(entry)
        self.command_name = str(getattr(self.controller, "command_name", "base_velocity"))
        self.yaw_command_name = getattr(self.controller, "yaw_command_name", None)
        self.goal_controlled = controller_uses_goal_position(self.controller)
        action_dim = (
            int(getattr(self.controller, "action_dim", 4))
            if getattr(self.controller, "control_mode", None) == "rotor_velocity" else 3
        )
        self.actions = {
            self.robot_id: torch.zeros((1, action_dim), dtype=torch.float32, device=self.env.device)
        }
        self.nodes = {row["id"]: row for row in inputs.topology["nodes"]}
        first = inputs.topology["nodes"][0]
        initial_support = node_support_z(first, inputs.topology)
        default_z = float(self.robot.data.default_root_state[0, 2].item())
        self.root_clearance = 0.0 if inputs.domain == "aerial" else default_z - initial_support
        self.bounds = self._bounds()
        self.current_support_z = initial_support
        self.current_route_node_ids: tuple[str, ...] = ()
        root_paths = tuple(getattr(self.robot.root_physx_view, "prim_paths", ()))
        if len(root_paths) != 1:
            raise InfrastructureError("cannot resolve one robot articulation root")
        link_paths = tuple(getattr(self.robot.root_physx_view, "link_paths", ()))
        if len(link_paths) != 1 or not link_paths[0]:
            raise InfrastructureError("cannot resolve robot articulation body paths")
        body_paths = tuple(str(path) for path in link_paths[0])
        robot_root = str(root_paths[0]).rstrip("/")
        if any(
            path != robot_root and not path.startswith(robot_root + "/")
            for path in body_paths
        ):
            common_parts = [path.strip("/").split("/") for path in body_paths]
            shared: list[str] = []
            for parts in zip(*common_parts):
                if len(set(parts)) != 1:
                    break
                shared.append(parts[0])
            robot_root = "/" + "/".join(shared)
        physics_dt = getattr(self.env, "physics_dt", None)
        if physics_dt is None:
            physics_dt = self.env.sim.get_physics_dt()
        self.monitor = PhysxCollisionMonitor(
            robot_root, body_paths, inputs.domain, self.env.sim, float(physics_dt)
        )
        self.monitor.bind_support_height_reader(self.expected_support_z)
        self.video = CaseVideoRecorder(
            omni.usd.get_context().get_stage(), self.env.sim, output / "videos"
        )
        self.active_video = False
        self.active_trace: list[dict[str, Any]] = []
        self.case_start_time = 0.0
        self.report["controller"] = {
            "configuration": type(self.controller).__module__ + "." + type(self.controller).__name__,
            "robot_type": getattr(self.controller, "robot_type", None),
            "command_name": self.command_name,
            "yaw_command_name": self.yaw_command_name,
            "control_mode": getattr(self.controller, "control_mode", None),
        }

    def _bounds(self) -> tuple[float, float, float, float, float, float]:
        points = [row["position_xyz"] for row in self.inputs.topology["nodes"]]
        return (
            min(float(row[0]) for row in points) - 2.0,
            max(float(row[0]) for row in points) + 2.0,
            min(float(row[1]) for row in points) - 2.0,
            max(float(row[1]) for row in points) + 2.0,
            min(float(row[2]) for row in points) - 2.0,
            max(float(row[2]) for row in points) + 2.0,
        )

    def sim_time(self) -> float:
        return float(self.env.sim.current_time)

    def expected_support_z(self) -> float:
        if self.inputs.domain != "ground" or not self.current_route_node_ids:
            return self.current_support_z
        position = self.robot.data.root_pos_w[0].tolist()
        return route_support_z(
            position[:2], self.current_route_node_ids, self.nodes, self.inputs.topology
        )

    def sequence(self, *_unused: Any) -> int:
        return int(getattr(self.env, "common_step_counter", self.env.sim.current_time_step_index))

    def pose(self, *_unused: Any) -> Any:
        position = tuple(float(value) for value in self.robot.data.root_pos_w[0].tolist())
        quaternion = tuple(float(value) for value in self.robot.data.root_quat_w[0].tolist())
        return NavigationPose(position, yaw_from_wxyz(quaternion))

    def pose_mapping(self) -> dict[str, Any]:
        pose = self.pose()
        return {"position_m": list(pose.position_m), "yaw_rad": pose.yaw_rad,
                "domain": self.inputs.domain}

    def velocity(self, *_unused: Any) -> tuple[float, ...]:
        linear = tuple(float(value) for value in self.robot.data.root_lin_vel_w[0].tolist())
        angular = tuple(float(value) for value in self.robot.data.root_ang_vel_w[0].tolist())
        if self.inputs.domain == "ground":
            return linear[0], linear[1], angular[2]
        return (*linear, *angular)

    def _set_base_velocity(self, values: Sequence[float]) -> None:
        tensor = self.torch.tensor([list(values)], dtype=self.torch.float32, device=self.env.device)
        if self.actions[self.robot_id].shape[1] != tensor.shape[1]:
            raise InfrastructureError("controller action dimension is not compatible with base velocity")
        self.actions[self.robot_id] = tensor
        self.env.set_command(self.robot_id, "base_velocity", tensor)

    def _set_goal_position(self, values: Sequence[float]) -> None:
        tensor = self.torch.tensor([list(values)], dtype=self.torch.float32, device=self.env.device)
        self.env.set_command(self.robot_id, "goal_position", tensor)

    def _set_goal_yaw(self, yaw: float) -> None:
        if not self.yaw_command_name:
            raise InfrastructureError("controller does not expose a yaw goal")
        tensor = self.torch.tensor([[float(yaw)]], dtype=self.torch.float32, device=self.env.device)
        self.env.set_command(self.robot_id, str(self.yaw_command_name), tensor)

    def safe_stop(self) -> None:
        try:
            if self.goal_controlled:
                self._set_goal_position(self.pose().position_m)
            else:
                self._set_base_velocity((0.0, 0.0, 0.0))
        except Exception:
            self.actions[self.robot_id].zero_()

    def _case_fault(self) -> str | None:
        if self.monitor.measurement_error is not None:
            return "collision_measurement_error"
        if self.monitor.events:
            return "obstacle_collision"
        position = self.robot.data.root_pos_w[0].tolist()
        quaternion = self.robot.data.root_quat_w[0].tolist()
        values = [*position, *quaternion, *self.velocity()]
        if not all(math.isfinite(float(value)) for value in values):
            return "nonfinite_robot_state"
        if self.inputs.domain == "ground" and upright_cosine(quaternion) < 0.70:
            return "robot_fallen"
        if self.inputs.domain == "ground" and self.current_route_node_ids:
            expected_support = route_support_z(
                position[:2], self.current_route_node_ids, self.nodes, self.inputs.topology
            )
            expected_root_z = expected_support + self.root_clearance
            if abs(float(position[2]) - expected_root_z) > GROUND_VERTICAL_DEVIATION_M:
                return "ground_height_deviation"
        low_x, high_x, low_y, high_y, low_z, high_z = self.bounds
        if not low_x <= position[0] <= high_x or not low_y <= position[1] <= high_y:
            return "navigation_bounds_exceeded"
        if self.inputs.domain == "aerial" and not low_z <= position[2] <= high_z:
            return "navigation_bounds_exceeded"
        return None

    def _trace_sample(self) -> dict[str, Any]:
        pose = self.pose()
        return {
            "tick": self.sequence(),
            "simulation_time_s": self.sim_time(),
            "position_m": list(pose.position_m),
            "yaw_rad": pose.yaw_rad,
            "quaternion_wxyz": [float(value) for value in self.robot.data.root_quat_w[0].tolist()],
            "velocity": list(self.velocity()),
        }

    def advance(self, *, force_video: bool = False) -> str | None:
        self.env.step(self.actions)
        sample = self._trace_sample()
        if not self.active_trace or self.sequence() % 5 == 0:
            self.active_trace.append(sample)
        if self.active_video:
            self.video.capture(
                self.sim_time() - self.case_start_time,
                self.pose_mapping(), force=force_video,
            )
        return self._case_fault()

    def reset_case(
        self,
        node_id: str,
        yaw: float,
        case_id: str,
        route_node_ids: Sequence[str] | None = None,
    ) -> None:
        node = self.nodes[node_id]
        point = [float(value) for value in node["position_xyz"]]
        support = node_support_z(node, self.inputs.topology)
        z = point[2] if self.inputs.domain == "aerial" else support + self.root_clearance
        state = self.robot.data.default_root_state
        state[0, :3] = self.torch.tensor([point[0], point[1], z], device=self.env.device)
        state[0, 3:7] = self.torch.tensor(
            [math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0)],
            device=self.env.device,
        )
        state[0, 7:] = 0.0
        self.actions[self.robot_id].zero_()
        self.env.reset()
        if self.goal_controlled:
            self._set_goal_position((point[0], point[1], z))
            if self.yaw_command_name:
                self._set_goal_yaw(yaw)
        else:
            self._set_base_velocity((0.0, 0.0, 0.0))
        self.current_support_z = support
        self.current_route_node_ids = tuple(route_node_ids or (node_id,))
        self._begin_case_recording(case_id, support)
        for _ in range(20):
            fault = self.advance()
            if fault:
                break

    def _begin_case_recording(self, case_id: str, support_z_m: float) -> None:
        """Start evidence capture without advancing the physics clock."""
        self.monitor.begin(support_z_m)
        self.active_trace = [self._trace_sample()]
        self.case_start_time = self.sim_time()
        self.video.start(case_id, self.pose_mapping())
        self.active_video = True
        self.video.capture(0.0, self.pose_mapping(), force=True)

    def begin_continuation_case(
        self,
        origin: str,
        case_id: str,
        route_node_ids: Sequence[str],
    ) -> float:
        """Open a new stress leg while preserving all simulated robot state."""
        support = node_support_z(self.nodes[origin], self.inputs.topology)
        self.current_support_z = support
        self.current_route_node_ids = tuple(route_node_ids)
        self._begin_case_recording(case_id, support)
        return _metric(
            self.pose().position_m,
            self.nodes[origin]["position_xyz"],
            self.inputs.domain,
        )

    def _finish_case(
        self,
        result: dict[str, Any],
        *,
        keep_video: bool,
        status: str,
        reason: str = "",
    ) -> dict[str, Any]:
        self.safe_stop()
        self.video.capture(
            self.sim_time() - self.case_start_time, self.pose_mapping(), force=True
        )
        self.active_video = False
        video, video_error = self.video.finish(keep=keep_video or status != "passed")
        contacts = self.monitor.end()
        result.update({
            "status": "failed" if video_error and status == "passed" else status,
            "reason": "recording_error" if video_error and status == "passed" else reason,
            "duration_s": max(0.0, self.sim_time() - self.case_start_time),
            "final_pose": self.pose_mapping(),
            "collisions": contacts["obstacle_contacts"],
            "contact_measurement": {
                "available": contacts["available"], "error": contacts["error"],
                "reporter_count": contacts["reporter_count"],
                "reporter_paths": contacts["reporter_paths"],
                "external_filter_count": contacts["external_filter_count"],
                "tensor_sample_count": contacts["tensor_sample_count"],
                "callback_invocation_count": contacts["callback_invocation_count"],
                "callback_error": contacts["callback_error"],
                "contact_header_count": contacts["contact_header_count"],
                "unrelated_contact_count": contacts["unrelated_contact_count"],
                "inactive_proximity_count": contacts["inactive_proximity_count"],
                "support_contact_count": contacts["support_contact_count"],
                "self_contact_count": contacts["self_contact_count"],
            },
            "video": video,
            "video_error": video_error,
        })
        trace_path = self.output / "trajectories" / (safe_name(result["case_id"]) + ".json")
        _write_json(trace_path, {"case_id": result["case_id"], "samples": self.active_trace})
        result["trajectory"] = "trajectories/" + trace_path.name
        return result

    def abort_active_case(
        self,
        planned: Mapping[str, Any],
        exc: BaseException,
    ) -> dict[str, Any]:
        """Close an interrupted encoder/monitor and retain partial evidence."""
        reason = f"infrastructure_error: {type(exc).__name__}: {exc}"
        result = {
            "case_id": planned["case_id"],
            "node_id": planned.get("node_id"),
            "origin": planned.get("origin"),
            "target": planned.get("target"),
            "batch_id": planned.get("batch_id"),
            "input_index": planned.get("input_index"),
            "raw_entry": planned.get("raw_entry"),
            "leg_index": planned.get("leg_index"),
            "round_trip": planned.get("round_trip"),
            "direction": planned.get("direction"),
            "infrastructure_error": reason,
        }
        if self.active_video:
            return self._finish_case(
                result, keep_video=True, status="failed", reason=reason
            )
        result.update({
            "status": "failed",
            "reason": reason,
            "video": None,
            "video_error": None,
            "collisions": [],
            "contact_measurement": {"available": False, "error": reason},
        })
        return result

    def _settle(self, sample_callback: Any | None = None) -> tuple[bool, str | None]:
        stable = 0
        deadline = self.sim_time() + STOP_TIMEOUT_S
        while self.sim_time() < deadline:
            fault = self.advance()
            if sample_callback is not None:
                sample_callback()
            if fault:
                return False, fault
            velocity = self.velocity()
            if self.inputs.domain == "ground":
                stopped = math.hypot(velocity[0], velocity[1]) <= STOP_LINEAR_M_S \
                    and abs(velocity[2]) <= STOP_ANGULAR_RAD_S
            else:
                stopped = math.sqrt(sum(value * value for value in velocity[:3])) <= STOP_LINEAR_M_S \
                    and math.sqrt(sum(value * value for value in velocity[3:])) <= STOP_ANGULAR_RAD_S
            stable = stable + 1 if stopped else 0
            if stable >= 3:
                return True, None
        return False, "stop_timeout"

    def run_rotation(self, planned: Mapping[str, Any]) -> dict[str, Any]:
        target_turns = int(planned.get("target_turns", 1))
        target_yaw = 2.0 * math.pi * target_turns
        timeout = float(planned.get("timeout_s", ROTATION_TIMEOUT_S * target_turns))
        result = {
            "case_id": planned["case_id"], "node_id": planned["node_id"],
            "batch_id": planned.get("batch_id"),
            "input_index": planned.get("input_index"),
            "raw_entry": planned.get("raw_entry"),
            "target_turns": target_turns,
            "target_yaw_rad": target_yaw,
            "timeout_s": timeout,
        }
        self.reset_case(planned["node_id"], 0.0, planned["case_id"], (planned["node_id"],))
        start = self.pose()
        previous_yaw = start.yaw_rad
        accumulated = 0.0
        max_drift = 0.0

        def sample_rotation() -> None:
            nonlocal previous_yaw, accumulated, max_drift
            current_pose = self.pose()
            accumulated += normalise_angle(current_pose.yaw_rad - previous_yaw)
            previous_yaw = current_pose.yaw_rad
            max_drift = max(
                max_drift, math.dist(start.position_m[:2], current_pose.position_m[:2])
            )

        fault = self._case_fault()
        deadline = self.sim_time() + timeout
        while fault is None and self.sim_time() < deadline \
                and abs(accumulated) < target_yaw - ROTATION_TOLERANCE_RAD:
            self._set_base_velocity((0.0, 0.0, 0.55))
            fault = self.advance()
            sample_rotation()
        timed_out = (
            fault is None
            and self.sim_time() >= deadline
            and abs(accumulated) < target_yaw - ROTATION_TOLERANCE_RAD
        )
        self.safe_stop()
        stopped, stop_reason = (
            self._settle(sample_rotation) if fault is None else (False, fault)
        )
        end = self.pose()
        drift = math.dist(start.position_m[:2], end.position_m[:2])
        max_drift = max(max_drift, drift)
        error = abs(abs(accumulated) - target_yaw)
        reason = fault or ("rotation_timeout" if timed_out else None) or stop_reason
        if reason is None and error > ROTATION_TOLERANCE_RAD:
            reason = "rotation_angle_out_of_tolerance"
        if reason is None and max_drift > ROTATION_DRIFT_M:
            reason = "rotation_drift_exceeded"
        if reason is None and not stopped:
            reason = "stop_not_confirmed"
        result.update({
            "accumulated_yaw_rad": accumulated,
            "rotation_error_deg": math.degrees(error),
            "position_drift_m": drift,
            "max_position_drift_m": max_drift,
            "stop_confirmed": stopped,
        })
        return self._finish_case(
            result, keep_video=bool(planned.get("keep_video")) or reason is not None,
            status="passed" if reason is None else "failed", reason=reason or "",
        )

    def _navigation_profile(self, topology: Mapping[str, Any], arrival_radius_m: float):
        return navigation_skill_profile(
            topology,
            tuple(self.plan["controller_capabilities"]),
            arrival_radius_m=arrival_radius_m,
        )

    def _apply_navigation_target(self, target: Any, profile: Any) -> None:
        """Compile the node-route Skill's command into this catalog controller.

        This is deliberately a host-side controller bridge.  Route selection,
        target references, directionality and lifecycle state all come from
        ``algorithm.skills.navigation``; no alternate navigation implementation
        is imported here.
        """
        if target is None:
            self.safe_stop()
            return
        position = tuple(float(value) for value in target.position_m)
        if self.inputs.domain == "aerial" or self.goal_controlled:
            self._set_goal_position(position)
            return
        pose = self.pose()
        dx = position[0] - pose.position_m[0]
        dy = position[1] - pose.position_m[1]
        remaining = math.hypot(dx, dy)
        desired = math.atan2(dy, dx)
        error = normalise_angle(desired - pose.yaw_rad)
        angular = max(-0.8, min(0.8, 2.0 * error))
        approach_scale = min(1.0, remaining / max(2.0 * profile.arrival_radius_m, 1e-9))
        linear = 0.0 if abs(error) > (
            math.pi if canonical_robot(self.args.robot) in ACKERMANN_ROBOTS else math.pi / 6.0
        ) else 0.45 * approach_scale
        self._set_base_velocity((linear, 0.0, angular))

    def _skill_observation(self) -> NavigationObservation:
        return NavigationObservation(
            self.pose(), self.sequence(), self.sim_time(), True, True
        )

    def run_navigation(
        self,
        planned: Mapping[str, Any],
        topology: Mapping[str, Any],
        *,
        keep_video: bool,
        reset_start: bool = True,
    ) -> dict[str, Any]:
        origin, target = planned["origin"], planned["target"]
        origin_point = self.nodes[origin]["position_xyz"]
        target_point = self.nodes[target]["position_xyz"]
        yaw = math.atan2(
            float(target_point[1]) - float(origin_point[1]),
            float(target_point[0]) - float(origin_point[0]),
        )
        route_node_ids = tuple(planned.get("node_ids", (origin, target)))
        if reset_start:
            self.reset_case(origin, yaw, planned["case_id"], route_node_ids)
            origin_error = _metric(
                self.pose().position_m, origin_point, self.inputs.domain
            )
        else:
            origin_error = self.begin_continuation_case(
                origin, planned["case_id"], route_node_ids
            )
        document = FrozenNavigationTopology(
            topology, scene_key=self.inputs.scene_key,
            source_digests=topology.get("source", {}),
        )
        timeout = max(60.0, float(planned["route_length_m"]) / 0.2 * 3.0)
        arrival_radius = (
            min(ARRIVAL_RADIUS_M, max(0.03, float(planned["route_length_m"]) * 0.20))
            if planned.get("case_type") == "edge" else ARRIVAL_RADIUS_M
        )
        profile = self._navigation_profile(topology, arrival_radius)
        provider = NavigationProvider(
            document,
            profile=profile,
            semantic_points=self.inputs.semantic_points,
        )
        task_id = safe_name(planned["case_id"])
        graph_digest = route_graph_digest(topology)
        base_params = {
            "world_state_seq": self.sequence(),
            "route_graph_digest": graph_digest,
            "origin_ref": origin,
        }

        def request(intent: str) -> dict[str, Any]:
            return {
                "request_id": task_id,
                "task_id": task_id,
                "skill_id": NODE_ROUTE_SKILL_ID,
                "skill_version": "simulator.skill.v1",
                "robot_id": self.robot_id,
                "target_ref": target,
                "parameters": {
                    **base_params,
                    "intent": intent,
                    "world_state_seq": self.sequence(),
                },
            }

        result = {
            "case_id": planned["case_id"], "origin": origin, "target": target,
            "batch_id": planned.get("batch_id"),
            "input_index": planned.get("input_index"),
            "raw_entry": planned.get("raw_entry"),
            "leg_index": planned.get("leg_index"),
            "round_trip": planned.get("round_trip"),
            "direction": planned.get("direction"),
            "reset_before_case": reset_start,
            "origin_error_m": origin_error,
            "route_length_m": float(planned["route_length_m"]),
            "arrival_radius_m": arrival_radius,
            "planned_node_ids": list(planned.get("node_ids", (origin, target))),
            "planned_edge_ids": list(planned.get("edge_ids", (planned.get("edge_id"),))),
        }
        response = None
        fault = self._case_fault()
        reason = fault
        if reason is None and not reset_start and origin_error > ARRIVAL_RADIUS_M:
            reason = "stress_leg_origin_mismatch"
        stop_confirmed = False
        try:
            observation = self._skill_observation()
            response = provider.dispatch(request("start"), observation=observation)
            deadline = self.sim_time() + timeout
            while reason is None and self.sim_time() < deadline:
                if response.state.get("status") in {"failed", "cancelled"}:
                    reason = str(response.state.get("reason_code") or "navigation_failed")
                    break
                intent = "start" if response.state.get("node_index") is None else "continue"
                request_value = request(intent)
                response = provider.control(
                    request_value,
                    observation=self._skill_observation(),
                    topology=document,
                )
                if response.state.get("status") in {"failed", "cancelled"}:
                    reason = str(response.state.get("reason_code") or "navigation_failed")
                    continue
                commands = response.plan.commands
                if commands and commands[0].command_type == "navigation_target":
                    payload = commands[0].payload
                    target_value = HostNavigationTarget(
                        tuple(float(value) for value in payload["position_xyz"]),
                        payload.get("target_yaw"),
                    )
                    self._apply_navigation_target(target_value, profile)
                else:
                    self.safe_stop()
                if response.state.get("status") == "completed":
                    break
                fault = self.advance()
                if fault:
                    reason = fault
                    break
                if self.sim_time() >= deadline:
                    reason = "navigation_timeout"
                    break
            if reason is None and response is not None and response.state.get("status") != "completed":
                reason = "navigation_timeout"
            if reason is None:
                self.safe_stop()
                stopped, stop_reason = self._settle()
                stop_confirmed = stopped
                if not stopped:
                    reason = stop_reason or "stop_timeout"
            if reason is not None:
                try:
                    self.safe_stop()
                except Exception:
                    pass
            if response is None:
                reason = reason or "navigation_no_response"
        except Exception as exc:
            reason = f"controller_or_navigation_error: {type(exc).__name__}: {exc}"
        finally:
            if reason is not None and response is not None:
                try:
                    response = provider.fail(task_id, reason, reason_code=reason)
                except Exception:
                    pass
            self.safe_stop()

        final_pose = self.pose()
        error = _metric(final_pose.position_m, target_point, self.inputs.domain)
        endpoint_height_error = (
            abs(float(final_pose.position_m[2]) - (
                node_support_z(self.nodes[target], self.inputs.topology) + self.root_clearance
            ))
            if self.inputs.domain == "ground" else None
        )
        if (reason is None and endpoint_height_error is not None
                and endpoint_height_error > GROUND_ENDPOINT_VERTICAL_ERROR_M):
            reason = "endpoint_height_error_exceeded"
        state = {} if response is None else dict(response.state)
        if reason is None and state.get("status") != "completed":
            reason = str(state.get("reason_code") or "navigation_incomplete")
        if reason is None and error > arrival_radius:
            reason = "endpoint_error_exceeded"
        execution_evidence = (
            {} if response is None else _jsonable(getattr(response, "private_evidence", {}))
        )
        route_evidence = execution_evidence.get("route_selection", {}) \
            if isinstance(execution_evidence, Mapping) else {}
        result.update({
            "navigation_skill_id": NODE_ROUTE_SKILL_ID,
            "navigation_state": state,
            "execution_evidence": execution_evidence,
            "actual_route_node_ids": list(route_evidence.get("topology_node_ids", ())),
            "actual_route_edge_ids": list(route_evidence.get("topology_edge_ids", ())),
            "endpoint_error_m": error,
            "endpoint_height_error_m": endpoint_height_error,
            "stop_confirmed": stop_confirmed,
        })
        return self._finish_case(
            result, keep_video=keep_video or reason is not None,
            status="passed" if reason is None else "failed", reason=reason or "",
        )

    def close(self) -> None:
        try:
            self.safe_stop()
        finally:
            self.video.close()
            self.monitor.close()


def _edge_runtime_case(planned: Mapping[str, Any], topology: Mapping[str, Any]) -> dict[str, Any]:
    nodes = {row["id"]: row for row in topology["nodes"]}
    result = deepcopy(dict(planned))
    result["route_length_m"] = _metric(
        nodes[planned["origin"]]["position_xyz"],
        nodes[planned["target"]]["position_xyz"],
        str(topology.get("domain", "ground")),
    )
    result["node_ids"] = [planned["origin"], planned["target"]]
    result["edge_ids"] = [planned["edge_id"]]
    result["case_type"] = "edge"
    return result


def _mark_remaining_not_run(report: dict[str, Any], plan: Mapping[str, Any]) -> None:
    for result_key, plan_key in (
        ("rotations", "rotations"), ("edges", "edges"), ("long_routes", "long_routes")
    ):
        existing = {row["case_id"] for row in report["results"][result_key]}
        for planned in plan[plan_key]:
            if planned["case_id"] not in existing:
                row = deepcopy(dict(planned))
                if row.get("status") not in {"unsupported", "not_applicable"}:
                    row["status"] = "not_run"
                    row["reason"] = "run_stopped_before_case"
                report["results"][result_key].append(row)
    if plan.get("mode") == "stress":
        existing_batches = {
            row["batch_id"]: row for row in report.get("stress_batches", [])
        }
        for row in existing_batches.values():
            if row.get("status") == "running":
                row["status"] = "not_run"
                row["reason"] = "stress_batch_interrupted"
        for planned in plan.get("stress_batches", ()):
            if planned["batch_id"] in existing_batches:
                continue
            row = deepcopy(dict(planned))
            if row.get("status") == "planned":
                row["status"] = "not_run"
                row["reason"] = "run_stopped_before_batch"
            report.setdefault("stress_batches", []).append(row)


def _has_infrastructure_error(result: Mapping[str, Any]) -> bool:
    measurement = result.get("contact_measurement", {})
    return (
        measurement.get("available") is False
        or result.get("reason") == "collision_measurement_error"
        or bool(result.get("video_error"))
    )


def _execute_stress_plan(
    runner: PhysicalRunner,
    inputs: MapInputs,
    plan: Mapping[str, Any],
    report: dict[str, Any],
    output: Path,
) -> None:
    """Execute pressure batches in the exact order supplied by the operator."""
    rotations = {row["batch_id"]: row for row in plan.get("rotations", ())}
    routes: dict[str, list[Mapping[str, Any]]] = {}
    for row in plan.get("long_routes", ()):
        routes.setdefault(row["batch_id"], []).append(row)

    for planned_batch in plan.get("stress_batches", ()):
        batch = deepcopy(dict(planned_batch))
        report["stress_batches"].append(batch)
        batch_id = batch["batch_id"]
        if batch["entry_type"] == "node":
            planned = rotations[batch_id]
            execution_error: BaseException | None = None
            if planned["status"] in {"unsupported", "not_applicable"}:
                result = deepcopy(dict(planned))
            else:
                batch["status"] = "running"
                persist_report(report, output)
                try:
                    result = runner.run_rotation(planned)
                except Exception as exc:
                    execution_error = exc
                    result = runner.abort_active_case(planned, exc)
            report["results"]["rotations"].append(result)
            batch["status"] = result["status"]
            batch["case_results"] = [{
                "case_id": result["case_id"],
                "status": result["status"],
                "reason": result.get("reason", ""),
            }]
            if result.get("reason"):
                batch["reason"] = result["reason"]
            persist_report(report, output)
            if execution_error is not None:
                raise InfrastructureError(
                    "simulation infrastructure failed during stress rotation"
                ) from execution_error
            if _has_infrastructure_error(result):
                raise InfrastructureError(
                    "measurement or recording failed during stress rotation"
                )
            continue

        if batch["status"] == "unreachable":
            persist_report(report, output)
            continue

        legs = sorted(routes.get(batch_id, ()), key=lambda row: int(row["leg_index"]))
        batch["status"] = "running"
        batch["case_results"] = []
        persist_report(report, output)
        for leg_offset, planned in enumerate(legs):
            try:
                result = runner.run_navigation(
                    planned,
                    inputs.topology,
                    keep_video=True,
                    reset_start=leg_offset == 0,
                )
                execution_error = None
            except Exception as exc:
                execution_error = exc
                result = runner.abort_active_case(planned, exc)
            report["results"]["long_routes"].append(result)
            batch["case_results"].append({
                "case_id": result["case_id"],
                "leg_index": result.get("leg_index"),
                "round_trip": result.get("round_trip"),
                "direction": result.get("direction"),
                "status": result["status"],
                "reason": result.get("reason", ""),
                "planned_node_ids": result.get("planned_node_ids", []),
            })
            if result["status"] != "passed":
                batch["status"] = "failed"
                batch["reason"] = result.get("reason") or "stress_navigation_leg_failed"
                for remaining in legs[leg_offset + 1:]:
                    skipped = deepcopy(dict(remaining))
                    skipped["status"] = "not_run"
                    skipped["reason"] = "stress_pair_stopped_after_failed_leg"
                    report["results"]["long_routes"].append(skipped)
                    batch["case_results"].append({
                        "case_id": skipped["case_id"],
                        "leg_index": skipped.get("leg_index"),
                        "round_trip": skipped.get("round_trip"),
                        "direction": skipped.get("direction"),
                        "status": "not_run",
                        "reason": skipped["reason"],
                        "planned_node_ids": skipped.get("node_ids", []),
                    })
                persist_report(report, output)
                if execution_error is not None:
                    raise InfrastructureError(
                        "simulation infrastructure failed during stress navigation"
                    ) from execution_error
                if _has_infrastructure_error(result):
                    raise InfrastructureError(
                        "measurement or recording failed during stress navigation"
                    )
                break
            persist_report(report, output)
        else:
            batch["status"] = "passed"
            persist_report(report, output)


def run_physical(
    args: argparse.Namespace,
    inputs: MapInputs,
    plan: dict[str, Any],
    report: dict[str, Any],
    output: Path,
) -> int:
    _prepare_kit_numpy()
    sys.path.insert(0, str(REPO_ROOT))
    from simulator import SimulatorLaunchConfig, open_simulator_session

    controller_cfg = _default_controller_cfg(args.robot)
    selection = _selection(args.robot, controller_cfg)
    first_node = inputs.topology["nodes"][0]
    config = SimulatorLaunchConfig(
        env="semantic_map_traversability",
        resolved_env_name="semantic_map_traversability",
        selection_data=selection,
        device=args.device,
        headless=True,
        enable_ros_bridge_extension=False,
        disable_orsus_ros_env=True,
        aerial_sensor_backend="disabled",
        env_cfg_hook=_scene_hook(inputs, first_node),
        app_launcher_args={
            "headless": True, "enable_cameras": True, "device": args.device,
            "fast_shutdown": False, "livestream": 0,
            "width": VIDEO_WIDTH, "height": VIDEO_HEIGHT,
        },
    )
    runner: PhysicalRunner | None = None
    infrastructure_failure = False
    try:
        with open_simulator_session(config) as session:
            try:
                runner = PhysicalRunner(
                    args=args, inputs=inputs, plan=plan, report=report,
                    output=output, session=session,
                )
                if plan.get("mode") == "stress":
                    _execute_stress_plan(runner, inputs, plan, report, output)
                else:
                    for planned in plan["rotations"]:
                        if planned["status"] in {"unsupported", "not_applicable"}:
                            report["results"]["rotations"].append(deepcopy(dict(planned)))
                            persist_report(report, output)
                        else:
                            result = runner.run_rotation(planned)
                            report["results"]["rotations"].append(result)
                            persist_report(report, output)
                            if _has_infrastructure_error(result):
                                raise InfrastructureError(
                                    "measurement or recording failed during rotation"
                                )
                    for planned in plan["edges"]:
                        if planned["status"] == "unsupported":
                            report["results"]["edges"].append(deepcopy(dict(planned)))
                            persist_report(report, output)
                            continue
                        runtime_case = _edge_runtime_case(planned, inputs.topology)
                        topology = restricted_edge_topology(inputs.topology, planned)
                        result = runner.run_navigation(runtime_case, topology, keep_video=False)
                        report["results"]["edges"].append(result)
                        persist_report(report, output)
                        if _has_infrastructure_error(result):
                            raise InfrastructureError(
                                "measurement or recording failed during edge navigation"
                            )
                    for planned in plan["long_routes"]:
                        result = runner.run_navigation(planned, inputs.topology, keep_video=True)
                        report["results"]["long_routes"].append(result)
                        persist_report(report, output)
                        if _has_infrastructure_error(result):
                            raise InfrastructureError(
                                "measurement or recording failed during long navigation"
                            )
            finally:
                if runner is not None:
                    runner.close()
                    runner = None
    except InfrastructureError as exc:
        infrastructure_failure = True
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        infrastructure_failure = True
        report["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    finally:
        _mark_remaining_not_run(report, plan)

    current_hashes = {
        path: _sha256(Path(path)) if Path(path).is_file() else "missing"
        for path in inputs.input_hashes
    }
    report["map"]["input_hashes_after"] = current_hashes
    report["map"]["inputs_unchanged"] = current_hashes == inputs.input_hashes
    if not report["map"]["inputs_unchanged"]:
        infrastructure_failure = True
        report["errors"].append({
            "type": "InputMutationError", "message": "map or scene inputs changed during the run"
        })
    all_rows = [row for group in report["results"].values() for row in group]
    failed = any(row.get("status") == "failed" for row in all_rows)
    failed = failed or any(
        row.get("status") == "failed" for row in report.get("stress_batches", ())
    )
    partial = (
        any(row.get("status") == "unsupported" for row in all_rows)
        or (
            plan.get("mode") != "stress"
            and plan["connectivity"]["candidate_shortage"] > 0
        )
        or any(row.get("status") == "not_run" for row in all_rows)
        or any(
            row.get("status") in {"unreachable", "unsupported", "not_run"}
            for row in report.get("stress_batches", ())
        )
    )
    if infrastructure_failure:
        report["status"] = "error"
        exit_code = 2
    elif failed:
        report["status"] = "failed"
        exit_code = 1
    elif partial:
        report["status"] = "partial"
        exit_code = 1
    else:
        report["status"] = "passed"
        exit_code = 0
    report["finished_at"] = datetime.now(BEIJING).isoformat()
    persist_report(report, output)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--robot", required=True, help="robot catalog key, for example go2 or iris")
    parser.add_argument("--scene", required=True, help="semantic project scene_key or scene directory name")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--check", action="store_true", help="write the deterministic plan without Isaac")
    parser.add_argument(
        "--StressT",
        nargs="*",
        default=None,
        metavar="NODE_OR_PAIR",
        help=(
            "run only ordered stress entries: a node rotates three turns; "
            "A,B performs two continuous round trips"
        ),
    )
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--output-root", type=Path, default=None,
                        help="parent directory; defaults to ~/tmp")
    return parser


def _minimal_error_report(args: argparse.Namespace, output: Path, exc: BaseException) -> dict[str, Any]:
    return {
        "schema": "eai.semantic_map.traversability_report", "version": 1,
        "status": "error", "started_at": datetime.now(BEIJING).isoformat(),
        "finished_at": datetime.now(BEIJING).isoformat(), "output_dir": str(output),
        "mode": "stress" if getattr(args, "StressT", None) is not None else "standard",
        "robot": str(args.robot), "scene": str(args.scene), "seed": args.seed,
        "stress_entries": list(getattr(args, "StressT", None) or ()),
        "stress_batches": [],
        "headless": True,
        "navigation_skill": {
            "module": NAVIGATION_SKILL_MODULE,
            "skill_id": NODE_ROUTE_SKILL_ID,
        },
        "results": {"rotations": [], "edges": [], "long_routes": []},
        "plan": {"mode": "stress" if getattr(args, "StressT", None) is not None else "standard",
                 "connectivity": {"candidate_shortage": 0}}, "summary": {},
        "errors": [{"type": type(exc).__name__, "message": str(exc)}],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = create_output_dir(args.robot, args.scene, root=args.output_root)
    try:
        robot = canonical_robot(args.robot)
        inputs = load_map_inputs(robot, args.scene, args.asset_root)
        plan = (
            build_stress_plan(inputs, robot, args.StressT)
            if args.StressT is not None
            else build_test_plan(inputs, robot, args.seed)
        )
        _write_json(output / "test-plan.json", plan)
        report = _initial_report(args, inputs, plan, output)
        if args.check:
            report["results"] = {
                "rotations": deepcopy(plan["rotations"]),
                "edges": deepcopy(plan["edges"]),
                "long_routes": deepcopy(plan["long_routes"]),
            }
            report["stress_batches"] = deepcopy(plan.get("stress_batches", []))
            report["finished_at"] = datetime.now(BEIJING).isoformat()
            persist_report(report, output)
            print(json.dumps({
                "status": report["status"], "report": str(output / "report.json"),
                "output_dir": str(output),
            }, ensure_ascii=False))
            return 0
        exit_code = run_physical(args, inputs, plan, report, output)
        print(json.dumps({
            "status": report["status"], "report": str(output / "report.json"),
            "output_dir": str(output),
        }, ensure_ascii=False))
        return exit_code
    except (InputError, InfrastructureError) as exc:
        report = _minimal_error_report(args, output, exc)
        persist_report(report, output)
        print(json.dumps({
            "status": "error", "error": str(exc), "report": str(output / "report.json"),
            "output_dir": str(output),
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        report = _minimal_error_report(args, output, exc)
        persist_report(report, output)
        print(json.dumps({
            "status": "error", "error": f"{type(exc).__name__}: {exc}",
            "report": str(output / "report.json"), "output_dir": str(output),
        }, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
