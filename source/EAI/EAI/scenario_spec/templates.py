"""Scene templates: an immutable stage plus a machine-readable anchor layout.

A template is the fixed "样板" layer of instance generation. The factory stage
itself never changes; robots, entities, and targets are placed on named
anchors inside semantic zones. Anchors are derived deterministically from the
provider occupancy map, so one map revision always yields the same anchor ids.
"""

from dataclasses import dataclass

from .validation import ScenarioError


@dataclass(frozen=True)
class Zone:
    id: str
    aabb: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    kind: str
    level: int = 0  # index into SceneTemplate.levels; 0 is the ground floor


@dataclass(frozen=True)
class Anchor:
    id: str
    x: float
    y: float
    zones: tuple[str, ...]


@dataclass(frozen=True)
class Level:
    """A walkable storey of the template stage."""

    id: str
    floor_z: float  # walkable surface height in world coordinates


@dataclass(frozen=True)
class StairLink:
    """A vertical connection between levels, extracted from the stage USD."""

    id: str
    aabb: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    low_z: float
    high_z: float
    kind: str  # "stair" (steps) or "ramp" (slope)


# Extracted from factory.usd world bounding boxes (2026-09-10 probe):
#   warehouse_extras x[-10.10, 8.70] y[-9.02, 9.86] top z 2.88 (mezzanine structure)
#   SM_StairsSmall   x[-2.62, 2.99] y[-7.13, -1.12] z up to 2.57 (south-central stair)
#   RampShort_A1     x[-10.71, -2.91] y[1.54, 2.08] z 0.00 -> 0.85 (west low ramp)
#   RampShort_A1_03  x[0.40, 0.77] y[-9.70, -6.93] z 1.55 -> 2.05 (south high ramp)
# The mezzanine is /World/wall/wall_06 (user-confirmed walkable platform,
# collision-enabled static mesh): top surface 1.57 m, x[0.37, 3.07]
# y[-9.71, -6.91], reached by the south high ramp. Settle verifies the
# physics verdict on every instance that uses it.
FACTORY_LEVELS = (
    Level("ground", 0.0),
    Level("ramp_west", 0.85),  # west ramp top (x~-10.5, y~1.8), wheeled-drivable slope
    Level("mezzanine", 1.57),
)
FACTORY_STAIRS = (
    StairLink("main_stair", (-2.62, -7.13, 2.99, -1.12), 0.0, 2.57, "stair"),
    StairLink("west_ramp", (-10.71, 1.54, -2.91, 2.08), 0.0, 0.85, "ramp"),
    StairLink("south_high_ramp", (0.40, -9.70, 0.77, -6.93), 1.55, 2.05, "ramp"),
)


@dataclass(frozen=True)
class SceneTemplate:
    key: str
    scene_key: str
    zones: tuple[Zone, ...]
    anchors: tuple[Anchor, ...]
    map_sha256: str
    levels: tuple[Level, ...] = ()
    stairs: tuple[StairLink, ...] = ()


# Hand-curated semantic zones over the factory ground floor. Anchor membership
# is still filtered by the occupancy map, so a zone only exposes anchors whose
# 0.9 m clearance disc is free.
FACTORY_TEMPLATE_ZONES = (
    Zone("storage_west", (-10.0, -9.0, -4.0, 0.0), "storage"),
    Zone("ramp_top_west", (-10.65, 1.6, -9.45, 2.0), "ramp_top", level=1),
    Zone("mezzanine_south", (0.77, -9.31, 2.67, -7.31), "platform", level=2),
    Zone("yard_south", (-4.0, -9.0, 6.0, -3.0), "yard"),
    Zone("midband", (-10.0, -1.0, 10.0, 2.0), "corridor"),
    Zone("assembly_east", (2.0, 2.0, 10.0, 9.0), "workshop"),
    Zone("dock_north", (-8.0, 3.0, -1.0, 9.0), "dock"),
)


def _platform_anchors(zone, index_offset):
    """Anchors inside a platform AABB: no occupancy map exists above the
    ground floor, so free spacing is assumed and physics settles it. Narrow
    strips (ramp tops) get a single centre-line row."""
    anchors = []
    margin = 0.25
    x0, y0, x1, y1 = zone.aabb
    usable_y = (y1 - margin) - (y0 + margin)
    counter = index_offset
    if usable_y < 0.5:
        ys = [ (y0 + y1) / 2 ]
    else:
        ys = []
        y = y0 + margin + 0.25
        while y <= y1 - margin:
            ys.append(round(y, 3))
            y += 0.6
    for y in ys:
        x = x0 + margin + 0.2
        while x <= x1 - margin + 1e-9:
            anchors.append(Anchor(f"a{counter:03d}", round(x, 3), round(y, 3), (zone.id,)))
            counter += 1
            x += 0.5
    return anchors, counter


