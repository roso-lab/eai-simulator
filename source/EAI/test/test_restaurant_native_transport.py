"""Native transport timing checks without starting Isaac."""

from copy import deepcopy

import pytest

from demo.benchmark import goal_restaurant_algorithm_episode as episode
from demo.benchmark.goal_driven_tasks import build_goal_task_package


class Clock:
    control_dt_s = 0.02

    def __init__(self):
        self.episode_time_s = 0.0
        self.steps = 0

    def step_once(self):
        self.steps += 1
        self.episode_time_s = self.steps * self.control_dt_s


def backend(clock, *, budget=1.0):
    public = episode._algorithm_public_task(build_goal_task_package("restaurant"))
    return episode.RestaurantMultiAgentGoalBackend(
        {"iris_1": clock, "carter_1": clock}, {}, public["public_world"]["routes"],
        episode_id="episode", simulation_budget_s=budget)


def test_native_idle_rounds_to_measured_tick_and_honors_budget():
    clock = Clock()
    subject = backend(clock, budget=0.05)
    assert subject.advance_idle(0.021)["time_s"] == pytest.approx(0.04)
    assert clock.steps == 2
    assert subject.evidence[-1]["requested_time_s"] == 0.021
    with pytest.raises(ValueError, match="exceed simulation budget"):
        subject.advance_idle(0.045)
    assert clock.steps == 2


def test_native_idle_rejects_nonadvancing_clock():
    clock = Clock()
    clock.step_once = lambda: None
    with pytest.raises(RuntimeError, match="clock did not advance"):
        backend(clock).advance_idle(0.01)


def test_native_diagnostic_success_uses_business_facts_not_release_flags():
    public = {"required_outcomes": [{"id": "receipt"}]}
    evaluation = {"receipt_review": {"validated": True}, "outcomes": {
        "receipt": {"status": "validated", "evaluator_ref": episode.EVALUATOR_REF,
                    "evidence_refs": ["native-proof"]}},
        "business_outcomes_validated": False, "scoreable": False}
    assert episode._native_business_facts_validated(evaluation, public)
    evaluation["outcomes"]["receipt"]["evidence_refs"] = []
    assert not episode._native_business_facts_validated(evaluation, public)


def dispatch(task, *, status="pending"):
    return {"session": {
        "episode_id": "episode", "current_time_s": 0.0, "current_plan_id": "plan",
        "tasks": {task["task_id"]: {"task": deepcopy(task), "status": status,
                                  "plan_id": "plan", "dispatch_action_id": "dispatch"}}}}


def test_late_command_fails_without_observation_or_deadline_extension():
    subject = backend(Clock())
    task = {"task_id": "observe", "assigned_agents": ["restaurant_carter_1"],
            "skill": "observe", "arguments": {"region_id": "kitchen_service"},
            "execution": {"start_s": 0.0, "duration_s": 0.01}}
    result = subject.execute(task, episode_id="episode", trusted_report=dispatch(task))
    assert result["status"] == "failed"
    assert result["completed_at_s"] == 0.0
    assert subject.evidence[-1]["kind"] == "execution_window_expired"
    assert subject.readiness.snapshot()["observations"] == {}


def test_native_cancel_requires_requested_dispatch_and_binds_receipt():
    subject = backend(Clock())
    task = {"task_id": "queued"}
    with pytest.raises(ValueError, match="requested dispatch"):
        subject.cancel(task, episode_id="episode", trusted_report=dispatch(task))
    result = subject.cancel(task, episode_id="episode",
                            trusted_report=dispatch(task, status="cancel_requested"))
    assert result["completed_at_s"] == 0.0
    assert result["receipt"]["action_id"] == "dispatch"
    assert result["receipt"]["evidence_refs"] == [subject.evidence[-1]["evidence_ref"]]


