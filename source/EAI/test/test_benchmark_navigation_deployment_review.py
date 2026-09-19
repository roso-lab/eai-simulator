"""Synthetic importer tests; no test here is physical deployment evidence."""

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from benchmark_evidence_support import frozen_attempt
from demo.benchmark.control_step_evidence import ControlStepEvidence
from demo.benchmark.navigation_deployment_review import audit_navigation_deployment
from demo.benchmark.provenance import REPO_ROOT, canonical_sha256, file_sha256


def _write(path, value):
    path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def _rehash(snapshot):
    snapshot["sha256"] = canonical_sha256({k: v for k, v in snapshot.items()
                                          if k not in {"sha256", "captured_at"}})


@pytest.fixture
def run(tmp_path):
    scenario = json.loads((REPO_ROOT / "demo/warehouse_benchmark/scenarios/navigation_smoke.json").read_text())
    scenario_path = tmp_path / "scenario.json"
    _write(scenario_path, scenario)
    attempt = frozen_attempt(tmp_path, scenario_sha256=file_sha256(scenario_path),
                             options={"steps": 1800, "aerial_motion_mode": "kinematic"})
    report = attempt["report_body"]
    runtime = report["execution_provenance"]["runtime"]
    runtime["effective"]["versions"] = {"isaacsim": "5.1.0.0", "isaaclab": "test-fixture"}
    runtime["files"][str(REPO_ROOT / "demo/benchmark/runtime.py")] = file_sha256(REPO_ROOT / "demo/benchmark/runtime.py")
    _rehash(runtime)
    first, last = [-3.575, -11.75], [-4.51, -11.68]
    report.update(
        steps_taken=254,
        scheduling={"time_s": 5.08, "states": {"approach": {
            "status": "succeeded", "ready_at_s": 0., "started_at_s": 0., "finished_at_s": 5.06}}},
        events=[{"event": "node_complete", "node": "approach", "agent": "carter_1",
                 "step": 253, "position": last, "confirmed_after_sim_step": True,
                 "arrival_predicate": {"fn": "robot_visited_zone", "args": ["carter_1", "destination"]}}],
        pose_trajectory={"frame": "world", "position_unit": "m", "samples": [
            {"step": step, "positions": {"carter_1": xy},
             "robot_poses_xyzyaw": {"carter_1": [*xy, .2, 0.]}}
            for step, xy in ((0, first), (253, last), (254, last))]},
        navigation_state_sources={"carter_1": "articulation_root"},
        final_poses={"carter_1": [*last, .2]},
    )
    report["execution_validation"].update(transport_mode="none", aerial_motion_mode="none")
    report_path = tmp_path / "report.json"

    def save():
        proof = report.get("execution_provenance")
        if proof:
            _write(report_path.with_suffix(".request.json"), proof["request"])
            _write(report_path.with_suffix(".runtime.json"), proof["runtime"])
        _write(report_path, report)

    save()
    return scenario_path, report_path, report, save


def audit(run):
    scenario, report, _, _ = run
    return audit_navigation_deployment(report, scenario_path=scenario, expected_scene="warehouse_nvd")


def test_roundtrip_binds_disk_bytes_but_never_claims_physical_control_or_scoring(run):
    before = {p: p.read_bytes() for p in run[0].parent.iterdir()}
    review = audit(run)
    assert review["status"] == "navigation_observed"
    assert review["navigation_validated"]
    assert review["isaac_loop"]["simulation_time_s"] == 5.08
    assert review["pose_readback"]["status"] == "observed"
    assert review["controller_commands"]["status"] == "pending"
    for field in ("physical_control_validated", "business_task_validated", "execution_validated",
                  "whole_task_execution_validated", "scoreable"):
        assert review[field] is False
    for binding in review["bindings"].values():
        assert binding["sha256"] == file_sha256(binding["path"])
    assert {p: p.read_bytes() for p in run[0].parent.iterdir()} == before
    digest = review.pop("review_sha256")
    assert digest == canonical_sha256(review)


