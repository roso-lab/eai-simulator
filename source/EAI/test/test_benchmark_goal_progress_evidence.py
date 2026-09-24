from copy import deepcopy
import json

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_progress_evidence import GoalProgressRecorder, replay_progress_evidence
from demo.benchmark.goal_scoring_adapter import progress_evidence_observations
from demo.benchmark.provenance import canonical_sha256


@pytest.fixture
def public():
    return build_goal_task_package("warehouse_nvd")["public_task"]


def sample(public, value, time=0.0, episode="progress-test"):
    return {"episode_id": episode, "time_s": time, "clock_source": "contract_clock",
            "evaluator_ref": "test.external-predicates",
            "predicates": {row["id"]: {"satisfied": value,
                "evidence_refs": [] if value is None else [f"external:{row['id']}:{time}"]}
                for row in public["required_outcomes"]}}


def test_raw_progress_revocation_unknown_and_duplicate_do_not_create_scores(public):
    recorder = GoalProgressRecorder(public, "progress-test")
    for boundary, time, value in [("initial", 0, None), ("skill_completed", 1, True),
                                   ("idle", 2, True), ("idle", 3, False),
                                   ("terminal", 4, None)]:
        recorder.append(sample(public, value, time), boundary=boundary, host_time_s=time)
    report = recorder.report(termination_reason="participant_stopped", host_cutoff_s=4)
    assert report["samples"][2]["changed_predicate_ids"] == []
    assert report["samples"][3]["changed_predicate_ids"] == report["predicate_ids"]
    assert report["weighted_progress"] is None
    assert report["termination"]["measurement_time_s"] == 4
    assert report["horizon_s"] > 4
    assert replay_progress_evidence(report, public, episode_id="progress-test")["validated"]
    assert report["scoreable"] is False


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(episode_id="foreign"),
    lambda row: row.update(time_s=float("nan")),
    lambda row: row.update(time_s=True),
    lambda row: row.update(predicates={}),
    lambda row: row.update(clock_source="model_wallclock"),
    lambda row: next(iter(row["predicates"].values())).update(satisfied=1),
    lambda row: next(iter(row["predicates"].values())).update(evidence_refs=[]),
])
def test_invalid_readings_are_rejected_atomically(public, mutation):
    recorder = GoalProgressRecorder(public, "progress-test")
    row = sample(public, True)
    mutation(row)
    with pytest.raises(ValueError):
        recorder.append(row, boundary="initial", host_time_s=0)
    assert recorder.samples == []


def test_clock_changes_and_backwards_time_rejected(public):
    recorder = GoalProgressRecorder(public, "progress-test")
    recorder.append(sample(public, False, 2), boundary="initial", host_time_s=2)
    for row, host_time in [(sample(public, True, 1), 2), (sample(public, True, 3), 1),
                           ({**sample(public, True, 3), "clock_source": "native_simulation"}, 3)]:
        with pytest.raises(ValueError):
            recorder.append(row, boundary="idle", host_time_s=host_time)


def test_replay_recomputes_derived_facts_and_binds_identity(public, tmp_path):
    recorder = GoalProgressRecorder(public, "progress-test")
    recorder.append(sample(public, True), boundary="terminal", host_time_s=0)
    report = recorder.report(termination_reason="participant_stopped", host_cutoff_s=0)
    with pytest.raises(ValueError):
        replay_progress_evidence(report, public, episode_id="another-episode")
    changed = deepcopy(report)
    changed["samples"][0]["changed_predicate_ids"] = []
    changed.pop("report_sha256")
    changed["report_sha256"] = canonical_sha256(changed)
    (tmp_path / "goal_progress_evidence.json").write_text(json.dumps(changed))
    assert progress_evidence_observations(
        tmp_path, public, episode_id="progress-test")["status"] == "invalid"


def test_missing_and_failed_measurements_are_not_zero_progress(public, tmp_path):
    assert progress_evidence_observations(
        tmp_path, public, episode_id="progress-test")["status"] == "missing"
    recorder = GoalProgressRecorder(public, "progress-test")
    recorder.record_error(boundary="initial", host_time_s=0, error="reader unavailable")
    report = recorder.report(termination_reason="execution_error", host_cutoff_s=0)
    assert report["status"] == "failed"
    assert report["termination"]["measurement_time_s"] is None
    assert replay_progress_evidence(report, public, episode_id="progress-test")["status"] == "failed"


def test_terminal_only_is_incomplete_and_opted_in_missing_archive_is_visible(public, tmp_path):
    from demo.benchmark.goal_scoring_adapter import build_scoring_adapter_metadata

    recorder = GoalProgressRecorder(public, "progress-test")
    recorder.append(sample(public, True), boundary="terminal", host_time_s=0)
    report = recorder.report(termination_reason="participant_stopped", host_cutoff_s=0)
    assert replay_progress_evidence(report, public, episode_id="progress-test")["status"] == "failed"
    (tmp_path / "episode_manifest.json").write_text(json.dumps({"progress_evidence": {
        "version": 1, "archive": "goal_progress_evidence.json"}}))
    metadata = build_scoring_adapter_metadata(public, episode_id="progress-test", output_root=tmp_path)
    assert metadata["progress_evidence"]["status"] == "missing"


