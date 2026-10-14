from dataclasses import replace
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from algorithm.skills import (
    G1_PICK_PLACE_SKILL_ID,
    G1PickPlaceProvider,
    SkillRequest,
    SimulatorSchemaError,
    simulator_skill_schema,
    validate_simulator_skill_request,
)
from algorithm.skills.g1_pick_place import COMMAND_ROUTE, COMMAND_TYPE
from algorithm.skills.registry import skill_records


def request(**changes):
    values = {
        "request_id": "g1-transfer-1",
        "skill_id": G1_PICK_PLACE_SKILL_ID,
        "robot_id": "g1_1",
        "parameters": {
            "pick_position": [-.26, 24.35, 1.170586],
            "place_position": [.16, 24.28, 1.1712941],
        },
    }
    values.update(changes)
    return SkillRequest(**values)


def test_schema_and_registry_publish_fixed_base_executor_contract():
    schema = simulator_skill_schema(G1_PICK_PLACE_SKILL_ID)
    assert schema.required_fields == {"pick_position", "place_position"}
    assert schema.command_types == {COMMAND_TYPE}
    assert schema.commands[0].route == COMMAND_ROUTE
    record = next(row for row in skill_records()
                  if row["skill_id"] == G1_PICK_PLACE_SKILL_ID)
    assert record["status"] == "implemented_shared_tick_binding"
    assert record["scope"] == "fixed-base calibrated Inspire right-hand USD ideal attachments"
    assert "physical" not in record["status"]


def test_provider_outputs_one_exact_world_command_and_stable_provenance():
    r = request(parameters={
        "pick_position": [-.26, 24.35, 1.170586],
        "place_position": [.16, 24.28, 1.1712941],
        "object_ref": "object:bottle",
    })
    provider = G1PickPlaceProvider()
    first = provider.plan(r, observation={"ignored": True})
    second = provider.plan(r)
    assert first.status == "planned"
    assert len(first.commands) == 1
    assert first.commands[0].command_type == "g1_pick_place"
    assert first.commands[0].payload == {
        "pick_position": [-.26, 24.35, 1.170586],
        "place_position": [.16, 24.28, 1.1712941],
        "frame": "world",
        "object_ref": "object:bottle",
    }
    assert first.metadata == second.metadata
    assert first.metadata["command_route"] == COMMAND_ROUTE
    assert first.metadata["physical_outcome"] == "external_evaluator_required"
    assert len(first.metadata["request_sha256"]) == 64
    assert json.loads(first.to_json())["commands"] == [first.commands[0].to_mapping()]


@pytest.mark.parametrize("field", ["pick_position", "place_position"])
@pytest.mark.parametrize("bad", [None, [1, 2], [1, 2, 3, 4], [1, True, 3],
                                  [1, "2", 3], [1, math.nan, 3], [1, math.inf, 3]])
def test_positions_are_required_strict_finite_world_xyz(field, bad):
    parameters = dict(request().parameters)
    if bad is None:
        parameters.pop(field)
    else:
        parameters[field] = bad
    plan = G1PickPlaceProvider().plan(request(parameters=parameters))
    assert plan.status == "invalid_request"
    assert plan.commands == ()


@pytest.mark.parametrize("parameters, reason", [
    ({"pick_position": [1, 2, 3], "place_position": [4, 5, 6], "frame": "base_link"}, "one of"),
    ({"pick_position": [1, 2, 3], "place_position": [4, 5, 6], "frame": None}, "cannot be null"),
    ({"pick_position": [1, 2, 3], "place_position": [4, 5, 6], "object_ref": ""}, "non-empty"),
    ({"pick_position": [1, 2, 3], "place_position": [4, 5, 6], "object_ref": None}, "cannot be null"),
    ({"pick_position": [1, 2, 3], "place_position": [4, 5, 6], "speed": 1}, "undeclared"),
])
def test_optional_and_unknown_parameter_fields_fail_closed(parameters, reason):
    plan = G1PickPlaceProvider().plan(request(parameters=parameters))
    assert plan.status == "invalid_request"
    assert reason in plan.metadata["reason"]


@pytest.mark.parametrize("changes, reason", [
    ({"target_ref": "object:bottle"}, "uses pick_position"),
    ({"target_pose": (1, 2, 3)}, "uses pick_position"),
    ({"skill_version": "simulator.skill.v2"}, "unsupported"),
    ({"timing": {}}, "does not accept timing"),
    ({"budget": {"duration_s": 10}}, "does not accept budget"),
    ({"skill_id": "eai.manipulation.pose"}, "does not match"),
])
def test_legacy_targets_version_limits_and_wrong_identity_are_rejected(changes, reason):
    plan = G1PickPlaceProvider().plan(request(**changes))
    assert plan.status == "invalid_request"
    assert plan.commands == ()
    assert reason in plan.metadata["reason"]


def test_shared_schema_validation_matches_provider_boundary():
    schema = validate_simulator_skill_request(request())
    assert schema.skill_id == G1_PICK_PLACE_SKILL_ID
    with pytest.raises(SimulatorSchemaError, match="uses pick_position"):
        validate_simulator_skill_request(request(target_pose=(1, 2, 3)))
    with pytest.raises(SimulatorSchemaError, match="optional parameters cannot be null"):
        validate_simulator_skill_request(request(parameters={
            "pick_position": [1, 2, 3], "place_position": [4, 5, 6],
            "object_ref": None,
        }))
    with pytest.raises(SimulatorSchemaError, match="does not accept timing"):
        validate_simulator_skill_request(request(timing={}))


def test_public_import_stays_dependency_light(tmp_path):
    root = Path(__file__).resolve().parents[3]
    code = """
import sys
from algorithm.skills import G1PickPlaceProvider, G1_PICK_PLACE_SKILL_ID
assert G1PickPlaceProvider.skill_id == G1_PICK_PLACE_SKILL_ID
assert not {'pxr', 'torch', 'omni', 'isaacsim', 'isaaclab'} & set(sys.modules)
"""
    subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=tmp_path,
        env={"PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}, check=True,
    )