def test_existing_minimal_pure_fixture_cannot_establish_isaac_loop(run):
    report = frozen_attempt(run[0].parent, scenario_sha256=file_sha256(run[0]))["report_body"]
    _write(run[1], report)
    _write(run[1].with_suffix(".request.json"), report["execution_provenance"]["request"])
    _write(run[1].with_suffix(".runtime.json"), report["execution_provenance"]["runtime"])
    review = audit(run)
    assert review["status"] == "pending"
    assert not review["navigation_validated"]
    assert "missing_isaac_runtime_versions" in review["isaac_loop"]["pending"]


@pytest.mark.parametrize("kind", ["fixture", "pose_override", "kinematic", "synthetic_origin"])
def test_explicit_nonphysical_data_cannot_be_promoted(run, kind):
    report = run[2]
    if kind in {"fixture", "pose_override"}:
        report[kind] = True
    elif kind == "synthetic_origin":
        report["evidence_origin"] = "synthetic"
    else:
        report["execution_validation"]["tier"] = "kinematic_execution_validated"
    run[3]()
    review = audit(run)
    assert review["status"] == "non_physical"
    assert not review["navigation_validated"]
    assert not review["physical_control_validated"]


@pytest.mark.parametrize("status,expected", [
    ("wall_time_exhausted", "timed_out"), ("timed_out", "timed_out"),
    ("steps_exhausted", "failed"), ("deadlocked", "failed"),
    ("physics_state_invalid", "failed"), ("unknown", "unknown"), (None, "unknown"),
])
def test_terminal_outcome_never_overridden_by_success_flags(run, status, expected):
    run[2]["status"] = status
    run[3]()
    review = audit(run)
    assert review["status"] == expected
    assert not review["navigation_validated"]


def test_no_final_summary_is_not_inferred_to_be_timeout_or_success(run):
    run[1].unlink()
    review = audit(run)
    assert review["status"] == "no_summary"
    assert "outer_process_outcome_unknown" in review["pending"]
    assert "report" not in review["bindings"]
    assert review["provenance"]["status"] == "pending"


def test_exception_sidecar_is_bound_and_prevents_stale_success(run):
    _write(run[1].with_suffix(".failure.json"), {"status": "runtime_exception"})
    review = audit(run)
    assert review["status"] == "failed"
    assert "failure" in review["bindings"]


@pytest.mark.parametrize("missing", ["request", "provenance", "poses", "sources", "event", "xyzyaw", "confirmation"])
def test_legacy_missing_evidence_is_pending_not_backfilled(run, missing):
    report = run[2]
    if missing == "provenance":
        report.pop("execution_provenance")
    elif missing == "poses":
        report.pop("pose_trajectory")
    elif missing == "sources":
        report.pop("navigation_state_sources")
    elif missing == "event":
        report.pop("events")
    elif missing == "xyzyaw":
        for sample in report["pose_trajectory"]["samples"]:
            sample.pop("robot_poses_xyzyaw")
    elif missing == "confirmation":
        report["events"][0].pop("confirmed_after_sim_step")
    run[3]()
    if missing == "request":
        run[1].with_suffix(".request.json").unlink()
    review = audit(run)
    assert review["status"] == "pending"
    assert not review["navigation_validated"]
    assert not review["physical_control_validated"]


@pytest.mark.parametrize("binding", ["scenario", "request", "runtime", "asset", "embedded_snapshot", "source"])
def test_mismatched_frozen_inputs_are_rejected(run, binding):
    scenario, report_path, report, save = run
    if binding == "scenario":
        scenario.write_text(scenario.read_text() + "\n")
    elif binding in {"request", "runtime"}:
        path = report_path.with_suffix(f".{binding}.json")
        snapshot = json.loads(path.read_text())
        snapshot["captured_at"] = "2000-01-01T00:00:00Z"
        _write(path, snapshot)
    elif binding == "asset":
        (scenario.parent / "evidence_asset.usda").write_text("different asset")
    elif binding == "embedded_snapshot":
        report["execution_provenance"]["runtime"]["effective"]["device"] = "cuda:9"
        save()
    else:
        request = report["execution_provenance"]["request"]
        request["code_provenance"]["demo/benchmark/runtime.py"] = "a" * 64
        _rehash(request)
        save()
    with pytest.raises(ValueError, match="mismatch|differ"):
        audit(run)


