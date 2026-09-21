import pytest

from demo.benchmark.algorithm_adapter import (
    CallableAlgorithmAdapter,
    DEFAULT_CONTROL_STACK,
    PlannerAdapter,
    build_algorithm_result,
    build_algorithm_run_manifest,
    compare_paired_metric,
    expand_algorithm_cases,
    holm_bonferroni,
    run_algorithm_batch,
    summarize_algorithm_metric,
)


HASH = "a" * 64


def _manifest(algorithm, seed, *, comparison_key=None):
    return build_algorithm_run_manifest(
        algorithm_id=algorithm,
        algorithm_version="test-1",
        benchmark_commit="commit-under-test",
        task_bundle_sha256=HASH,
        scenario_sha256="b" * 64,
        scene_key="warehouse_nvd",
        task_id="W-DAG-01",
        provider_revision="v0.1.0-beta.1",
        seed=seed,
        run_root=f"/tmp/benchmark/{algorithm}/{seed}",
        comparison_key=comparison_key,
    )


def test_callable_adapter_copies_visible_events_and_exposes_manifest():
    seen = []
    adapter = CallableAlgorithmAdapter(
        "teamweaver", 1, lambda events: seen.append(events) or [],
        action_types=("allocate", "navigate_goal"), backend="TeamWeaverPipeline",
    )
    events = [{"type": "phase_ready", "payload": {"node_id": "n1"}}]
    assert adapter.act(events) == []
    events[0]["payload"]["node_id"] = "tampered"
    assert seen[0][0]["payload"]["node_id"] == "n1"
    assert adapter.manifest()["input_scope"] == "participant_visible_events_only"


def test_planner_adapter_keeps_plan_translation_at_the_boundary():
    adapter = PlannerAdapter(
        "allocation_model", 1, lambda events: {"events": len(events)},
        lambda plan, events: [{"plan_size": plan["events"]}],
    )
    assert adapter.act([{"type": "tick"}]) == [{"plan_size": 1}]


def test_adapter_rejects_low_level_control_actions():
    with pytest.raises(ValueError, match="low-level control"):
        CallableAlgorithmAdapter("bad", 1, lambda events: [], action_types=("cmd_vel",))
    adapter = CallableAlgorithmAdapter("bad-output", 1, lambda events: [{"type": "trajectory"}])
    with pytest.raises(ValueError, match="low-level control actions"):
        adapter.act([])


def test_run_manifest_rejects_non_content_hashes_and_truth_fields():
    with pytest.raises(ValueError, match="SHA-256"):
        build_algorithm_run_manifest(
            algorithm_id="bad", algorithm_version=1, benchmark_commit="x",
            task_bundle_sha256="runtime", scenario_sha256=HASH, scene_key="warehouse_nvd",
            task_id="W-DAG-01", provider_revision="p", seed=1, run_root="/tmp/x",
        )
    with pytest.raises(ValueError, match="evaluator-only"):
        build_algorithm_run_manifest(
            algorithm_id="bad", algorithm_version=1, benchmark_commit="x",
            task_bundle_sha256=HASH, scenario_sha256="b" * 64, scene_key="warehouse_nvd",
            task_id="W-DAG-01", provider_revision="p", seed=1, run_root="/tmp/x",
            runtime_config={"ground_truth": True},
        )


def test_expand_cases_and_parallel_runner_keep_independent_roots():
    cases = expand_algorithm_cases(
        [{"id": "baseline", "version": 1}, {"id": "candidate", "version": 2}],
        [{"benchmark_commit": "c", "task_bundle_sha256": HASH, "scenario_sha256": "b" * 64,
          "scene_key": "warehouse_nvd", "task_id": "W-DAG-01", "provider_revision": "p"}],
        [1, 2], run_root="/tmp/runs", branch_ids=("main", "alt"),
    )
    assert len(cases) == 8
    assert len({case["run_root"] for case in cases}) == 8
    results = run_algorithm_batch(
        cases[:2],
        lambda manifest: build_algorithm_result(
            manifest, status="completed", metrics={"makespan_s": float(manifest["seed"])}),
        max_workers=2,
    )
    assert [row["status"] for row in results] == ["completed", "completed"]
    assert results[0]["manifest"]["control_stack"] == DEFAULT_CONTROL_STACK