def profile():
    return {"schema": "eai.restaurant.native_execution_profile", "version": 1,
            "communication_contract": {"pools": {
                name: {"capacity_bps": 1000000, "fixed_delay_s": 0.001}
                for name in ("backhaul_uplink", "backhaul_downlink", "p2p")}},
            "downlink_ttl_s": 30.0, "restaurant_interventions": True,
            "factor_id": "communication_quality", "factor_level": "immediate"}


def test_native_profile_is_versioned_explicit_and_detached():
    config = profile()
    normalized = episode._validate_execution_profile(config)
    assert normalized["communication_contract"]["drop"] is False
    assert "drop" not in config["communication_contract"]
    for replacement in (True, float("nan"), -1):
        with pytest.raises(ValueError):
            episode._validate_execution_profile({**config, "downlink_ttl_s": replacement})
    with pytest.raises(ValueError, match="three communication pools"):
        episode._validate_execution_profile({**config, "communication_contract": {}})
    with pytest.raises(ValueError, match="requires request version 2"):
        episode._validate_request({"version": 1, "execution_profile": config})


def test_profile_chooses_existing_factor_without_mutating_baseline():
    config = profile()
    config["factor_level"] = "delayed"
    variant = episode._request_bundle({"execution_profile": config})
    original = episode._request_bundle({})
    assert variant["public_task"]["experiment_factor"]["level"] == "delayed"
    assert original["public_task"]["experiment_factor"]["level"] == "immediate"


def test_scoring_index_preserves_new_raw_transport_artifacts(tmp_path):
    from demo.benchmark.goal_scoring_adapter import build_archive_index

    for name in ("execution_downlink_report.json", "execution_observer_report.json"):
        (tmp_path / name).write_text("{}")
    index = build_archive_index(tmp_path)
    assert set(index["files"]) == {
        "execution_downlink_report.json", "execution_observer_report.json"}
    assert index["raw_only"] is True


@pytest.mark.parametrize(
    "cost_mode", ["legacy_v1", "legacy_v2", "legacy", "formal"])
