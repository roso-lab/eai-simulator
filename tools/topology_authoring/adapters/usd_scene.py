"""Descriptor-driven adapter for arbitrary static, metre/Z-up USD scenes."""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path

from ..adapter import SceneSource, SemanticTarget
from ..surfaces import SupportSurface


class UsdSceneAdapter:
    def __init__(self, scene_key="usd_scene"):
        self.scene_key = scene_key
        self.active_level_id = None
        self._surface = None
        self._stage = None
        self._source = None
        self._geometry_files = {}
        self._view_stage = None
        self._view_visibility = {}

    def load_source(self, asset_root):
        from ..product import _unique_object, _reject_constant
        root = Path(asset_root).resolve()
        config = root / "scene.json"
        data = json.loads(config.read_text(encoding="utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        if (not isinstance(data, dict) or data.get("schema") != "eai.topology.usd_scene"
                or type(data.get("version")) is not int or data["version"] != 1):
            raise ValueError("scene.json must use eai.topology.usd_scene version 1")
        if data.get("scene_key", self.scene_key) != self.scene_key:
            raise ValueError("descriptor scene_key differs from requested scene")
        if not isinstance(data.get("usd_path"),str) or not data["usd_path"].strip():
            raise ValueError("descriptor usd_path must be a nonempty path")
        usd_path = Path(data["usd_path"])
        data = dict(data, usd_path=str((root / usd_path).resolve()), descriptor_path=str(config))
        if not Path(data["usd_path"]).is_file():
            raise ValueError("descriptor USD does not exist")
        from ..model import TopologyDocument
        from algorithm.skills.navigation.topology import validate_ground_topology
        draft = TopologyDocument(self.scene_key)
        draft.set_levels(data.get("levels"))
        errors = validate_ground_topology(draft)
        if errors:
            raise ValueError("; ".join(errors))
        def paths(value,label):
            if (not isinstance(value,list) or any(not isinstance(p,str) or not p.startswith("/")
                    or p=="/" or "//" in p or any(s in {".",".."} for s in p.split("/")) for p in value)):
                raise ValueError(f"{label} must be an array of absolute USD prim paths")
        for key in ("support_mesh_paths","runtime_view_hide_paths"):
            if key in data:
                paths(data[key],key)
        levels={row["id"]:row["support_z_m"] for row in data["levels"]}
        for key in ("level_view_hide_paths","level_view_cut_z_m"):
            values=data.get(key,{})
            if not isinstance(values,dict) or set(values)-set(levels):
                raise ValueError(f"{key} must map declared level IDs")
            for level,value in values.items():
                if key=="level_view_hide_paths":
                    paths(value,key)
                elif (isinstance(value,bool) or not isinstance(value,(int,float)) or
                      not math.isfinite(value) or value <= levels[level]):
                    raise ValueError("cutaway height must be finite and above level support")
        camera=data.get("camera",{})
        if not isinstance(camera,dict):
            raise ValueError("camera must be an object")
        from algorithm.skills.navigation.topology import finite_xyz
        for key in ("eye","target"):
            if key in camera and not finite_xyz(camera[key]):
                raise ValueError(f"camera {key} must be finite XYZ")
        source = SceneSource(self.scene_key, root, data)
        self.semantic_targets(source)
        return source

    def _geometry(self, source):
        def signature(path):
            stat=Path(path).stat()
            return stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size
        changed = any(signature(path) != stamp for path,stamp in self._geometry_files.items())
        if self._source != source or changed:
            from pxr import Usd
            stage = Usd.Stage.Open(source.payload["usd_path"])
            if stage is None:
                raise ValueError("cannot open scene USD")
            # OpenUSD may return existing cached layers for an on-disk path.
            # Refresh them when the file signature changed before extracting.
            if changed:
                stage.Reload()
            self._surface = SupportSurface.from_stage(stage, mesh_paths=source.payload.get("support_mesh_paths", ()))
            self._stage, self._source = stage, source
            self._geometry_files = {layer.realPath:signature(layer.realPath)
                                    for layer in stage.GetUsedLayers() if layer.realPath}
        return self._surface

    def semantic_targets(self, source):
        rows = source.payload.get("semantic_targets", [])
        if not isinstance(rows, list):
            raise ValueError("semantic_targets must be an array")
        targets, refs = [], set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("ref"), str)
                    or ":" not in row["ref"] or row["ref"] in refs
                    or not isinstance(row.get("label"), str)):
                raise ValueError("invalid/duplicate semantic target")
            point = row.get("position_xyz")
            if point is not None:
                from algorithm.skills.navigation.topology import finite_xyz
                if not finite_xyz(point):
                    raise ValueError("semantic target position must be finite XYZ")
                point = tuple(point)
            refs.add(row["ref"])
            targets.append(SemanticTarget(row["ref"], row["label"], row.get("kind", "target_entrance"), point))
        return targets

    def source_digests(self, source):
        self._geometry(source)
        inventory = {"scene_descriptor": hashlib.sha256(Path(source.payload["descriptor_path"]).read_bytes()).hexdigest()}
        layers = sorted((Path(layer.realPath) for layer in self._stage.GetUsedLayers() if layer.realPath), key=str)
        for index, path in enumerate(layers):
            inventory[f"usd_layer_{index:04d}"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return inventory

    def load_stage(self, source):
        import omni.usd
        if not omni.usd.get_context().open_stage(source.payload["usd_path"]):
            raise RuntimeError("cannot load authoring stage")
        # All authoring overlays/visibility edits are confined to a new session
        # sublayer; the original USD and its physical collision remain unchanged.
        from pxr import Sdf, Usd
        stage = omni.usd.get_context().get_stage()
        for path in ("/World/TopologyCutawayPreview", "/World/TopologyAuthoringOverlay"):
            if stage.GetPrimAtPath(path):
                raise ValueError(f"source uses reserved authoring prim path: {path}")
        layer = Sdf.Layer.CreateAnonymous("topology_authoring")
        stage.GetSessionLayer().subLayerPaths.append(layer.identifier)
        stage.SetEditTarget(Usd.EditTarget(layer))

    def close(self):
        """Release standalone USD handles before Kit unloads its USD plugins."""
        self._view_stage = self._stage = None
        self._surface = self._source = None
        self._geometry_files.clear()
        self._view_visibility.clear()

    def set_active_level(self, level_id, source):
        if level_id is not None and level_id not in {row["id"] for row in source.payload["levels"]}:
            raise ValueError("unknown active level")
        self.active_level_id = level_id

    def set_level_view(self, level_id, source):
        self.set_active_level(level_id, source)
        import omni.usd
        from pxr import UsdGeom, Vt
        stage = omni.usd.get_context().get_stage()
        mapping = source.payload.get("level_view_hide_paths", {})
        hidden = set(mapping.get(level_id, ())) if level_id is not None else set()
        cut = source.payload.get("level_view_cut_z_m", {}).get(level_id)
        if stage is not self._view_stage:
            self._view_stage, self._view_visibility = stage, {}
        # A separate, untextured collision-geometry preview makes levels usable
        # even when the source has one mesh spanning several floors. Original
        # meshes are hidden only, never edited, and support/collision stay intact.
        preview_path = "/World/TopologyCutawayPreview"
        if stage.GetPrimAtPath(preview_path):
            stage.RemovePrim(preview_path)
        if cut is not None:
            import numpy as np
            if isinstance(cut,bool) or not isinstance(cut,(int,float)) or not math.isfinite(cut):
                raise ValueError("level cutaway height must be finite")
            floor = next(row["support_z_m"] for row in source.payload["levels"] if row["id"] == level_id)
            if cut <= floor:
                raise ValueError("cutaway height must be above level support")
            surface = self._geometry(source)
            tri = surface.points[surface.triangles]
            keep = (tri[:,:,2].max(axis=1) <= cut) & (tri[:,:,2].min(axis=1) >= floor-.15)
            selected = surface.triangles[keep]
            if not len(selected):
                raise ValueError("level cutaway contains no triangles")
            for prim in stage.Traverse():
                if prim.IsA(UsdGeom.Mesh) and not str(prim.GetPath()).startswith("/World/TopologyAuthoringOverlay"):
                    hidden.add(str(prim.GetPath()))
            mesh = UsdGeom.Mesh.Define(stage,preview_path)
            mesh.CreatePointsAttr().Set(Vt.Vec3fArray.FromNumpy(surface.points.astype("float32")))
            mesh.CreateFaceVertexCountsAttr().Set([3]*len(selected))
            mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(selected.astype("int32").reshape(-1)))
            mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
            mesh.CreateDisplayColorAttr().Set([(0.66,0.71,0.77)])
            mesh.CreateDoubleSidedAttr().Set(True)
        for path in hidden | set(self._view_visibility):
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsA(UsdGeom.Imageable):
                raise ValueError(f"invalid authoring visibility path: {path}")
            visibility = UsdGeom.Imageable(prim).CreateVisibilityAttr()
            if path not in self._view_visibility:
                self._view_visibility[path] = visibility.Get()
            visibility.Set(UsdGeom.Tokens.invisible if path in hidden else self._view_visibility[path])

    def project_to_support_surface(self, position, source):
        point = list(position)
        if self.active_level_id is not None:
            point[2] = next(row["support_z_m"] for row in source.payload["levels"] if row["id"] == self.active_level_id)
        return self._geometry(source).nearest(point)

    def validate_polyline(self, polyline, profile, source):
        # This is support continuity only. Traversability/clearance belongs to
        # scene authoring and native physical validation, never a mesh screenshot.
        del profile
        surface = self._geometry(source)
        for a, b in zip(polyline, polyline[1:]):
            steps = max(1, math.ceil(math.dist(a, b) / .10))
            for i in range(steps + 1):
                point = [a[j] + (b[j] - a[j]) * i / steps for j in range(3)]
                try:
                    surface.nearest(point, max_distance_m=.20)
                except ValueError:
                    return ["node segment has no continuous support within 0.20 m of its XYZ path"]
        return []
