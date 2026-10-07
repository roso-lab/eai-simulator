import unittest

from algorithm.system.coordination.coherent.adapter import COHERENTAdapter
from algorithm.system.coordination.coherent import ALGORITHM_ID
from algorithm.system.coordination.coherent.contract import CapabilityProfile, ContractError, Feedback, SkillSpec, TaskSpec
from algorithm.system.coordination.coherent.fire_rescue import fire_rescue_fixture
from algorithm.system.coordination.coherent.provenance import DongliConfig, ProvenanceError


class CoherentAdapterTests(unittest.TestCase):
    def setUp(self):
        robots, skills, tasks = fire_rescue_fixture()
        self.adapter = COHERENTAdapter(robots=robots, skills=skills)
        self.tasks = tasks

    def test_algorithm_identity_is_explicitly_adapted(self):
        self.assertEqual(ALGORITHM_ID, "COHERENT-adapted-to-EAI")

    def test_fixture_runs_with_precedence_and_ledger(self):
        def execute(command):
            return Feedback(command.task_id, command.robot_id, True, "fixture milestone verified")

        report = self.adapter.run(self.tasks, execute)
        self.assertTrue(report.terminal)
        self.assertEqual(set(report.completed_tasks), {task.task_id for task in self.tasks})
        self.assertEqual(report.steps, len(self.tasks))
        self.assertIn("N/A: member-exit recovery", report.unsupported)
        self.assertEqual(report.ledger[0].kind, "proposal")
        self.assertEqual(report.ledger[-1].kind, "terminal")

    def test_feedback_failure_reassigns_to_capable_robot(self):
        robots, skills, tasks = fire_rescue_fixture()
        # Two scouts make the adjustment observable without changing the task contract.
        from algorithm.system.coordination.coherent.contract import CapabilityProfile
        robots = (CapabilityProfile("scout_uav_1", frozenset({"thermal_scan"})), CapabilityProfile("scout_uav_2", frozenset({"thermal_scan"}))) + robots[1:]
        adapter = COHERENTAdapter(robots=robots, skills=skills)
        calls = {"locate_fire": 0}

        def execute(command):
            if command.task_id == "locate_fire" and calls[command.task_id] == 0:
                calls[command.task_id] += 1
                return Feedback(command.task_id, command.robot_id, False, "scout unavailable", capability_lost=True)
            return Feedback(command.task_id, command.robot_id, True)

        report = adapter.run(tasks, execute)
        self.assertTrue(report.terminal)
        self.assertTrue(any(event.kind == "adjustment" for event in report.ledger))
        self.assertIn("scout_uav_2", {event.payload.get("robot_id") for event in report.ledger if event.kind == "adjustment"})

    def test_invalid_planner_assignment_fails_closed(self):
        from algorithm.system.coordination.coherent.contract import PlanAssignment, Proposal

        def planner(tasks, robots):
            return Proposal(tuple(PlanAssignment(task.task_id, "missing") for task in tasks), source="test")

        adapter = COHERENTAdapter(robots=self.adapter.robots, skills=self.adapter.skills, planner=planner)
        with self.assertRaises(ValueError):
            adapter.propose(self.tasks)

    def test_repeated_runs_reset_ledger_and_steps(self):
        def execute(command):
            return Feedback(command.task_id, command.robot_id, True)

        first = self.adapter.run(self.tasks, execute)
        second = self.adapter.run(self.tasks, execute)
        self.assertEqual(first.steps, second.steps)
        self.assertEqual(first.ledger[0].sequence, 0)
        self.assertEqual(second.ledger[0].sequence, 0)

    def test_provenance_is_redacted_and_selects_requested_model(self):
        import os

        old = {key: os.environ.get(key) for key in ("DONGLI_BASE_URL", "DONGLI_API_KEY")}
        try:
            os.environ["DONGLI_BASE_URL"] = "https://example.invalid/v1"
            os.environ["DONGLI_API_KEY"] = "secret-not-for-output"
            config = DongliConfig.from_env(model="gpt-6-luna")
            manifest = config.manifest()
            self.assertEqual(manifest["model"], "gpt-6-luna")
            self.assertTrue(manifest["api_key_present"])
            self.assertNotIn("secret-not-for-output", repr(manifest))
        finally:
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_provenance_requires_http_url(self):
        import os

        old = os.environ.get("DONGLI_BASE_URL")
        try:
            os.environ["DONGLI_BASE_URL"] = "file:///tmp/key"
            with self.assertRaises(ProvenanceError):
                DongliConfig.from_env()
        finally:
            if old is None:
                os.environ.pop("DONGLI_BASE_URL", None)
            else:
                os.environ["DONGLI_BASE_URL"] = old

    def test_dependency_cycle_fails_closed(self):
        cyclic = (
            TaskSpec("a", "A", frozenset({"thermal_scan"}), dependencies=("b",)),
            TaskSpec("b", "B", frozenset({"thermal_scan"}), dependencies=("a",)),
        )
        with self.assertRaises(ContractError):
            self.adapter.propose(cyclic)

    def test_executor_feedback_must_match_pending_command(self):
        self.adapter.propose(self.tasks)
        command = self.adapter.next_action()
        self.assertIsNotNone(command)
        wrong = Feedback("clear_route", "carrier_ugv", True)
        with self.assertRaises(ContractError):
            self.adapter.feedback(wrong)

    def test_malformed_executor_result_fails_closed(self):
        with self.assertRaises(ContractError):
            self.adapter.run(self.tasks, lambda command: object())

    def test_no_alternate_robot_is_reported_and_run_stalls(self):
        robots = (CapabilityProfile("only_scout", frozenset({"thermal_scan"})),)
        adapter = COHERENTAdapter(
            robots=robots,
            skills=(SkillSpec("scan_fire", frozenset({"thermal_scan"})),),
        )
        task = (TaskSpec("locate", "Locate fire", frozenset({"thermal_scan"})),)

        def execute(command):
            return Feedback(command.task_id, command.robot_id, False, capability_lost=True)

        report = adapter.run(task, execute)
        self.assertFalse(report.terminal)
        self.assertIn("locate", report.failed_tasks)
        self.assertTrue(any(event.kind == "adjustment_failed" for event in report.ledger))

    def test_max_steps_must_be_positive_integer(self):
        with self.assertRaises(ContractError):
            self.adapter.run(self.tasks, lambda command: Feedback(command.task_id, command.robot_id, True), max_steps=0)


if __name__ == "__main__":
    unittest.main()
