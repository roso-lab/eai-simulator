#!/usr/bin/env python3
"""Convert an Isaac Sim USD scene into a Gazebo Classic 11 asset.

Part of the `usd-to-gazebo` agent skill (tools/agent_skill/usd-to-gazebo).
Read the SKILL.md workflow before changing the conversion logic; several
behaviors encode empirically verified gazebo11 facts (raw Collada
coordinates, 6-value RPY poses, lambert-wrapped effects, triangle counts).

Usage:
    python usd_to_gazebo.py --source usd/scene/factory --scene factory \
        --out usd/scene/factory_gazebo [--obj-out DIR] [--no-collisions]

Requires the pip packages `usd-core` and `numpy` (no Isaac Sim needed).
Outputs:  <out>/world.sdf, <out>/models/<name>/{model.config, model.sdf,
meshes/*.dae, materials/{scripts/*.material,textures/*}},
<out>/conversion_manifest.json.

Coordinate handling: the source scene must be Z-up meters (checked at
startup); gazebo11 loads Collada data unmodified (verified empirically),
so geometry keeps its raw coordinates and SDF poses use the USD world
transforms. Every emitted <pose> is 6-value RPY because gazebo11's
sdformat9 silently misparses 7-value quaternion poses.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
from collections import defaultdict

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

SDF_VERSION = "1.6"
# NOTE: gazebo11's sdformat9 parses <pose> strictly as 6 values
# "x y z roll pitch yaw" -- the 7-value quaternion form is a gz-sim-only
# extension and silently corrupts orientations on gazebo11.

# ---------------------------------------------------------------------------
# small helpers


def sanitize(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return name[:80] if name else "mesh"


def xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def fmt(v: float) -> str:
    return f"{float(v):.7g}"


def listp(x):
    return list(x)


def effectively_active(prim: Usd.Prim) -> bool:
    """A prim is rendered only if it and every ancestor are active."""
    p = prim
    while p is not None and p.GetPath() != "/":
        if not p.IsActive():
            return False
        p = p.GetParent()
    return True


# ---------------------------------------------------------------------------
# material resolution (MDL + UsdPreviewSurface)

TEX_PARAM_PRIORITY = [
    "diffuse_texture",
    "AlbedoTexture",
    "BaseColor",
    "albedo_sup",
    "albedo_texture",
    "DiffuseTexture",
    "Emission",
    "diffuse_texture_0",
]
COLOR_PARAM_PRIORITY = [
    "diffuse_color_constant",
    "ColorAlbedo",
    "diffuse_tint",
    "glass_color",
    "Color",
    "BaseColor_Tint",
    "base_color",
    "body_color",
]

FALLBACK_COLORS = {
    "Chrome": (0.55, 0.55, 0.55),
    "Silver": (0.55, 0.55, 0.55),
    "Steel_Cast": (0.463, 0.444, 0.444),
}


class MaterialInfo:
    def __init__(self):
        self.texture: str | None = None  # absolute source path of albedo texture
        self.color: tuple | None = None  # rgb 0..1
        self.transparent = False
        self.emissive = False
        self.warnings: list[str] = []


class MdlLibrary:
    """Parses local MDL module files for parameter defaults."""

    _tex_re = re.compile(r'uniform\s+texture_2d\s+(\w+)\s*=\s*texture_2d\(\s*"([^"]+)"')
    _color_re = re.compile(
        r"\bcolor\s+(\w+)\s*=\s*color\(\s*([-+0-9.eEf]+)f?\s*,\s*([-+0-9.eEf]+)f?\s*,\s*([-+0-9.eEf]+)f?"
    )
    _f4_re = re.compile(r"\bfloat4\s+(\w+)\s*=\s*float4\(\s*([-+0-9.eEf]+)f?\s*,\s*([-+0-9.eEf]+)f?\s*,\s*([-+0-9.eEf]+)f?")
    _f_re = re.compile(r"\b(?:uniform\s+)?float\s+(\w+)\s*=\s*([-+0-9.eEf]+)")

    def __init__(self):
        self._cache: dict[str, dict] = {}

    @staticmethod
    def _mf(s: str) -> float:
        s = re.sub(r"[fF]$", "", s.strip())
        if s in ("", ".", "-", "+", "-.", "+."):
            raise ValueError(s)
        return float(s)

    def module_params(self, mdl_path: str) -> dict:
        """Returns {subIdentifier: {param_name: ("tex"|"color"|"float", value)}}"""
        if mdl_path in self._cache:
            return self._cache[mdl_path]
        result: dict[str, dict] = {}
        try:
            text = open(mdl_path, encoding="utf-8", errors="replace").read()
        except OSError:
            self._cache[mdl_path] = result
            return result
        starts = [(m.start(), m.group(1)) for m in re.finditer(r"export\s+material\s+(\w+)\s*\(", text)]
        for idx, (start, name) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(text)
            block = text[start:end]
            params: dict[str, tuple] = {}
            for m in self._tex_re.finditer(block):
                params[m.group(1)] = ("tex", m.group(2))
            for m in self._color_re.finditer(block):
                try:
                    params[m.group(1)] = ("color", (self._mf(m.group(2)), self._mf(m.group(3)), self._mf(m.group(4))))
                except ValueError:
                    pass
            for m in self._f4_re.finditer(block):
                try:
                    params.setdefault(
                        m.group(1), ("color", (self._mf(m.group(2)), self._mf(m.group(3)), self._mf(m.group(4))))
                    )
                except ValueError:
                    pass
            for m in self._f_re.finditer(block):
                try:
                    params.setdefault(m.group(1), ("float", self._mf(m.group(2))))
                except ValueError:
                    pass
            result[name] = params
        self._cache[mdl_path] = result
        return result


def _connected_texture_file(shader_prim: Usd.Prim, input_name: str) -> str | None:
    """Follows input connection one hop to a UsdUVTexture node's file."""
    attr = shader_prim.GetAttribute(f"inputs:{input_name}")
    if not attr or not attr.HasAuthoredValue():
        # connection without authored value still counts
        if not attr:
            return None
    for source, _name, _type in attr.GetConnections() and [attr.GetValueSources()[0]] if attr.GetValueSources() else []:
        src_prim = source.sourcePrim
        if src_prim.GetTypeName() == "Shader":
            file_attr = src_prim.GetAttribute("inputs:file")
            if file_attr:
                v = file_attr.Get()
                if isinstance(v, Sdf.AssetPath):
                    return getattr(v, "resolvedPath", None) or v.path
    return None


