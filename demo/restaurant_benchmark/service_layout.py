"""Metre placements for the restaurant order-and-delivery props.

Kitchen and dining centres come from the admitted Carter routes. The two
tables sit on opposite sides of the dining centre so two ground robots can
stand at once. Dishes sit on kinematic pass shelves beside the kitchen cell,
outside the robot body. Customers use the first frame of the in-place phone-call clip and then stay
frozen. A 2026-09-28 CUDA probe recorded the dishes resting on the shelves at
root z 0.90 m and the customers standing with feet on the ground.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DISH_USD = REPO_ROOT / "usd/scene/restaurant/Assets/ServiceProps/dish_plate_apple.usda"
HUMAN_MANIFEST = REPO_ROOT / "usd/human/manifest.json"

# Admitted service cells from demo/restaurant_benchmark/navigation_probe.py.
KITCHEN_SERVICE_XY_M = (24.225, 17.625)
DINING_SERVICE_XY_M = (14.225, 21.925)
# Rest height of the dish root. A kinematic pass shelf holds the plate bottom.
# The plate bottom offset is the measured dish-stage minimum z. Keeping it as a
# constant lets layout construction avoid importing pxr before Isaac Sim starts.
DISH_Z_M = 0.90
PLATE_BOTTOM_Z_M = 0.002758140502315598
DISH_SIDE_OFFSET_M = 0.70
DROP_SIDE_OFFSET_M = 0.55
PASS_SHELF_SIZE_M = (0.42, 0.42, 0.04)
# Same plan size as the pass shelf, but deep enough that a short drop cannot
# step through the kinematic top. The top stays at the plate-bottom height.
DROP_SHELF_SIZE_M = (0.42, 0.42, 0.20)
CUSTOMER_POSE_MOTION_ID = "phone_call"
CUSTOMER_INTERACTION_DISTANCE_M = 0.80
TABLE_OFFSET_M = 0.75

TABLE_A_XY_M = (
    DINING_SERVICE_XY_M[0] - TABLE_OFFSET_M,
    DINING_SERVICE_XY_M[1] - TABLE_OFFSET_M,
)
TABLE_B_XY_M = (
    DINING_SERVICE_XY_M[0] + TABLE_OFFSET_M,
    DINING_SERVICE_XY_M[1] + TABLE_OFFSET_M,
)

_APPROACH_XY = (
    KITCHEN_SERVICE_XY_M[0] - DINING_SERVICE_XY_M[0],
    KITCHEN_SERVICE_XY_M[1] - DINING_SERVICE_XY_M[1],
)
_APPROACH_LENGTH_M = math.hypot(*_APPROACH_XY)
APPROACH_UNIT_XY = (
    _APPROACH_XY[0] / _APPROACH_LENGTH_M,
    _APPROACH_XY[1] / _APPROACH_LENGTH_M,
)
# Sideways from the kitchen standing cell, so the shelf is outside the body.
DISH_SIDE_UNIT_XY = (-APPROACH_UNIT_XY[1], APPROACH_UNIT_XY[0])
DISH_A_XY_M = (
    KITCHEN_SERVICE_XY_M[0] + DISH_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[0],
    KITCHEN_SERVICE_XY_M[1] + DISH_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[1],
)
DISH_B_XY_M = (
    KITCHEN_SERVICE_XY_M[0] - DISH_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[0],
    KITCHEN_SERVICE_XY_M[1] - DISH_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[1],
)


def _yaw_toward(origin_xy: tuple[float, float], target_xy: tuple[float, float]) -> float:
    return math.atan2(target_xy[1] - origin_xy[1], target_xy[0] - origin_xy[0])


def _drop_xy(table_xy: tuple[float, float]) -> tuple[float, float]:
    """Place the dining drop beside the customer, off the Pepper standing line."""
    return (
        table_xy[0] + DROP_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[0],
        table_xy[1] + DROP_SIDE_OFFSET_M * DISH_SIDE_UNIT_XY[1],
    )


def _pepper_xy(table_xy: tuple[float, float]) -> tuple[float, float]:
    return (
        table_xy[0] + CUSTOMER_INTERACTION_DISTANCE_M * APPROACH_UNIT_XY[0],
        table_xy[1] + CUSTOMER_INTERACTION_DISTANCE_M * APPROACH_UNIT_XY[1],
    )


def _customer_spawn_yaw(desired_yaw: float) -> float:
    manifest = json.loads(HUMAN_MANIFEST.read_text(encoding="utf-8"))
    motion = next(row for row in manifest["motions"] if row["id"] == CUSTOMER_POSE_MOTION_ID)
    return desired_yaw - float(motion.get("facing_yaw_offset", 0.0))


def service_layout() -> dict[str, Any]:
    """Return dish, customer, pass-shelf, and Pepper interaction placements."""
    pepper_a = _pepper_xy(TABLE_A_XY_M)
    pepper_b = _pepper_xy(TABLE_B_XY_M)
    plate_min_z = PLATE_BOTTOM_Z_M
    shelf_top_z = DISH_Z_M + plate_min_z
    shelf_center_z = shelf_top_z - PASS_SHELF_SIZE_M[2] / 2.0
    drop_center_z = shelf_top_z - DROP_SHELF_SIZE_M[2] / 2.0
    return {
        "schema": "eai.restaurant.service_layout",
        "version": 1,
        "dish_usd": DISH_USD.relative_to(REPO_ROOT).as_posix(),
        "height_status": "pass_shelf_contact_recorded",
        "customer_stage_status": "phone_call_frame_frozen_feet_on_ground",
        "customer_pose_motion_id": CUSTOMER_POSE_MOTION_ID,
        "pass_shelf_size_m": list(PASS_SHELF_SIZE_M),
        "drop_shelf_size_m": list(DROP_SHELF_SIZE_M),
        "dishes": {
            "service_plate_apple_a": {
                "position_m": [DISH_A_XY_M[0], DISH_A_XY_M[1], DISH_Z_M],
                "region_id": "kitchen_service",
            },
            "service_plate_apple_b": {
                "position_m": [DISH_B_XY_M[0], DISH_B_XY_M[1], DISH_Z_M],
                "region_id": "kitchen_service",
            },
        },
        "pass_shelves": {
            "pass_shelf_a": {
                "position_m": [DISH_A_XY_M[0], DISH_A_XY_M[1], shelf_center_z],
                "top_z_m": shelf_top_z,
                "supports": "service_plate_apple_a",
            },
            "pass_shelf_b": {
                "position_m": [DISH_B_XY_M[0], DISH_B_XY_M[1], shelf_center_z],
                "top_z_m": shelf_top_z,
                "supports": "service_plate_apple_b",
            },
        },
        "drop_shelves": {
            "drop_shelf_a": {
                "position_m": [*_drop_xy(TABLE_A_XY_M), drop_center_z],
                "size_m": list(DROP_SHELF_SIZE_M),
                "top_z_m": shelf_top_z,
                "accepts": "service_plate_apple_a",
                "table_id": "dining_table_a",
            },
            "drop_shelf_b": {
                "position_m": [*_drop_xy(TABLE_B_XY_M), drop_center_z],
                "size_m": list(DROP_SHELF_SIZE_M),
                "top_z_m": shelf_top_z,
                "accepts": "service_plate_apple_b",
                "table_id": "dining_table_b",
            },
        },
        "tables": {
            "dining_table_a": {"center_xy_m": list(TABLE_A_XY_M)},
            "dining_table_b": {"center_xy_m": list(TABLE_B_XY_M)},
        },
        "customers": {
            "static_customer_a": {
                "asset_id": "synbody-0000001",
                "position_m": [TABLE_A_XY_M[0], TABLE_A_XY_M[1], 0.0],
                "yaw_rad": _yaw_toward(TABLE_A_XY_M, pepper_a),
                "spawn_yaw_rad": _customer_spawn_yaw(_yaw_toward(TABLE_A_XY_M, pepper_a)),
                "pose_motion_id": CUSTOMER_POSE_MOTION_ID,
                "waypoints": [],
                "dialogue": False,
                "animate_while_idle": False,
            },
            "static_customer_b": {
                "asset_id": "synbody-0000020",
                "position_m": [TABLE_B_XY_M[0], TABLE_B_XY_M[1], 0.0],
                "yaw_rad": _yaw_toward(TABLE_B_XY_M, pepper_b),
                "spawn_yaw_rad": _customer_spawn_yaw(_yaw_toward(TABLE_B_XY_M, pepper_b)),
                "pose_motion_id": CUSTOMER_POSE_MOTION_ID,
                "waypoints": [],
                "dialogue": False,
                "animate_while_idle": False,
            },
        },
        "pepper_interaction": {
            "dining_table_a": {
                "position_xy_m": list(pepper_a),
                "yaw_rad": _yaw_toward(pepper_a, TABLE_A_XY_M),
                "distance_m": CUSTOMER_INTERACTION_DISTANCE_M,
            },
            "dining_table_b": {
                "position_xy_m": list(pepper_b),
                "yaw_rad": _yaw_toward(pepper_b, TABLE_B_XY_M),
                "distance_m": CUSTOMER_INTERACTION_DISTANCE_M,
            },
        },
    }


def measured_dish_bounds_m() -> dict[str, list[float]]:
    """World bounds of the composed dish in the metre stage."""
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.Open(str(DISH_USD))
    if stage is None:
        raise ValueError(f"cannot open dish assembly: {DISH_USD}")
    errors = [error for prim in stage.Traverse() for error in prim.GetPrimIndex().localErrors]
    if errors:
        raise ValueError(f"dish assembly has composition errors: {errors[:3]}")
    if float(UsdGeom.GetStageMetersPerUnit(stage)) != 1.0:
        raise ValueError("dish assembly must use metres")
    if str(UsdGeom.GetStageUpAxis(stage)) != "Z":
        raise ValueError("dish assembly must use a Z-up stage")
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
    box = cache.ComputeWorldBound(stage.GetDefaultPrim()).ComputeAlignedBox()
    low, high = box.GetMin(), box.GetMax()
    return {
        "min": [float(low[i]) for i in range(3)],
        "max": [float(high[i]) for i in range(3)],
        "size": [float(high[i] - low[i]) for i in range(3)],
    }


def validate_service_layout() -> dict[str, Any]:
    """Check asset closure, separation, and the static-customer contract."""
    layout = service_layout()
    if not DISH_USD.is_file():
        raise ValueError("dish assembly is missing")
    bounds = measured_dish_bounds_m()
    size = bounds["size"]
    if any(value <= 0 or value > 0.5 for value in size):
        raise ValueError(f"composed dish size is not a tabletop payload: {size}")
    if bounds["min"][2] < -0.01:
        raise ValueError("composed dish extends below the local ground")
    if abs(bounds["min"][2] - PLATE_BOTTOM_Z_M) > 1e-9:
        raise ValueError("plate bottom offset drifted from the pass-shelf constant")
    dishes = layout["dishes"]
    dish_xy = [tuple(row["position_m"][:2]) for row in dishes.values()]
    if math.dist(*dish_xy) <= max(size[0], size[1]):
        raise ValueError("kitchen dishes overlap")
    for name, row in dishes.items():
        offset = math.dist(row["position_m"][:2], KITCHEN_SERVICE_XY_M)
        if abs(offset - DISH_SIDE_OFFSET_M) > 1e-9:
            raise ValueError("dish is not on the kitchen side shelf line")
        if offset <= 0.45:
            raise ValueError("dish overlaps the kitchen standing cell")
        if row["position_m"][2] != DISH_Z_M:
            raise ValueError("dish height drifted from the authoring pass height")
        shelf = layout["pass_shelves"]["pass_shelf_a" if name.endswith("_a") else "pass_shelf_b"]
        if shelf["supports"] != name:
            raise ValueError("pass shelf supports the wrong dish")
        if tuple(shelf["position_m"][:2]) != tuple(row["position_m"][:2]):
            raise ValueError("pass shelf is not under its dish")
        drop = layout["drop_shelves"]["drop_shelf_a" if name.endswith("_a") else "drop_shelf_b"]
        if drop["accepts"] != name:
            raise ValueError("drop shelf accepts the wrong dish")
        table_xy = tuple(layout["tables"][drop["table_id"]]["center_xy_m"])
        if abs(math.dist(drop["position_m"][:2], table_xy) - DROP_SIDE_OFFSET_M) > 1e-9:
            raise ValueError("drop shelf is not beside the customer")
        if abs(shelf["top_z_m"] - (DISH_Z_M + bounds["min"][2])) > 1e-9:
            raise ValueError("pass shelf top does not meet the plate bottom")
        if abs(drop["top_z_m"] - shelf["top_z_m"]) > 1e-9:
            raise ValueError("drop shelf top does not meet the plate bottom")
        drop_size = tuple(drop["size_m"])
        if abs(drop["position_m"][2] - (drop["top_z_m"] - drop_size[2] / 2.0)) > 1e-9:
            raise ValueError("drop shelf center is not under its top")
        if drop_size[2] < 0.12:
            raise ValueError("drop shelf is too thin to catch a released dish")
    tables = [tuple(row["center_xy_m"]) for row in layout["tables"].values()]
    if math.dist(*tables) < 1.5:
        raise ValueError("dining tables are closer than two ground robots can share")
    manifest = json.loads(HUMAN_MANIFEST.read_text(encoding="utf-8"))
    enabled = {
        row["id"]: row for row in manifest["assets"]
        if row.get("enabled") and row.get("articulated") and row.get("can_play_actions")
    }
    pose = next(row for row in manifest["motions"] if row["id"] == CUSTOMER_POSE_MOTION_ID)
    if not pose.get("enabled") or pose.get("root_motion") != "in_place":
        raise ValueError("customer pose must be an enabled in-place motion")
    for table_id, customer_id in (
            ("dining_table_a", "static_customer_a"),
            ("dining_table_b", "static_customer_b")):
        customer = layout["customers"][customer_id]
        table_xy = tuple(layout["tables"][table_id]["center_xy_m"])
        pepper = layout["pepper_interaction"][table_id]
        asset = enabled.get(customer["asset_id"])
        if asset is None:
            raise ValueError(f"static customer asset is not an enabled human: {customer['asset_id']}")
        if CUSTOMER_POSE_MOTION_ID not in asset["motions"]:
            raise ValueError("static customer cannot play the frozen pose")
        if customer["pose_motion_id"] != CUSTOMER_POSE_MOTION_ID:
            raise ValueError("static customer pose drifted")
        if customer["waypoints"] or customer["dialogue"] or customer["animate_while_idle"]:
            raise ValueError("static customer must not walk, speak, or idle-animate")
        if tuple(customer["position_m"][:2]) != table_xy:
            raise ValueError("static customer must stand at the declared table")
        pepper_xy = tuple(pepper["position_xy_m"])
        if abs(math.dist(pepper_xy, table_xy) - CUSTOMER_INTERACTION_DISTANCE_M) > 1e-9:
            raise ValueError("Pepper interaction distance drifted")
        if abs(pepper["yaw_rad"] - _yaw_toward(pepper_xy, table_xy)) > 1e-9:
            raise ValueError("Pepper does not face the customer")
        if abs(customer["yaw_rad"] - _yaw_toward(table_xy, pepper_xy)) > 1e-9:
            raise ValueError("customer does not face Pepper")
    return {"layout": layout, "dish_size_m": size}