def test_native_archive_cross_checks_link_delivery_against_physical_start(tmp_path, cost_mode):
    from demo.benchmark.communication import validate_communication_report

    config = profile()
    config["restaurant_interventions"] = False
    config = episode._validate_execution_profile(config)
    public = episode._algorithm_public_task(episode._request_bundle({"execution_profile": config}))
    services = episode._execution_services(public, episode_id="episode", profile=config)
    runtime, downlink = services["shared_communication_runtime"], services["downlink"]
    task = {"task_id": "observe", "assigned_agents": ["restaurant_carter_1"],
            "skill": "observe", "arguments": {"region_id": "kitchen_service"},
            "execution": {"start_s": 0.0, "duration_s": 2.0}}
    downlink.queue_dispatch(task, dispatch_action_id="dispatch", time_s=0.0)
    downlink.advance(1.0, active_agent_ids=task["assigned_agents"],
                     task_states={"observe": {"status": "pending"}})
    downlink.mark_started("observe", dispatch_action_id="dispatch", time_s=1.0)
    downlink.mark_terminal("observe", dispatch_action_id="dispatch", status="succeeded", time_s=2.0)
    runtime.advance(2.0)
    request = {"public_task": public, "episode_id": "episode", "execution_profile": config,
               "public_task_sha256": episode.canonical_sha256(public)}
    manifest = {"execution_downlink": {"profile_sha256": episode.canonical_sha256(downlink.profile())}}
    for name, payload in {
        "execution_downlink_profile.json": downlink.profile(),
        "execution_downlink_report.json": downlink.report(),
        "communication_transport_report.json": runtime.report(),
        "communication_transport_replay.json": validate_communication_report(runtime.report()),
        "skill_receipts.json": [{"task_id": "observe", "action_id": "dispatch"}],
    }.items():
        episode._write_json(tmp_path / name, payload)
    from demo.benchmark.goal_cost_evidence import build_cost_evidence, build_cost_source_bindings

    if cost_mode == "formal":
        episode._write_json(tmp_path / "goal_session_report.json", {
            "episode_id": "episode", "report": {
                "public_task_sha256": request["public_task_sha256"]}})
        episode._write_json(tmp_path / "goal_cost_source_bindings.json", build_cost_source_bindings(
            tmp_path, episode_id="episode", task_sha256=request["public_task_sha256"]))
    cost = build_cost_evidence(
        tmp_path, episode_id="episode" if cost_mode == "formal" else None,
        task_sha256=request["public_task_sha256"] if cost_mode == "formal" else None)
    if cost_mode in {"legacy_v1", "legacy_v2"}:
        cost["version"] = 1 if cost_mode == "legacy_v1" else 2
        for field in ("byte_accounting", "total_bytes_interpretation",
                      "total_physical_progress"):
            cost["communication"].pop(field)
    if cost_mode == "legacy_v1":
        cost.pop("source_binding")
        cost["communication"].pop("binding_status")
        cost["communication"].pop("binding_file_statuses")
    episode._write_json(tmp_path / "cost_evidence.json", cost)
    records = [{"task_id": "observe", "execution_started_at_s": 1.0}]
    assert episode._verify_native_transport(
        tmp_path, request, manifest, {"observe": task}, records)["validated"] is True
    records[0]["execution_started_at_s"] = 0.0
    with pytest.raises(ValueError, match="physical start"):
        episode._verify_native_transport(tmp_path, request, manifest, {"observe": task}, records)
    if cost_mode == "legacy_v2":
        records[0]["execution_started_at_s"] = 1.0
        forged = build_cost_evidence(tmp_path)
        forged["version"] = 2
        episode._write_json(tmp_path / "cost_evidence.json", forged)
        with pytest.raises(ValueError, match="unexpected byte semantics"):
            episode._verify_native_transport(
                tmp_path, request, manifest, {"observe": task}, records)
    if cost_mode == "legacy_v1":
        records[0]["execution_started_at_s"] = 1.0
        forged = build_cost_evidence(tmp_path)
        forged["version"] = 1
        for field in ("byte_accounting", "total_bytes_interpretation",
                      "total_physical_progress"):
            forged["communication"].pop(field)
        episode._write_json(tmp_path / "cost_evidence.json", forged)
        with pytest.raises(ValueError, match="unexpected source binding"):
            episode._verify_native_transport(
                tmp_path, request, manifest, {"observe": task}, records)
    if cost_mode == "formal":
        records[0]["execution_started_at_s"] = 1.0
        downgraded = deepcopy(cost)
        downgraded["version"] = 1
        downgraded.pop("source_binding")
        for field in ("byte_accounting", "total_bytes_interpretation",
                      "total_physical_progress", "binding_status",
                      "binding_file_statuses"):
            downgraded["communication"].pop(field)
        episode._write_json(tmp_path / "cost_evidence.json", downgraded)
        with pytest.raises(ValueError, match="v1 communication cost cannot be formal"):
            episode._verify_native_transport(
                tmp_path, request, manifest, {"observe": task}, records)
        episode._write_json(tmp_path / "cost_evidence.json", cost)
        (tmp_path / "goal_cost_source_bindings.json").unlink()
        with pytest.raises(ValueError, match="communication cost"):
            episode._verify_native_transport(tmp_path, request, manifest, {"observe": task}, records)
        cost.pop("source_binding")
        episode._write_json(tmp_path / "cost_evidence.json", cost)
        with pytest.raises(ValueError, match="communication cost"):
            episode._verify_native_transport(tmp_path, request, manifest, {"observe": task}, records)
        cost["source_binding"] = {"mode": "unknown"}
        episode._write_json(tmp_path / "cost_evidence.json", cost)
        with pytest.raises(ValueError, match="invalid binding mode"):
            episode._verify_native_transport(tmp_path, request, manifest, {"observe": task}, records)
        cost["version"] = True
        episode._write_json(tmp_path / "cost_evidence.json", cost)
        with pytest.raises(ValueError, match="unsupported schema"):
            episode._verify_native_transport(tmp_path, request, manifest, {"observe": task}, records)
