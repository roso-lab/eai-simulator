#!/usr/bin/env python
"""Search a valid NVD task-layout origin against the rebuilt occupancy map.

The scene generators place every robot, entity and target at fixed offsets
from one layout origin.  After the occupancy products were rebuilt from the
complete scene geometry (``tools.assets.rebuild_nvd_products``), the previous
hand-tuned origins can sit inside real obstacles.  This helper scans the map
deterministically and prints every origin for which the generated task passes
the full admission chain (schema, geometry placement, connectivity, executor
plan and design review).  The chosen origin is then baked back into the
scene's ``challenge.py`` as a constant.

Usage (offline; no Isaac):

    python -m tools.scenario_generation.search_nvd_origin --scene warehouse_nvd
    python -m tools.scenario_generation.search_nvd_origin --scene modern_house_nvd
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATORS = {
    "warehouse_nvd": ("demo.warehouse_benchmark.challenge", (6.425, 0.0)),
    "modern_house_nvd": ("demo.modern_house_benchmark.challenge", (3.2, 0.0)),
}


def search(scene_key: str, *, step: float = 0.5, limit: int = 5):
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from EAI.scenario_spec.geometry import provider_map
    from demo.benchmark.scene_challenge import (
        audit_generated_scene_scenario,
        generate_scene_scenario,
        review_task_design,
    )

    module_name, secondary_offset = GENERATORS[scene_key]
    import importlib

    challenge = importlib.import_module(module_name)

    occupancy = provider_map(scene_key=scene_key)
    height, width = occupancy.free.shape
    res, ox, oy = occupancy.resolution, occupancy.origin[0], occupancy.origin[1]

    task_name = {
        "warehouse_nvd": "warehouse_nvd_coordinated_logistics",
        "modern_house_nvd": "modern_house_nvd_coordinated_home_move",
    }[scene_key]

    def free_with_margin(x, y, margin_cells):
        return occupancy.rectangle_free(x, y, margin_cells * res, margin_cells * res)

    if not math.isfinite(step) or step <= 0 or limit < 1:
        raise ValueError("search step must be positive and limit at least one")
    stride = max(1, math.ceil(step / res))
    candidates = []
    for grid_y in range(int(height * 0.15), int(height * 0.85), stride):
        for grid_x in range(int(width * 0.15), int(width * 0.85), stride):
            candidates.append((ox + grid_x * res, oy + grid_y * res))
    candidates.sort(key=lambda p: (p[1], p[0]))  # deterministic scan order

    found = []
    for origin in candidates:
        # cheap pre-filter: layout bounding box corners plus the robot line
        # must be free with an inflation margin before the expensive audit
        base_x, base_y = origin
        probes = [
            (base_x - 3.5, base_y, 12),  # aerial spawn (loose ground margin)
            (base_x + 1.0, base_y, 18),  # m20 with mounted arm, 0.9 m radius
            (base_x + secondary_offset[0], base_y + secondary_offset[1], 8),
            (base_x + 3.0, base_y + 3.0, 6),
            (base_x + 3.0, base_y + 2.0, 6),
            (base_x - 2.5, base_y, 4),
            (base_x + 5.0, base_y + 1.0, 4),
        ]
        if not all(free_with_margin(x, y, m) for x, y, m in probes):
            continue
        try:
            data = generate_scene_scenario(
                scene_key=scene_key, task_name=task_name, seed=17,
                origin=(base_x, base_y),
                secondary_ground_offset=secondary_offset,
            )
            audit = audit_generated_scene_scenario(data)
            design = review_task_design(
                data, challenge.scenario.TASK_PROFILE, challenge.scenario.TASK_BLUEPRINT)
        except Exception:
            continue
        if audit["validated"] and design["validated"]:
            found.append((base_x, base_y))
            print(f"[{len(found)}] origin=({base_x:.3f}, {base_y:.3f}) passes "
                  f"full admission for {scene_key}")
            if len(found) >= limit:
                break
    if not found:
        print(f"no valid origin found for {scene_key}")
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(GENERATORS), required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--step", type=float, default=0.5)
    args = parser.parse_args(argv)
    search(args.scene, limit=args.limit, step=args.step)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