def test_expected_scene_is_an_independent_binding(run):
    with pytest.raises(ValueError, match="expected_scene"):
        audit_navigation_deployment(run[1], scenario_path=run[0], expected_scene="factory")


@pytest.mark.parametrize("mutation", ["agent", "target", "outside", "pose", "final", "time", "order", "duplicate", "future", "timing"])
def test_contradictory_arrival_pose_timing_is_rejected(run, mutation):
    report = run[2]
    event = report["events"][0]
    samples = report["pose_trajectory"]["samples"]
    if mutation == "agent":
        event["agent"] = "another_robot"
    elif mutation == "target":
        event["arrival_predicate"]["args"][1] = "wrong_target"
    elif mutation == "outside":
        event["position"] = [-3., -11.68]
        samples[1]["positions"]["carter_1"] = event["position"]
        samples[1]["robot_poses_xyzyaw"]["carter_1"][:2] = event["position"]
    elif mutation == "pose":
        event["position"] = [-4.6, -11.68]
    elif mutation == "final":
        report["final_poses"]["carter_1"][0] += 1
    elif mutation == "time":
        report["scheduling"]["states"]["approach"]["finished_at_s"] = 99.
    elif mutation == "order":
        samples.reverse()
    elif mutation == "duplicate":
        report["events"].append(deepcopy(event))
    elif mutation == "future":
        samples[-1]["step"] = 255
    else:
        report["control_dt_s"] = .04
    run[3]()
    with pytest.raises(ValueError, match="mismatch|outside|duplicate|does not match|differs"):
        audit(run)


@pytest.mark.parametrize("mutation", ["steps", "scheduler_end", "deadline"])
def test_loop_and_node_budgets_must_match_frozen_inputs(run, mutation):
    report = run[2]
    if mutation == "steps":
        request = report["execution_provenance"]["request"]
        request["options"]["steps"] = 200
        _rehash(request)
    elif mutation == "scheduler_end":
        report["scheduling"]["time_s"] = 9.
    else:
        scenario = json.loads(run[0].read_text())
        scenario["phase_graph"]["nodes"][0]["timeout_s"] = 5.06
        _write(run[0], scenario)
        request = report["execution_provenance"]["request"]
        request["scenario_sha256"] = file_sha256(run[0])
        _rehash(request)
    run[3]()
    with pytest.raises(ValueError, match="mismatch"):
        audit(run)


def test_known_nonfinite_physics_state_cannot_be_hidden_by_success_flags(run):
    run[2]["physics_state_health"] = {"validated": False, "failure": {"step": 200}}
    run[3]()
    assert audit(run)["status"] == "failed"


def test_postrun_source_additions_are_listed_not_backfilled(run, monkeypatch):
    from demo.benchmark import navigation_deployment_review as module
    current = module.execution_code_provenance()
    current["demo/benchmark/future_audit.py"] = "a" * 64
    monkeypatch.setattr(module, "execution_code_provenance", lambda: current)
    review = audit(run)
    assert review["provenance"]["additional_current_sources"] == ["demo/benchmark/future_audit.py"]
    assert "demo/benchmark/future_audit.py" not in run[2]["execution_provenance"]["request"]["code_provenance"]


@pytest.mark.parametrize("payload", ['{"status":"executed","status":"timed_out"}',
                                    '{"steps_taken":NaN}', '{"control_dt_s":1e999}', '[]'])
def test_ambiguous_or_nonfinite_json_is_rejected(run, payload):
    run[1].write_text(payload)
    with pytest.raises(ValueError):
        audit(run)


def test_changed_file_during_review_is_rejected(run, monkeypatch):
    from demo.benchmark import navigation_deployment_review as module
    validate = module.validate_execution_provenance

    def mutate(*args, **kwargs):
        value = validate(*args, **kwargs)
        run[1].write_text(run[1].read_text() + "\n")
        return value

    monkeypatch.setattr(module, "validate_execution_provenance", mutate)
    with pytest.raises(ValueError, match="changed during"):
        audit(run)