@pytest.mark.parametrize("path", ["drop", "local_cancel", "backend_cancel"])
def test_every_terminal_receipt_has_progress_boundary(tmp_path, path):
    from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
    from demo.benchmark.goal_execution_downlink import GoalExecutionDownlink
    from test_benchmark_goal_execution_downlink import (
        _runner_fixture, _runtime, _Participant, _IdleBackend)

    public, api, robot, plan, regions, objects = _runner_fixture()
    runtime = _runtime({"active_drop": True} if path == "drop" else {"fixed_delay_s": 1.0})
    backend = _IdleBackend(regions, objects)

    def cancel(task, *, episode_id, trusted_report):
        time = trusted_report["session"]["current_time_s"]
        return {"status": "cancelled", "completed_at_s": time, "receipt": {
            "episode_id": episode_id, "task_id": task["task_id"], "action_id": "dispatch",
            "backend_id": "test.physical-cancel", "receipt_ref": "test.stop",
            "status": "cancelled", "completed_at_s": time, "evidence_refs": ["test.stop"]}}

    if path == "backend_cancel":
        backend.cancel = cancel
    kwargs = {} if path == "backend_cancel" else {
        "shared_communication_runtime": runtime,
        "downlink": GoalExecutionDownlink(public, episode_id="progress-test", runtime=runtime)}
    root = tmp_path / path
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot, cancel=path != "drop"), backend,
        output_root=root, episode_id="progress-test",
        progress_reader=lambda **kw: sample(public, None,
            kw["trusted_report"]["session"]["current_time_s"], kw["episode_id"]),
        **kwargs,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert result["error"] is None
    assert result["skill_receipt_count"] == 1
    report = json.loads((root / "goal_progress_evidence.json").read_text())
    assert sum(row["boundary"] == "skill_completed" for row in report["samples"]) == 1
    assert progress_evidence_observations(root, public, episode_id="progress-test")["status"] == "observed"
    receipt_path = root / "skill_receipts.json"
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.unlink()
    assert progress_evidence_observations(root, public, episode_id="progress-test")["status"] == "missing"
    receipt_path.write_bytes(receipt_bytes)
    # Rebuilding a valid digest after deleting the boundary must still fail
    # when compared with the runner's independently archived terminal receipt.
    rebuilt = GoalProgressRecorder(public, "progress-test")
    for row in report["samples"]:
        if row["boundary"] != "skill_completed":
            rebuilt.append({key: row[key] for key in (
                "episode_id", "time_s", "clock_source", "evaluator_ref", "predicates")},
                boundary=row["boundary"], host_time_s=row["host_time_s"])
    changed = rebuilt.report(termination_reason=report["termination"]["reason"],
                             host_cutoff_s=report["termination"]["host_time_s"])
    (root / "goal_progress_evidence.json").write_text(json.dumps(changed))
    assert progress_evidence_observations(root, public, episode_id="progress-test")["status"] == "invalid"