class MaterialResolver:
    def __init__(self, factory_root: str, stage: Usd.Stage):
        self.root = factory_root
        self.mdl = MdlLibrary()
        self.stage = stage
        self._cache: dict[str, MaterialInfo] = {}

    def resolve(self, material_path: str) -> MaterialInfo:
        if material_path in self._cache:
            return self._cache[material_path]
        info = MaterialInfo()
        prim = self.stage.GetPrimAtPath(material_path)
        shader = None
        if prim:
            for c in prim.GetChildren():
                if c.GetTypeName() == "Shader":
                    shader = c
        overrides: dict[str, object] = {}
        src_asset, sub = None, None
        preview_id = None
        if shader is not None:
            id_attr = shader.GetAttribute("info:id")
            if id_attr and id_attr.HasValue():
                preview_id = str(id_attr.Get())
            for attr in shader.GetAttributes():
                n = attr.GetName()
                if n == "info:mdl:sourceAsset" and attr.HasValue():
                    v = attr.Get()
                    src_asset = getattr(v, "resolvedPath", None) or str(v)
                elif n == "info:mdl:sourceAsset:subIdentifier" and attr.HasValue():
                    sub = str(attr.Get())
                elif n.startswith("inputs:"):
                    overrides[n[7:]] = attr.Get()

        if preview_id == "UsdPreviewSurface":
            self._resolve_preview(shader, overrides, info)
            self._cache[material_path] = info
            return info

        sub = sub or (os.path.splitext(os.path.basename(src_asset))[0] if src_asset else "")

        params: dict[str, tuple] = {}
        if src_asset and os.path.isfile(src_asset):
            for pname, val in self.mdl.module_params(src_asset).get(sub, {}).items():
                params[pname] = val
        elif src_asset:
            info.warnings.append(f"MDL module not found locally: {src_asset} (sub={sub})")

        def override_value(pname: str):
            if pname not in overrides:
                return None
            v = overrides[pname]
            if isinstance(v, Sdf.AssetPath):
                return ("tex", v)
            if isinstance(v, (Gf.Vec3f, Gf.Vec4d, Gf.Vec4f)):
                return ("color", (v[0], v[1], v[2]))
            if isinstance(v, float):
                return ("float", v)
            return None

        # 1) albedo texture
        for pname in TEX_PARAM_PRIORITY:
            ov = override_value(pname)
            cand = ov if ov is not None else params.get(pname)
            if cand and cand[0] == "tex":
                path = self._texture_path(cand[1], src_asset)
                if path:
                    info.texture = path
                    break
        if info.texture is None:
            for pname, cand in sorted(params.items()):
                if cand[0] == "tex" and any(k in pname.lower() for k in ("albedo", "basecolor", "diffuse")):
                    path = self._texture_path(cand[1], src_asset)
                    if path:
                        info.texture = path
                        break

        # 2) color
        for pname in COLOR_PARAM_PRIORITY:
            ov = override_value(pname)
            cand = ov if ov is not None else params.get(pname)
            if cand and cand[0] == "color":
                info.color = cand[1]
                break

        # 3) transparency / emissive
        def flt(pname):
            ov = override_value(pname)
            if ov is not None:
                return ov[1]
            v = params.get(pname)
            return v[1] if v and v[0] == "float" else None

        glassy = sub and any(k in sub.lower() for k in ("glass", "plastic_clear"))
        if flt("enable_opacity") or glassy:
            info.transparent = True
        if (flt("EmissionIntensity") or 0) > 0 or "Emission" in overrides:
            info.emissive = True

        # 4) fallbacks
        if info.texture is None and info.color is None:
            if sub in FALLBACK_COLORS:
                info.color = FALLBACK_COLORS[sub]
            else:
                info.color = (0.5, 0.5, 0.5)
                info.warnings.append(f"no texture/color resolved for {material_path} (sub={sub})")
        self._cache[material_path] = info
        return info

    def _resolve_preview(self, shader: Usd.Prim, overrides: dict, info: MaterialInfo):
        """UsdPreviewSurface: diffuseColor color or connected UsdUVTexture file."""
        def col(name):
            v = overrides.get(name)
            if isinstance(v, (Gf.Vec3f, Gf.Vec4d, Gf.Vec4f)):
                return (v[0], v[1], v[2])
            a = shader.GetAttribute(f"inputs:{name}")
            if a:
                w = a.Get()
                if isinstance(w, (Gf.Vec3f, Gf.Vec4d, Gf.Vec4f)):
                    return (w[0], w[1], w[2])
            return None

        tex = _connected_texture_file(shader, "diffuseColor")
        if tex:
            info.texture = self._texture_path(tex, None)
        if info.texture is None:
            info.color = col("diffuseColor") or (0.5, 0.5, 0.5)
        op = overrides.get("opacity")
        if isinstance(op, float):
            if op < 0.999:
                info.transparent = True
                info.opacity = max(op, 0.05)
        if (col("emissiveColor") or (0, 0, 0)) != (0, 0, 0):
            info.emissive = True

    def _texture_path(self, value, src_asset: str | None) -> str | None:
        if isinstance(value, Sdf.AssetPath):
            resolved = value.resolvedPath
            authored = value.path
        else:
            resolved, authored = None, str(value)
        if resolved and os.path.isfile(resolved):
            return self._udim(resolved)
        if os.path.isfile(authored):
            return self._udim(authored)
        base = authored.replace("./", "")
        anchors = [self.root, os.path.join(self.root, "SubUSDs")]
        if src_asset:
            anchors.append(os.path.dirname(src_asset))
        for a in anchors:
            p = os.path.normpath(os.path.join(a, base))
            if os.path.isfile(p):
                return self._udim(p)
        return None

    @staticmethod
    def _udim(path: str) -> str | None:
        if "<UDIM>" not in path:
            return path
        for tile in range(1001, 1100):
            p = path.replace("<UDIM>", str(tile))
            if os.path.isfile(p):
                return p
        return None


