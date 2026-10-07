import unittest

from algorithm.system.coordination.coherent.fire_rescue_policy import ALGORITHM_ID, assign, condition_manifest
from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.system.coordination.emos.types import scenario_from_dict
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request


def _request(*, excluded=()):
    scenario = scenario_from_dict(
        {
            "scenario_id": "test_fire_rescue",
            "scene_title": "test",
            "anchor_label": "fire",
            "task_description": "test",
            "subtasks": {
                "red": {"name": "scout", "description": "terrain scouting"},
                "green": {"name": "open channel", "description": "press button"},
                "blue": {"name": "data", "description": "sensor data"},
                "yellow": {"name": "extinguish", "description": "carry extinguisher"},
            },
            "position_rules": {
                "red": {"type": "fixed", "xy": [1.0, 1.0]},
                "green": {"type": "fixed", "xy": [2.0, 2.0]},
                "blue": {"type": "fixed", "xy": [3.0, 3.0]},
                "yellow": {"type": "fixed", "xy": [4.0, 4.0]},
            },
            "robot_profiles": {
                "m20_1": {"robot_type": "M20", "capabilities": ["quadruped", "UR5"]},
                "m20_2": {"robot_type": "M20", "capabilities": ["quadruped", "UR5"]},
                "carter_1": {"robot_type": "Carter", "capabilities": ["sensor"]},
                "scout_1": {"robot_type": "Scout UR5", "capabilities": ["button"]},
            },
            "preferred_fallback": {
                "m20_1": "red", "m20_2": "yellow", "carter_1": "blue", "scout_1": "green"
            },
            "yellow_subtask_id": "yellow",
            "yellow_robot_prefix": "m20",
            "green_subtask_id": "green",
            "green_robot_name": "scout_1",
        }
    )
    return AssignmentRequest(
        task_description="test",
        scenario=scenario,
        hazard_id=1,
        hazard_pos=(0.0, 0.0, 0.0),
        robot_positions={
            "m20_1": (0.0, 0.0, 0.0),
            "m20_2": (0.0, 1.0, 0.0),
            "carter_1": (0.0, 2.0, 0.0),
            "scout_1": (0.0, 3.0, 0.0),
        },
        subtask_positions={"red": (1.0, 1.0), "green": (2.0, 2.0), "blue": (3.0, 3.0), "yellow": (4.0, 4.0)},
        completed_extinguisher=(),
        completed_rescue=(),
        rescue_constraints=None,
        excluded_subtask_ids=tuple(excluded),
    )


class FireRescuePolicyTests(unittest.TestCase):
    def test_repository_factory_scenario_contract(self):
        request = build_fire_rescue_assignment_request()
        result = assign(request)
        self.assertEqual(result, {
            "m20_1": "red",
            "scout_1": "green",
            "carter_1": "blue",
            "m20_2": "yellow",
        })

    def test_factory_mapping_is_one_to_one_and_respects_hard_constraints(self):
        result = assign(_request())
        self.assertEqual(set(result), {"m20_1", "m20_2", "carter_1", "scout_1"})
        self.assertEqual(set(result.values()), {"red", "green", "blue", "yellow"})
        self.assertEqual(result["scout_1"], "green")
        self.assertTrue(result["m20_2"].endswith("yellow"))

    def test_excluded_subtask_is_not_reintroduced(self):
        result = assign(_request(excluded=("yellow",)))
        self.assertNotIn("yellow", result.values())

    def test_completed_robot_is_not_reassigned(self):
        request = _request()
        request = request.__class__(
            **{**request.__dict__, "completed_extinguisher": ("m20_2",), "excluded_subtask_ids": ("yellow",)}
        )
        result = assign(request)
        self.assertNotIn("m20_2", result)

    def test_algorithm_identity_is_explicit(self):
        self.assertEqual(ALGORITHM_ID, "COHERENT-adapted-to-EAI")

    def test_condition_manifest_exposes_adaptation_scope(self):
        manifest = condition_manifest()
        self.assertEqual(manifest["algorithm_id"], ALGORITHM_ID)
        self.assertEqual(manifest["execution_scope"], "assignment_level")
        self.assertFalse(manifest["serial_action_loop"])
        self.assertNotIn("api_key", repr(manifest).lower())

    def test_impossible_allocation_fails_closed(self):
        request = _request()
        request = request.__class__(
            **{**request.__dict__, "robot_positions": {"carter_1": (0.0, 0.0, 0.0)}}
        )
        with self.assertRaises(ValueError):
            assign(request)

    def test_green_owner_is_hard_constraint(self):
        request = _request()
        request = request.__class__(
            **{**request.__dict__, "robot_positions": {
                "m20_1": (0.0, 0.0, 0.0),
                "m20_2": (0.0, 1.0, 0.0),
                "carter_1": (0.0, 2.0, 0.0),
            }}
        )
        with self.assertRaises(ValueError):
            assign(request)


if __name__ == "__main__":
    unittest.main()
