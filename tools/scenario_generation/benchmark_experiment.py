"""Experimental design vocabulary for the three-scene benchmark.

This is a design manifest, not a claim that every provider asset or payload
capacity is locally available or physically calibrated.  It keeps scene,
asset, robot-combination and difficulty factors explicit so generated episodes
can be grouped into statistical families without mixing causes.
"""

from __future__ import annotations

from demo.benchmark.registry import SCENARIOS
SCENE_ASSET_KEYS = {key: scenario.ASSET_KEYS for key, scenario in SCENARIOS.items()}

# These are experiment cells, not assertions of equal physical performance.
# A capacity policy must be supplied per cell and kept fixed inside a family.
ROBOT_COMBINATION_CELLS = {
    "aerial_cf2x_ur5_pair": {
        "scout": ("cf2x",),
        "ground": ("go2", "b2", "m20", "scout", "lite3"),
        "arm": "ur5",
        "purpose": "aerial information plus two ground manipulators",
    },
    "aerial_cf2x_z1_pair": {
        "scout": ("cf2x",),
        "ground": ("carter", "go2", "b2", "m20", "scout", "lite3"),
        "arm": "z1",
        "purpose": "aerial information plus two ground manipulators",
    },
    "aerial_platform_controls": {
        "scout": ("cf2x", "iris", "pegasus"),
        "ground": ("simple_car", "pepper", "mushr_v2", "coco", "g1"),
        "arm": None,
        "purpose": "navigation and information baseline; not a payload benchmark",
    },
    "ground_homogeneous_control": {
        "scout": (),
        "ground": ("carter", "go2", "b2", "m20", "scout", "lite3"),
        "arm": "matched",
        "purpose": "same-role control condition for common-task comparisons",
    },
}

# Quality levels alter one declared factor at a time.  The resulting object
# size, mass and mode must still be written into each scenario and hashed.
DIFFICULTY_AXES = {
    "cargo_mass": ("low", "single_robot_feasible", "joint_only", "team_infeasible"),
    "clearance": ("wide", "nominal", "tight"),
    "route_length": ("short", "medium", "long"),
    "obstacle_count": (0, 2, 4),
    "information_mode": ("known_map", "scan_prerequisite", "sensor_evidence_required"),
}

SCENE_EXPERIMENT_NOTES = {key: scenario.EXPERIMENT_NOTES for key, scenario in SCENARIOS.items()}


def available_scene_assets(scene_key: str, available: dict[str, object]) -> list[str]:
    """Return the scene allowlist intersected with runtime availability."""
    if scene_key not in SCENE_ASSET_KEYS:
        raise ValueError(f"unsupported scene_key {scene_key!r}")
    return [key for key in SCENE_ASSET_KEYS[scene_key] if key in available]


__all__ = [
    "DIFFICULTY_AXES", "ROBOT_COMBINATION_CELLS", "SCENE_ASSET_KEYS",
    "SCENE_EXPERIMENT_NOTES", "available_scene_assets",
]
