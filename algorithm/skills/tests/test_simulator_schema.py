import pytest

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.simulator_schema import (
    SIMULATOR_SKILL_SCHEMAS,
    SimulatorSchemaError,
    simulator_skill_schema,
    validate_simulator_skill_request,
)


def test_registry_covers_six_skill_kinds_without_isaac_imports():
    kinds = {schema.kind.value for schema in SIMULATOR_SKILL_SCHEMAS}
    assert kinds == {"navigation", "manipulation", "inspection", "coordination", "external_io"}
    assert all(schema.version == "simulator.skill.v1" for schema in SIMULATOR_SKILL_SCHEMAS)


def test_node_route_accepts_authored_target_and_declared_parameters():
    request = SkillRequest(
        "req-1",
        "eai.navigation.node_route",
        "robot_a",
        target_ref="node:goal",
        parameters={"world_state_seq": 2, "intent": "start"},
    )
    schema = validate_simulator_skill_request(request)
    assert schema.command_types == {"navigation_target"}


def test_node_route_rejects_undeclared_fields_and_direct_coordinates():
    with pytest.raises(SimulatorSchemaError, match="undeclared"):
        validate_simulator_skill_request(
            SkillRequest("req-2", "eai.navigation.node_route", "robot_a", target_ref="node:goal",
                         parameters={"world_state_seq": 2, "speed": 1.0})
        )
    with pytest.raises(SimulatorSchemaError, match="target_ref only"):
        validate_simulator_skill_request(
            SkillRequest("req-3", "eai.navigation.node_route", "robot_a", target_pose=(1, 2, 3),
                         parameters={"world_state_seq": 2})
        )


def test_retired_controller_specific_navigation_schemas_are_not_public_skills():
    for retired in (
        "eai.navigation.goal_position", "eai.navigation.velocity",
        "eai.navigation.rotor_velocity", "eai.navigation.ground_navigation",
    ):
        with pytest.raises(SimulatorSchemaError, match="unknown"):
            validate_simulator_skill_request(SkillRequest("retired", retired, "robot_a"))


def test_manipulation_and_coordination_fields_are_explicit():
    pose = simulator_skill_schema("eai.manipulation.pose")
    assert pose.required_fields == {"frame_id", "position", "orientation_xyzw"}
    handoff = simulator_skill_schema("eai.coordination.handoff")
    assert "handoff_request" in handoff.command_types
    assert handoff.composite is True


@pytest.mark.parametrize("skill,parameters,extra", [
    ("eai.navigation.node_route", {"world_state_seq": 1, "frame": "camera"}, {"target_ref": "node:a"}),
    ("eai.navigation.node_route", {"world_state_seq": 1}, {"target_ref": "node:a", "skill_version": "simulator.skill.v999"}),
    ("eai.navigation.node_route", {"world_state_seq": True}, {"target_ref": "node:a"}),
    ("eai.manipulation.pose", {"frame_id": "world", "position": [1], "orientation_xyzw": [0, 0, 0, 1]}, {}),
    ("eai.manipulation.pose", {"frame_id": "camera", "position": [1, 2, 3], "orientation_xyzw": [0, 0, 0, 1]}, {}),
    ("eai.manipulation.pose", {"frame_id": "world", "position": [1, 2, 3], "orientation_xyzw": [0, 0, 0, 0]}, {}),
    ("eai.manipulation.joint", {"joint_names": ["a", "b"], "joint_positions": [1]}, {}),
    ("eai.manipulation.joint", {"joint_names": ["a", "a"], "joint_positions": [1, 2]}, {}),
])
def test_fixed_shape_frame_version_and_joint_alignment(skill, parameters, extra):
    with pytest.raises(SimulatorSchemaError):
        validate_simulator_skill_request(SkillRequest("invalid", skill, "robot", parameters=parameters, **extra))
