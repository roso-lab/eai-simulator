"""Focused offline checks for derived success predicates and the step-0 filter."""

import copy
from pathlib import Path

import pytest

from EAI.scenario_spec.evaluation import (
    derive_success_predicates,
    evaluate_predicate,
    validate_evaluation,
)
from EAI.scenario_spec.operators import instantiate
from EAI.scenario_spec.validation import ScenarioError

from test_scenario_family import _synthetic_template, _spec


def _instance(tmp_path: Path):
    template, occupancy = _synthetic_template(tmp_path)
    return instantiate(_spec(template), template, occupancy, seed=5)


def test_predicates_derive_from_transport_nodes(tmp_path: Path) -> None:
    data = _instance(tmp_path)
    predicates = derive_success_predicates(data)
    assert predicates == [{"fn": "is_at_zone", "args": ["cargo", "delivery"]}]
    report = validate_evaluation(data)
    assert report["status"] == "evaluation_validated"
    assert report["step0"] == {"is_at_zone(cargo,delivery)": False}


def test_goal_state_flips_the_predicate(tmp_path: Path) -> None:
    data = _instance(tmp_path)
    predicate = derive_success_predicates(data)[0]
    (zx, zy) = next(t["position"][:2] for t in data["composition"]["targets"]
                    if t["id"] == "delivery")
    delivered = {"cargo": (zx, zy)}
    assert evaluate_predicate(data, predicate, positions=delivered) is True


def test_trivial_transport_goal_is_rejected(tmp_path: Path) -> None:
    data = _instance(tmp_path)
    target = next(t for t in data["composition"]["targets"] if t["id"] == "delivery")
    entity = next(e for e in data["composition"]["entities"] if e["id"] == "cargo")
    entity["spawn_pose"]["position"][:2] = target["position"][:2]
    with pytest.raises(ScenarioError, match="trivial task"):
        validate_evaluation(data)


def test_degenerate_navigate_leg_is_rejected(tmp_path: Path) -> None:
    data = _instance(tmp_path)
    node = next(n for n in data["phase_graph"]["nodes"] if n["skill_key"] == "navigate")
    # This navigate leg points at the cargo entity, not a zone target.
    goal_xy = next(e["spawn_pose"]["position"][:2] for e in data["composition"]["entities"]
                   if e["id"] == node["target_ref"])
    robot = next(r for r in data["robots"] if r["id"] == node["preferred_agent"])
    robot["spawn_pose"]["position"][:2] = goal_xy
    with pytest.raises(ScenarioError, match="trivial task"):
        validate_evaluation(data)
    _ = copy.deepcopy(data)  # keep the mutation local to this test


def test_llm_style_dag_with_duplicate_goals_dedupes(tmp_path: Path) -> None:
    data = _instance(tmp_path)
    data["phase_graph"]["nodes"].append({
        "id": "recheck", "skill_key": "transport_object",
        "preferred_agent": "carter_1", "target_ref": "delivery",
        "object_ref": "cargo", "prerequisites": ["transport"], "timeout_s": 30.0,
    })
    predicates = derive_success_predicates(data)
    assert len(predicates) == 1


def test_runtime_evaluator_trajectory_and_summary(tmp_path: Path) -> None:
    from EAI.scenario_spec.runtime_evaluator import (
        PredicateEvaluator,
        summarize_trajectory,
    )

    data = _instance(tmp_path)
    evaluator = PredicateEvaluator(data)
    spawn = {e["id"]: tuple(e["spawn_pose"]["position"][:2])
             for e in data["composition"]["entities"]}
    verdict = evaluator.evaluate(spawn)
    assert verdict == {"is_at_zone(cargo,delivery)": False}

    (zx, zy) = next(t["position"][:2] for t in data["composition"]["targets"]
                    if t["id"] == "delivery")
    delivered = dict(spawn, cargo=(zx, zy))
    assert evaluator.evaluate(delivered) == {"is_at_zone(cargo,delivery)": True}
    assert evaluator.percent_complete(delivered) == 100.0

    trajectory = [evaluator.trajectory_step(0, spawn),
                  evaluator.trajectory_step(50, spawn),
                  evaluator.trajectory_step(100, delivered)]
    summary = summarize_trajectory(trajectory)
    assert summary["verdict"] == "success"
    assert summary["first_satisfied_step"]["is_at_zone(cargo,delivery)"] == 100

    summary = summarize_trajectory([evaluator.trajectory_step(s, spawn) for s in (0, 50, 100)])
    assert summary["verdict"] == "failure" and summary["percent"] == 0.0