def attach_control_trace(run, *, extra_articulation=False):
    """Synthetic per-step inputs for cross-artifact consistency tests only."""
    report = run[2]
    collector = ControlStepEvidence(report["control_dt_s"])
    first = report["pose_trajectory"]["samples"][0]["positions"]["carter_1"]
    last = report["pose_trajectory"]["samples"][-1]["positions"]["carter_1"]
    for step in range(1, report["steps_taken"] + 1):
        xy = [a + (b - a) * min(step / 253, 1.) for a, b in zip(first, last)]
        states = {"carter_1": [*xy, .2, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]}
        if extra_articulation:
            states["carter_1_arm"] = [*xy, .6, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]
        collector.record(step, {"carter_1": [.1, 0., 0.]}, states)
    report["control_step_evidence"] = collector.report()
    if extra_articulation:
        report["navigation_state_sources"]["carter_1_arm"] = "articulation_root"
        report["final_poses"]["carter_1_arm"] = [*last, .6]
        for sample in report["pose_trajectory"]["samples"]:
            xy = sample["positions"]["carter_1"]
            sample["positions"]["carter_1_arm"] = list(xy)
            sample["robot_poses_xyzyaw"]["carter_1_arm"] = [*xy, .6, 0.]
    runtime = report["execution_provenance"]["runtime"]
    source = REPO_ROOT / "demo/benchmark/control_step_evidence.py"
    runtime["files"][str(source)] = file_sha256(source)
    _rehash(runtime)
    run[3]()
    return report["control_step_evidence"]


def test_submitted_command_trace_is_bound_without_actuator_or_physics_authority(run):
    evidence = attach_control_trace(run)
    review = audit(run)
    assert review["navigation_validated"] is True
    commands = review["controller_commands"]
    assert commands["status"] == "submitted_and_post_step_readback"
    assert commands["command_scope"] == "submitted_to_env_step"
    assert commands["evidence_sha256"] == evidence["sha256"]
    assert commands["sample_count"] == 254
    assert commands["compared_pose_count"] == 2
    assert commands["actuator_applied_validated"] is False
    assert commands["physical_control_validated"] is False
    assert review["physical_control_validated"] is False
    assert review["whole_task_execution_validated"] is False
    assert review["scoreable"] is False


@pytest.mark.parametrize("mutation", ["digest", "length", "time", "dt", "position", "yaw", "quaternion", "robot", "null"])
def test_control_trace_cannot_disagree_with_report_or_pose_samples(run, mutation):
    evidence = attach_control_trace(run)
    if mutation == "digest":
        evidence["samples"][0]["commands"]["carter_1"][0] = 9.
    elif mutation == "length":
        evidence["samples"].pop()
    elif mutation == "time":
        evidence["samples"][0]["submitted_at_sim_time_s"] = .02
    elif mutation == "dt":
        evidence["control_dt_s"] = .01
    elif mutation == "position":
        evidence["samples"][252]["post_root_states"]["carter_1"][0] += .1
    elif mutation == "yaw":
        evidence["samples"][252]["post_root_states"]["carter_1"][3:7] = [math.cos(.5), 0., 0., math.sin(.5)]
    elif mutation == "quaternion":
        evidence["samples"][252]["post_root_states"]["carter_1"][3:7] = [0., 0., 0., 0.]
    elif mutation == "robot":
        evidence["robot_ids"] = evidence["state_ids"] = ["impostor"]
        for sample in evidence["samples"]:
            sample["commands"]["impostor"] = sample["commands"].pop("carter_1")
            sample["post_root_states"]["impostor"] = sample["post_root_states"].pop("carter_1")
    else:
        run[2]["control_step_evidence"] = None
    if mutation not in {"digest", "null"}:
        _rehash(evidence)
    run[3]()
    with pytest.raises(ValueError):
        audit(run)


def test_failure_trace_is_json_safe_and_refuses_admission(run):
    collector = ControlStepEvidence(.02)
    collector.record(1, {"carter_1": [.1, 0., 0.]},
                     {"carter_1": [float("nan"), 0., .2, 1., 0., 0., 0., 0., 0., 0., 0., 0., 0.]})
    run[2]["control_step_evidence"] = collector.report()
    run[3]()
    with pytest.raises(ValueError, match="failure"):
        audit(run)


