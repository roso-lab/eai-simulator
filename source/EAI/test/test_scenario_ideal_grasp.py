"""Pure tests for the deterministic ideal-grasp benchmark model."""

from EAI.scenario_spec.ideal_grasp import evaluate_grasp


def _robot(identifier, robot_type="m20", manipulator="ur5"):
    return {
        "id": identifier,
        "type": robot_type,
        "attachments": [{"type": manipulator}],
    }


def _object(mass=20.0, size=(0.7, 0.5, 0.5)):
    return {"id": "crate", "size_m": list(size), "physics": {"mass_kg": mass}}


def _policy(capacity):
    return {
        "capacity_kg": capacity,
        "grasp_envelope_m": {
            "m20_1": [1.0, 1.0, 1.0],
            "carter_1": [1.0, 1.0, 1.0],
        },
    }


def test_ideal_grasp_accepts_single_robot_with_declared_capacity_and_envelope():
    result = evaluate_grasp(_robot("m20_1"), _object(), _policy({"m20_1": 30.0}))
    assert result["success"] is True
    assert result["model"] == "ideal_grasp"
    assert result["physical_grasp"] is False
    assert result["attachment"]["mode"] == "ideal_fixed_attachment"
    assert result["ownership"]["members"] == ["m20_1"]


def test_ideal_grasp_rejects_mass_above_single_capacity():
    result = evaluate_grasp(_robot("m20_1"), _object(mass=60.0), _policy({"m20_1": 40.0}))
    assert result["success"] is False
    assert "capacity" in result["reason"]


def test_ideal_grasp_accepts_joint_team_by_summed_capacity():
    result = evaluate_grasp(
        _robot("m20_1"), _object(mass=60.0),
        _policy({"m20_1": 40.0, "carter_1": 40.0}),
        team=[_robot("carter_1", "carter", manipulator="z1")],
    )
    assert result["success"] is True
    assert result["ownership"]["kind"] == "joint_team"
    assert result["joint_capacity_kg"] == 80.0


def test_ideal_grasp_fails_closed_without_manipulator_or_envelope():
    robot = {"id": "carter_1", "type": "carter", "attachments": []}
    result = evaluate_grasp(robot, _object(), _policy({"carter_1": 40.0}))
    assert result["success"] is False
    assert "manipulation capability" in result["reason"]