# ---------------------------------------------------------------------------
# mesh extraction


class MeshData:
    def __init__(self, name: str):
        self.name = name
        self.positions: list = []
        self.uvs: list = []
        self.groups: list = []  # (material_path_or_None, [idx pairs...])
        self.bbox_min = (1e30,) * 3
        self.bbox_max = (-1e30,) * 3


def _primvar_corner_values(pv: UsdGeom.Primvar, n_total_corners: int, n_values: int):
    vals = pv.Get()
    if vals is None or len(vals) == 0:
        return None
    comp = len(vals[0]) if hasattr(vals[0], "__len__") else 1
    flat = np.asarray(vals, dtype=np.float64).reshape(-1, comp)
    idx = pv.GetIndices()
    if idx is not None and len(idx) > 0:
        idx = np.asarray(idx, dtype=np.int64)
    else:
        idx = np.arange(n_values, dtype=np.int64)
    interp = pv.GetInterpolation()
    if interp in (UsdGeom.Tokens.vertex, UsdGeom.Tokens.varying):
        return ("vertex", flat, idx)
    if len(idx) != n_total_corners:
        idx = np.resize(idx, n_total_corners)
    return ("faceVarying", flat, idx)


def extract_mesh(mesh_prim: Usd.Prim, name: str) -> MeshData | None:
    mesh = UsdGeom.Mesh(mesh_prim)
    points = mesh.GetPointsAttr().Get()
    fvc = mesh.GetFaceVertexCountsAttr().Get()
    fvi = mesh.GetFaceVertexIndicesAttr().Get()
    if not points or not fvc or not fvi:
        return None
    pts = np.asarray(points, dtype=np.float64)
    fvc = np.asarray(fvc, dtype=np.int64)
    fvi = np.asarray(fvi, dtype=np.int64)
    n_total_corners = int(fvc.sum())
    if len(fvi) != n_total_corners:
        fvi = np.resize(fvi, n_total_corners)

    st = UsdGeom.PrimvarsAPI(mesh_prim).GetPrimvar("st")
    uv_mode, uv_flat, uv_idx = (None, None, None)
    if st is not None:
        r = _primvar_corner_values(st, n_total_corners, len(points))
        if r is not None:
            uv_mode, uv_flat, uv_idx = r

    md = MeshData(name)
    md.positions = pts.reshape(-1)
    pos_count = len(pts)
    if uv_flat is not None:
        md.uvs = uv_flat.reshape(-1)
        n_uv = len(uv_flat)
    else:
        n_uv = pos_count

    face_mat = {}
    base_binding = UsdShade.MaterialBindingAPI(mesh_prim).GetDirectBinding()
    base_mat = str(base_binding.GetMaterialPath()) if base_binding and base_binding.GetMaterialPath() else None
    subs = UsdGeom.Subset.GetAllGeomSubsets(mesh)
    for s in subs:
        b = UsdShade.MaterialBindingAPI(s.GetPrim()).GetDirectBinding()
        mp = str(b.GetMaterialPath()) if b and b.GetMaterialPath() else base_mat
        idx = s.GetIndicesAttr().Get()
        if idx:
            for f in idx:
                face_mat[int(f)] = mp
    groups: dict[str | None, list] = defaultdict(list)

    corner = 0
    for face, count in enumerate(fvc):
        mat = face_mat.get(face, base_mat)
        corners = [fvi[corner + k] for k in range(count)]
        if uv_mode == "faceVarying":
            uv_corners = [int(uv_idx[corner + k]) for k in range(count)]
        elif uv_mode == "vertex":
            uv_corners = corners
        else:
            uv_corners = corners
        for k in range(1, count - 1):
            tri = (0, k, k + 1)
            for t in tri:
                groups[mat].append(int(corners[t]))
                groups[mat].append(int(uv_corners[t]) if uv_corners is not None else 0)
        corner += count

    if uv_flat is None:
        md.uvs = np.zeros((pos_count, 2), dtype=np.float64).reshape(-1)
    for mat, indices in groups.items():
        md.groups.append((mat, indices))

    mn = pts.min(axis=0)
    mx = pts.max(axis=0)
    md.bbox_min = tuple(mn)
    md.bbox_max = tuple(mx)
    return md


# ---------------------------------------------------------------------------
# collada / obj writing


