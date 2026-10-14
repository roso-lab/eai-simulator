"""Machine-readable simulator contracts for registered EAI skills."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .contracts import SkillKind, SkillRequest


SIMULATOR_SKILL_SCHEMA_VERSION = "simulator.skill.v1"
GROUND_NAVIGATION_SKILL_ID = "eai.navigation.ground_navigation"
GROUND_NAVIGATION_SKILL_IDS = frozenset({GROUND_NAVIGATION_SKILL_ID, "ground_navigation"})
AERIAL_NAVIGATION_SKILL_ID = "eai.navigation.aerial_navigation"
PICK_PLACE_SKILL_ID = "eai.manipulation.pick_place"


class SimulatorSchemaError(ValueError):
    """Raised when a fixed simulator skill input does not match its schema."""


@dataclass(frozen=True)
class SimulatorFieldSpec:
    name: str
    value_type: str
    required: bool = False
    unit: str | None = None
    frame: str | None = None
    description: str = ""
    enum_values: tuple[str, ...] = ()
    vector_size: int | None = None


@dataclass(frozen=True)
class SimulatorCommandSpec:
    command_type: str
    route: str
    payload_fields: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class SimulatorSkillSchema:
    skill_id: str
    kind: SkillKind
    version: str = SIMULATOR_SKILL_SCHEMA_VERSION
    fields: tuple[SimulatorFieldSpec, ...] = ()
    commands: tuple[SimulatorCommandSpec, ...] = ()
    target_required: bool = False
    composite: bool = False

    @property
    def field_names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields)

    @property
    def required_fields(self) -> frozenset[str]:
        return frozenset(field.name for field in self.fields if field.required)

    @property
    def command_types(self) -> frozenset[str]:
        return frozenset(command.command_type for command in self.commands)

    def validate(self, request: SkillRequest) -> None:
        if request.skill_version != self.version:
            raise SimulatorSchemaError("unsupported simulator Skill schema version")
        ground = self.skill_id == GROUND_NAVIGATION_SKILL_ID
        if request.skill_id != self.skill_id and not (ground and request.skill_id in GROUND_NAVIGATION_SKILL_IDS):
            raise SimulatorSchemaError(
                f"request skill_id {request.skill_id!r} does not match {self.skill_id!r}"
            )
        parameters = dict(request.parameters)
        unknown = sorted(set(parameters) - self.field_names)
        if unknown:
            raise SimulatorSchemaError(
                f"{self.skill_id} has undeclared parameter fields: {unknown}"
            )
        missing = sorted(self.required_fields - set(parameters))
        if missing:
            raise SimulatorSchemaError(
                f"{self.skill_id} is missing required parameter fields: {missing}"
            )
        for field in self.fields:
            if field.name in parameters:
                _validate_field(field.name, parameters[field.name], field)
        if "route_graph_digest" in parameters:
            pin = parameters["route_graph_digest"]
            if not isinstance(pin, str) or len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin):
                raise SimulatorSchemaError("route_graph_digest must be a SHA-256 hex digest")
        if self.skill_id == "eai.manipulation.pose":
            if math.hypot(*parameters["orientation_xyzw"]) <= 1e-12:
                raise SimulatorSchemaError("orientation_xyzw must be nonzero")
        if self.skill_id == PICK_PLACE_SKILL_ID and "operation" in self.field_names:
            if request.target_ref is not None or request.target_pose is None or len(request.target_pose) != 3:
                raise SimulatorSchemaError("local pick/place requires an XYZ target_pose and no target_ref")
            if any(v is None for v in parameters.values()):
                raise SimulatorSchemaError("pick/place parameters cannot be null")
            for name in ("base_orientation_xyzw", "orientation_xyzw"):
                if name in parameters and math.hypot(*parameters[name]) <= 1e-12:
                    raise SimulatorSchemaError(f"{name} must be nonzero")
            if parameters["simulation_time_s"] < 0:
                raise SimulatorSchemaError("simulation_time_s must be nonnegative")
            if request.timing is not None or request.budget is not None:
                raise SimulatorSchemaError("local pick/place uses timeout_s for its simulation-time budget")
        if self.skill_id == "eai.manipulation.joint":
            names = parameters["joint_names"]
            if not names or len(names) != len(set(names)) or len(names) != len(parameters["joint_positions"]):
                raise SimulatorSchemaError("joint_names must be unique and match joint_positions")
        if self.skill_id == AERIAL_NAVIGATION_SKILL_ID:
            if request.skill_version != self.version:
                raise SimulatorSchemaError("unsupported simulator Skill schema version")
            if any(value is None for value in parameters.values()):
                raise SimulatorSchemaError("aerial parameters cannot be null")
            if self.skill_id == AERIAL_NAVIGATION_SKILL_ID:
                if (request.target_ref is None) == (request.target_pose is None):
                    raise SimulatorSchemaError("aerial navigation requires exactly one target")
                if request.target_ref is not None:
                    _validate_field("target_ref", request.target_ref, SimulatorFieldSpec("target_ref", "string", required=True))
                if request.target_pose is not None:
                    _validate_field("target_pose", request.target_pose, SimulatorFieldSpec("target_pose", "xyz"))
            for name in ("timing", "budget"):
                value = getattr(request, name)
                if value is not None:
                    allowed = {"duration_s"}
                    if name == "timing":
                        allowed.add("deadline_s")
                    if set(value) - allowed:
                        raise SimulatorSchemaError(f"aerial {name} accepts only {sorted(allowed)}")
                    for field in allowed & set(value):
                        _validate_field(field, value[field], SimulatorFieldSpec(field, "positive_number", required=True))
            if (self.skill_id == AERIAL_NAVIGATION_SKILL_ID and "horizon_ticks" in parameters
                    and type(parameters["horizon_ticks"]) is not int):
                raise SimulatorSchemaError("horizon_ticks must be a positive integer")
        if ground:
            if request.skill_version != self.version:
                raise SimulatorSchemaError("unsupported simulator Skill schema version")
            if (request.target_ref is None) == (request.target_pose is None):
                raise SimulatorSchemaError("ground navigation requires exactly one target")
            if request.target_ref is not None and (
                not isinstance(request.target_ref, str) or not request.target_ref.strip()
            ):
                raise SimulatorSchemaError("target_ref must be a non-empty string")
            if request.target_pose is not None and len(request.target_pose) not in (2, 3):
                raise SimulatorSchemaError("ground navigation target_pose must contain XY or XYZ")
            for name, default, allowed in (
                ("intent", "start", {"start", "continue", "hold", "replan", "stop"}),
                ("mode", "velocity", {"goal", "velocity"}),
                ("frame", "base_link", {"world", "base_link"}),
            ):
                value = parameters.get(name, default)
                if not isinstance(value, str) or value not in allowed:
                    raise SimulatorSchemaError(f"ground navigation {name} is invalid")
            if "horizon_ticks" in parameters and type(parameters["horizon_ticks"]) is not int:
                raise SimulatorSchemaError("horizon_ticks must be a positive integer")
        if self.target_required and self.skill_id not in {
            AERIAL_NAVIGATION_SKILL_ID,
            GROUND_NAVIGATION_SKILL_ID,
        }:
            if request.target_ref is None and request.target_pose is None:
                raise SimulatorSchemaError(
                    f"{self.skill_id} requires target_ref or target_pose"
                )
            if request.target_ref is not None:
                _validate_field("target_ref", request.target_ref, SimulatorFieldSpec("target_ref", "string", required=True))
            if request.target_pose is not None and self.kind is SkillKind.NAVIGATION:
                if len(request.target_pose) != 3:
                    raise SimulatorSchemaError(
                        f"{self.skill_id} target_pose must be (x, y, z)"
                    )


def _validate_field(name: str, value: Any, spec: SimulatorFieldSpec) -> None:
    if value is None:
        if spec.required:
            raise SimulatorSchemaError(f"{name} cannot be null")
        return
    if spec.value_type == "positive_int":
        try:
            integer = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SimulatorSchemaError(f"{name} must be a positive integer") from exc
        if isinstance(value, bool) or integer != value or integer <= 0:
            raise SimulatorSchemaError(f"{name} must be a positive integer")
        return
    if spec.value_type == "string":
        if not isinstance(value, str) or not value.strip():
            raise SimulatorSchemaError(f"{name} must be a non-empty string")
        if spec.enum_values and value not in spec.enum_values:
            raise SimulatorSchemaError(f"{name} must be one of {spec.enum_values}")
        return
    if spec.value_type == "nonnegative_int":
        if type(value) is not int or value < 0:
            raise SimulatorSchemaError(f"{name} must be a nonnegative integer")
        return
    if spec.value_type == "finite_number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SimulatorSchemaError(f"{name} must be finite numeric")
        return
    if spec.value_type == "positive_number":
        _validate_field(name, value, SimulatorFieldSpec(name, "finite_number"))
        if value <= 0:
            raise SimulatorSchemaError(f"{name} must be positive")
        return
    if spec.value_type == "xyz":
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise SimulatorSchemaError(f"{name} must contain XYZ")
        for item in value:
            _validate_field(name, item, SimulatorFieldSpec(name, "finite_number", required=True))
        return
    if spec.value_type == "single_robot":
        if not isinstance(value, (list, tuple)) or len(value) != 1 or not isinstance(value[0], str) or not value[0].strip():
            raise SimulatorSchemaError(f"{name} must contain one robot id")
        return
    if spec.value_type == "finite_vector":
        if not isinstance(value, (list, tuple)) or not value:
            raise SimulatorSchemaError(f"{name} must be a non-empty vector")
        if spec.vector_size is not None and len(value) != spec.vector_size:
            raise SimulatorSchemaError(f"{name} must contain {spec.vector_size} values")
        for item in value:
            _validate_field(
                name,
                item,
                SimulatorFieldSpec(name, "finite_number", required=True),
            )
        return
    if spec.value_type == "string_list":
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise SimulatorSchemaError(f"{name} must be a list of non-empty strings")
        return
    raise SimulatorSchemaError(f"unsupported field type for {name}: {spec.value_type}")


SIMULATOR_SKILL_SCHEMAS: tuple[SimulatorSkillSchema, ...] = (
    SimulatorSkillSchema(
        PICK_PLACE_SKILL_ID, SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("operation", "string", required=True, enum_values=("pick", "place")),
            SimulatorFieldSpec("object_ref", "string", required=True),
            SimulatorFieldSpec("base_position_world_m", "xyz", required=True, unit="m", frame="world"),
            SimulatorFieldSpec("base_orientation_xyzw", "finite_vector", required=True, vector_size=4),
            SimulatorFieldSpec("orientation_xyzw", "finite_vector", vector_size=4),
            SimulatorFieldSpec("grasp_offset_object_m", "finite_vector", vector_size=3,
                              unit="m", frame="object", description="Object-origin to grasp-point offset."),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("simulation_time_s", "finite_number", required=True, unit="s"),
            SimulatorFieldSpec("timeout_s", "positive_number", unit="s"),
        ),
        commands=(SimulatorCommandSpec("pick_place", "PickPlaceExecutor -> registered local backend",
             ("operation", "object_ref", "target_position_base_m", "target_orientation_base_xyzw",
             "grasp_offset_object_m", "base_position_world_m", "base_orientation_xyzw",
             "world_state_seq", "simulation_time_s", "timeout_s")),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        AERIAL_NAVIGATION_SKILL_ID, SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("intent", "string", enum_values=("start", "continue", "hold", "replan", "stop")),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("frame", "string", enum_values=("world",)),
            SimulatorFieldSpec("mode", "string", enum_values=("goal",)),
            SimulatorFieldSpec("target_yaw", "finite_number", unit="rad", description="Reserved; XYZ navigation does not align yaw."),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
            SimulatorFieldSpec("route_graph_digest", "string", description="Frozen executable graph identity."),
            SimulatorFieldSpec("origin_ref", "string", description="Declared origin; measured start must be nearby."),
        ),
        commands=(SimulatorCommandSpec("goal_position", "env.set_command -> EAI position controller -> env.step", ("target_pose", "frame", "horizon_ticks")),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        GROUND_NAVIGATION_SKILL_ID,
        SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("intent", "string", enum_values=("start", "continue", "hold", "replan", "stop")),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("mode", "string", enum_values=("goal", "velocity")),
            SimulatorFieldSpec("frame", "string", enum_values=("world", "base_link")),
            SimulatorFieldSpec("target_yaw", "finite_number", unit="rad"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
            SimulatorFieldSpec("origin_ref", "string", description="Optional authored start landmark; start verifies proximity."),
            SimulatorFieldSpec("route_graph_digest", "string", description="Frozen executable topology identity from route resolution."),
        ),
        commands=(SimulatorCommandSpec(
            "base_velocity", "env.step -> EAI velocity controller",
            ("velocity", "frame", "horizon_ticks"),
            "Apply body-frame [vx, vy, wz]; physical success requires external evidence.",
        ),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        "eai.navigation.goal_position",
        SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("frame", "string", unit="m", frame="world", enum_values=("world",)),
            SimulatorFieldSpec("target_yaw", "finite_number", unit="rad", frame="world"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
            SimulatorFieldSpec("command_name", "string"),
        ),
        commands=(
            SimulatorCommandSpec(
                "goal_position",
                "base_env.set_command",
                ("target_pose", "horizon_ticks"),
            ),
            SimulatorCommandSpec(
                "goal_yaw",
                "base_env.set_command",
                ("target_yaw", "horizon_ticks"),
            ),
        ),
        target_required=True,
    ),
    SimulatorSkillSchema(
        "eai.navigation.velocity",
        SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("velocity", "finite_vector", required=True, unit="m/s,m/s,rad/s", frame="base", vector_size=3),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(SimulatorCommandSpec("velocity", "env.step", ("velocity", "horizon_ticks")),),
    ),
    SimulatorSkillSchema(
        "eai.navigation.rotor_velocity",
        SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("rotor_velocity", "finite_vector", required=True, unit="normalized"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(SimulatorCommandSpec("rotor_velocity", "env.step", ("rotor_velocity", "horizon_ticks")),),
    ),
    SimulatorSkillSchema(
        "eai.manipulation.pose",
        SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("frame_id", "string", required=True, frame="world|base_link", enum_values=("world", "base_link")),
            SimulatorFieldSpec("position", "finite_vector", required=True, unit="m", vector_size=3),
            SimulatorFieldSpec("orientation_xyzw", "finite_vector", required=True, vector_size=4),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(SimulatorCommandSpec(
            "manipulator_pose",
            "ros2.omnigraph.target_pose",
            ("frame_id", "position", "orientation_xyzw"),
        ),),
    ),
    SimulatorSkillSchema(
        "eai.manipulation.joint",
        SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("joint_names", "string_list", required=True),
            SimulatorFieldSpec("joint_positions", "finite_vector", required=True, unit="rad"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(SimulatorCommandSpec(
            "joint_position",
            "ros2.omnigraph.joint_command",
            ("joint_names", "joint_positions"),
        ),),
    ),
    SimulatorSkillSchema(
        "eai.manipulation.gripper",
        SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("gripper_position", "finite_number", required=True, unit="rad"),
            SimulatorFieldSpec("gripper_velocity", "finite_number", unit="rad/s"),
            SimulatorFieldSpec("horizon_ticks", "positive_int", unit="tick"),
        ),
        commands=(SimulatorCommandSpec(
            "gripper_position",
            "articulation.set_joint_position_target",
            ("gripper_position",),
        ),),
    ),
    SimulatorSkillSchema(
        "eai.inspection.scan",
        SkillKind.INSPECTION,
        fields=(
            SimulatorFieldSpec("observation_ref", "string", required=True),
            SimulatorFieldSpec("sensor_profile", "string", required=True),
            SimulatorFieldSpec("dwell_ticks", "positive_int", unit="tick"),
            SimulatorFieldSpec("coverage_requirement", "finite_number"),
        ),
        commands=(SimulatorCommandSpec(
            "sensor_trigger",
            "scene.sensor_adapter",
            ("observation_ref", "sensor_profile", "dwell_ticks"),
        ),),
        composite=True,
    ),
    SimulatorSkillSchema(
        "eai.coordination.handoff",
        SkillKind.COORDINATION,
        fields=(
            SimulatorFieldSpec("robot_ids", "string_list", required=True),
            SimulatorFieldSpec("phase", "string", required=True),
            SimulatorFieldSpec("sync_tick", "positive_int", required=True, unit="tick"),
            SimulatorFieldSpec("resource_ref", "string"),
            SimulatorFieldSpec("handoff_ref", "string"),
        ),
        commands=(
            SimulatorCommandSpec("handoff_request", "fleet.runtime", ("robot_ids", "phase", "sync_tick", "handoff_ref")),
            SimulatorCommandSpec("handoff_accept", "fleet.runtime", ("robot_ids", "phase", "sync_tick", "handoff_ref")),
            SimulatorCommandSpec("resource_release", "fleet.runtime", ("resource_ref", "sync_tick")),
        ),
        composite=True,
    ),
    SimulatorSkillSchema(
        "eai.external_io.bridge",
        SkillKind.EXTERNAL_IO,
        fields=(
            SimulatorFieldSpec("interface_id", "string", required=True),
            SimulatorFieldSpec("message_type", "string", required=True),
            SimulatorFieldSpec("payload", "finite_vector", required=True),
            SimulatorFieldSpec("endpoint", "string"),
            SimulatorFieldSpec("timeout_ticks", "positive_int", unit="tick"),
        ),
        commands=(
            SimulatorCommandSpec("ros_twist", "ros2.cmd_vel_bridge", ("payload", "endpoint")),
            SimulatorCommandSpec("nav_goal", "ros2.nav2_bridge", ("payload", "endpoint")),
            SimulatorCommandSpec("joint_state", "ros2.omnigraph", ("payload", "endpoint")),
        ),
    ),
)

_SCHEMA_BY_ID = {schema.skill_id: schema for schema in SIMULATOR_SKILL_SCHEMAS}
_SCHEMA_BY_ID["ground_navigation"] = _SCHEMA_BY_ID[GROUND_NAVIGATION_SKILL_ID]


def simulator_skill_schema(skill_id: str) -> SimulatorSkillSchema:
    try:
        return _SCHEMA_BY_ID[str(skill_id).strip()]
    except KeyError as exc:
        raise SimulatorSchemaError(f"unknown simulator skill schema: {skill_id!r}") from exc


def validate_simulator_skill_request(request: SkillRequest) -> SimulatorSkillSchema:
    schema = simulator_skill_schema(request.skill_id)
    schema.validate(request)
    return schema


__all__ = [
    "PICK_PLACE_SKILL_ID",
    "AERIAL_NAVIGATION_SKILL_ID",
    "GROUND_NAVIGATION_SKILL_ID",
    "GROUND_NAVIGATION_SKILL_IDS",
    "SIMULATOR_SKILL_SCHEMAS",
    "SIMULATOR_SKILL_SCHEMA_VERSION",
    "SimulatorCommandSpec",
    "SimulatorFieldSpec",
    "SimulatorSchemaError",
    "SimulatorSkillSchema",
    "simulator_skill_schema",
    "validate_simulator_skill_request",
]
