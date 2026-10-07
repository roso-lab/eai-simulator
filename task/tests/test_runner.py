from algorithm.common_coordination.contract import AlgorithmCondition, RobotCapability

from task.contracts import EaiTaskPackage, RobotState, TaskRunRequest
from task.runner import TaskRunner


def test_task_runner_is_na_without_eai_executor():
    package = EaiTaskPackage.from_json("task/config/fire_rescue.json")
    condition = AlgorithmCondition(
        algorithm_id="test",
        source_reference="local",
        implementation="test",
        execution_scope="assignment_only",
        planner="fixture",
    )

    def decide(task, robots):
        del robots
        return []

    result = TaskRunner().run(TaskRunRequest(package), decide, condition=condition)
    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"
    assert result.ledger.events[-1]["kind"] == "verdict"


def test_task_package_binds_runtime_roster_without_mutating_static_contract():
    package = EaiTaskPackage.from_json("task/config/fire_rescue.json")
    robot = RobotState(RobotCapability("z1_1", frozenset({"navigate", "manipulate"})))
    bound = package.bind_runtime(robots=(robot,), scene_id="EAI-Factory-Z1-v0")
    assert package.robots == ()
    assert bound.scene_id == "EAI-Factory-Z1-v0"
    assert bound.robots[0].capability.robot_id == "z1_1"
