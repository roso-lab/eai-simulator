"""Common application CLI with scene ownership and local asset preflight."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def _collision_proxy_render_policy_validated(layer):
    """Require physics-only proxy roots to be imageable and inherited-hidden."""
    proxy_root = layer.GetPrimAtPath("/World/BenchCollision")
    if proxy_root is None:
        return True
    visibility = proxy_root.attributes.get("visibility")
    return (proxy_root.typeName == "Xform"
            and visibility is not None
            and visibility.default == "invisible")


# Mirrors tools/assets/prepare_nvd_props._MDL_BUILTIN_MODULES: MDL stdlib and
# Kit-builtin modules that legitimately resolve outside the shipped tree.
_MDL_BUILTIN_MODULES = frozenset({
    "base", "tex", "state", "anno", "math", "df", "misc", "dist", "nvidia",
    "OmniPBR", "OmniGlass",
})
_MDL_IMPORT_RE = re.compile(
    r"^\s*(?:using\s+([A-Za-z_]\w*)\s+import"
    r"|import\s+::([A-Za-z_]\w*)::"
    r"|import\s+([A-Za-z_]\w*)::)",
    re.M,
)


def _mdl_imports_validated(boundary, layer):
    """Require every MDL import edge inside the NVD tree to have its module.

    USD closure audits cannot see MDL-to-MDL ``import``/``using`` edges; a
    missing module (e.g. ``GlassWithVolume``) compiles red or drops meshes at
    runtime while every USD-level check stays green.
    """
    nvd_root = boundary / "assets" / "nvd"
    if not nvd_root.is_dir():
        return True
    available = {}
    for path in nvd_root.rglob("*.mdl"):
        if ".thumbs" not in path.parts:
            available.setdefault(path.stem, path)
    unresolved = set()
    for path in nvd_root.rglob("*.mdl"):
        if ".thumbs" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for groups in _MDL_IMPORT_RE.findall(text):
            module = next((group for group in groups if group), None)
            if module and module not in _MDL_BUILTIN_MODULES and module not in available:
                unresolved.add(module)
    return not unresolved


def _nvd_product_provenance_report(config, boundary):
    scene_key = getattr(config, "NVD_PRODUCT_PROVENANCE_SCENE", None)
    if scene_key is None:
        return None
    from tools.assets.rebuild_nvd_products import validate_product_provenance

    try:
        manifest = validate_product_provenance(scene_key, asset_root=boundary)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"validated": False, "error": str(exc)}
    return {
        "validated": True,
        "schema": manifest["schema"],
        "publication_status": manifest["publication_status"],
        "provider_revision": manifest["provider_revision"],
    }


def check_assets(config):
    from tools.assets.usd_closure import audit

    from simulator import _ensure_repo_sources_on_path

    _ensure_repo_sources_on_path()
    from EAI_assets.asset_resolver import usd_root

    boundary = usd_root()
    report = audit([boundary / config.USD_RELATIVE_PATH], boundary=boundary)
    from pxr import Sdf

    layer = Sdf.Layer.FindOrOpen(str(boundary / config.USD_RELATIVE_PATH))
    report["wrapper_metadata_validated"] = (
        layer.pseudoRoot.GetInfo("metersPerUnit") == 1.0
        and layer.pseudoRoot.GetInfo("upAxis") == "Z")
    report["collision_proxy_policy_validated"] = all(
        (attribute := layer.GetAttributeAtPath(prim + ".physics:collisionEnabled")) is not None
        and attribute.default is False
        for prim in getattr(config, "DISABLED_COLLISION_PROXIES", ())
    )
    report["collision_proxy_render_policy_validated"] = (
        _collision_proxy_render_policy_validated(layer))
    report["mdl_imports_validated"] = _mdl_imports_validated(boundary, layer)
    import yaml

    map_path = boundary / config.MAP_RELATIVE_PATH
    map_data = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    image = (map_path.parent / map_data["image"]).resolve()
    report["map_validated"] = (not Path(map_data["image"]).is_absolute()
                               and image.is_relative_to(boundary) and image.is_file())
    provenance = _nvd_product_provenance_report(config, boundary)
    if provenance is not None:
        report["product_provenance"] = provenance
        report["product_provenance_validated"] = provenance["validated"]
    else:
        report["product_provenance_validated"] = True
    report["validated"] = (report["validated"] and report["map_validated"]
                           and report["wrapper_metadata_validated"]
                           and report["collision_proxy_policy_validated"]
                           and report["collision_proxy_render_policy_validated"]
                           and report["mdl_imports_validated"]
                           and report["product_provenance_validated"])
    prim_check = getattr(config, "PRIM_BINDING_CHECK", None)
    if prim_check is not None:
        prim_report = prim_check(boundary / config.USD_RELATIVE_PATH)
        report["prim_binding_audit"] = prim_report
        report["prim_bindings_validated"] = bool(prim_report.get("validated"))
        report["validated"] = report["validated"] and report["prim_bindings_validated"]
    return report


def run_demo(config, argv=None, *, participant_adapter=None,
             domain_evidence_provider=None, domain_adapter_id=None,
             lifecycle_callbacks=None, communication_contract=None,
             communication_schedule=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--check-assets" in arguments:
        parser = argparse.ArgumentParser(description="Read-only scene closure audit; no Isaac startup")
        parser.add_argument("--check-assets", action="store_true")
        parser.parse_args(arguments)
        report = check_assets(config)
        print(json.dumps({key: value for key, value in report.items() if key != "files"}, indent=2))
        return int(not report["validated"])
    from . import runtime

    return runtime.main(arguments, expected_scene=config.SCENE_KEY,
                        asset_check=lambda: check_assets_isolated(config),
                        participant_adapter=participant_adapter,
                        domain_evidence_provider=domain_evidence_provider,
                        domain_adapter_id=domain_adapter_id,
                        lifecycle_callbacks=lifecycle_callbacks,
                        communication_contract=communication_contract,
                        communication_schedule=communication_schedule)


def check_assets_isolated(config):
    """Keep standalone OpenUSD out of the process that will start Isaac Sim."""
    completed = subprocess.run(
        [sys.executable, "-m", f"{config.__package__}.main", "--check-assets"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=120,
    )
    if completed.returncode:
        raise ValueError(f"Scene asset check failed:\n{completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout)
