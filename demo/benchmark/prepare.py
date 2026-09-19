"""Normalize benchmark wrapper metadata through OpenUSD without rescaling geometry."""

import argparse
import json
import shutil
from pathlib import Path


def normalize_wrapper(path, backup, *, disabled_collision_proxies=()):
    from pxr import Usd, UsdGeom, UsdPhysics

    path, backup = Path(path).resolve(), Path(backup)
    stage = Usd.Stage.Open(str(path), load=Usd.Stage.LoadNone)
    if stage is None:
        raise ValueError(f"Cannot open {path}")
    layer = stage.GetRootLayer()
    original = {key: layer.pseudoRoot.GetInfo(key) if layer.pseudoRoot.HasInfo(key) else None
                for key in ("metersPerUnit", "upAxis")}
    proxies = []
    for prim_path in disabled_collision_proxies:
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError(f"Expected collision proxy is missing: {prim_path}")
        proxies.append(UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr())
    if original == {"metersPerUnit": 1.0, "upAxis": "Z"} and all(
            attribute.Get() is False for attribute in proxies):
        return original
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise FileExistsError(backup)
    shutil.copy2(path, backup)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    for attribute in proxies:
        attribute.Set(False)
    layer.Save()
    return original


def main(config, argv=None):
    from simulator import _ensure_repo_sources_on_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args(argv)
    _ensure_repo_sources_on_path()
    from EAI_assets.asset_resolver import usd_root

    previous = normalize_wrapper(
        usd_root() / config.USD_RELATIVE_PATH, args.backup,
        disabled_collision_proxies=getattr(config, "DISABLED_COLLISION_PROXIES", ()),
    )
    print(json.dumps({"previous_metadata": previous, "metersPerUnit": 1.0, "upAxis": "Z"}))
    return 0
