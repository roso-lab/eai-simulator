"""Editable map projects: one document, immutable geometry, atomic derived revisions.

This module deliberately needs no Isaac/Kit imports. Geometry comes from a measured
grid package; semantic edits never turn occupied or unknown cells into free space.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
import uuid

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import box, mapping
from shapely.ops import unary_union

from .model import TopologyDocument
from .product import load_product, save_draft, validate_document, _unique_object, _reject_constant
from .semantics import empty_semantics, polygon_error, semantic_level

SCHEMA = "eai.semantic_map.project"
COLORS = [(38, 132, 180), (216, 139, 43), (139, 100, 190), (44, 153, 126),
          (203, 101, 115), (103, 139, 62), (94, 116, 197), (165, 109, 65)]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    """Serialize before touching destination, then atomic replace and fsync."""
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".pending-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def child(root, relative):
    """Package references must stay inside their owning directory."""
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError("package file path must be relative")
    result = (Path(root) / relative).resolve()
    if not result.is_relative_to(Path(root).resolve()):
        raise ValueError("package path escapes project")
    return result


def load_grid(path):
    with np.load(path, allow_pickle=False) as archive:
        grid = {key: archive[key].copy() for key in archive.files}
    required = {"x", "y", "occupied", "support", "state", "resolution_m"}
    if not required <= grid.keys():
        raise ValueError(f"grid missing fields: {sorted(required - grid.keys())}")
    x, y = grid["x"], grid["y"]
    res = float(grid["resolution_m"])
    if (not math.isfinite(res) or res <= 0 or x.ndim != 1 or y.ndim != 1
            or not len(x) or not len(y) or len(x) * len(y) > 2_000_000
            or not np.isfinite(x).all() or not np.isfinite(y).all()
            or not np.allclose(np.diff(x), res) or not np.allclose(np.diff(y), res)):
        raise ValueError("grid must have finite uniformly increasing metre coordinates")
    if "semantic_occupied" not in grid:
        grid["semantic_occupied"] = grid["occupied"].copy()
    for key in ("occupied", "semantic_occupied", "support", "state"):
        if grid[key].shape != (len(y), len(x)):
            raise ValueError(f"invalid grid shape: {key}")
    for key in ("occupied", "semantic_occupied", "support"):
        if not np.isin(grid[key], [0, 1]).all():
            raise ValueError(f"invalid boolean mask: {key}")
        grid[key] = grid[key].astype(bool)
    if not np.isin(grid["state"], [-1, 0, 1]).all():
        raise ValueError("invalid occupancy state")
    for key in ("instance_id", "region_id"):
        if key in grid and (grid[key].shape != grid["state"].shape
                            or not np.issubdtype(grid[key].dtype, np.integer)):
            raise ValueError(f"invalid label grid: {key}")
    return grid


def extent(grid):
    half = float(grid["resolution_m"]) / 2
    return [float(grid["x"][0] - half), float(grid["x"][-1] + half),
            float(grid["y"][0] - half), float(grid["y"][-1] + half)]


def polygon_mask(points, grid):
    polygon = [[p[0], p[1], 0.] for p in points]
    problem = polygon_error(polygon)
    if problem:
        raise ValueError(problem)
    x, y = grid["x"][None, :], grid["y"][:, None]
    inside = np.zeros(grid["state"].shape, dtype=bool)
    border = inside.copy()
    for a, b in zip(points, points[1:] + points[:1]):
        cross = (b[0]-a[0])*(y-a[1]) - (b[1]-a[1])*(x-a[0])
        border |= ((np.abs(cross) < 1e-9) & (x >= min(a[0], b[0])-1e-9)
                   & (x <= max(a[0], b[0])+1e-9) & (y >= min(a[1], b[1])-1e-9)
                   & (y <= max(a[1], b[1])+1e-9))
        if b[1] != a[1]:
            inside ^= (((a[1] > y) != (b[1] > y))
                       & (x < a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1])))
    return inside | border


def mask_geometry(mask, grid):
    """Lossless cell boundary GeoJSON (holes and disconnected components retained)."""
    x0, _, y0, _ = extent(grid)
    res = float(grid["resolution_m"])
    pieces, runs = [], []
    for row, values in enumerate(mask):
        edges = np.diff(np.r_[False, values, False].astype(np.int8))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            runs.append([int(row), int(start), int(end)])
            pieces.append(box(x0+start*res, y0+row*res, x0+end*res, y0+(row+1)*res))
    geometry = json.loads(json.dumps(mapping(unary_union(pieces)))) if pieces else None
    return geometry, runs


def color(identifier):
    return COLORS[int(hashlib.sha256(identifier.encode()).hexdigest()[:8], 16) % len(COLORS)]


class MapProject:
    """Transactional commands shared by CLI, browser and Python callers.

    `project.json` is the only commit pointer. Published revisions are immutable;
    failed exports leave the previous pointer untouched. A second process cannot
    overwrite a revision saved after it opened the project.
    """

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read_json(self.root / "project.json")
        if self.manifest.get("schema") != SCHEMA or self.manifest.get("version") != 1:
            raise ValueError("unsupported map project")
        current = child(self.root, self.manifest["current_revision"])
        export = read_json(current / "manifest.json")
        for name, digest in export["files"].items():
            if sha256(child(current, name)) != digest:
                raise ValueError(f"revision file changed outside tool: {name}")
        self.document = load_product(current / "topology.json")
        self.grids = {}
        for entry in self.manifest["base_grids"]:
            path = child(self.root, entry["file"])
            if sha256(path) != entry["sha256"]:
                raise ValueError("base geometry changed outside tool")
            self.grids[entry["level_id"]] = load_grid(path)
        self._initialize()
        self._manifest_digest = sha256(self.root / "project.json")
        self.saved_revision = self.manifest["current_revision"]
        self._saved_document = self.document.to_dict()

    def _initialize(self):
        self.revision = 0
        self._undo, self._redo = [], []
        self._derived = None
        self._check(self.document)

    @classmethod
    def _create(cls, root, doc, grids, provenance):
        root = Path(root).resolve()
        if root.exists():
            raise ValueError("new project directory must not exist; use open for an existing project")
        project = cls.__new__(cls)
        project.root, project.document, project.grids = root, doc, grids
        project.manifest = {"schema": SCHEMA, "version": 1, "scene_key": doc.scene_key,
                            "coordinate_frame": "scene_world", "meters_per_unit": 1,
                            "up_axis": "Z", "base_grids": [], "provenance": provenance}
        project.saved_revision = None
        project._manifest_digest = None
        project._saved_document = None
        project._initialize()  # Validate before creating any directory.
        (root / "base").mkdir(parents=True)
        for i, (lid, grid) in enumerate(grids.items()):
            name = f"base/level-{i:03d}.npz"
            np.savez_compressed(root / name, **grid)
            project.manifest["base_grids"].append(
                {"level_id": lid, "file": name, "sha256": sha256(root / name)})
        project.save()
        return project

    @classmethod
    def import_scene(cls, source, root):
        source = Path(source).resolve()
        doc = load_product(source / "topology.json")
        if doc.version != 2:
            raise ValueError("grid import requires explicit topology levels (version 2)")
        grids, names = {}, {}
        for level in doc.levels:
            name = f"{level['id']}-semantic-grid.npz"
            grids[level["id"]] = load_grid(child(source, name))
            names[level["id"]] = name
        semantics = doc.semantics or empty_semantics()
        records = {row["id"]: row for key in ("regions", "entities") for row in semantics[key]}
        for row in semantics["regions"]:
            lid = semantic_level(row, records)
            number = row.get("properties", {}).get("raster_region_id")
            if lid in grids and type(number) is int and number > 0:
                row.setdefault("properties", {})["map_project"] = {
                    "mode": "base_region", "level_id": lid, "region_id": number,
                    "polygon_xyz": deepcopy(row["polygon_xyz"])}
        for row in semantics["entities"]:
            binding = row.get("properties", {}).get("geometry_binding", {})
            lid = semantic_level(row, records)
            number = binding.get("instance_id")
            if lid in grids and type(number) is int and number > 0:
                row.setdefault("properties", {})["map_project"] = {
                    "mode": "base_instance", "level_id": lid, "instance_id": number}
        doc.semantics = semantics
        provenance = {"import_directory": str(source), "files": {
            name: sha256(source / name) for name in ("topology.json", *names.values())}}
        # Retain original visual observations and geometric limitations verbatim.
        for name in ("semantic-footprints.json", "semantic-manifest.json"):
            if (source / name).is_file():
                provenance[name] = read_json(source / name)
                provenance["files"][name] = sha256(source / name)
        return cls._create(root, doc, grids, provenance)

    @classmethod
    def create_blank(cls, root, scene_key, bounds, resolution=.1, levels=None):
        if (len(bounds) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds)
                or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]
                or not math.isfinite(resolution) or resolution <= 0):
            raise ValueError("invalid bounds/resolution")
        nx, ny = math.ceil((bounds[2]-bounds[0])/resolution), math.ceil((bounds[3]-bounds[1])/resolution)
        if nx*ny > 2_000_000:
            raise ValueError("grid exceeds 2 million cells")
        doc = TopologyDocument(scene_key)
        doc.set_levels(levels or [{"id": "ground", "label": "Ground", "support_z_m": 0.}])
        doc.semantics = empty_semantics()
        grids = {level["id"]: {
            "x": bounds[0]+(np.arange(nx)+.5)*resolution,
            "y": bounds[1]+(np.arange(ny)+.5)*resolution,
            "resolution_m": np.array(resolution), "state": np.full((ny, nx), -1, np.int8),
            "support": np.zeros((ny, nx), bool), "occupied": np.zeros((ny, nx), bool),
            "semantic_occupied": np.zeros((ny, nx), bool),
            "ground_z": np.full((ny, nx), np.nan)} for level in doc.levels}
        return cls._create(root, doc, grids, {"geometry_status": "unknown_blank_canvas"})

    @property
    def dirty(self):
        return self.document.to_dict() != self._saved_document

    def _check(self, doc):
        errors = validate_document(doc)
        if errors:
            raise ValueError("; ".join(errors))
        if {row["id"] for row in doc.levels} != set(self.grids):
            raise ValueError("document floors must match base geometry floors")
        return self.derive(doc)

    def derive(self, doc=None):
        doc = doc or self.document
        semantics = doc.semantics or empty_semantics()
        records = {row["id"]: row for key in ("regions", "entities", "interaction_poses")
                   for row in semantics[key]}
        output = {}
        for lid, grid in self.grids.items():
            regions, entities = [], []
            region_ids = np.zeros(grid["state"].shape, np.int32)
            semantic_obstacles = grid["semantic_occupied"]
            instance_ids = np.where(semantic_obstacles, 0, -1).astype(np.int32)
            for row in semantics["regions"]:
                if semantic_level(row, records) != lid:
                    continue
                binding = row.get("properties", {}).get("map_project", {})
                if not isinstance(binding, dict):
                    raise ValueError("region map_project binding must be an object")
                if (binding.get("mode") == "base_region" and binding.get("level_id") == lid
                        and binding.get("polygon_xyz") == row["polygon_xyz"]):
                    if type(binding.get("region_id")) is not int or binding["region_id"] <= 0:
                        raise ValueError("base region ID must be a positive integer")
                    mask = grid.get("region_id", region_ids) == binding["region_id"]
                else:
                    mask = polygon_mask([p[:2] for p in row["polygon_xyz"]], grid) & grid["support"]
                regions.append((row, mask))
                region_ids[mask] = len(regions)  # Later regions own overlap, as in legacy batch.
            # Materialized region masks reflect the same overlap rule as the raster.
            regions = [(row, region_ids == i) for i, (row, _) in enumerate(regions, 1)]
            for row in semantics["entities"]:
                if semantic_level(row, records) != lid:
                    continue
                binding = row.get("properties", {}).get("map_project")
                if not binding:
                    continue  # Point semantics may intentionally have no obstacle footprint.
                if not isinstance(binding, dict) or binding.get("level_id") != lid:
                    raise ValueError(f"footprint level differs from entity: {row['id']}")
                if binding.get("mode") == "base_instance":
                    if type(binding.get("instance_id")) is not int or binding["instance_id"] <= 0:
                        raise ValueError("base instance ID must be a positive integer")
                    mask = ((grid.get("instance_id", instance_ids) == binding["instance_id"])
                            & semantic_obstacles)
                elif binding.get("mode") == "polygon":
                    points = binding.get("polygon_xy")
                    if not isinstance(points, list) or any(not isinstance(p, list) or len(p) != 2 for p in points):
                        raise ValueError("footprint polygon requires XY pairs")
                    mask = polygon_mask(points, grid) & semantic_obstacles
                else:
                    raise ValueError("unknown obstacle binding mode")
                if not mask.any():
                    raise ValueError(f"no occupied cells in obstacle selection: {row['id']}")
                if np.any(instance_ids[mask] > 0):
                    raise ValueError(f"obstacle selection overlaps another object: {row['id']}")
                entities.append((row, mask))
                instance_ids[mask] = len(entities)
            output[lid] = {"regions": regions, "entities": entities,
                           "region_id": region_ids, "instance_id": instance_ids}
        return output

    def _expect(self, revision):
        if revision is not None and (type(revision) is not int or revision != self.revision):
            raise ValueError("stale editor revision; reload state before editing")

    def edit(self, operation, *, revision=None, **args):
        self._expect(revision)
        candidate = TopologyDocument.from_dict(self.document.to_dict())
        # The browser is the authoring authority. Delete atomically together
        # with dependent bindings; leave the strict document API unchanged.
        if operation in {"remove_node", "remove_edge", "clear_topology"}:
            nodes = ({row["id"] for row in candidate.nodes} if operation == "clear_topology"
                     else {args["id"]} if operation == "remove_node" else set())
            edges = {row["id"] for row in candidate.edges
                     if operation == "clear_topology" or row["from"] in nodes or row["to"] in nodes
                     or operation == "remove_edge" and row["id"] == args["id"]}
            semantics = candidate.semantics
            if semantics:
                semantics["routes"] = [row for row in semantics["routes"]
                    if not (set(row["node_ids"]) & nodes or set(row["edge_ids"]) & edges)
                    and operation != "clear_topology"]
                for row in semantics["regions"]:
                    row["entrance_node_ids"] = [n for n in row.get("entrance_node_ids", []) if n not in nodes]
                for row in semantics["interaction_poses"]:
                    if row.get("node_id") in nodes:
                        row.pop("node_id", None)
            for row in candidate.nodes:
                if row["id"] in nodes:
                    row.pop("locked", None)
            for row in candidate.edges:
                if row["id"] in edges:
                    row.pop("locked_geometry", None)
        if operation == "remove_semantic" and candidate.semantics:
            collection, identifier = args["collection"], args["id"]
            semantics = candidate.semantics
            if collection in {"regions", "entities"}:
                semantics["interaction_poses"] = [row for row in semantics["interaction_poses"]
                    if row.get("owner_id") != identifier]
            if collection == "regions":
                for row in semantics["entities"]:
                    if row.get("region_id") == identifier:
                        records = {r["id"]: r for key in ("regions", "entities", "interaction_poses")
                                   for r in semantics[key]}
                        level = semantic_level(row, records)
                        row.pop("region_id", None)
                        if level is not None:
                            row["level_id"] = level
        if operation == "add_node":
            candidate.add_node(args["position_xyz"], level_id=args.get("level_id"),
                               label=args.get("label", ""), kind=args.get("kind", "custom"),
                               heading_rad=args.get("heading_rad"), source_ref=args.get("source_ref"))
        elif operation == "move_node":
            candidate.move_node(args["id"], args["position_xyz"])
        elif operation == "update_node":
            row = candidate.node(args["id"])
            if row.get("locked"):
                raise ValueError("locked node cannot be edited")
            for field in ("label", "level_id", "kind", "heading_rad"):
                if field in args:
                    row[field] = args[field]
        elif operation == "remove_node":
            candidate.remove_node(args["id"])
        elif operation == "clear_topology":
            candidate.nodes = []
            candidate.edges = []
        elif operation == "add_edge":
            mode = args.get("traversal_mode", "walk")
            first, last = candidate.node(args["from_id"]), candidate.node(args["to_id"])
            if type(args.get("directed", False)) is not bool:
                raise ValueError("directed must be boolean")
            candidate.add_edge(first["id"], last["id"], [first["position_xyz"], last["position_xyz"]],
                               directed=args.get("directed", False), traversal_mode=mode,
                               required_controller_capabilities=["stair_capable"] if mode == "stairs" else [])
        elif operation == "update_edge":
            if type(args["directed"]) is not bool:
                raise ValueError("directed must be boolean")
            candidate.set_edge_direction(args["id"], args["directed"])
            old = candidate.edge(args["id"])
            caps = set(old.get("required_controller_capabilities", [])) - {"stair_capable"}
            if args["traversal_mode"] == "stairs":
                caps.add("stair_capable")
            candidate.set_edge_traversal(args["id"], args["traversal_mode"], sorted(caps))
        elif operation == "remove_edge":
            candidate.remove_edge(args["id"])
        elif operation == "upsert_semantic":
            record = deepcopy(args["record"])
            # Old exported geometry is derivative, never authoritative after edits.
            candidate.upsert_semantic(args["collection"], record)
        elif operation == "remove_semantic":
            candidate.remove_semantic(args["collection"], args["id"])
        elif operation == "bind_obstacle":
            lid = args["level_id"]
            grid = self.grids[lid]
            mask = polygon_mask(args["polygon_xy"], grid) & grid["semantic_occupied"]
            if not mask.any():
                raise ValueError("圈选范围没有可见语义障碍，请在深灰或灰蓝色障碍上圈选")
            rows = (candidate.semantics or empty_semantics())["entities"]
            record = deepcopy(next((row for row in rows if row["id"] == args["id"]),
                                   {"id": args["id"], "aliases": [], "properties": {}, "source_ref": None}))
            cells = np.argwhere(mask)
            z = next(row["support_z_m"] for row in candidate.levels if row["id"] == lid)
            record.update(label=args["label"], kind=args["kind"], level_id=lid,
                          region_id=args.get("region_id"),
                          position_xyz=[float(grid["x"][cells[:, 1]].mean()),
                                        float(grid["y"][cells[:, 0]].mean()), z])
            record.setdefault("properties", {})["map_project"] = {
                "mode": "polygon", "level_id": lid, "polygon_xy": deepcopy(args["polygon_xy"])}
            record["properties"].pop("geometry_binding", None)
            record["properties"]["annotation_source"] = "manual"
            candidate.upsert_semantic("entities", record)
        else:
            raise ValueError(f"unknown edit operation: {operation}")
        derived = self._check(candidate)
        self._undo.append(self.document.to_dict())
        self._undo = self._undo[-100:]
        self._redo.clear()
        self.document, self._derived = candidate, derived
        self.revision += 1

    def undo(self, *, revision=None):
        self._expect(revision)
        if not self._undo:
            return
        self._redo.append(self.document.to_dict())
        self.document = TopologyDocument.from_dict(self._undo.pop())
        self._derived = None
        self.revision += 1

    def redo(self, *, revision=None):
        self._expect(revision)
        if not self._redo:
            return
        self._undo.append(self.document.to_dict())
        self.document = TopologyDocument.from_dict(self._redo.pop())
        self._derived = None
        self.revision += 1

    def layer_image(self, lid, derived=None):
        grid = self.grids[lid]
        layers = derived or self._derived or self.derive()
        layer = layers[lid]
        rgb = np.full((*grid["state"].shape, 3), [201, 210, 220], dtype=np.uint8)
        rgb[grid["state"] == 0] = [248, 250, 252]
        for row, mask in layer["regions"]:
            rgb[mask & (grid["state"] == 0)] = (np.array(color(row["id"]))*.20 + 255*.80).astype(np.uint8)
        semantic_only = grid["semantic_occupied"] & ~grid["occupied"]
        rgb[semantic_only] = [111, 124, 140]
        rgb[grid["occupied"] | (grid["state"] == 1)] = [61, 72, 85]
        for row, mask in layer["entities"]:
            rgb[mask] = color(row["id"])
        return Image.fromarray(rgb[::-1].copy())

    def state(self):
        levels = []
        for level in self.document.levels:
            grid = self.grids[level["id"]]
            levels.append({**level, "extent_xy": extent(grid),
                           "resolution_m": float(grid["resolution_m"]),
                           "width": len(grid["x"]), "height": len(grid["y"]),
                           "image_url": "/api/layer?level=" + quote(level["id"], safe="") + f"&revision={self.revision}"})
        warnings = ["路线为人工/几何草稿，尚未验证机器人净空、动力学和楼梯通行能力。",
                    "深灰为导航障碍，灰蓝为仅用于语义标注的高处/非导航障碍；修改语义不会改变几何栅格。"]
        for level in levels:
            if not np.any(self.grids[level["id"]]["state"] == 0):
                warnings.append(f"{level['id']}：底图没有已知自由格，几何仍需确认。")
        return {"revision": self.revision, "dirty": self.dirty, "scene_key": self.document.scene_key,
                "project_path": str(self.root), "document": self.document.to_dict(), "levels": levels,
                "warnings": warnings, "saved_revision": self.saved_revision,
                "can_undo": bool(self._undo), "can_redo": bool(self._redo)}

    def _preview(self, lid, derived):
        """Portable annotated PNG, with the same axes and masks as browser view."""
        grid = self.grids[lid]
        image = self.layer_image(lid, derived)
        scale = min(4., 1400/max(image.size))
        image = image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale))), Image.Resampling.NEAREST)
        margin = 50
        canvas = Image.new("RGB", (image.width+2*margin, image.height+100), "#f0f4f8")
        canvas.paste(image, (margin, margin))
        draw = ImageDraw.Draw(canvas)
        fontpath = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
        font = ImageFont.truetype(str(fontpath), 14) if fontpath.exists() else ImageFont.load_default()
        xmin, xmax, ymin, ymax = extent(grid)
        def xy(point):
            return (margin+(point[0]-xmin)/(xmax-xmin)*image.width,
                    margin+(ymax-point[1])/(ymax-ymin)*image.height)
        nodes = {row["id"]: row for row in self.document.nodes}
        for edge in self.document.edges:
            a, b = nodes[edge["from"]], nodes[edge["to"]]
            if a.get("level_id") != lid or b.get("level_id") != lid:
                continue
            draw.line([xy(a["position_xyz"]), xy(b["position_xyz"])], fill="#258468", width=2)
        for node in nodes.values():
            if node.get("level_id") == lid:
                px, py = xy(node["position_xyz"])
                draw.ellipse((px-3, py-3, px+3, py+3), fill="#116949")
        labels = []
        for group in ("regions", "entities"):
            for row, mask in derived[lid][group]:
                cells = np.argwhere(mask)
                if len(cells):
                    labels.append((row, [float(grid["x"][cells[:,1]].mean()), float(grid["y"][cells[:,0]].mean())]))
        records = {row["id"]: row for key in ("regions", "entities", "interaction_poses")
                   for row in (self.document.semantics or empty_semantics())[key]}
        for row in (self.document.semantics or empty_semantics())["interaction_poses"]:
            if semantic_level(row, records) == lid:
                labels.append((row, row["position_xyz"]))
                px, py = xy(row["position_xyz"])
                draw.ellipse((px-5, py-5, px+5, py+5), fill="#cb3c86")
        bound = {row["id"] for row, _ in derived[lid]["entities"]}
        for row in (self.document.semantics or empty_semantics())["entities"]:
            if row["id"] not in bound and semantic_level(row, records) == lid and row.get("position_xyz"):
                labels.append((row, row["position_xyz"]))
                px, py = xy(row["position_xyz"])
                draw.rectangle((px-4, py-4, px+4, py+4), fill=color(row["id"]))
        for row, position in labels:
            point = xy(position)
            label = row.get("label") or row["id"]
            bbox = draw.textbbox(point, label, font=font)
            draw.rectangle(bbox, fill="white")
            draw.text(point, label, fill=color(row["id"]), font=font)
        draw.text((margin, 12), f"{self.document.scene_key} / {lid}", fill="#21364b", font=font)
        draw.text((margin, image.height+60), "X →  Y ↑  / metres / routes: unvalidated draft", fill="#21364b", font=font)
        return canvas

    def _export(self, destination, revision_id, derived):
        destination.mkdir(parents=True, exist_ok=False)
        export_doc = TopologyDocument.from_dict(self.document.to_dict())
        semantics = export_doc.semantics or empty_semantics()
        doc_entities = {row["id"]: row for row in semantics["entities"]}
        doc_regions = {row["id"]: row for row in semantics["regions"]}
        for row in semantics["entities"]:
            row.setdefault("properties", {}).pop("geometry_binding", None)
        features, objects, level_info = [], [], []
        for index, (lid, grid) in enumerate(self.grids.items()):
            base = f"level-{index:03d}"
            gridname = base+"-semantic-grid.npz"
            data = {**grid, "instance_id": derived[lid]["instance_id"], "region_id": derived[lid]["region_id"]}
            np.savez_compressed(destination / gridname, **data)
            for group in ("regions", "entities"):
                for number, (row, mask) in enumerate(derived[lid][group], 1):
                    geometry, runs = mask_geometry(mask, grid)
                    properties = {"id": row["id"], "label": row.get("label", ""), "kind": row["kind"],
                                  "level_id": lid, "feature_type": "object_instance" if group == "entities" else "room_region",
                                  "raster_id": number, "cell_count": int(mask.sum()),
                                  "confidence": row.get("properties", {}).get("confidence", "manual_unverified")}
                    features.append({"type": "Feature", "geometry": geometry, "properties": properties})
                    if group == "entities":
                        doc_entities[row["id"]].setdefault("properties", {})["geometry_binding"] = {
                            "raster_file": gridname, "instance_id": number, "footprint_geojson": geometry}
                        record = {**deepcopy(doc_entities[row["id"]]), **properties, "instance_id": number,
                                  "mask_rle_row_start_end": runs, "footprint_geojson": geometry, "raster_file": gridname}
                        objects.append(record)
                    else:
                        doc_regions[row["id"]].setdefault("properties", {})["raster_region_id"] = number
            self.layer_image(lid, derived).save(destination / (base+"-layer.png"))
            self._preview(lid, derived).save(destination / (base+"-semantic-map.png"))
            level_info.append({"level_id": lid, "grid": gridname, "preview": base+"-semantic-map.png",
                               "layer": base+"-layer.png", "extent_xy": extent(grid)})
        # Point-only objects and task poses are also included in GeoJSON.
        bound_ids = {row["id"] for row in objects}
        for group in ("entities", "interaction_poses"):
            for row in semantics[group]:
                if row["id"] not in bound_ids:
                    geometry = {"type": "Point", "coordinates": row["position_xyz"]} if row.get("position_xyz") else None
                    features.append({"type": "Feature", "geometry": geometry,
                                     "properties": {**deepcopy(row), "feature_type": group}})
        for edge in export_doc.edges:
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [
                export_doc.node(edge["from"])["position_xyz"], export_doc.node(edge["to"])["position_xyz"]]},
                "properties": {**deepcopy(edge), "feature_type": "topology_edge", "robot_validated": False}})
        save_draft(destination / "topology.json", export_doc)
        write_json(destination / "semantic-footprints.geojson", {"type": "FeatureCollection", "features": features})
        write_json(destination / "semantic-footprints.json", {
            "schema": "eai.semantic_footprints", "version": 1, "scene_id": export_doc.scene_key,
            "project_revision": revision_id, "coordinate_frame": "scene_world", "objects": objects,
            "semantics": semantics, "robot_validated": False,
            "visual_review": self.manifest.get("provenance", {}).get("semantic-footprints.json", {}).get("visual_review", {})})
        files = {path.name: sha256(path) for path in sorted(destination.iterdir()) if path.is_file()}
        write_json(destination / "manifest.json", {"schema": "eai.semantic_map.revision", "version": 1,
                   "revision": revision_id, "scene_key": export_doc.scene_key, "levels": level_info,
                   "files": files, "robot_validated": False})
        for path in destination.iterdir():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return export_doc

    def save(self, *, revision=None):
        self._expect(revision)
        derived = self._check(self.document)
        with (self.root / ".write.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            manifest_path = self.root / "project.json"
            actual = sha256(manifest_path) if manifest_path.exists() else None
            if actual != self._manifest_digest:
                raise ValueError("project changed in another process; reopen before saving")
            for entry in self.manifest["base_grids"]:
                if sha256(child(self.root, entry["file"])) != entry["sha256"]:
                    raise ValueError("base geometry changed outside tool")
            revision_id = uuid.uuid4().hex
            relative = f"revisions/{revision_id}"
            export_doc = self._export(self.root / relative, revision_id, derived)
            manifest = {**self.manifest, "current_revision": relative}
            write_json(manifest_path, manifest)
            self.manifest = manifest
            self.saved_revision = relative
            self._manifest_digest = sha256(manifest_path)
            self.document = export_doc
            self._saved_document = export_doc.to_dict()
            self._derived = derived
            self.revision += 1
        return self.root / relative


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="import an existing scene map without modifying it")
    imp.add_argument("--source", type=Path, required=True)
    imp.add_argument("--project", type=Path, required=True)
    batch = sub.add_parser("import-batch", help="import a scene batch as an editable project catalog")
    batch.add_argument("--source", type=Path, required=True)
    batch.add_argument("--catalog", type=Path, required=True)
    new = sub.add_parser("create", help="create an unknown blank map (no invented free space)")
    new.add_argument("--project", type=Path, required=True)
    new.add_argument("--scene-key", required=True)
    new.add_argument("--bounds", nargs=4, type=float, required=True, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    new.add_argument("--resolution", type=float, default=.1)
    new.add_argument("--levels", type=Path, help="JSON array of id, label, support_z_m")
    check = sub.add_parser("validate")
    check.add_argument("--project", type=Path, required=True)
    edit = sub.add_parser("edit", help="apply a JSON command and publish one coherent revision")
    edit.add_argument("--project", type=Path, required=True)
    edit.add_argument("--command-file", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "import-batch":
        catalog = args.catalog.resolve()
        if catalog.exists():
            raise ValueError("catalog directory must be new")
        batch = read_json(args.source / "batch-manifest.json")
        scenes = batch.get("scenes")
        if not isinstance(scenes, dict) or not scenes:
            raise ValueError("source batch has no scenes")
        catalog.mkdir(parents=True)
        report = {"schema": "eai.semantic_map.catalog", "version": 1,
                  "source": str(args.source.resolve()), "projects": [], "failures": {}}
        for scene in scenes:
            try:
                source = child(args.source, scene)
                target = child(catalog, scene)
                project = MapProject.import_scene(source, target)
                restored = MapProject(target)
                if restored.document.to_dict() != project.document.to_dict():
                    raise ValueError("reopened document differs from saved document")
                for lid, grid in restored.grids.items():
                    original = load_grid(child(source, f"{lid}-semantic-grid.npz"))
                    for key in ("occupied", "support", "state"):
                        if not np.array_equal(grid[key], original[key]):
                            raise ValueError(f"import changed geometry: {lid}/{key}")
                report["projects"].append({"id": scene, "scene_key": project.document.scene_key,
                                            "levels": len(project.grids), "revision": project.saved_revision})
                print(f"Imported {scene} ({len(project.grids)} levels)", flush=True)
            except (ValueError, KeyError, OSError) as exc:
                report["failures"][scene] = str(exc)
                print(f"FAILED {scene}: {exc}", flush=True)
        report["complete"] = not report["failures"] and len(report["projects"]) == len(scenes)
        write_json(catalog / "catalog.json", report)
        print(json.dumps({"complete": report["complete"], "projects": len(report["projects"]),
                          "failures": report["failures"]}, ensure_ascii=False))
        return 0 if report["complete"] else 1
    if args.command == "import":
        project = MapProject.import_scene(args.source, args.project)
    elif args.command == "create":
        project = MapProject.create_blank(args.project, args.scene_key, args.bounds, args.resolution,
                                          read_json(args.levels) if args.levels else None)
    else:
        project = MapProject(args.project)
        if args.command == "edit":
            command = read_json(args.command_file)
            operation = command.pop("operation")
            project.edit(operation, **command)
            project.save()
    print(json.dumps({"ok": True, "project": str(project.root), "scene": project.document.scene_key,
                      "revision": project.saved_revision, "levels": len(project.grids)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
