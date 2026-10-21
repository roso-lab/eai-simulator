"""Machine-readable schemas for the public simulator Skills.

Navigation deliberately has one public contract.  ``NavigationProvider`` emits
an abstract ``navigation_target`` and controller command fields are owned by
adapter implementations under ``task.executors``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .contracts import SkillKind, SkillRequest


SIMULATOR_SKILL_SCHEMA_VERSION = "simulator.skill.v1"
NODE_ROUTE_SKILL_ID = "eai.navigation.node_route"
PICK_PLACE_SKILL_ID = "eai.manipulation.pick_place"


class SimulatorSchemaError(ValueError):
    """Raised when a fixed simulator Skill request is malformed."""


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
        if request.skill_id != self.skill_id:
            raise SimulatorSchemaError(
                f"request skill_id {request.skill_id!r} does not match {self.skill_id!r}"
            )
        if request.skill_version != self.version:
            raise SimulatorSchemaError("unsupported simulator Skill schema version")
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

        if self.skill_id == NODE_ROUTE_SKILL_ID:
            _validate_node_route_request(request, parameters)
        elif self.skill_id == PICK_PLACE_SKILL_ID:
            _validate_pick_place_request(request, parameters)
        elif self.skill_id == "eai.manipulation.pose":
            if math.hypot(*parameters["orientation_xyzw"]) <= 1e-12:
                raise SimulatorSchemaError("orientation_xyzw must be nonzero")
        elif self.skill_id == "eai.manipulation.joint":
            names = parameters["joint_names"]
            if not names or len(names) != len(set(names)) or len(names) != len(parameters["joint_positions"]):
                raise SimulatorSchemaError("joint_names must be unique and match joint_positions")

        if self.target_required and request.target_ref is None and request.target_pose is None:
            raise SimulatorSchemaError(f"{self.skill_id} requires a target")


def _validate_node_route_request(request: SkillRequest, parameters: dict[str, Any]) -> None:
    """Validate the sole navigation boundary.

    ``target_ref`` is deliberately checked at the request boundary rather than
    accepting coordinates and trying to infer an authored node later.
    """
    if any(value is None for value in parameters.values()):
        raise SimulatorSchemaError("node-route parameters cannot be null")
    if request.target_pose is not None:
        raise SimulatorSchemaError("node-route navigation accepts target_ref only")
    if not isinstance(request.target_ref, str) or not request.target_ref.strip():
        raise SimulatorSchemaError("node-route navigation requires target_ref")
    if type(parameters.get("world_state_seq")) is not int or parameters["world_state_seq"] < 0:
        raise SimulatorSchemaError("world_state_seq must be a nonnegative integer")
    if "route_graph_digest" in parameters:
        digest = parameters["route_graph_digest"]
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise SimulatorSchemaError("route_graph_digest must be a SHA-256 hex digest")
    for name, value in (("timing", request.timing), ("budget", request.budget)):
        if value is None:
            continue
        allowed = {"duration_s", "deadline_s"} if name == "timing" else {"duration_s"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise SimulatorSchemaError(f"node-route {name} accepts only {sorted(allowed)}")
        for key, item in value.items():
            _validate_field(key, item, SimulatorFieldSpec(key, "positive_number", required=True))


def _validate_pick_place_request(request: SkillRequest, parameters: dict[str, Any]) -> None:
    if request.target_ref is not None or request.target_pose is None or len(request.target_pose) != 3:
        raise SimulatorSchemaError("local pick/place requires an XYZ target_pose and no target_ref")
    if any(item is None for item in parameters.values()):
        raise SimulatorSchemaError("pick/place parameters cannot be null")
    for name in ("base_orientation_xyzw", "orientation_xyzw"):
        if name in parameters and math.hypot(*parameters[name]) <= 1e-12:
            raise SimulatorSchemaError(f"{name} must be nonzero")
    extension_fields = {
        "grasp_pose_object", "approach_direction_world",
        "pregrasp_distance_m", "retreat_distance_m", "approach_speed_m_s",
    }
    mode = parameters.get("approach_mode")
    has_cartesian_fields = bool(extension_fields.intersection(parameters))
    if mode is None:
        mode = "cartesian_pose" if has_cartesian_fields else "vertical_legacy"
    elif mode not in {"vertical_legacy", "cartesian_pose"}:
        raise SimulatorSchemaError(
            "approach_mode must be 'vertical_legacy' or 'cartesian_pose'"
        )
    if mode == "vertical_legacy" and extension_fields.intersection(parameters):
        raise SimulatorSchemaError(
            "cartesian approach fields require approach_mode='cartesian_pose'"
        )
    if mode == "cartesian_pose":
        required = {"grasp_pose_object", "approach_direction_world",
                    "pregrasp_distance_m", "retreat_distance_m",
                    "approach_speed_m_s"}
        missing = sorted(required - set(parameters))
        if missing:
            raise SimulatorSchemaError(
                f"cartesian_pose approach is missing required fields: {missing}"
            )
        grasp = parameters["grasp_pose_object"]
        if not isinstance(grasp, dict) or set(grasp) != {"position_m", "orientation_xyzw"}:
            raise SimulatorSchemaError(
                "grasp_pose_object must contain position_m and orientation_xyzw"
            )
        _validate_field("grasp_pose_object.position_m", grasp["position_m"],
                       SimulatorFieldSpec("position_m", "xyz"))
        _validate_field("grasp_pose_object.orientation_xyzw", grasp["orientation_xyzw"],
                       SimulatorFieldSpec("orientation_xyzw", "finite_vector", vector_size=4))
        if math.hypot(*grasp["orientation_xyzw"]) <= 1e-12:
            raise SimulatorSchemaError("grasp_pose_object.orientation_xyzw must be nonzero")
        direction = parameters["approach_direction_world"]
        norm = math.sqrt(sum(float(value) ** 2 for value in direction))
        if norm <= 1e-12:
            raise SimulatorSchemaError("approach_direction_world must be nonzero")
    if parameters["simulation_time_s"] < 0:
        raise SimulatorSchemaError("simulation_time_s must be nonnegative")
    if request.timing is not None or request.budget is not None:
        raise SimulatorSchemaError("local pick/place uses timeout_s for its budget")


def _validate_field(name: str, value: Any, spec: SimulatorFieldSpec) -> None:
    if value is None:
        if spec.required:
            raise SimulatorSchemaError(f"{name} cannot be null")
        return
    if spec.value_type == "positive_int":
        if type(value) is not int or value <= 0:
            raise SimulatorSchemaError(f"{name} must be a positive integer")
        return
    if spec.value_type == "string":
        if (not isinstance(value, str) or not value.strip()
                or (spec.enum_values and value not in spec.enum_values)):
            raise SimulatorSchemaError(f"{name} must be a valid string")
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
        if (not isinstance(value, (list, tuple)) or len(value) != 3
                or any(isinstance(item, bool) or not isinstance(item, (int, float))
                       or not math.isfinite(item) for item in value)):
            raise SimulatorSchemaError(f"{name} must contain XYZ")
        return
    if spec.value_type == "finite_vector":
        if (not isinstance(value, (list, tuple)) or not value
                or (spec.vector_size is not None and len(value) != spec.vector_size)):
            raise SimulatorSchemaError(f"{name} has an invalid vector size")
        if any(isinstance(item, bool) or not isinstance(item, (int, float))
               or not math.isfinite(item) for item in value):
            raise SimulatorSchemaError(f"{name} must contain finite numbers")
        return
    if spec.value_type == "string_list":
        if (not isinstance(value, (list, tuple))
                or any(not isinstance(item, str) or not item.strip() for item in value)):
            raise SimulatorSchemaError(f"{name} must be a list of nonempty strings")
        return
    if spec.value_type == "object":
        if not isinstance(value, dict):
            raise SimulatorSchemaError(f"{name} must be an object")
        return
    raise SimulatorSchemaError(f"unsupported field type for {name}: {spec.value_type}")


SIMULATOR_SKILL_SCHEMAS: tuple[SimulatorSkillSchema, ...] = (
    SimulatorSkillSchema(
        PICK_PLACE_SKILL_ID, SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("operation", "string", required=True, enum_values=("pick", "place")),
            SimulatorFieldSpec("object_ref", "string", required=True),
            SimulatorFieldSpec("base_position_world_m", "xyz", required=True),
            SimulatorFieldSpec("base_orientation_xyzw", "finite_vector", required=True, vector_size=4),
            SimulatorFieldSpec("orientation_xyzw", "finite_vector", vector_size=4),
            SimulatorFieldSpec("grasp_offset_object_m", "finite_vector", vector_size=3),
            SimulatorFieldSpec("approach_mode", "string", enum_values=("vertical_legacy", "cartesian_pose")),
            SimulatorFieldSpec("grasp_pose_object", "object"),
            SimulatorFieldSpec("approach_direction_world", "finite_vector", vector_size=3),
            SimulatorFieldSpec("pregrasp_distance_m", "positive_number", unit="m"),
            SimulatorFieldSpec("retreat_distance_m", "positive_number", unit="m"),
            SimulatorFieldSpec("approach_speed_m_s", "positive_number", unit="m/s"),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("simulation_time_s", "finite_number", required=True),
            SimulatorFieldSpec("timeout_s", "positive_number"),
        ),
        commands=(SimulatorCommandSpec("pick_place", "PickPlaceExecutor -> registered backend", ("operation", "object_ref")),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        NODE_ROUTE_SKILL_ID, SkillKind.NAVIGATION,
        fields=(
            SimulatorFieldSpec("intent", "string", enum_values=("start", "continue", "hold", "replan", "stop")),
            SimulatorFieldSpec("world_state_seq", "nonnegative_int", required=True),
            SimulatorFieldSpec("target_yaw", "finite_number", unit="rad"),
            SimulatorFieldSpec("route_graph_digest", "string", description="Frozen executable graph identity."),
            SimulatorFieldSpec("origin_ref", "string", description="Declared origin; measured start must be nearby."),
        ),
        commands=(SimulatorCommandSpec(
            "navigation_target", "NavigationExecutor -> registered controller adapter",
            ("node_id", "node_index", "position_xyz", "target_yaw"),
            "One authored topology node; the adapter owns controller command compilation.",
        ),),
        target_required=True,
    ),
    SimulatorSkillSchema(
        "eai.manipulation.pose", SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("frame_id", "string", required=True, enum_values=("world", "base_link")),
            SimulatorFieldSpec("position", "finite_vector", required=True, vector_size=3),
            SimulatorFieldSpec("orientation_xyzw", "finite_vector", required=True, vector_size=4),
            SimulatorFieldSpec("horizon_ticks", "positive_int"),
        ),
        commands=(SimulatorCommandSpec("manipulator_pose", "controller", ("frame_id", "position", "orientation_xyzw")),),
    ),
    SimulatorSkillSchema(
        "eai.manipulation.joint", SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("joint_names", "string_list", required=True),
            SimulatorFieldSpec("joint_positions", "finite_vector", required=True),
            SimulatorFieldSpec("horizon_ticks", "positive_int"),
        ),
        commands=(SimulatorCommandSpec("joint_position", "controller", ("joint_names", "joint_positions")),),
    ),
    SimulatorSkillSchema(
        "eai.manipulation.gripper", SkillKind.MANIPULATION,
        fields=(
            SimulatorFieldSpec("gripper_position", "finite_number", required=True),
            SimulatorFieldSpec("gripper_velocity", "finite_number"),
            SimulatorFieldSpec("horizon_ticks", "positive_int"),
        ),
        commands=(SimulatorCommandSpec("gripper_position", "controller", ("gripper_position",)),),
    ),
    SimulatorSkillSchema(
        "eai.inspection.scan", SkillKind.INSPECTION,
        fields=(
            SimulatorFieldSpec("observation_ref", "string", required=True),
            SimulatorFieldSpec("sensor_profile", "string", required=True),
            SimulatorFieldSpec("dwell_ticks", "positive_int"),
            SimulatorFieldSpec("coverage_requirement", "finite_number"),
        ),
        commands=(SimulatorCommandSpec("sensor_trigger", "scene.sensor_adapter", ("observation_ref", "sensor_profile")),),
        composite=True,
    ),
    SimulatorSkillSchema(
        "eai.coordination.handoff", SkillKind.COORDINATION,
        fields=(
            SimulatorFieldSpec("robot_ids", "string_list", required=True),
            SimulatorFieldSpec("phase", "string", required=True),
            SimulatorFieldSpec("sync_tick", "positive_int", required=True),
            SimulatorFieldSpec("resource_ref", "string"),
            SimulatorFieldSpec("handoff_ref", "string"),
        ),
        commands=(SimulatorCommandSpec("handoff_request", "fleet.runtime", ("robot_ids", "phase", "sync_tick")),),
        composite=True,
    ),
    SimulatorSkillSchema(
        "eai.external_io.bridge", SkillKind.EXTERNAL_IO,
        fields=(
            SimulatorFieldSpec("interface_id", "string", required=True),
            SimulatorFieldSpec("message_type", "string", required=True),
            SimulatorFieldSpec("payload", "finite_vector", required=True),
            SimulatorFieldSpec("endpoint", "string"),
            SimulatorFieldSpec("timeout_ticks", "positive_int"),
        ),
        commands=(SimulatorCommandSpec("bridge_message", "external_io", ("interface_id", "message_type", "payload")),),
    ),
)

_SCHEMA_BY_ID = {schema.skill_id: schema for schema in SIMULATOR_SKILL_SCHEMAS}


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
    "NODE_ROUTE_SKILL_ID", "PICK_PLACE_SKILL_ID", "SIMULATOR_SKILL_SCHEMAS",
    "SIMULATOR_SKILL_SCHEMA_VERSION", "SimulatorCommandSpec", "SimulatorFieldSpec",
    "SimulatorSchemaError", "SimulatorSkillSchema", "simulator_skill_schema",
    "validate_simulator_skill_request",
]