def build_template(key, scene_key, zones, occupancy) -> SceneTemplate:
    elevated_zone_ids = {zone.id for zone in zones if zone.level}
    anchors = []
    for index, (x, y) in enumerate(occupancy.placement_hints()):
        member = tuple(
            zone.id for zone in zones
            if zone.aabb[0] <= x <= zone.aabb[2] and zone.aabb[1] <= y <= zone.aabb[3]
            and zone.id not in elevated_zone_ids
        )
        if member:
            anchors.append(Anchor(f"a{index:03d}", x, y, member))
    counter = len(occupancy.placement_hints())
    for zone in zones:
        if zone.level:
            platform, counter = _platform_anchors(zone, counter)
            anchors.extend(platform)
    if not anchors:
        raise ScenarioError(f"template '{key}': no free anchors inside any zone")
    return SceneTemplate(key, scene_key, tuple(zones), tuple(anchors), occupancy.sha256)


def factory_template(occupancy=None) -> SceneTemplate:
    if occupancy is None:
        from .geometry import provider_map

        occupancy = provider_map()
    template = build_template("factory", "factory", FACTORY_TEMPLATE_ZONES, occupancy)
    return SceneTemplate(template.key, template.scene_key, template.zones, template.anchors,
                         template.map_sha256, FACTORY_LEVELS, FACTORY_STAIRS)


def zone_anchors(template: SceneTemplate, zone_id: str) -> tuple[Anchor, ...]:
    if zone_id not in {zone.id for zone in template.zones}:
        raise ScenarioError(f"unknown zone '{zone_id}' in template '{template.key}'")
    return tuple(anchor for anchor in template.anchors if zone_id in anchor.zones)


def template_view(template: SceneTemplate) -> dict:
    """Zone/anchor summary for prompts and manifests; no file paths."""
    return {
        "template_key": template.key,
        "scene_key": template.scene_key,
        "map_sha256": template.map_sha256,
        "levels": [{"id": lv.id, "floor_z": lv.floor_z} for lv in template.levels],
        "stairs": [{"id": st.id, "kind": st.kind, "low_z": st.low_z, "high_z": st.high_z,
                    "aabb_xy": list(st.aabb)} for st in template.stairs],
        "zones": [
            {"id": zone.id, "kind": zone.kind,
             "aabb_xy": [zone.aabb[0], zone.aabb[1], zone.aabb[2], zone.aabb[3]],
             "anchor_count": len(zone_anchors(template, zone.id))}
            for zone in template.zones
        ],
        "anchor_spacing_m": 1.5,
    }


# Warehouse NVD zones (metres, world frame of the cm->m overlay). Derived from
# the ground_map_0.6m occupancy and the inventory group layout: racks line the
# southern band, the northern half is the open assembly yard, the centre is a
# wide cross aisle, and the west end cap is the dock apron.
WAREHOUSE_NVD_ZONES = (
    Zone("storage_south", (-15.0, -28.0, 12.0, -6.0), "storage"),
    Zone("aisle_center", (-8.0, -4.0, 12.0, 12.0), "corridor"),
    Zone("yard_north", (-12.0, 14.0, 14.0, 38.0), "workshop"),
    Zone("dock_west", (-16.0, -6.0, -8.0, 20.0), "dock"),
    Zone("yard_east", (12.0, -10.0, 17.0, 30.0), "workshop"),
)


def warehouse_nvd_template(occupancy=None) -> SceneTemplate:
    """Template over the NVD warehouse overlay using the 0.6 m ground map."""
    if occupancy is None:
        from .geometry import provider_map

        occupancy = provider_map(scene_key="warehouse_nvd")
    return build_template("warehouse_nvd", "warehouse_nvd", WAREHOUSE_NVD_ZONES, occupancy)


def template_for(scene_key, occupancy=None) -> SceneTemplate:
    """Template dispatch by V2 scene key."""
    if scene_key == "warehouse_nvd":
        return warehouse_nvd_template(occupancy)
    if scene_key == "modern_house_nvd":
        return modern_house_nvd_template(occupancy)
    return factory_template(occupancy)


# Modern House NVD zones (metres, cm->m overlay world frame). The map shows a
# broad residence: west wing (living, with furniture), central hall corridor,
# east wing (rooms + kitchen), north strip beyond the main block.
MODERN_HOUSE_ZONES = (
    Zone("living_west", (-2.5, -0.2, 8.0, 15.2), "living"),
    Zone("hall_center", (8.0, 2.5, 24.0, 12.5), "corridor"),
    Zone("rooms_east", (24.0, 2.5, 39.0, 15.0), "room"),
    Zone("garden_south", (0.0, -2.0, 20.0, 2.0), "outdoor"),
    Zone("north_strip", (10.0, 13.0, 36.0, 15.5), "outdoor"),
)


def modern_house_nvd_template(occupancy=None) -> SceneTemplate:
    """Template over the NVD Modern House overlay using the 0.6 m ground map."""
    if occupancy is None:
        from .geometry import provider_map

        occupancy = provider_map(scene_key="modern_house_nvd")
    return build_template("modern_house_nvd", "modern_house_nvd", MODERN_HOUSE_ZONES, occupancy)