def test_body_navigation_pose_is_not_misidentified_as_articulation_root(run):
    evidence = attach_control_trace(run)
    run[2]["navigation_state_sources"]["carter_1"] = "body:base_link"
    for sample in evidence["samples"]:
        sample["post_root_states"]["carter_1"][0] = 100.
    _rehash(evidence)
    run[3]()
    review = audit(run)
    assert review["controller_commands"]["status"] == "pending"
    assert "root_state_cannot_bind_navigation_body:carter_1" in review["controller_commands"]["pending"]
    assert review["navigation_validated"] is False


def test_collector_must_be_bound_to_loaded_sources_not_attached_after_run(run):
    attach_control_trace(run)
    runtime = run[2]["execution_provenance"]["runtime"]
    runtime["files"].pop(str(REPO_ROOT / "demo/benchmark/control_step_evidence.py"))
    _rehash(runtime)
    run[3]()
    review = audit(run)
    assert "collector_not_bound_to_loaded_runtime_sources" in review["controller_commands"]["pending"]
    assert review["controller_commands"]["status"] == "pending"
    assert not review["navigation_validated"]


def test_unverified_provenance_cannot_upgrade_submitted_command_evidence(run):
    attach_control_trace(run)
    run[1].with_suffix(".request.json").unlink()
    review = audit(run)
    assert review["controller_commands"]["status"] == "pending"
    assert "unverified_execution_provenance" in review["controller_commands"]["pending"]


def test_equivalent_quaternion_sign_does_not_change_pose_binding(run):
    evidence = attach_control_trace(run)
    for sample in evidence["samples"]:
        sample["post_root_states"]["carter_1"][3] = -1.
    _rehash(evidence)
    run[3]()
    assert audit(run)["controller_commands"]["status"] == "submitted_and_post_step_readback"


def test_extra_articulation_readback_does_not_require_a_command(run):
    attach_control_trace(run, extra_articulation=True)
    review = audit(run)
    assert review["controller_commands"]["status"] == "submitted_and_post_step_readback"
    assert review["controller_commands"]["robot_ids"] == ["carter_1"]


def test_extra_sampled_articulation_is_also_cross_checked(run):
    attach_control_trace(run, extra_articulation=True)
    report = run[2]
    report["navigation_state_sources"]["carter_1_arm"] = "articulation_root"
    for sample in report["pose_trajectory"]["samples"]:
        sample["positions"]["carter_1_arm"] = [0., 0.]
        sample["robot_poses_xyzyaw"]["carter_1_arm"] = [0., 0., .6, 0.]
    run[3]()
    with pytest.raises(ValueError, match="root state/pose sample"):
        audit(run)


@pytest.mark.parametrize("with_command", [False, True])
def test_foreign_control_trace_inventory_cannot_be_added_by_rehashing(run, with_command):
    evidence = attach_control_trace(run)
    evidence["state_ids"].append("foreign")
    if with_command:
        evidence["robot_ids"].append("foreign")
    for sample in evidence["samples"]:
        sample["post_root_states"]["foreign"] = list(sample["post_root_states"]["carter_1"])
        if with_command:
            sample["commands"]["foreign"] = [0., 0., 0.]
    _rehash(evidence)
    run[3]()
    with pytest.raises(ValueError, match="foreign articulation"):
        audit(run)


def test_known_mounted_articulation_can_have_commands_when_inventory_and_poses_match(run):
    evidence = attach_control_trace(run, extra_articulation=True)
    evidence["robot_ids"].append("carter_1_arm")
    for sample in evidence["samples"]:
        sample["commands"]["carter_1_arm"] = [0., 0., 0.]
    _rehash(evidence)
    run[3]()
    review = audit(run)
    assert review["controller_commands"]["status"] == "submitted_and_post_step_readback"
    assert review["controller_commands"]["robot_ids"] == ["carter_1", "carter_1_arm"]
