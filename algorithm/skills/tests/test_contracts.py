import pytest

from algorithm.skills.contracts import (
    SkillCommand,
    SkillContractError,
    SkillKind,
    SkillPlan,
    SkillRequest,
)
from algorithm.skills.registry import SKILL_REGISTRY
from algorithm.system.coordination.registry import system_algorithm_records


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
