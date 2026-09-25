"""Trusted, host-independent identities for the optional initial oracle.

These inputs identify the execution; collecting them does not qualify a domain
or create a certificate. The certificate and its externally pinned digest are
separate evaluator inputs, never a source of these measurements.
"""

from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
import platform
import subprocess

from .provenance import canonical_sha256


ROOT = Path(__file__).resolve().parents[2]


def runtime_profile(args):
    versions = {"python": platform.python_version()}
    for name in ("isaacsim", "isaaclab", "torch", "numpy", "warp-lang"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name", "").lower().replace("_", "-")
        if name.startswith(("isaac", "nvidia-")):
            versions[name] = distribution.version
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            check=True, capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        gpu = None
    return {"device": args.device, "warmup_ticks": args.warmup_ticks,
            "max_ticks": args.max_ticks, "dependency_versions": versions,
            "system": {"system": platform.system(), "release": platform.release(),
                       "machine": platform.machine(), "gpu_driver_inventory": gpu},
            "publication_treatment_enabled": bool(getattr(args, "publication_treatment", False))}


def normalized_execution_sources(sources, controller_manifest, *, repository_root=ROOT):
    """Re-key measured hashes, rejecting unknown external files and collisions."""
    from .goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY

    root = Path(repository_root).resolve()
    aliases, controllers = {}, {}
    for robot, row in controller_manifest["controllers"].items():
        identity = "controller/" + FLEET_CONTROLLER_REGISTRY[robot]["controller_source"]
        path = str(Path(row["controller_source"]).resolve())
        digest = row["controller_source_sha256"]
        if identity in controllers and controllers[identity] != digest:
            raise ValueError("conflicting controller identities")
        controllers[identity] = digest
        aliases[path] = identity
    result = {}
    for raw, digest in sources.items():
        path = Path(raw).resolve()
        if str(path) in aliases:
            identity = aliases[str(path)]
            if digest != controllers[identity]:
                raise ValueError("controller snapshot changed")
        else:
            try:
                identity = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError("unregistered external execution source") from exc
        if identity in result:
            raise ValueError("duplicate normalized execution source")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("execution source changed since request preparation")
        result[identity] = digest
    if any(result.get(key) != value for key, value in controllers.items()):
        raise ValueError("execution source snapshot is missing controller sources")
    return dict(sorted(result.items())), dict(sorted(controllers.items()))


def collect_initial_oracle_inputs(args, request, asset_preflight):
    """Read current resolver products and code, without importing Isaac Sim."""
    from EAI_assets import restaurant_benchmark_products as products
    from EAI_assets.asset_requirements import RequirementKind, requirements_for_selection
    from EAI_assets.asset_resolver import controller_root, requirement_local_paths, usd_root
    from .goal_restaurant_fleet import restaurant_fleet_source_manifest

    if (asset_preflight.get("status") != "ready"
            or asset_preflight.get("hash_resolver") != "passed"
            or Path(asset_preflight["root"]).resolve() != args.products_root.resolve()):
        raise ValueError("trusted ready asset preflight is required")
    resolved = products.resolve_restaurant_benchmark_products(args.products_root)
    source = Path(asset_preflight["overlay_source_reference"])
    source_digest = products._sha256(source)
    if source_digest != products.EXPECTED_SOURCE_SHA256:
        raise ValueError("Restaurant source differs from frozen resolver identity")
    asset_rows = []
    for requirement in requirements_for_selection(request["selection"]):
        is_controller = requirement.kind is RequirementKind.CONTROLLER
        root = controller_root() if is_controller else usd_root()
        prefix = "controller/" if is_controller else "usd/"
        for path in requirement_local_paths(requirement):
            asset_rows.append({"requirement_id": requirement.id,
                               "path": prefix + path.relative_to(root).as_posix(),
                               "sha256": products._sha256(path)})
    for name in (*products.PRODUCT_NAMES, products.METADATA_NAME):
        asset_rows.append({"requirement_id": "product:restaurant_benchmark",
                           "path": "products/" + name,
                           "sha256": products._sha256(resolved.root / name)})
    assets = {
        "scope": "declared_full8_requirement_graph_and_frozen_restaurant_products_and_source",
        "files": sorted(asset_rows, key=lambda row: (row["requirement_id"], row["path"])),
        "product_sha256": dict(products.EXPECTED_PRODUCT_SHA256),
        "metadata_sha256": products._sha256(resolved.metadata),
        "source_sha256": source_digest,
        "bundle_manifest_sha256": resolved.bundle_manifest_sha256,
        "used_layer_manifest_sha256": products.EXPECTED_USED_LAYER_MANIFEST_SHA256,
    }
    manifest, controllers = normalized_execution_sources(
        request["source_sha256"], restaurant_fleet_source_manifest())
    profile = runtime_profile(args)
    authorities = {
        "evidence_scope": "native_empirical_domain",
        "task_condition_binding_sha256": request["task_conditions"]["binding_sha256"],
        "execution_dependency_manifest": manifest,
        "asset_manifest_sha256": canonical_sha256(assets),
        "controller_source_sha256": controllers,
        "runtime_profile_sha256": canonical_sha256(profile),
    }
    return {"schema": "eai.restaurant.initial_oracle_inputs", "version": 1,
            "visibility": "evaluator_only", "episode_id": request["episode_id"],
            "authorities": authorities, "asset_manifest": assets,
            "runtime_profile": profile, "seed": args.seed,
            "qualification_created": False}
