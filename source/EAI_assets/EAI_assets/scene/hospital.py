import re
from pathlib import Path

from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.sim.spawners.from_files import from_files as _from_files
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path


HOSPITAL_USD_PATH = asset_path("scene/hospital/hospital_local.usda")
HOSPITAL_BED_USD_PATH = asset_path("scene/hospital/Bed_local.usda")
_LOCAL_REFERENCE_PATTERN = re.compile(r"@(\./Materials/[^@]+)@")
_MDL_TEXTURE_PATTERN = re.compile(r'texture_2d\("([^"]+)"')

# The original rehab scene patched this one floor mesh at runtime. Re-serializing
# the hospital USD is fragile, so keep the patch in the spawner wrapper.
_MISSING_COLLISION_RELATIVE_PATHS = (
    "hospital/Geo_o_Floor_936/Geo_o_Floor/Geo_o_Floor",
)


def _spawn_hospital_with_collision_patch(prim_path, cfg, translation=None, orientation=None, **kwargs):
    prim = _from_files.spawn_from_usd(prim_path, cfg, translation, orientation, **kwargs)

    import omni.usd
    from pxr import UsdPhysics
    from isaaclab.sim.utils import find_matching_prim_paths

    stage = omni.usd.get_context().get_stage()
    patched = 0
    for relative_path in _MISSING_COLLISION_RELATIVE_PATHS:
        for path in find_matching_prim_paths(f"{prim_path}/{relative_path}"):
            target = stage.GetPrimAtPath(path)
            if not (target and target.IsValid()) or target.HasAPI(UsdPhysics.CollisionAPI):
                continue
            UsdPhysics.CollisionAPI.Apply(target).CreateCollisionEnabledAttr(True)
            UsdPhysics.MeshCollisionAPI.Apply(target).CreateApproximationAttr("none")
            patched += 1
    if patched:
        print(f"[hospital] runtime-patched {patched} floor collision(s).")
    return prim


def _hospital_material_dependencies() -> tuple[str, ...]:
    hospital_dir = Path(HOSPITAL_USD_PATH).parent
    dependencies: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        normalized = str(path)
        if normalized not in seen:
            seen.add(normalized)
            dependencies.append(normalized)

    usd_paths = (Path(HOSPITAL_USD_PATH), Path(HOSPITAL_BED_USD_PATH))
    mdl_paths: list[Path] = []
    for usd_path in usd_paths:
        if not usd_path.is_file():
            continue
        text = usd_path.read_text(encoding="utf-8", errors="ignore")
        for reference in sorted(set(_LOCAL_REFERENCE_PATTERN.findall(text))):
            path = hospital_dir / reference.removeprefix("./")
            add(path)
            if path.suffix.lower() == ".mdl":
                mdl_paths.append(path)

    for mdl_path in sorted(set(mdl_paths)):
        if not mdl_path.is_file():
            continue
        text = mdl_path.read_text(encoding="utf-8-sig", errors="ignore")
        for texture_ref in sorted(set(_MDL_TEXTURE_PATTERN.findall(text))):
            if texture_ref.startswith("./Textures/"):
                add(mdl_path.parent / texture_ref.removeprefix("./"))

    return tuple(dependencies)


HOSPITAL_MATERIAL_DEPENDENCIES = _hospital_material_dependencies()


@configclass
class HospitalCfg(UsdFileCfg):
    usd_path: str = HOSPITAL_USD_PATH
    func = _spawn_hospital_with_collision_patch
    asset_dependencies = (HOSPITAL_BED_USD_PATH, *HOSPITAL_MATERIAL_DEPENDENCIES)


HOSPITAL_CFG = HospitalCfg()
