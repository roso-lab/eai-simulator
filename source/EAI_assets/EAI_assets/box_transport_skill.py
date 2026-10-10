"""Pure state machine for composing box grasping with planar locomotion.

The state machine emits velocity commands.  Robot-specific controller adapters
feed those commands through their normal EAI command/action path and keep the
existing two-palm grasp runtime responsible for arm IK and ideal attachment.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math
from typing import Any

from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig, G1BoxGraspPhase


Vec3 = tuple[float, float, float]


class BoxTransportPhase(str, Enum):
    WAIT_FOR_GRASP = "wait_for_grasp"
    TRANSPORT = "transport"
    ARRIVAL_HOLD = "arrival_hold"
    PLACE = "place"
    DONE = "done"


@dataclass(frozen=True)
class BoxTransportConfig:
    """Geometry and closed-loop limits for a source-to-destination table move."""

    destination_offset: Vec3 = (0.0, 0.75, 0.0)
    cruise_speed_m_s: float = 0.24
    position_gain: float = 1.5
    lateral_correction_gain: float = 1.2
    yaw_correction_gain: float = 1.5
    station_keep_gain: float = 1.8
    station_keep_speed_m_s: float = 0.22
    arrival_tolerance_m: float = 0.045
    arrival_yaw_tolerance_rad: float = 0.12
    arrival_hold_s: float = 0.6
    source_settle_s: float = 0.5
    pickup_clearance_m: float = 0.01
    table_top_z: float = 0.47
    # A thin physical tabletop large enough to support every validated carton
    # size.  Robot/table contacts are filtered by the composite controller;
    # carton/table contacts remain physical at pickup and release.
    table_size: Vec3 = (0.42, 0.44, 0.08)
    table_center_x: float = 0.20
    source_table_name: str = "box_source_table"
    destination_table_name: str = "box_destination_table"

    def __post_init__(self) -> None:
        if len(self.destination_offset) != 3:
            raise ValueError("destination_offset must contain three coordinates")
        if math.hypot(float(self.destination_offset[0]), float(self.destination_offset[1])) <= 0.0:
            raise ValueError("destination_offset must include non-zero planar travel")
        for name in (
            "cruise_speed_m_s",
            "position_gain",
            "lateral_correction_gain",
            "yaw_correction_gain",
            "station_keep_gain",
            "station_keep_speed_m_s",
            "arrival_tolerance_m",
            "arrival_yaw_tolerance_rad",
            "arrival_hold_s",
            "source_settle_s",
            "pickup_clearance_m",
            "table_top_z",
        ):
            if float(getattr(self, name)) < 0.0:
                raise ValueError(f"{name} must be non-negative")
        if len(self.table_size) != 3 or any(float(item) <= 0.0 for item in self.table_size):
            raise ValueError("table_size must contain three positive values")

    @property
    def source_table_position(self) -> Vec3:
        return (
            float(self.table_center_x),
            0.0,
            float(self.table_top_z) - float(self.table_size[2]) * 0.5,
        )

    @property
    def destination_table_position(self) -> Vec3:
        return (
            float(self.table_center_x) + float(self.destination_offset[0]),
            float(self.destination_offset[1]),
            float(self.table_top_z) - float(self.table_size[2]) * 0.5,
        )


def transport_grasp_config(
    base: G1BoxGraspConfig,
    transport: BoxTransportConfig,
) -> G1BoxGraspConfig:
    """Return matching pickup/place geometry for a physical tabletop.

    The environment box-size override is applied before deriving its centre
    height, so small, nominal, and large cartons all start on the same top
    surface instead of floating or intersecting it.
    """

    resolved = G1BoxGraspConfig.from_environment(base)
    supported_center_z = float(transport.table_top_z) + float(resolved.box_size[2]) * 0.5
    return replace(
        resolved,
        box_position=(
            float(resolved.box_position[0]),
            float(resolved.box_position[1]),
            supported_center_z + float(transport.pickup_clearance_m),
        ),
        place_position=(
            float(resolved.place_position[0]),
            float(resolved.place_position[1]),
            supported_center_z,
        ),
    )


class BoxTransportState:
    """Generate autonomous planar commands around the grasp state machine."""

    def __init__(self, config: BoxTransportConfig | None = None) -> None:
        self.config = config or BoxTransportConfig()
        self.reset()

    def reset(self) -> None:
        self.phase = BoxTransportPhase.WAIT_FOR_GRASP
        self.phase_elapsed_s = 0.0
        self.initial_pose: tuple[float, float, float] | None = None
        self.last_pose: tuple[float, float, float] | None = None
        self.last_command: Vec3 = (0.0, 0.0, 0.0)
        self.completed_cycles = 0

    @property
    def freeze_carry(self) -> bool:
        return self.phase in {BoxTransportPhase.TRANSPORT, BoxTransportPhase.ARRIVAL_HOLD}

    @property
    def destination_offset(self) -> Vec3:
        return tuple(float(item) for item in self.config.destination_offset)

    def displacement(self) -> Vec3:
        if self.initial_pose is None or self.last_pose is None:
            return (0.0, 0.0, 0.0)
        return (
            float(self.last_pose[0] - self.initial_pose[0]),
            float(self.last_pose[1] - self.initial_pose[1]),
            0.0,
        )

    def _transport_command(self) -> Vec3:
        dx, dy, _ = self.displacement()
        target_x, target_y, _ = self.destination_offset
        error_x = target_x - dx
        error_y = target_y - dy
        distance = math.hypot(error_x, error_y)
        if distance <= self.config.arrival_tolerance_m:
            return (0.0, 0.0, 0.0)
        speed = min(float(self.config.cruise_speed_m_s), float(self.config.position_gain) * distance)
        vx = speed * error_x / distance
        vy = speed * error_y / distance
        if abs(error_x) < abs(error_y):
            vx = max(
                -speed,
                min(speed, float(self.config.lateral_correction_gain) * error_x),
            )
        yaw = 0.0
        if self.initial_pose is not None and self.last_pose is not None:
            yaw_error = _wrap_angle(self.initial_pose[2] - self.last_pose[2])
            yaw = max(-0.5, min(0.5, self.config.yaw_correction_gain * yaw_error))
        return (vx, vy, yaw)

    def _station_keep_command(self) -> Vec3:
        dx, dy, _ = self.displacement()
        limit = float(self.config.station_keep_speed_m_s)
        vx = max(-limit, min(limit, -float(self.config.station_keep_gain) * dx))
        vy = max(-limit, min(limit, -float(self.config.station_keep_gain) * dy))
        yaw = 0.0
        if self.initial_pose is not None and self.last_pose is not None:
            yaw_error = _wrap_angle(self.initial_pose[2] - self.last_pose[2])
            yaw = max(-0.5, min(0.5, self.config.yaw_correction_gain * yaw_error))
        if math.hypot(dx, dy) < 0.005 and abs(yaw) < 0.01:
            return (0.0, 0.0, 0.0)
        return (vx, vy, yaw)

    def _arrived(self) -> bool:
        dx, dy, _ = self.displacement()
        tx, ty, _ = self.destination_offset
        planar_error = math.hypot(tx - dx, ty - dy)
        yaw_error = 0.0
        if self.initial_pose is not None and self.last_pose is not None:
            yaw_error = abs(_wrap_angle(self.initial_pose[2] - self.last_pose[2]))
        return (
            planar_error <= self.config.arrival_tolerance_m
            and yaw_error <= self.config.arrival_yaw_tolerance_rad
        )

    def update(
        self,
        grasp_phase: G1BoxGraspPhase | str,
        attached: bool,
        planar_pose: tuple[float, float, float],
        dt_s: float,
    ) -> Vec3:
        if dt_s < 0.0:
            raise ValueError("dt_s must be non-negative")
        phase_value = grasp_phase.value if isinstance(grasp_phase, Enum) else str(grasp_phase)
        pose = tuple(float(item) for item in planar_pose)
        if self.initial_pose is None:
            self.initial_pose = pose
        self.last_pose = pose

        if self.phase is BoxTransportPhase.WAIT_FOR_GRASP:
            if attached and phase_value == G1BoxGraspPhase.CARRY.value:
                self.phase = BoxTransportPhase.TRANSPORT
                self.phase_elapsed_s = 0.0
        elif self.phase is BoxTransportPhase.TRANSPORT:
            if self._arrived():
                self.phase = BoxTransportPhase.ARRIVAL_HOLD
                self.phase_elapsed_s = 0.0
        elif self.phase is BoxTransportPhase.ARRIVAL_HOLD:
            self.phase_elapsed_s += float(dt_s)
            if self.phase_elapsed_s >= float(self.config.arrival_hold_s):
                self.phase = BoxTransportPhase.PLACE
                self.phase_elapsed_s = 0.0
        elif self.phase is BoxTransportPhase.PLACE:
            if phase_value == G1BoxGraspPhase.DONE.value:
                self.phase = BoxTransportPhase.DONE
                self.completed_cycles += 1

        if self.phase is BoxTransportPhase.WAIT_FOR_GRASP:
            # Humanoid arm motion shifts the centre of mass and can make a
            # locomotion policy creep even under a nominal zero command.  Use
            # the same real movement controller to hold the measured pickup
            # pose until the bilateral reach gate succeeds.
            self.last_command = self._station_keep_command()
        elif self.phase is BoxTransportPhase.TRANSPORT:
            self.last_command = self._transport_command()
        else:
            self.last_command = (0.0, 0.0, 0.0)
        return self.last_command

    def grasp_world_offset(self) -> Vec3:
        if self.phase is BoxTransportPhase.WAIT_FOR_GRASP:
            return (0.0, 0.0, 0.0)
        if self.phase is BoxTransportPhase.TRANSPORT:
            return self.displacement()
        return self.destination_offset

    def snapshot(self) -> dict[str, Any]:
        dx, dy, dz = self.displacement()
        tx, ty, _ = self.destination_offset
        return {
            "phase": self.phase.value,
            "phase_elapsed_s": round(float(self.phase_elapsed_s), 6),
            "freeze_carry": self.freeze_carry,
            "displacement": [dx, dy, dz],
            "destination_offset": list(self.destination_offset),
            "position_error_m": math.hypot(tx - dx, ty - dy),
            "last_command": list(self.last_command),
            "completed_cycles": int(self.completed_cycles),
        }


def _wrap_angle(value: float) -> float:
    return (float(value) + math.pi) % (2.0 * math.pi) - math.pi


def world_to_body_planar_command(command: Vec3, yaw_rad: float) -> Vec3:
    """Rotate a world-frame planar velocity into the robot body frame."""

    vx_w, vy_w, wz = (float(value) for value in command)
    cosine = math.cos(float(yaw_rad))
    sine = math.sin(float(yaw_rad))
    return (
        cosine * vx_w + sine * vy_w,
        -sine * vx_w + cosine * vy_w,
        wz,
    )


def spawn_transport_robot_with_collision_filters(
    prim_path: str,
    cfg: Any,
    translation: tuple[float, float, float] | None = None,
    orientation: tuple[float, float, float, float] | None = None,
    **kwargs: Any,
) -> Any:
    """Spawn a transport robot and author filtered pairs before tensor views.

    Adding ``FilteredPairsAPI`` after an articulation tensor view exists can
    force USD recomposition and invalidate that view (the mobile G1 asset is a
    concrete example).  This spawner wraps Isaac Lab's normal USD spawner and
    authors robot↔carton/table pairs while the scene is still being built.
    Relationship targets may safely precede creation of the cuboid prims.
    """

    import isaaclab.sim as sim_utils
    from pxr import Sdf, Usd, UsdPhysics

    spawned = sim_utils.spawn_from_usd(
        prim_path,
        cfg,
        translation=translation,
        orientation=orientation,
        **kwargs,
    )
    stage = spawned.GetStage()
    robot_paths = sim_utils.find_matching_prim_paths(prim_path, stage=stage)
    if not robot_paths:
        robot_paths = [str(spawned.GetPath())]
    target_names = (
        G1BoxGraspConfig().cube_prim_name,
        BoxTransportConfig().source_table_name,
        BoxTransportConfig().destination_table_name,
    )
    for robot_path in robot_paths:
        robot_root = stage.GetPrimAtPath(robot_path)
        env_root = robot_path.rsplit("/", 1)[0]
        targets = [Sdf.Path(f"{env_root}/{name}") for name in target_names]
        for body in Usd.PrimRange(robot_root):
            if not body.HasAPI(UsdPhysics.RigidBodyAPI):
                continue
            relation = UsdPhysics.FilteredPairsAPI.Apply(body).CreateFilteredPairsRel()
            for target in targets:
                relation.AddTarget(target)
    return spawned


def install_robot_box_collision_filter(
    env: Any,
    robot_name: str,
    cube_prim_name: str,
    support_prim_names: tuple[str, ...] = (),
) -> None:
    """Filter robot/carton/support contacts while preserving carton/table physics.

    The grasp is intentionally logical after a measured bilateral reach gate;
    allowing the dynamic carton to collide with the same palms at the same
    time creates a second, contradictory coupling and large solver impulses.
    USD collision groups remove robot↔carton contacts and, for the optional
    support prims, robot↔table contacts.  Carton and table belong to the same
    opposite group, so their mutual collision remains enabled.  This matters
    for a mobile manipulator: its palms necessarily pass the destination table
    edge while carrying the carton laterally, and that contact must not inject
    an impulse into the virtual/learned base controller.  The released carton
    still falls onto and is supported by the physical destination table.
    """

    from pxr import Sdf, Usd, UsdGeom, UsdPhysics
    import omni.usd

    robot = env.scene.articulations[robot_name]
    box = env.scene.rigid_objects[cube_prim_name]
    robot_physics_path = str(robot.root_physx_view.prim_paths[0])
    marker = f"/{robot_name}"
    marker_index = robot_physics_path.find(marker)
    robot_root_path = (
        robot_physics_path[: marker_index + len(marker)]
        if marker_index >= 0
        else robot_physics_path
    )
    box_root_path = str(box.root_physx_view.prim_paths[0])
    stage = omni.usd.get_context().get_stage()
    scope_path = "/World/box_transport_collision_filters"
    if not stage.GetPrimAtPath(scope_path).IsValid():
        UsdGeom.Scope.Define(stage, scope_path)

    safe_name = "".join(character if character.isalnum() else "_" for character in robot_name)
    robot_group_path = f"{scope_path}/{safe_name}_robot"
    box_group_path = f"{scope_path}/{safe_name}_payload_and_supports"
    robot_group = UsdPhysics.CollisionGroup.Define(stage, robot_group_path)
    box_group = UsdPhysics.CollisionGroup.Define(stage, box_group_path)
    env_root_path = box_root_path.rsplit("/", 1)[0]
    opposite_paths = [box_root_path]
    opposite_paths.extend(f"{env_root_path}/{name}" for name in support_prim_names)
    for group, included_paths in (
        (robot_group, [robot_root_path]),
        (box_group, opposite_paths),
    ):
        collection = Usd.CollectionAPI.Apply(group.GetPrim(), "colliders")
        collection.CreateExpansionRuleAttr().Set("expandPrims")
        collection.CreateIncludesRel().SetTargets([Sdf.Path(path) for path in included_paths])
    robot_group.GetFilteredGroupsRel().SetTargets([Sdf.Path(box_group_path)])
    box_group.GetFilteredGroupsRel().SetTargets([Sdf.Path(robot_group_path)])



__all__ = [
    "BoxTransportConfig",
    "BoxTransportPhase",
    "BoxTransportState",
    "transport_grasp_config",
    "world_to_body_planar_command",
    "spawn_transport_robot_with_collision_filters",
    "install_robot_box_collision_filter",
]
