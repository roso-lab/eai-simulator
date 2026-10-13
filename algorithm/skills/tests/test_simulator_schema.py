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
    assert kinds == {"navigation", "manipulation", "transport", "inspection", "coordination", "external_io"}
    assert all(schema.version == "simulator.skill.v1" for schema in SIMULATOR_SKILL_SCHEMAS)


def test_goal_navigation_accepts_fixed_target_and_declared_parameters():
    request = SkillRequest(
        "req-1",
        "eai.navigation.goal_position",
        "robot_a",
        target_pose=(1.0, 2.0, 0.5),
        parameters={"horizon_ticks": 2, "frame": "world"},
    )
    schema = validate_simulator_skill_request(request)
    assert "goal_position" in schema.command_types


def test_goal_navigation_rejects_undeclared_or_wrong_pose_fields():
    with pytest.raises(SimulatorSchemaError, match="undeclared"):
        validate_simulator_skill_request(
            SkillRequest("req-2", "eai.navigation.goal_position", "robot_a", target_pose=(1, 2, 3), parameters={"speed": 1.0})
        )
    with pytest.raises(SimulatorSchemaError, match="target_pose"):
        validate_simulator_skill_request(
            SkillRequest("req-3", "eai.navigation.goal_position", "robot_a", target_pose=(1, 2), parameters={})
        )


def test_velocity_schema_requires_vector_and_rotor_schema_has_env_step_route():
    with pytest.raises(SimulatorSchemaError, match="required"):
        validate_simulator_skill_request(SkillRequest("req-4", "eai.navigation.velocity", "robot_a"))
    request = SkillRequest(
        "req-5",
        "eai.navigation.rotor_velocity",
        "cf2x_1",
        parameters={"rotor_velocity": [0.1, 0.2, 0.3, 0.4], "horizon_ticks": 1},
    )
    schema = validate_simulator_skill_request(request)
    assert schema.commands[0].route == "env.step"


def test_manipulation_and_coordination_fields_are_explicit():
    pose = simulator_skill_schema("eai.manipulation.pose")
    assert pose.required_fields == {"frame_id", "position", "orientation_xyzw"}
    handoff = simulator_skill_schema("eai.coordination.handoff")
    assert "handoff_request" in handoff.command_types
    assert handoff.composite is True
