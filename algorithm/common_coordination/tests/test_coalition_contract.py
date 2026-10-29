import pytest

from algorithm.common_coordination import CoalitionAssignment, CoalitionSkillRequest, ContractError


def test_coalition_assignment_normalizes_members_without_changing_membership():
    value = CoalitionAssignment(
        task_id="carry",
        robot_ids=("r2", "r1"),
        source="native-sadcher",
        role_bindings={"r2": "support", "r1": "lead"},
    )
    assert value.robot_ids == ("r1", "r2")
    assert value.role_bindings == {"r2": "support", "r1": "lead"}
    assert value.task_id == "carry"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"robot_ids": ()}, "at least two"),
        ({"robot_ids": ("r1",)}, "at least two"),
        ({"robot_ids": ("r1", "r1")}, "unique"),
        ({"robot_ids": "r1"}, "sequence"),
        ({"robot_ids": ("r1", "r2"), "role_bindings": {"r3": "lead"}}, "unknown robot"),
    ],
)
def test_coalition_assignment_rejects_unsafe_shapes(kwargs, message):
    defaults = {"task_id": "carry", "robot_ids": ("r1", "r2"), "source": "fixture"}
    defaults.update(kwargs)
    with pytest.raises(ContractError, match=message):
        CoalitionAssignment(**defaults)


def test_coalition_type_does_not_change_single_assignment_contract():
    from algorithm.common_coordination import Assignment

    single = Assignment("carry", "r1", source="fixture")
    coalition = CoalitionAssignment("carry", ("r1", "r2"), source="fixture")
    assert single.robot_id == "r1"
    assert coalition.robot_ids == ("r1", "r2")


def test_coalition_skill_request_is_lossless_and_not_started():
    request = CoalitionSkillRequest(
        CoalitionAssignment("carry", ("r2", "r1"), source="native-sadcher"),
        skill="joint_carry", sync_window_ticks=(3, 8), minimum_holders=2,
    )
    payload = request.to_dict()
    assert payload["robot_ids"] == ["r1", "r2"]
    assert payload["sync_window_ticks"] == [3, 8]
    assert payload["execution_status"] == "not_started"
    assert len(request.coalition_id) == 16


@pytest.mark.parametrize("kwargs", [
    {"sync_window_ticks": (4, 2)},
    {"sync_window_ticks": (-1, 2)},
    {"minimum_holders": 1},
    {"minimum_holders": 3},
])
def test_coalition_skill_request_rejects_unsafe_constraints(kwargs):
    with pytest.raises(ContractError):
        CoalitionSkillRequest(CoalitionAssignment("carry", ("r1", "r2"), source="fixture"), "carry", **kwargs)