@pytest.mark.parametrize("observer_error", [False, True])
def test_post_skill_sampler_observes_trusted_hooks_even_if_hook_raises(tmp_path, observer_error):
    from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
    from test_benchmark_goal_execution_downlink import (
        _runner_fixture, _runtime, _Participant, _IdleBackend, _Observer)

    public, api, robot, plan, regions, objects = _runner_fixture()
    runtime = _runtime()
    observer = _Observer(public, "progress-test", runtime)
    changed = [False]

    def after_skill(task, result, *, host):
        changed[0] = True
        if observer_error:
            raise RuntimeError("trusted hook failed after state change")

    observer.after_skill = after_skill
    root = tmp_path / "hooks"
    result = FormalGoalEpisodeRunner(
        public, api, _Participant(plan, robot), _IdleBackend(regions, objects),
        output_root=root, episode_id="progress-test", execution_observer=observer,
        shared_communication_runtime=runtime,
        progress_reader=lambda **kw: sample(public, changed[0],
            kw["trusted_report"]["session"]["current_time_s"], kw["episode_id"]),
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    assert (result["error"] is not None) is observer_error
    report = json.loads((root / "goal_progress_evidence.json").read_text())
    skill_sample = next(row for row in report["samples"] if row["boundary"] == "skill_completed")
    assert all(row["satisfied"] is True for row in skill_sample["predicates"].values())


def _episode(tmp_path, reader, *, evaluator=None, help_requested=False):
    from demo.benchmark.goal_deployment_assessment import enable_deployment_assessment
    from demo.benchmark.goal_driven_contract import GOAL_PLAN_VERSION, build_goal_participant_api
    from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
    from test_benchmark_goal_episode_runner import _Adapter, _Backend, _action

    bundle = build_goal_task_package("warehouse_nvd")
    public = bundle["public_task"]
    robot = "warehouse_ground_1"
    plan = {"schema": "eai.benchmark.goal_plan", "version": GOAL_PLAN_VERSION,
            "plan_id": "measurement-plan", "assigned_team": [robot], "tasks": [{
                "task_id": "formal_nav", "skill": "ground_navigation",
                "arguments": {"region_id": "consolidation_area",
                              "route_ids": ["rack_a_to_consolidation"]},
                "required_capabilities": ["ground_navigation"], "assigned_agents": [robot],
                "prerequisites": [], "evidence_refs": [],
                "execution": {"start_s": 0.0, "duration_s": 16.0}}]}
    adapter = _Adapter(plan, robot)
    if help_requested:
        public = enable_deployment_assessment(public)

        def act(events):
            for event in events:
                if event["type"] == "deployment_assessment_required":
                    source = event["payload"]
                    return [_action("help", "deployment_assessment", {
                        **{key: source[key] for key in ("assessment_id", "assessment_epoch",
                                                      "roster_version", "capability_versions")},
                        "decision": "not_deployable", "reason_codes": ["missing_capability"],
                        "evidence_refs": [robot]}, submitted_at_s=event["time_s"])]
            return []

        adapter.act = act
    regions = {row["id"]: "rack_aisle_a" for row in public["candidate_robot_pool"]}
    objects = {row["id"]: {"region_id": row["physical_properties"]["initial_region_id"]}
               for row in public["public_world"]["objects"]}
    result = FormalGoalEpisodeRunner(
        public, build_goal_participant_api(public), adapter,
        _Backend(public, robot, regions, objects), output_root=tmp_path,
        episode_id="progress-test", session_audit=True,
        progress_reader=reader(public), evaluator=evaluator,
    ).run(initial_state={"robot_regions": regions, "object_states": objects})
    return result, public


def test_runner_samples_actual_boundaries_and_preserves_distinct_clocks(tmp_path):
    measurement_time = [0.0]

    def reader(public):
        def read(*, episode_id, boundary, trusted_report):
            host_time = trusted_report["session"]["current_time_s"]
            measurement_time[0] = max(measurement_time[0], host_time)
            return sample(public, boundary != "initial", measurement_time[0], episode_id)
        return read

    def evaluator(episode, report):
        measurement_time[0] = 25.0  # Native business evaluation may advance past last receipt.
        return {row["id"]: {"status": "validated", "evidence_refs": ["external:business"],
                            "evaluator_ref": "test.external-business"}
                for row in report["session"]["business_outcomes"]}

    root = tmp_path / "run"
    result, public = _episode(root, reader, evaluator=evaluator)
    assert result["error"] is None
    report = json.loads((root / "goal_progress_evidence.json").read_text())
    assert [row["boundary"] for row in report["samples"]] == [
        "initial", "skill_completed", "execution_stopped", "business_evaluated", "terminal"]
    assert report["termination"] == {"reason": "participant_stopped", "host_time_s": 16.0,
                                      "measurement_time_s": 25.0}
    metadata = json.loads((root / "scoring_adapter_metadata.json").read_text())
    assert metadata["progress_evidence"]["replay"]["validated"] is True
    assert "goal_progress_evidence.json" in metadata["archive_index"]["files"]
    assert result["business_outcomes_validated"] is False
    assert "test.external-predicates" not in (root / "participant_trace.json").read_text()


def test_reader_failure_is_infrastructure_not_skill_failure(tmp_path):
    def reader(public):
        def read(**kwargs):
            raise RuntimeError("trusted sensor unavailable")
        return read

    root = tmp_path / "run"
    result, _ = _episode(root, reader)
    assert result["error"].startswith("lifecycle_error:")
    receipts = json.loads((root / "skill_receipts.json").read_text())
    assert receipts[0]["status"] == "succeeded"
    report = json.loads((root / "goal_progress_evidence.json").read_text())
    assert report["status"] == "failed"
    assert report["samples"] == []


def test_early_help_records_real_cutoff_without_business_success(tmp_path):
    def reader(public):
        return lambda **kwargs: sample(public, None, 0, kwargs["episode_id"])

    root = tmp_path / "help"
    result, _ = _episode(root, reader, help_requested=True)
    assert result["error"] is None
    assert result["skill_receipt_count"] == 0
    report = json.loads((root / "goal_progress_evidence.json").read_text())
    assert report["termination"]["reason"] == "human_assistance_requested"
    assert report["termination"]["measurement_time_s"] == 0
    assert report["horizon_s"] > 0
    assert all(row["satisfied"] is None for row in report["samples"][-1]["predicates"].values())
