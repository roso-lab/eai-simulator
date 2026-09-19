"""Prepare isolated city benchmark inputs without launching Isaac Sim.

Example: python -m demo.city_benchmark.prepare --output-dir tmp/city_benchmark/prepared
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from demo.benchmark.goal_driven_contract import build_goal_participant_api, validate_goal_participant_api
from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark.config import CityPreparationConfig, integration_manifest
from demo.city_benchmark.contracts import authoring_record, build_public_task, review_readiness
from demo.city_benchmark.maps import file_hash, inspect_geometry, load_sources, rasterize, semantic_map, write_map_products
from demo.city_benchmark.selection import navigation_handoff


def _write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def prepare(output_dir: Path, *, config: CityPreparationConfig | None = None):
    config = config or CityPreparationConfig()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output_dir}")
    asset_root = config.asset_root.resolve()
    if output_dir == asset_root or asset_root in output_dir.parents:
        raise ValueError("generated outputs must not be written into city source assets")
    layout, traffic, provenance = load_sources(asset_root)
    obstacles, layers = inspect_geometry(asset_root)
    provenance["composed_usd_layers_sha256"] = layers
    grid, metadata = rasterize(layout, obstacles, resolution_m=config.resolution_m, inflation_m=config.inflation_m)
    semantics = semantic_map(layout, traffic, obstacles, metadata, grid)
    navigation = navigation_handoff(grid, metadata, semantics)
    public = build_public_task(semantics, response_budget_s=config.response_budget_s,
                               max_active_agents=config.max_active_agents)
    api = build_goal_participant_api(public)
    validate_goal_participant_api(api, task_spec=public)
    review = review_readiness(public, semantics)
    if any(r["candidate_spawn_xyz_m"] is None for r in navigation["start_reviews"]):
        review["blockers"].append("candidate_robot_spawns")
    private = authoring_record(public, provenance, obstacles, semantics)
    manifest = integration_manifest()
    manifest["counts"] = {"buildings": sum(r["kind"] == "building" for r in obstacles),
                          "static_obstacles": len(obstacles), "vehicle_routes": len(traffic["vehicle_routes"]),
                          "pedestrian_routes": len(traffic["pedestrian_routes"]),
                          "crosswalks": len(traffic["crosswalks"]),
                          "assembly_candidates": review["assembly_candidate_count"]}
    manifest["public_task_sha256"] = canonical_sha256(public)
    manifest["public_map_semantics_sha256"] = canonical_sha256(semantics)
    manifest["package_kind"] = "preparation_draft_not_goal_bundle"
    manifest["public_inputs"] = ["release/public_task.json", "release/participant_api.json", "release/maps/"]
    manifest["private_inputs"] = ["authoring/private.json", "authoring/review.json"]
    # Recheck original sources and all composed layers before publishing.
    for filename, digest in provenance["source_sha256"].items():
        if file_hash(asset_root / filename) != digest:
            raise RuntimeError(f"source changed during preparation: {filename}")
    for filename, digest in layers.items():
        if file_hash(Path(filename)) != digest:
            raise RuntimeError(f"USD dependency changed during preparation: {filename}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".city-preparation-", dir=output_dir.parent))
    try:
        _write(temp / "release/public_task.json", public)
        _write(temp / "release/participant_api.json", api)
        write_map_products(temp / "release/maps", grid, metadata, semantics)
        _write(temp / "authoring/private.json", private)
        _write(temp / "authoring/review.json", review)
        _write(temp / "authoring/navigation_config.json", navigation)
        manifest["artifact_sha256"] = {str(p.relative_to(temp)): file_hash(p)
                                      for p in sorted(temp.rglob("*")) if p.is_file()}
        _write(temp / "integration_manifest.json", manifest)
        if output_dir.exists():
            raise FileExistsError(f"output appeared during preparation: {output_dir}")
        temp.rename(output_dir)
    except BaseException:
        shutil.rmtree(temp)
        raise
    return {"output_dir": str(output_dir), "counts": manifest["counts"], "map_cells": metadata["cells"],
            "public_contract_validated": True, "scoreable": False,
            "authoring_status": review["authoring_status"], "blockers": review["blockers"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, default=CityPreparationConfig().asset_root)
    parser.add_argument("--resolution", type=float, default=.25, help="map metres per cell")
    parser.add_argument("--inflation", type=float, default=.45, help="static obstacle padding in metres")
    parser.add_argument("--response-budget", type=float, default=180.0)
    parser.add_argument("--max-active-agents", type=int, default=3)
    args = parser.parse_args(argv)
    result = prepare(args.output_dir, config=CityPreparationConfig(
        asset_root=args.asset_root, resolution_m=args.resolution, inflation_m=args.inflation,
        response_budget_s=args.response_budget, max_active_agents=args.max_active_agents))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