def write_collada(path: str, model_name: str, md: MeshData, material_entries, warnings: list):
    """material_entries: list of dicts {name, texture_rel|None, color|None, transparent, emissive}."""
    pos = md.positions
    uv = md.uvs
    n_pos = len(pos) // 3
    n_uv = len(uv) // 2

    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8"?>')
    lines.append(
        '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">'
    )
    lines.append(
        "<asset><contributor><authoring_tool>eai usd_to_gazebo</authoring_tool></contributor>"
        '<unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>'
    )

    images = []
    effects = []
    materials = []
    for i, me in enumerate(material_entries):
        fx = f"fx_{i}"
        img_id = None
        if me["texture_rel"]:
            img_id = f"img_{i}"
            images.append((img_id, me["texture_rel"]))
        params = []
        lamb = []
        if me["texture_rel"]:
            params.append(
                f'<newparam sid="{img_id}_surf"><surface type="2D"><init_from>{img_id}</init_from></surface></newparam>'
            )
            params.append(f'<newparam sid="{img_id}_samp"><sampler2D><source>{img_id}_surf</source></sampler2D></newparam>')
        if me["emissive"]:
            r, g, b = me.get("emission_color", (1, 1, 1))
            lamb.append(f"<emission><color>{r} {g} {b} 1</color></emission>")
        else:
            lamb.append("<emission><color>0 0 0 1</color></emission>")
        if me["texture_rel"]:
            lamb.append(f'<diffuse><texture texture="{img_id}_samp" texcoord="UVSET0"/></diffuse>')
        else:
            r, g, b = me["color"] or (0.5, 0.5, 0.5)
            lamb.append(f"<diffuse><color>{fmt(r)} {fmt(g)} {fmt(b)} 1</color></diffuse>")
        if me["transparent"]:
            lamb.append('<transparent opaque="A_ONE"><color>1 1 1 1</color></transparent>')
            lamb.append(f"<transparency><float>{fmt(me.get('opacity', 0.45))}</float></transparency>")
        effects.append(
            f'<effect id="{fx}"><profile_COMMON>' + "".join(params)
            + '<technique sid="common"><lambert>' + "".join(lamb) + "</lambert></technique>"
            + "</profile_COMMON></effect>"
        )
        mid = f"mat_{i}"
        materials.append(f'<material id="{mid}" name="{xml_escape(me["name"])}"><instance_effect url="#{fx}"/></material>')

    if images:
        lines.append("<library_images>")
        for img_id, rel in images:
            lines.append(f'<image id="{img_id}" name="{xml_escape(os.path.basename(rel))}"><init_from>{xml_escape(rel)}</init_from></image>')
        lines.append("</library_images>")
    lines.append("<library_effects>")
    lines.extend(effects)
    lines.append("</library_effects>")
    lines.append("<library_materials>")
    lines.extend(materials)
    lines.append("</library_materials>")

    lines.append("<library_geometries>")
    lines.append('<geometry id="g" name="' + xml_escape(md.name) + '"><mesh>')
    lines.append('<source id="g-pos"><float_array id="g-pos-arr" count="%d">%s</float_array>' % (len(pos), " ".join(fmt(v) for v in pos)))
    lines.append(
        '<technique_common><accessor source="#g-pos-arr" count="%d" stride="3">'
        '<param name="X" type="float"/><param name="Y" type="float"/><param name="Z" type="float"/>'
        "</accessor></technique_common></source>" % n_pos
    )
    lines.append('<source id="g-uv"><float_array id="g-uv-arr" count="%d">%s</float_array>' % (len(uv), " ".join(fmt(v) for v in uv)))
    lines.append(
        '<technique_common><accessor source="#g-uv-arr" count="%d" stride="2">'
        '<param name="S" type="float"/><param name="T" type="float"/>'
        "</accessor></technique_common></source>" % n_uv
    )
    lines.append('<vertices id="g-vtx"><input semantic="POSITION" source="#g-pos"/></vertices>')
    symbols = []
    for i, (mat_path, indices) in enumerate(md.groups):
        mid = f"mat_{i}"
        symbols.append((mid, len(indices) // 6))
        lines.append(
            f'<triangles count="{len(indices)//6}" material="{mid}">'
            '<input semantic="VERTEX" source="#g-vtx" offset="0"/>'
            '<input semantic="TEXCOORD" source="#g-uv" offset="1" set="0"/>'
            f"<p>{' '.join(str(v) for v in indices)}</p></triangles>"
        )
    lines.append("</mesh></geometry></library_geometries>")

    lines.append('<library_visual_scenes><visual_scene id="S" name="S">')
    lines.append('<node id="n" name="n"><instance_geometry url="#g"><bind_material><technique_common>')
    for mid, _cnt in symbols:
        lines.append(
            f'<instance_material symbol="{mid}" target="#{mid}">'
            '<bind_vertex_input semantic="UVSET0" input_semantic="TEXCOORD" input_set="0"/>'
            "</instance_material>"
        )
    lines.append("</technique_common></bind_material></instance_geometry></node>")
    lines.append("</visual_scene></library_visual_scenes>")
    lines.append('<scene><instance_visual_scene url="#S"/></scene>')
    lines.append("</COLLADA>")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def write_obj(path: str, mtl_name: str, md: MeshData, material_entries):
    """Wavefront OBJ with per-group materials; global vertex/uv indexing."""
    out = [f"mtllib {mtl_name}", f"o {md.name}"]
    pos = md.positions
    uv = md.uvs
    for i in range(0, len(pos), 3):
        out.append(f"v {fmt(pos[i])} {fmt(pos[i+1])} {fmt(pos[i+2])}")
    for i in range(0, len(uv), 2):
        out.append(f"vt {fmt(uv[i])} {fmt(uv[i+1])}")
    for gi, (_mat_path, indices) in enumerate(md.groups):
        out.append(f"usemtl {material_entries[gi]['name']}")
        for t in range(len(indices) // 6):
            a = indices[t*6:t*6+6]
            out.append(f"f {a[0]+1}/{a[1]+1} {a[2]+1}/{a[3]+1} {a[4]+1}/{a[5]+1}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------------------
# transform decomposition (row-vector USD convention)


def mat3_to_quat(R: Gf.Matrix3d) -> Gf.Quatd:
    """Shepperd's method. Gf stores row-major matrices and USD transforms row
    vectors, so the column-convention formulas must be fed the transpose."""
    m = [[R[j][i] for j in range(3)] for i in range(3)]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        qw = 0.25 * s
        qx = (m[2][1] - m[1][2]) / s
        qy = (m[0][2] - m[2][0]) / s
        qz = (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        qw = (m[2][1] - m[1][2]) / s
        qx = 0.25 * s
        qy = (m[0][1] + m[1][0]) / s
        qz = (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        qw = (m[0][2] - m[2][0]) / s
        qx = (m[0][1] + m[1][0]) / s
        qy = 0.25 * s
        qz = (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        qw = (m[1][0] - m[0][1]) / s
        qx = (m[0][2] + m[2][0]) / s
        qy = (m[1][2] + m[2][1]) / s
        qz = 0.25 * s
    return Gf.Quatd(qw, qx, qy, qz)


def rigid_part(world_matrix: Gf.Matrix4d):
    """(quat, translation) of a transform, with the rotation extracted by row
    normalization so it stays consistent with bake_local_transform when the
    transform carries non-uniform scale."""
    R3 = world_matrix.ExtractRotationMatrix()
    rows = [Gf.Vec3d(R3[i][0], R3[i][1], R3[i][2]) for i in range(3)]
    lens = [r.GetLength() for r in rows]
    rot_rows = [r / l for r, l in zip(rows, lens)]
    Rrot = Gf.Matrix3d(
        rot_rows[0][0], rot_rows[0][1], rot_rows[0][2],
        rot_rows[1][0], rot_rows[1][1], rot_rows[1][2],
        rot_rows[2][0], rot_rows[2][1], rot_rows[2][2],
    )
    return mat3_to_quat(Rrot), world_matrix.ExtractTranslation()


def bake_local_transform(mesh_world: Gf.Matrix4d, top_world: Gf.Matrix4d, warnings: list):
    """Bakes a mesh transform into (quat, trans, scale) for SDF.

    The SDF include pose is rigid (translation+rotation only; scale cannot be
    expressed), so the per-visual bake must strip exactly the top prim's
    rigid part: q = R_inc^-1 * (p_world - t) in column convention, i.e.
    B = Mw * T(-t) * R_inc^-1 as row-convention matrix products. The top
    prim's scale therefore stays in the baked transform and lands in the
    per-visual SDF <scale>."""
    q_inc, t_top = rigid_part(top_world)
    rot = Gf.Rotation(q_inc)
    Rinv = Gf.Matrix4d()
    Rinv.SetIdentity()
    Rinv.SetRotate(rot.GetInverse())
    strip_t = Gf.Matrix4d()
    strip_t.SetIdentity()
    strip_t.SetTranslate(-t_top)
    B = mesh_world * strip_t * Rinv
    t = B.ExtractTranslation()
    L = B.ExtractRotationMatrix()
    rows = [Gf.Vec3d(L[i][0], L[i][1], L[i][2]) for i in range(3)]
    lens = [c.GetLength() for c in rows]
    if min(lens) < 1e-9:
        warnings.append("degenerate scale in baked transform")
        lens = [max(v, 1e-9) for v in lens]
    ok = True
    for i in range(3):
        for j in range(i + 1, 3):
            if abs(rows[i] * rows[j]) > 1e-6 * lens[i] * lens[j]:
                ok = False
    if not ok:
        warnings.append("shear in baked transform; using nearest rotation (slight distortion)")
    rot_rows = [r / l for r, l in zip(rows, lens)]
    R = Gf.Matrix3d(
        rot_rows[0][0], rot_rows[0][1], rot_rows[0][2],
        rot_rows[1][0], rot_rows[1][1], rot_rows[1][2],
        rot_rows[2][0], rot_rows[2][1], rot_rows[2][2],
    )
    if R.GetDeterminant() < 0:
        warnings.append("mirrored transform detected; flipping winding")
        R = Gf.Matrix3d(-R[0][0], -R[0][1], -R[0][2], R[1][0], R[1][1], R[1][2], R[2][0], R[2][1], R[2][2])
    quat = mat3_to_quat(R)
    scale = Gf.Vec3d(*lens)
    return (quat, t, scale)


def quat_to_rpy(q: Gf.Quatd):
    """SDF pose roll-pitch-yaw (extrinsic X-Y-Z, column-convention
    R = Rz(yaw)*Ry(pitch)*Rx(roll)) from a USD row-convention quaternion."""
    qw, qx, qy, qz = q.GetReal(), q.GetImaginary()[0], q.GetImaginary()[1], q.GetImaginary()[2]
    if qw < 0:
        qw, qx, qy, qz = -qw, -qx, -qy, -qz
    m00, m01, m02 = 1-2*(qy*qy+qz*qz), 2*(qx*qy-qw*qz), 2*(qx*qz+qw*qy)
    m10, m11, m12 = 2*(qx*qy+qw*qz), 1-2*(qx*qx+qz*qz), 2*(qy*qz-qw*qx)
    m20, m21, m22 = 2*(qx*qz-qw*qy), 2*(qy*qz+qw*qx), 1-2*(qx*qx+qy*qy)
    sp = -m20
    if abs(sp) < 1.0 - 1e-9:
        pitch = math.asin(sp)
        roll = math.atan2(m21, m22)
        yaw = math.atan2(m10, m00)
    else:
        pitch = math.copysign(math.pi / 2, sp)
        roll = math.atan2(-m12, m11)
        yaw = 0.0
    return roll, pitch, yaw


def quat_sdf(q: Gf.Quatd) -> str:
    roll, pitch, yaw = quat_to_rpy(q)
    return f"{fmt(roll)} {fmt(pitch)} {fmt(yaw)}"


# ---------------------------------------------------------------------------
# conversion


class Converter:
    def __init__(self, source_dir: str, scene_stem: str, out_root: str, collisions: bool, obj_out: str | None = None):
        self.source = source_dir
        self.scene_stem = scene_stem
        self.out = out_root
        self.collisions = collisions
        self.obj_out = obj_out
        self.models_dir = os.path.join(out_root, "models")
        self.warnings: list[str] = []
        self.resolver: MaterialResolver | None = None
        self.model_entries: list[dict] = []
        self.copied_textures = 0
        self._mtl_entries: list = []

    def open_stage(self) -> Usd.Stage:
        return Usd.Stage.Open(os.path.join(self.source, f"{self.scene_stem}.usd"))

    def payload_key(self, prim: Usd.Prim) -> str | None:
        for spec in prim.GetPrimStack():
            for item in listp(spec.payloadList.prependedItems) + listp(spec.payloadList.addedItems) + listp(spec.payloadList.explicitItems):
                if item.assetPath:
                    p = item.assetPath
                    if not os.path.isabs(p):
                        p = os.path.normpath(os.path.join(self.source, p))
                    return p
        return None

    def top_scale(self, xcache, prim) -> Gf.Vec3d:
        w = xcache.GetLocalToWorldTransform(prim)
        cols = [Gf.Vec3d(w[i][0], w[i][1], w[i][2]) for i in range(3)]
        return Gf.Vec3d(*[c.GetLength() for c in cols])

    def collect_models(self, stage: Usd.Stage) -> dict[str, dict]:
        xcache = UsdGeom.XformCache(Usd.TimeCode.Default())
        groups: dict[str, dict] = {}
        root_prim = stage.GetDefaultPrim()
        for top in root_prim.GetChildren():
            tn = top.GetTypeName()
            if tn in ("Material", "Scope", "PhysicsScene", "CylinderLight", "SphereLight",
                      "DomeLight", "RenderSettings", "OmniGraph", "OmniGraphNode",
                      "RenderProduct", "RenderVar", "RenderProduct"):
                continue
            meshes = [p for p in Usd.PrimRange(top, Usd.PrimDefaultPredicate)
                      if p.GetTypeName() == "Mesh" and effectively_active(p)]
            if not meshes:
                continue
            payload = self.payload_key(top)
            scale = self.top_scale(xcache, top)
            skey = tuple(round(v, 4) for v in scale)
            key = f"{payload or 'prim:' + str(top.GetPath())}|{skey}"
            entry = groups.get(key)
            if entry is None:
                base = os.path.splitext(os.path.basename(payload))[0] if payload else top.GetName()
                entry = {"key": key, "name": sanitize(base), "prims": [], "scale": scale}
                groups[key] = entry
            entry["prims"].append(top)
        return groups

    def export(self):
        stage = self.open_stage()
        self.resolver = MaterialResolver(self.source, stage)
        groups = self.collect_models(stage)

        if os.path.isdir(self.models_dir):
            shutil.rmtree(self.models_dir)
        os.makedirs(self.models_dir, exist_ok=True)

        used_names = set()
        xcache = UsdGeom.XformCache(Usd.TimeCode.Default())

        for key, entry in sorted(groups.items()):
            name = entry["name"]
            candidate, n = name, 1
            while candidate in used_names:
                candidate = f"{name}_{n}"
                n += 1
            name = candidate
            used_names.add(name)

            top = entry["prims"][0]
            top_world = xcache.GetLocalToWorldTransform(top)
            mdir = os.path.join(self.models_dir, name)
            os.makedirs(os.path.join(mdir, "meshes"), exist_ok=True)
            os.makedirs(os.path.join(mdir, "materials", "textures"), exist_ok=True)

            rel_seen = {}
            mesh_records = []
            for prim in Usd.PrimRange(top, Usd.PrimDefaultPredicate):
                if prim.GetTypeName() != "Mesh" or not effectively_active(prim):
                    continue
                rel = str(prim.GetPath())[len(str(top.GetPath())):].strip("/") or "mesh"
                if rel in rel_seen:
                    continue
                rel_seen[rel] = True
                md = extract_mesh(prim, sanitize(rel.split("/")[-1]))
                if md is None or not md.groups:
                    continue
                mesh_world = xcache.GetLocalToWorldTransform(prim)
                baked = bake_local_transform(mesh_world, top_world, self.warnings)
                if baked is None:
                    continue
                quat, tvec, scale = baked
                ments = []
                for mat_path, indices in md.groups:
                    info = self.resolver.resolve(mat_path) if mat_path else MaterialInfo()
                    ments.append(self._material_entry(info, mdir))
                self._assign_material_names([(None, md, None, ments)])
                for me in ments:
                    self._mtl_entries.append((name, me))
                mesh_records.append((rel, md, (quat, tvec, scale), ments))

            visual_lines = []
            collision_lines = []
            for rel, md, (quat, tvec, scale), ments in mesh_records:
                dae_name = sanitize(rel.replace("/", "_"))
                if not dae_name.endswith(".dae"):
                    dae_name += ".dae"
                dae_path = os.path.join(mdir, "meshes", dae_name)
                write_collada(dae_path, name, md, ments, self.warnings)
                if self.obj_out:
                    odir = os.path.join(self.obj_out, name, "meshes")
                    os.makedirs(odir, exist_ok=True)
                    write_obj(os.path.join(odir, dae_name[:-4] + ".obj"), f"{name}.mtl", md, ments)
                pose = (
                    f"{fmt(tvec[0])} {fmt(tvec[1])} {fmt(tvec[2])} {quat_sdf(quat)}"
                )
                vscale = f"<scale>{fmt(scale[0])} {fmt(scale[1])} {fmt(scale[2])}</scale>" if not np.allclose(scale, 1.0, atol=1e-6) else ""
                vname = sanitize(dae_name[:-4])
                visual_lines.append(
                    f'      <visual name="{vname}"><geometry><mesh>'
                    f"<uri>model://{name}/meshes/{dae_name}</uri>{vscale}</mesh></geometry>"
                    f"<pose>{pose}</pose></visual>"
                )
                if self.collisions:
                    mn = np.asarray(md.bbox_min)
                    mx = np.asarray(md.bbox_max)
                    size = (mx - mn)
                    center = (mx + mn) / 2.0
                    if size.max() > 1e-6:
                        s = np.asarray([scale[0], scale[1], scale[2]])
                        csize = np.maximum(np.abs(size * s), 0.02)  # avoid zero-thickness boxes
                        rot3 = Gf.Rotation(quat)
                        r = rot3.TransformDir(Gf.Vec3d(*(center * s)))
                        collision_lines.append(
                            f'      <collision name="{vname}_col"><geometry><box>'
                            f"<size>{fmt(csize[0])} {fmt(csize[1])} {fmt(csize[2])}</size></box></geometry>"
                            f"<pose>{fmt(tvec[0]+r[0])} {fmt(tvec[1]+r[1])} {fmt(tvec[2]+r[2])} {quat_sdf(quat)}</pose>"
                            f"</collision>"
                        )
                    else:
                        collision_lines.append(
                            f'      <collision name="{vname}_col"><geometry><sphere><radius>0.01</radius>'
                            f"</sphere></geometry><pose>{pose}</pose></collision>"
                        )

            self._write_model_config(mdir, name)
            self._write_material_script(mdir, name, mesh_records)
            self._write_model_sdf(mdir, name, visual_lines, collision_lines)

            for p in entry["prims"]:
                w = xcache.GetLocalToWorldTransform(p)
                q, t = rigid_part(w)
                self.model_entries.append(
                    {
                        "model": name,
                        "instance": str(p.GetPath()),
                        "pose": [round(v, 6) for v in (t[0], t[1], t[2], q.GetReal(), *q.GetImaginary())],
                    }
                )
            tri = sum(len(idx) // 6 for _r, md, _b, _m in mesh_records for _p, idx in md.groups)
            print(f"[model] {name}: {len(mesh_records)} meshes, {tri} tris, {len(entry['prims'])} instance(s)")

        if self.obj_out:
            self._write_obj_mtls()
        self._write_world()
        self._write_manifest()

    def _material_entry(self, info: MaterialInfo, mdir: str) -> dict:
        entry = {
            "name": None,
            "texture_rel": None,
            "color": info.color,
            "transparent": info.transparent,
            "emissive": info.emissive,
        }
        if info.texture:
            src = info.texture
            base = os.path.basename(src)
            dst = os.path.join(mdir, "materials", "textures", base)
            if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
                shutil.copyfile(src, dst)
                self.copied_textures += 1
            entry["texture_rel"] = f"../materials/textures/{base}"
            entry["texture_src"] = src
        return entry

    def _assign_material_names(self, mesh_records):
        for _rel, _md, _b, ments in mesh_records:
            for me in ments:
                if me["name"]:
                    continue
                if me.get("texture_rel"):
                    base = os.path.splitext(os.path.basename(me["texture_rel"]))[0]
                    name = sanitize("tex_" + base)
                elif me.get("color"):
                    name = "col_" + "_".join(f"{int(round(v*255)):02x}" for v in me["color"])
                else:
                    name = "mat_plain"
                if me.get("transparent"):
                    name += "_glass"
                me["name"] = name
        return mesh_records

    def _write_model_config(self, mdir: str, name: str):
        with open(os.path.join(mdir, "model.config"), "w", encoding="utf-8") as f:
            f.write(
                f"""<?xml version="1.0"?>
<model>
  <name>{xml_escape(name)}</name>
  <version>1.0</version>
  <sdf version="{SDF_VERSION}">model.sdf</sdf>
  <author>
    <name>EAI USD-to-Gazebo skill</name>
    <email>none@localhost</email>
  </author>
  <description>Converted from the Isaac Sim USD scene {self.scene_stem}.</description>
</model>
"""
            )

    def _write_model_sdf(self, mdir: str, name: str, visual_lines, collision_lines):
        parts = [
            '<?xml version="1.0"?>',
            f'<sdf version="{SDF_VERSION}">',
            f'  <model name="{xml_escape(name)}">',
            "    <static>true</static>",
            '    <link name="link">',
        ]
        parts.extend(visual_lines)
        parts.extend(collision_lines)
        parts.append("    </link>")
        parts.append("  </model>")
        parts.append("</sdf>")
        with open(os.path.join(mdir, "model.sdf"), "w", encoding="utf-8") as f:
            f.write("\n".join(parts))

    def _write_material_script(self, mdir: str, name: str, mesh_records):
        seen = {}
        blocks = []
        for _rel, _md, _b, ments in mesh_records:
            for me in ments:
                mname = me["name"]
                if mname in seen:
                    continue
                seen[mname] = True
                tex = os.path.basename(me["texture_rel"]) if me.get("texture_rel") else None
                if me.get("color"):
                    r, g, b = me["color"]
                    ambient = f"{fmt(r)} {fmt(g)} {fmt(b)} 1"
                else:
                    ambient = "0.8 0.8 0.8 1"
                tu = f"      texture_unit {{ texture {tex} }}\n" if tex else ""
                if me.get("transparent"):
                    blocks.append(
                        f"material {mname}\n{{\n  technique\n  {{\n    pass\n    {{\n"
                        f"      ambient {ambient}\n      diffuse 1 1 1 1\n"
                        f"      scene_blend alpha_blend\n      depth_write off\n"
                        + tu
                        + f"    }}\n  }}\n}}\n"
                    )
                    continue
                blocks.append(
                    f"material {mname}\n{{\n  technique\n  {{\n    pass\n    {{\n"
                    f"      ambient {ambient}\n      diffuse 1 1 1 1\n{tu}"
                    f"    }}\n  }}\n}}\n"
                )
        script_dir = os.path.join(mdir, "materials", "scripts")
        os.makedirs(script_dir, exist_ok=True)
        with open(os.path.join(script_dir, f"{name}.material"), "w", encoding="utf-8") as f:
            f.write("".join(blocks))

    def _write_world(self):
        lines = [
            '<?xml version="1.0"?>',
            f'<sdf version="{SDF_VERSION}">',
            '  <world name="' + xml_escape(self.scene_stem) + '">',
            '    <light name="sun" type="directional">',
            "      <cast_shadows>true</cast_shadows>",
            "      <pose>0 0 10 0 0 0</pose>",
            "      <diffuse>0.8 0.8 0.8 1</diffuse>",
            "      <specular>0.2 0.2 0.2 1</specular>",
            "      <attenuation><range>100</range><constant>0.9</constant><linear>0.01</linear><quadratic>0.001</quadratic></attenuation>",
            "      <direction>-0.4 0.3 -0.85</direction>",
            "    </light>",
            "    <scene>",
            "      <ambient>0.45 0.45 0.45 1</ambient>",
            "      <background>0.65 0.72 0.8 1</background>",
            "      <shadows>true</shadows>",
            "    </scene>",
            '    <physics type="ode" default="true">',
            "      <max_step_size>0.001</max_step_size>",
            "      <real_time_factor>1.0</real_time_factor>",
            "      <real_time_update_rate>1000</real_time_update_rate>",
            "    </physics>",
        ]
        per_model_count = defaultdict(int)
        for e in self.model_entries:
            per_model_count[e["model"]] += 1
        emitted = defaultdict(int)
        for e in self.model_entries:
            emitted[e["model"]] += 1
            name = e["model"] if per_model_count[e["model"]] == 1 else f"{e['model']}_{emitted[e['model']]}"
            x, y, z = e["pose"][:3]
            q = Gf.Quatd(*e["pose"][3:])
            lines.append(
                "    <include>"
                f"<uri>model://{xml_escape(e['model'])}</uri>"
                f"<name>{xml_escape(name)}</name>"
                f"<pose>{fmt(x)} {fmt(y)} {fmt(z)} {quat_sdf(q)}</pose>"
                "</include>"
            )
        lines.append("  </world>")
        lines.append("</sdf>")
        with open(os.path.join(self.out, "world.sdf"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _write_obj_mtls(self):
        written = set()
        for model, me in self._mtl_entries:
            if (model, me["name"]) in written:
                continue
            written.add((model, me["name"]))
            lines = []
            if me.get("color"):
                r, g, b = me["color"]
                lines.append(f"Kd {fmt(r)} {fmt(g)} {fmt(b)}")
            else:
                lines.append("Kd 0.8 0.8 0.8")
            lines.append("Ka 0.2 0.2 0.2")
            if me.get("texture_src"):
                lines.append(f"map_Kd {me['texture_src']}")
            if me.get("transparent"):
                lines.append(f"d {fmt(me.get('opacity', 0.45))}")
            path = os.path.join(self.obj_out, model, "meshes", f"{model}.mtl")
            mode = "a" if os.path.isfile(path) else "w"
            with open(path, mode, encoding="utf-8") as f:
                prefix = "" if mode == "w" else "\n"
                f.write(prefix + f"newmtl {me['name']}\n" + "\n".join(lines) + "\n")

    def _write_manifest(self):
        manifest = {
            "source": self.source,
            "scene": self.scene_stem,
            "sdf_version": SDF_VERSION,
            "instances": self.model_entries,
            "models": sorted({e["model"] for e in self.model_entries}),
            "copied_texture_files": self.copied_textures,
            "warnings": self.warnings,
        }
        with open(os.path.join(self.out, "conversion_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="USD scene directory (contains <scene>.usd and sub-assets)")
    ap.add_argument("--scene", default=None, help="USD filename stem inside --source (default: the only *.usd there)")
    ap.add_argument("--out", default=None, help="output directory (default: sibling <scene>_gazebo)")
    ap.add_argument("--no-collisions", action="store_true", help="skip box collision generation")
    ap.add_argument("--obj-out", default=None, help="also export meshes as OBJ+MTL into this directory")
    args = ap.parse_args()

    source = os.path.abspath(args.source)
    if not os.path.isdir(source):
        sys.exit(f"source directory not found: {source}")
    scene = args.scene
    if scene is None:
        cands = [f[:-4] for f in os.listdir(source) if f.endswith(".usd") and os.path.isfile(os.path.join(source, f))]
        if len(cands) != 1:
            sys.exit(f"multiple/zero *.usd files in {source}; pass --scene explicitly: {sorted(cands)}")
        scene = cands[0]
    out = args.out or os.path.join(os.path.dirname(source), f"{scene}_gazebo")
    os.makedirs(out, exist_ok=True)

    stage = Usd.Stage.Open(os.path.join(source, f"{scene}.usd"))
    up = UsdGeom.GetStageUpAxis(stage)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    if str(up) != "Z" or abs(mpu - 1.0) > 1e-6:
        print(f"WARNING: stage is up-axis={up}, metersPerUnit={mpu}; this converter assumes Z-up meters.")

    conv = Converter(source, scene, out, collisions=not args.no_collisions, obj_out=args.obj_out)
    conv.export()
    print(f"\nmodels: {len(set(e['model'] for e in conv.model_entries))}")
    print(f"instances: {len(conv.model_entries)}")
    print(f"texture files copied: {conv.copied_textures}")
    if conv.warnings:
        print(f"warnings ({len(conv.warnings)}):")
        for w in conv.warnings[:20]:
            print("  -", w)
    print(f"\noutput: {out}")
    print("next: run the Phase 3 validation gates from SKILL.md")


if __name__ == "__main__":
    main()
