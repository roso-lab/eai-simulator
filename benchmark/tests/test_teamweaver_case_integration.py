from __future__ import annotations

import pytest

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from benchmark.teamweaver import TeamWeaverDecisionAdapter
from task.contracts import EaiTaskPackage, RobotState


def package(*, target: bool = True):
    metadata = {"target_positions": {"dock": (2.0, 0.0, 0.0)}} if target else {}
    graph = TaskGraph((CoordinationTask("inspect", "inspect dock", frozenset({"sensing"}), target_ref="dock"),), "test")
    robots = tuple(RobotState(RobotCapability(robot_id, frozenset({"sensing"})), (float(i), 0.0, 0.0)) for i, robot_id in enumerate(("r1", "r2")))
    return EaiTaskPackage("case", "scene", graph, robots, metadata=metadata), robots


def initialized(adapter, robots):
    adapter.initialize({}, {"robot_positions": {r.capability.robot_id: r.position for r in robots}})


def test_teamweaver_end_to_end_returns_typed_decision_cpu():
    task, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    initialized(adapter, robots)
    decision = adapter(task, robots)
    assert {item.task_id for item in decision.assignments} == {"inspect"}
    assert decision.provenance["adapter"].endswith("TeamWeaverDecisionAdapter")
    assert "physical_success" not in decision.__dict__


def test_budget_exhaustion_fail_closed():
    task, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"), initialization_budget_calls=0)
    with pytest.raises(RuntimeError, match="budget exhausted"):
        initialized(adapter, robots)


def test_robot_positions_roster_mismatch_fail_closed():
    task, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    with pytest.raises(ValueError, match="roster mismatch"):
        adapter.initialize({}, {"robot_positions": {"r1": (0, 0, 0)}})


def test_malformed_task_package_fails_closed():
    task, robots = package(target=False)
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    initialized(adapter, robots)
    with pytest.raises(ValueError, match="no finite target"):
        adapter(task, robots)


def test_algorithm_exception_fails_closed():
    task, robots = package()

    class Broken:
        def allocate(self, *_args):
            raise ValueError("native failure")

    adapter = TeamWeaverDecisionAdapter(allocator=Broken(), robot_ids=("r1", "r2"))
    initialized(adapter, robots)
    with pytest.raises(RuntimeError, match="allocation failed"):
        adapter(task, robots)


def test_initialization_is_mandatory():
    task, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    with pytest.raises(RuntimeError, match="must be initialized"):
        adapter(task, robots)


def test_failed_initialization_consumes_budget():
    _, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    with pytest.raises(ValueError):
        adapter.initialize({}, {"robot_positions": {}})
    with pytest.raises(RuntimeError, match="budget exhausted"):
        initialized(adapter, robots)


def test_nonempty_history_awaits_mapping_ruling():
    _, robots = package()
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    with pytest.raises(ValueError, match="awaiting ruling"):
        adapter.initialize({"completed_task_ids": ["inspect"]}, {"robot_positions": {r.capability.robot_id: r.position for r in robots}})


def test_dependency_edges_await_mapping_ruling():
    from dataclasses import replace
    task, robots = package()
    dependent = CoordinationTask("next", "next step", prerequisites=("inspect",), target_ref="dock")
    task = replace(task, graph=TaskGraph((*task.graph.tasks, dependent), "test"))
    adapter = TeamWeaverDecisionAdapter(robot_ids=("r1", "r2"))
    initialized(adapter, robots)
    with pytest.raises(ValueError, match="awaiting ruling"):
        adapter(task, robots)


@pytest.mark.parametrize("kind", ["mapping", "foreign", "duplicate", "incomplete"])
def test_malformed_native_output_is_rejected(kind):
    from dataclasses import replace
    from algorithm.system.coordination.TeamWeaver.eai_adapter.dynamic_allocator import AllocationResult, TaskAssignment
    task, robots = package()

    class Malformed:
        def allocate(self, rs, ts):
            if kind == "mapping":
                return {}
            item = TaskAssignment(rs[0], ts[0], 0.0)
            if kind == "foreign":
                item = replace(item, robot=replace(rs[0], name="alien"))
            rows = [item, item] if kind == "duplicate" else [] if kind == "incomplete" else [item]
            return AllocationResult(rows, "miqp", 0.0)

    adapter = TeamWeaverDecisionAdapter(allocator=Malformed(), robot_ids=("r1", "r2"))
    initialized(adapter, robots)
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        adapter(task, robots)