def test_runtime_evaluator_requires_sampled_objects(tmp_path: Path) -> None:
    from EAI.scenario_spec.runtime_evaluator import PredicateEvaluator
    from EAI.scenario_spec.validation import ScenarioError

    data = _instance(tmp_path)
    evaluator = PredicateEvaluator(data)
    with pytest.raises(ScenarioError, match="missing sampled position"):
        evaluator.evaluate({})


def test_summarize_empty_contract_is_not_success() -> None:
    from EAI.scenario_spec.runtime_evaluator import summarize_trajectory

    trajectory = [{"step": 0, "satisfied": {}, "percent": 0.0}]
    summary = summarize_trajectory(trajectory)
    assert summary["verdict"] == "no_predicates" and summary["percent"] is None


def test_waypoint_predicates_visitation_semantics(tmp_path: Path) -> None:
    from EAI.scenario_spec.evaluation import derive_success_predicates
    from EAI.scenario_spec.runtime_evaluator import (
        PredicateEvaluator,
        summarize_trajectory,
    )

    data = _instance(tmp_path)
    predicates = derive_success_predicates(data)
    # _spec 有一个 pinned navigate（carter_1 -> cargo 实体目标，非 zone）→ 不生成访问谓词
    assert predicates == [{"fn": "is_at_zone", "args": ["cargo", "delivery"]}]

    # 构造 zone 目标的 navigate：carter_1 -> delivery
    data["phase_graph"]["nodes"].append({
        "id": "patrol", "skill_key": "navigate", "preferred_agent": "carter_1",
        "target_ref": "delivery", "object_ref": None, "prerequisites": [], "timeout_s": 30.0,
    })
    predicates = derive_success_predicates(data)
    assert {"fn": "robot_visited_zone", "args": ["carter_1", "delivery"]} in predicates

    evaluator = PredicateEvaluator(data)
    (zx, zy) = next(t["position"][:2] for t in data["composition"]["targets"]
                    if t["id"] == "delivery")
    cargo_spawn = {e["id"]: tuple(e["spawn_pose"]["position"][:2])
                   for e in data["composition"]["entities"]}
    carter_spawn = tuple(data["robots"][0]["spawn_pose"]["position"][:2])
    far = dict(cargo_spawn, carter_1=carter_spawn)
    visited = dict(cargo_spawn, carter_1=(zx, zy), cargo=(zx, zy))

    trajectory = [
        evaluator.trajectory_step(0, far),
        evaluator.trajectory_step(10, visited),   # 到访 + 货物已投递
        evaluator.trajectory_step(20, far),       # 之后离开（访问仍算，终态失效）
    ]
    summary = summarize_trajectory(trajectory)
    # 访问谓词 ever 满足；终态谓词 final 时刻必须为真——步骤 20 货物被拿走 → failure
    assert summary["ever_satisfied"]["robot_visited_zone(carter_1,delivery)"] is True
    assert summary["first_satisfied_step"]["robot_visited_zone(carter_1,delivery)"] == 10
    assert summary["final_state"]["is_at_zone(cargo,delivery)"] is False
    assert summary["verdict"] == "failure" and summary["percent"] == 50.0

    trajectory_ok = [evaluator.trajectory_step(0, far), evaluator.trajectory_step(9, visited)]
    summary_ok = summarize_trajectory(trajectory_ok)
    assert summary_ok["verdict"] == "success" and summary_ok["percent"] == 100.0
