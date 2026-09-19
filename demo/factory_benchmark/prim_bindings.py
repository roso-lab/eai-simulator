"""Stable Factory USD bindings used by the benchmark scene contract."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


FACTORY_PRIM_BINDINGS = {
    "ground_collision": {"prim_path": "/World/GroundPlane/CollisionMesh", "role": "ground_surface"},
    "storage_rack_west": {"prim_path": "/World/RackSmallEmpty_A2", "role": "storage_fixture"},
    "storage_rack_east": {"prim_path": "/World/RackSmallEmpty_A2_01", "role": "storage_fixture"},
    "container_main": {
        "prim_path": "/World/Container_F11_123x80x89cm_PR_V_NVD_01",
        "role": "container_fixture",
    },
    "inspection_target_box": {
        "prim_path": "/World/warehouse_extras/Extras/BoxSetA/SM_CardBoxC_02",
        "role": "inspection_target_visual_reference",
    },
    "west_ramp": {"prim_path": "/World/RampShort_A1", "role": "ramp_surface"},
    "south_high_ramp": {"prim_path": "/World/RampShort_A1_03", "role": "ramp_surface"},
    "main_stair": {"prim_path": "/World/SM_StairsSmall", "role": "stair_surface"},
    "mezzanine_surface": {"prim_path": "/World/wall/wall_06", "role": "elevated_surface"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_factory_prim_bindings(source: str | Path) -> dict[str, Any]:
    """Audit required prims and authored stage metadata without starting Isaac."""
    from pxr import Usd, UsdGeom

    path = Path(source).resolve()
    if not path.is_file():
        raise ValueError(f"Factory USD does not exist: {path}")
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"Factory USD could not be opened: {path}")
    units = stage.GetMetadata("metersPerUnit")
    up_axis = stage.GetMetadata("upAxis")
    bindings = []
    missing = []
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render", "proxy"])
    for semantic_id, binding in FACTORY_PRIM_BINDINGS.items():
        prim_path = binding["prim_path"]
        prim = stage.GetPrimAtPath(prim_path)
        valid = bool(prim and prim.IsValid())
        item = {"id": semantic_id, **binding, "valid": valid}
        if valid:
            item["type_name"] = prim.GetTypeName()
            bounds = bbox_cache.ComputeWorldBound(prim).ComputeAlignedBox()
            item["world_bounds_m"] = {
                "min": [float(value) for value in bounds.GetMin()],
                "max": [float(value) for value in bounds.GetMax()],
            }
        else:
            missing.append(semantic_id)
        bindings.append(item)
    return {
        "schema": "eai.factory_prim_binding_audit",
        "version": 1,
        "usd_path": str(path),
        "usd_sha256": _sha256(path),
        "units": units,
        "up_axis": up_axis,
        "metadata_validated": units == 1.0 and up_axis == "Z",
        "bindings": bindings,
        "missing": missing,
        "validated": not missing and units == 1.0 and up_axis == "Z",
        "semantic_limitations": [
            "prim bindings identify geometry only; they do not prove inventory or equipment behavior",
            "bounds and visual materials do not establish a business operation or sensor observation",
            "elevated surfaces require a separate robot-specific physics validation",
        ],
    }


__all__ = ["FACTORY_PRIM_BINDINGS", "audit_factory_prim_bindings"]
