"""Probe the factory's 3-D surface structure with downward physics raycasts.

Opens one headless factory session (settle-style, no ROS bridge), then casts
a vertical ray grid over the occupancy-map extent and records the first hit
height per cell. The artifact feeds level segmentation (ground vs mezzanine
platform), stair/ramp candidates (cells strictly between levels), and is the
data basis for layered connectivity and mobility-gated routing.

Output: JSON height map + PNG visualization + a levels summary.
"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    import common
else:
    from . import common

if str(common.REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(common.REPO_ROOT))


def cast_grid(session, extent, step):
    """extent = (xmin, ymin, xmax, ymax); returns {"rows": [[z|None, ...], ...]}."""
    import warp as wp

    from isaaclab.sim.converters import MeshConverter, MeshConverterCfg

    converter = MeshConverter(MeshConverterCfg(prim_path="/World/Indoor"))
    mesh = converter.mesh
    xmin, ymin, xmax, ymax = extent
    rows = []
    y = ymin + step / 2
    while y < ymax:
        row = []
        x = xmin + step / 2
        while x < xmax:
            query = wp.mesh_query_ray(mesh, wp.vec3(float(x), float(y), 8.0),
                                      wp.vec3(0.0, 0.0, -1.0), 16.0)
            row.append(round(8.0 - query.t, 3) if query else None)
            x += step
        rows.append(row)
        y += step
    return {"rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=None,
                        help="Scenario JSON for the session (default: minimal single-robot factory)")
    parser.add_argument("--step", type=float, default=0.25, help="Ray grid step in metres")
    parser.add_argument("--output", default="tmp/heightmaps/factory")
    args = parser.parse_args(argv)

    from EAI.scenario_spec.geometry import provider_map

    occupancy = provider_map()
    # Map extent in world coordinates.
    res = occupancy.resolution
    xmin = occupancy.origin[0]
    ymin = occupancy.origin[1]
    xmax = xmin + occupancy.width * res
    ymax = ymin + occupancy.height * res
    extent = (xmin, ymin, xmax, ymax)

    scenario = args.scenario or str(common.REPO_ROOT / "tmp" / "generated_scenarios" / "smoke_visual_17.json")
    if not Path(scenario).exists():
        print(json.dumps({"status": "rejected", "error": f"scenario not found: {scenario}"}))
        return 1

    from simulator import SimulatorLaunchConfig, open_simulator_session
    from EAI.scenario_spec import load_scenario

    spec = load_scenario(scenario)
    launcher_args = {"headless": True, "enable_cameras": True, "device": "cpu",
                     "num_envs": 1, "seed": spec.get("seed", 0)}
    config = SimulatorLaunchConfig(
        env=None, scenario_file=scenario, num_envs=1, device="cpu",
        seed=spec.get("seed", 0), headless=True,
        enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        app_launcher_args=launcher_args,
    )
    with open_simulator_session(config) as session:
        import torch

        actions = {agent: torch.zeros((session.num_envs, 3), device=session.device)
                   for agent in session.possible_agents}
        for _ in range(5):
            session.env.step(actions)
        heights = cast_grid(session, extent, args.step)

    rows = heights["rows"]
    hits = [z for row in rows for z in row if z is not None]
    summary = {
        "status": "probed",
        "extent": list(extent),
        "step": args.step,
        "rows": len(rows),
        "cols": len(rows[0]) if rows else 0,
        "hit_ratio": round(len(hits) / max(1, len(rows) * len(rows[0])), 3),
    }
    if hits:
        import statistics

        summary["z_min"] = round(min(hits), 3)
        summary["z_max"] = round(max(hits), 3)
        summary["z_median"] = round(statistics.median(hits), 3)
        # Level bands: cluster hit heights on 0.5 m rounding to spot surfaces.
        bands = {}
        for z in hits:
            bands[round(z * 2) / 2] = bands.get(round(z * 2) / 2, 0) + 1
        summary["z_bands_top"] = sorted(bands.items(), key=lambda kv: -kv[1])[:8]

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "factory_heightmap.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False), encoding="utf-8")
    try:
        from PIL import Image

        zmin, zmax = summary.get("z_min", 0.0), summary.get("z_max", 1.0)
        span = max(1e-6, zmax - zmin)
        pixels = []
        for row in rows:
            line = []
            for z in row:
                if z is None:
                    line.append(0)
                else:
                    line.append(int(30 + 225 * (z - zmin) / span))
            pixels.append(line)
        if pixels:
            Image.frombytes("L", (len(pixels[0]), len(pixels)), bytes(v for row in pixels for v in row)).save(
                out_dir / "factory_heightmap.png")
    except Exception as exc:  # visualization is best-effort
        summary["png_error"] = str(exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