def test_run_manifest_rejects_control_stack_drift():
    manifest = _manifest("baseline", 1)
    manifest["control_stack"]["navigation"] = "participant_custom_planner"
    with pytest.raises(ValueError, match="default ideal controls"):
        build_algorithm_result(manifest, status="completed", metrics={"success": 1.0})


def test_statistics_are_paired_and_holm_adjusted():
    baseline = [_manifest("baseline", seed, comparison_key=f"case-{seed}") for seed in (1, 2, 3)]
    candidate = [_manifest("candidate", seed, comparison_key=f"case-{seed}") for seed in (1, 2, 3)]
    results = [
        build_algorithm_result(baseline[0], status="completed", metrics={"success": 1.0}),
        build_algorithm_result(baseline[1], status="completed", metrics={"success": 0.0}),
        build_algorithm_result(baseline[2], status="completed", metrics={"success": 1.0}),
        build_algorithm_result(candidate[0], status="completed", metrics={"success": 1.0}),
        build_algorithm_result(candidate[1], status="completed", metrics={"success": 1.0}),
        build_algorithm_result(candidate[2], status="completed", metrics={"success": 1.0}),
    ]
    summary = summarize_algorithm_metric(results, "success", bootstrap_samples=100, seed=4)
    assert summary["algorithms"]["candidate"]["mean"] == 1.0
    comparison = compare_paired_metric(results, "baseline", "candidate", "success",
                                       bootstrap_samples=100, seed=4)
    assert comparison["n_pairs"] == 3
    assert comparison["wins"] == 1 and comparison["ties"] == 2
    assert holm_bonferroni({"a": 0.01, "b": 0.03})["a"] <= 0.03


def test_lower_cost_wins_and_missing_metrics_remain_visible():
    results = [
        build_algorithm_result(_manifest("baseline", 1), status="completed", metrics={"time_s": 10}),
        build_algorithm_result(_manifest("candidate", 1), status="completed", metrics={"time_s": 8}),
        build_algorithm_result(_manifest("baseline", 2), status="failed"),
        build_algorithm_result(_manifest("candidate", 2), status="failed"),
    ]
    comparison = compare_paired_metric(results, "baseline", "candidate", "time_s",
                                       higher_is_better=False)
    assert comparison["wins"] == 1 and comparison["losses"] == 0
    assert comparison["mean_difference_candidate_minus_baseline"] == -2
    assert comparison["missing_both_metrics"] == 1
    summary = summarize_algorithm_metric(results, "time_s")
    assert summary["algorithms"]["baseline"]["missing_metric"] == 1
    with pytest.raises(ValueError, match="duplicate"):
        compare_paired_metric(results + [results[-1]], "baseline", "candidate", "time_s")
    with pytest.raises(ValueError, match="duplicate"):
        summarize_algorithm_metric(results + [results[-1]], "time_s")


def test_summary_rejects_mixed_versions():
    old, new = _manifest("baseline", 1), _manifest("baseline", 2)
    new["algorithm"]["version"] = "test-2"
    results = [build_algorithm_result(m, status="completed", metrics={"success": 1}) for m in (old, new)]
    with pytest.raises(ValueError, match="different algorithm versions"):
        summarize_algorithm_metric(results, "success")


@pytest.mark.parametrize("changed", ["runtime_config", "scenario", "benchmark"])
def test_manual_comparison_key_cannot_hide_changed_control_or_task_inputs(changed):
    baseline = _manifest("baseline", 1, comparison_key="same")
    candidate = _manifest("candidate", 1, comparison_key="same")
    if changed == "runtime_config":
        candidate[changed]["speed"] = 99.0
    elif changed == "scenario":
        candidate[changed]["sha256"] = "c" * 64
    else:
        candidate[changed]["commit"] = "other"
    results = [build_algorithm_result(m, status="completed", metrics={"success": 1.0})
               for m in (baseline, candidate)]
    with pytest.raises(ValueError, match="mismatched frozen inputs"):
        compare_paired_metric(results, "baseline", "candidate", "success")
