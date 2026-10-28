"""Logical host checks plus real native MIQP integration (never a solver stub)."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from algorithm.common_coordination.contract import Assignment, FailureCode, TaskGraph
from algorithm.system.coordination.TeamWeaver.native_adapter import NativeTeamWeaverAdapter
from algorithm.system.coordination.native_runtime import sha256_file
from task.contracts import DependencyOutcome
from task.observation import WorldSnapshot
from tools.algorithm_adapters.teamweaver_logical import (
    LogicalHost, make_package, preflight, run_logical, verify_step,
)


def first_step():
    package = make_package()
    host = LogicalHost(package)
    assignments = tuple(Assignment(f"phase0_{rid}", rid, "host_unit_fixture")
                        for rid in ("mobile", "sensor"))
    before = host.observe()
    updates = host.execute_batch(assignments, {t.task_id: t for t in package.graph.tasks}, scene={})
    return package, host, assignments, updates, before, host.observe()


def test_executor_updates_positions_and_records_observations():
    package, host, assignments, updates, before, after = first_step()
    assert [r.position for r in before.robots] == [(-1., 0., 0.), (1., 0., 0.)]
    assert [r.position for r in after.robots] == [(0., 0., 0.), (0., 0., 0.)]
    assert host.completed == {"phase0_mobile", "phase0_sensor"}
    verify_step(package, assignments, updates, before, after, set())
    json.dumps(host.steps, allow_nan=False)


@pytest.mark.parametrize("change", ["target_xy", "target_position", "height", "missing", "nan", "frontier"])
def test_preflight_rejects_target_inconsistency(change):
    package = make_package()
    first = package.graph.tasks[0]
    metadata = dict(first.metadata)
    targets = dict(package.metadata["logical_targets"])
    if change in {"target_xy", "target_position"}:
        metadata[change] = [9., 1.] if change == "target_xy" else [9., 1., 0.]
    elif change == "height":
        targets["inspection"] = [0., 0., 1.]
        metadata["target_position"] = [0., 0., 1.]
    elif change == "missing":
        metadata.pop("target_xy")
        metadata.pop("target_position")
    elif change == "nan":
        metadata["target_xy"] = [float("nan"), 0.]
    else:
        first = replace(first, target_ref="return")
        metadata.update(target_xy=[2., 1.], target_position=[2., 1., 0.])
    first = replace(first, metadata=metadata)
    package = replace(package, graph=TaskGraph((first,) + package.graph.tasks[1:], "test"),
                      metadata={"logical_targets": targets})
    with pytest.raises(ValueError):
        preflight(package)


@pytest.mark.parametrize("change", ["identity", "failure", "outcome", "dependency", "timestamp", "no_motion", "duplicate"])
def test_independent_verifier_rejects_false_step(change):
    package, host, assignments, updates, before, after = first_step()
    update = updates[0]
    if change == "identity":
        update = replace(update, feedback=replace(update.feedback, robot_id="sensor"))
    elif change == "failure":
        update = replace(update, feedback=replace(update.feedback, failure_code=FailureCode.EXECUTION_FAILURE))
    elif change == "outcome":
        update = replace(update, feedback=replace(update.feedback, outcome="failed"))
    elif change == "dependency":
        update = replace(update, dependency_outcome=DependencyOutcome.TERMINAL_FAILURE)
    elif change == "timestamp":
        update = replace(update, feedback=replace(update.feedback, timestamp_s=0.))
    elif change == "no_motion":
        after = WorldSnapshot(scene_id=package.scene_id, revision=1, tick=1, robots=before.robots)
    else:
        assignments = (assignments[0], assignments[0])
    with pytest.raises(ValueError):
        verify_step(package, assignments, (update, updates[1]), before, after, set())


@pytest.mark.parametrize("kind", ["dependency", "identity", "capability", "duplicate", "repeated", "empty"])
def test_bad_dispatch_leaves_state_unchanged(kind):
    package = make_package()
    host = LogicalHost(package)
    assignments = (Assignment("phase0_mobile", "mobile", "test"),)
    if kind == "dependency":
        assignments = (Assignment("phase1_mobile", "mobile", "test"),)
    elif kind == "identity":
        assignments = (Assignment("phase0_mobile", "unknown", "test"),)
    elif kind == "capability":
        assignments = (Assignment("phase0_sensor", "mobile", "test"),)
    elif kind == "duplicate":
        assignments *= 2
    elif kind == "empty":
        assignments = ()
    else:
        host.execute_batch(assignments, {t.task_id: t for t in package.graph.tasks}, scene={})
    before = host.observe().digest
    with pytest.raises(ValueError):
        host.execute_batch(assignments, {t.task_id: t for t in package.graph.tasks}, scene={})
    assert host.observe().digest == before


def test_real_native_two_robot_two_frontier_closed_loop():
    adapter = NativeTeamWeaverAdapter()
    if not (adapter.source_root / ".git").exists() or not Path(adapter.python_executable).exists():
        pytest.skip("official source/native interpreter unavailable")
    if not callable(getattr(adapter, "replan", None)):
        pytest.skip("main agent has not integrated native adapter.replan yet")
    source_files = tuple((adapter.source_root / "TeamWeaver/planner/HRCS").rglob("*.py"))
    assert source_files
    hashes = {str(p): sha256_file(p) for p in source_files}
    receipt = run_logical(adapter)
    assert hashes == {str(p): sha256_file(p) for p in source_files}
    assert receipt["logical_success"] is True
    assert receipt["physical_success"] == "N/A" and receipt["scoreable"] is False
    assert len(receipt["steps"]) == 2
    result = receipt["result"]
    assert result["dispatch_count"] == 4
    assert len(result["feedback"]) == 4
    assert result["verdict"]["status"] == "N/A"
    decisions = [d for d in result["decisions"] if d["assignments"]]
    assert len(decisions) == 2
    for index, decision in enumerate(decisions):
        native = decision["provenance"]["native_result"]
        assert native["status"] == "Optimal"
        assert native["fallback_used"] is False
        assert native["native_error_count"] == 0
        assert native["entrypoint"].endswith("RTA.solve_miqp_phase_aware")
        assert {a["task_id"] for a in decision["assignments"]} == {
            f"phase{index}_mobile", f"phase{index}_sensor"}
        assert len({a["robot_id"] for a in decision["assignments"]}) == 2
    assert decisions[1]["provenance"]["native_input"]["x"] == [[0., 0., 0.], [0., 0., 0.]]
    assert {tuple(r["position"]) for r in receipt["steps"][1]["after"]["robots"]} == {(2., 1., 0.)}
    json.dumps(receipt, allow_nan=False)
