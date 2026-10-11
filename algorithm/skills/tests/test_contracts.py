import pytest
import json

from algorithm.skills.contracts import (
    SkillCommand,
    SkillContractError,
    SkillKind,
    SkillPlan,
    SkillRequest,
)
from algorithm.skills.registry import SKILL_REGISTRY
from algorithm.system.coordination.registry import system_algorithm_records
from algorithm.skills.simulator_schema import (
    SimulatorSchemaError, SimulatorSkillSchema,
    simulator_skill_schema, validate_simulator_skill_request,
)


def test_skill_request_and_plan_are_serialization_friendly():
    request = SkillRequest(
        request_id="r-1",
        skill_id="global_2d_navigation",
        robot_id="z1_1",
        target_pose=(1.0, 2.0),
        parameters={"map": "factory"},
    )
    plan = SkillPlan(
        request_id=request.request_id,
        skill_id=request.skill_id,
        robot_id=request.robot_id,
        commands=(SkillCommand("velocity", {"vx": 0.2, "wz": 0.0}),),
    )
    assert request.target_pose == (1.0, 2.0)
    assert plan.commands[0].command_type == "velocity"


def test_skill_contract_rejects_invalid_pose_and_empty_command():
    with pytest.raises(SkillContractError):
        SkillRequest("r-1", "navigation", "z1_1", target_pose=(1.0,))
    with pytest.raises(SkillContractError):
        SkillCommand("", {})


def test_registries_keep_system_and_skill_layers_distinct():
    system = system_algorithm_records()
    assert system
    assert all(item["layer"] == "system" for item in system)
    assert all("AlgorithmDecision" in item["output_contract"] for item in system)
    assert {item["kind"] for item in SKILL_REGISTRY} >= {
        SkillKind.NAVIGATION.value,
        SkillKind.EXTERNAL_IO.value,
    }
    assert not any(item["kind"] == "system" for item in SKILL_REGISTRY)


@pytest.mark.parametrize("size", [2, 3, 6, 7])
def test_request_context_roundtrips_without_narrowing_existing_pose_shapes(size):
    request = SkillRequest(
        "r-1", "navigation", "z1_1", target_pose=tuple(float(i) for i in range(size)),
        task_id="task-1", branch_context={"branch_id": "private-branch"},
        timing={"tick": 3}, budget={"ticks": 10}, simulator_snapshot={"seq": 3})
    mapping = request.to_mapping()
    assert SkillRequest.from_mapping(json.loads(json.dumps(mapping))) == request


@pytest.mark.parametrize("status", ["planned", "degraded", "invalid_request", "unsupported_capability"])
def test_plan_serialization_preserves_existing_statuses_and_defaults(status):
    plan = SkillPlan("r", "eai.transport.composite", "pepper_1", status=status)
    assert plan.commands == ()
    assert json.loads(plan.to_json()) == plan.to_mapping()
    assert plan.to_mapping()["status"] == status


def _ground_request(**parameters):
    return SkillRequest("r", "eai.navigation.ground_navigation", "carter_1",
                        target_ref="place:goal", parameters={"world_state_seq": 1, **parameters})


def test_schema_lookup_and_validation_keep_existing_return_contract_and_pepper_schema():
    ground = simulator_skill_schema("eai.navigation.ground_navigation")
    assert isinstance(ground, SimulatorSkillSchema)
    assert validate_simulator_skill_request(_ground_request()) is ground
    assert ground.commands[0].command_type == "base_velocity"
    assert simulator_skill_schema("ground_navigation") is ground
    assert {item["skill_id"] for item in SKILL_REGISTRY} >= {
        "eai.navigation.ground_navigation", "eai.transport.composite"}
    request = SkillRequest("pepper", "eai.transport.composite", "pepper_1", parameters={
        "object_ref": "box", "source_ref": "source", "destination_ref": "destination",
        "carry_mode": "bilateral_ideal_attachment"})
    assert validate_simulator_skill_request(request) is simulator_skill_schema("eai.transport.composite")


@pytest.mark.parametrize("parameters", [
    {"world_state_seq": True}, {"world_state_seq": -1}, {"world_state_seq": 1.0},
    {"intent": "go"}, {"intent": None}, {"mode": "trajectory"}, {"frame": "map"},
    {"target_yaw": float("nan")}, {"target_yaw": True}, {"horizon_ticks": 1.0},
    {"horizon_ticks": None}, {"horizon_ticks": 0}, {"kp": 2.0}, {"route_nodes": []},
])
def test_ground_schema_rejects_invalid_or_private_parameters(parameters):
    with pytest.raises(SimulatorSchemaError):
        validate_simulator_skill_request(_ground_request(**parameters))


def test_ground_schema_keeps_target_choice_and_version_validation_local():
    for values in (
        {"target_ref": None}, {"target_pose": (1.0, 2.0)},
        {"target_ref": None, "target_pose": (1.0,) * 6}, {"skill_version": "v0"},
    ):
        from dataclasses import replace
        with pytest.raises(SimulatorSchemaError):
            validate_simulator_skill_request(replace(_ground_request(), **values))


@pytest.mark.parametrize("change", [
    {"request_id": None}, {"robot_id": ""}, {"parameters": []},
    {"parameters": {"bad": float("inf")}}, {"unknown": 1}, {"target_pose": ["1", "2"]},
])
def test_mapping_request_rejects_missing_identity_and_non_json_data(change):
    value = {"request_id": "r", "skill_id": "navigation", "robot_id": "robot", "parameters": {}}
    value.update(change)
    with pytest.raises(SkillContractError):
        SkillRequest.from_mapping(value)
