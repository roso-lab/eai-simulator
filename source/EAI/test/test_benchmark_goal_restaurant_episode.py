"""Offline checks for the formal Restaurant GoalSession episode bridge."""

import json
from pathlib import Path
import uuid

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_episode_runner import FormalGoalEpisodeRunner
from demo.benchmark.goal_restaurant_episode import (
    MINIMUM_REGION_SUPPORT_SAMPLES,
    PHYSICAL_LEGS,
    REGION_OBSERVATION_TOLERANCE_M,
    REGION_SUPPORT_RAY_DISTANCE_M,
    REGION_SUPPORT_RAY_HEIGHT_M,
    REGION_SUPPORT_SAMPLE_OFFSETS_M,
    ROBOT_ID,
    RestaurantNativeGoalBackend,
    RestaurantWitnessParticipant,
    build_restaurant_episode_plan,
    evaluate_restaurant_business,
    verify_episode_archive,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.restaurant_benchmark import contact_probe, runtime_probe


class _FakeNavigator:
    def __init__(self):
        self.episode_time_s = 0.0
        self.step = 0
        self.driven = []
        self.xy = list(PHYSICAL_LEGS["restaurant_staging_to_kitchen"]["start_xy_m"])

    def _stamp(self):
        return {
            "simulation_step": self.step,
            "episode_control_step": self.step,
            "time_s": self.episode_time_s,
            "root_state_w": [*self.xy, 0.2, 1.0, 0.0, 0.0, 0.0,
                             0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        }

    def drive(self, route_id, *, deadline_s):
        started = self._stamp()
        self.step += 1
        self.episode_time_s += 1.0
        self.driven.append(route_id)
        definition = PHYSICAL_LEGS[route_id]
        self.xy = list(definition["goal_xy_m"])
        wheels = {
            name: {"ground_force_N": [0.0, 0.0, 25.0],
                   "buffer_capacity_reached": False}
            for name in contact_probe.WHEEL_NAMES
        }
        return {
            "kind": "arrival", "route_id": route_id,
            "from_region_id": definition["from_region_id"],
            "region_id": definition["to_region_id"],
            "producer_id": ROBOT_ID, "arrival": self._stamp(),
            "started": started,
            "steps_taken": 1, "deadline_s": deadline_s,
            "control_step_evidence": {"validated": True, "sample_count": 1},
            "contact_view_identities": {
                name: {"sensor_paths": [f"/World/m20/{name}"],
                       "filter_paths": [contact_probe.GROUND_COLLISION_PATH],
                       "max_contact_data_count": contact_probe.CONTACT_BUFFER_CAPACITY}
                for name in contact_probe.WHEEL_NAMES
            },
            "contact_snapshots": [{"route_control_step": 1,
                                   "weight_support_ratio": 1.0,
                                   "wheels": wheels}],
            "wheel_contact_sample_ratios": {
                name: 1.0 for name in contact_probe.WHEEL_NAMES},
            "median_weight_support_ratio": 1.0,
            "contact_buffer_capacity_reached": False,
            "contact_health_validated": True,
        }

    def step_once(self, command=None):
        del command
        self.step += 1
        self.episode_time_s += 0.02
        return self._stamp()

    def wait_until(self, target_time_s):
        start = self._stamp()
        self.step += 1
        self.episode_time_s = float(target_time_s)
        return {"kind": "native_window_hold", "start": start, "end": self._stamp()}

    def observe_region(self, region_id):
        stamp = self.step_once()
        samples = []
        for dx, dy in REGION_SUPPORT_SAMPLE_OFFSETS_M:
            samples.append({
                "origin_m": [self.xy[0] + dx, self.xy[1] + dy,
                             REGION_SUPPORT_RAY_HEIGHT_M],
                "direction": [0.0, 0.0, -1.0],
                "distance_m": REGION_SUPPORT_RAY_DISTANCE_M,
                "hit": {"hit": True, "collision": contact_probe.GROUND_COLLISION_PATH,
                        "position_m": [self.xy[0] + dx, self.xy[1] + dy, 0.0],
                        "normal": [0.0, 0.0, 1.0]},
                "passed": True,
            })
        return {
            "schema": "eai.restaurant.native_region_observation", "version": 1,
            "region_id": region_id, "producer_id": ROBOT_ID,
            "query_kind": "episode_timestamped_physx_ground_support_raycast",
            "scene_queries_enabled": True,
            "expected_collision_path": contact_probe.GROUND_COLLISION_PATH,
            "region_center_xy_m": list(self.xy),
            "observed_robot_xy_m": list(self.xy),
            "region_error_m": 0.0,
            "region_tolerance_m": REGION_OBSERVATION_TOLERANCE_M,
            "support_samples": samples,
            "minimum_support_samples": MINIMUM_REGION_SUPPORT_SAMPLES,
            "passed_support_samples": len(samples),
            "scene_observation_validated": True, "stamp": stamp,
        }


def test_restaurant_episode_plan_uses_measured_round_trip_and_budget():
    public = build_goal_task_package("restaurant")["public_task"]
    plan = build_restaurant_episode_plan(public)
    routes = [row["arguments"]["route_ids"][0] for row in plan["tasks"]
              if row["skill"] == "ground_navigation"]
    assert plan["assigned_team"] == [ROBOT_ID]
    assert routes == [
        "restaurant_staging_to_kitchen",
        "restaurant_kitchen_to_staging",
        "restaurant_staging_to_dining",
        "restaurant_dining_to_staging",
    ]
    assert plan["tasks"][-1]["execution"]["start_s"] + \
        plan["tasks"][-1]["execution"]["duration_s"] <= public["budgets"]["simulation_s"]


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _run_offline_episode(root: Path, *, episode_id: str):
    bundle = build_goal_task_package("restaurant")
    plan = build_restaurant_episode_plan(bundle["public_task"])
    navigator = _FakeNavigator()
    backend = RestaurantNativeGoalBackend(navigator, plan, episode_id=episode_id)
    participant = RestaurantWitnessParticipant(plan)
    result = FormalGoalEpisodeRunner(
        bundle["public_task"], bundle["participant_api"], participant, backend,
        output_root=root / "goal_episode", episode_id=episode_id,
        evaluator=lambda current, trusted: evaluate_restaurant_business(
            backend, current, trusted), max_turns=32,
    ).run(initial_state={"robot_regions": {ROBOT_ID: "restaurant_staging"},
                         "object_states": {}})
    return bundle, plan, backend, participant, result


def test_restaurant_formal_episode_keeps_one_ledger_and_validates_all_outcomes(
        tmp_path: Path):
    episode_id = "restaurant-formal-offline-001"
    bundle, plan, backend, _participant, result = _run_offline_episode(
        tmp_path, episode_id=episode_id)
    root = tmp_path / "goal_episode"

    assert result["error"] is None
    assert result["skill_receipt_count"] == 7
    assert result["business_outcomes_validated"] is True
    assert result["execution_validated"] is False
    assert result["scoreable"] is False
    assert [row["route_id"] for row in backend.evidence
            if row["kind"] == "arrival"] == [
        "restaurant_staging_to_kitchen",
        "restaurant_kitchen_to_staging",
        "restaurant_staging_to_dining",
        "restaurant_dining_to_staging",
    ]
    business = json.loads((root / "business_evidence.json").read_text())
    assert set(business["outcomes"]) == {
        "kitchen_service_inspected", "dining_service_inspected",
        "readiness_receipt_committed",
    }
    service = backend.readiness.snapshot()
    assert service["receipt"]["episode_id"] == episode_id
    assert len(service["relay_publications"]) == 1
    assert all(row["episode_id"] == episode_id for row in service["observations"].values())
    usage = json.loads((root / "team_usage.json").read_text())
    assert usage["selected_team"] == [ROBOT_ID]
    assert usage["plan_assigned_agents"] == [ROBOT_ID]
    assert usage["active_agents"] == [ROBOT_ID]
    assert usage["selected_team_size"] == usage["active_team_size"] == 1
    assert usage["action_count"] == 9


def _build_synthetic_archive(base: Path) -> Path:
    """Assemble a complete episode archive the way the Isaac worker would."""
    from demo.benchmark.goal_restaurant_episode import _source_files
    from demo.restaurant_benchmark import config as restaurant_config

    try:
        source_files = _source_files()
    except RuntimeError:
        pytest.skip("Carter controller source is unavailable; set EAI_CONTROLLER_ROOT")
    run_id = uuid.uuid4().hex
    episode_id = f"restaurant-goal:{run_id}"
    bundle, plan, backend, _participant, result = _run_offline_episode(
        base, episode_id=episode_id)
    assert result["error"] is None
    episode_root = base / "goal_episode"
    _write(episode_root / "native_evidence_log.json", {
        "schema": "eai.restaurant.formal_goal_native_evidence", "version": 1,
        "episode_id": episode_id, "records": backend.evidence})
    _write(episode_root / "readiness_service_snapshot.json", {
        "schema": "eai.restaurant.formal_goal_readiness_snapshot", "version": 1,
        "episode_id": episode_id,
        "service": backend.readiness.snapshot(),
        "evaluation": backend.business_evaluation})
    request = {
        "schema": "eai.restaurant.formal_goal_episode_request", "version": 1,
        "run_id": run_id, "episode_id": episode_id,
        "scene_key": restaurant_config.BENCHMARK_SCENE_KEY,
        "products_root": str(base / "products"),
        "source_usd": str(base / "restaurant.usd"),
        "source_sha256": restaurant_config.SOURCE_SHA256,
        "metadata_sha256": "0" * 64,
        "bundle_manifest_sha256": "0" * 64,
        "product_validation": {"validated": True},
        "selection": runtime_probe._selection(), "requested_device": "cpu",
        "robot_binding": {"public_robot_id": ROBOT_ID, "native_agent_id": "carter_1",
                          "spawn_region_id": "restaurant_staging",
                          "spawn_xy_m": list(
                              PHYSICAL_LEGS["restaurant_staging_to_kitchen"]["start_xy_m"])},
        "region_bindings": {
            "restaurant_staging": {"center_xy_m": list(
                PHYSICAL_LEGS["restaurant_kitchen_to_staging"]["goal_xy_m"])},
            "kitchen_service": {"center_xy_m": list(
                PHYSICAL_LEGS["restaurant_staging_to_kitchen"]["goal_xy_m"])},
            "dining_service": {"center_xy_m": list(
                PHYSICAL_LEGS["restaurant_staging_to_dining"]["goal_xy_m"])},
        },
        "physical_routes": {route_id: {"route_id": route_id}
                            for route_id in PHYSICAL_LEGS},
        "public_task_sha256": canonical_sha256(bundle["public_task"]),
        "participant_api_sha256": canonical_sha256(bundle["participant_api"]),
        "plan_sha256": canonical_sha256(plan),
        "source_files": source_files,
        "release_gates": {"navigation_ready": False, "execution_validated": False,
                          "scoreable": False},
    }
    _write(base / "request.json", request)
    _write(episode_root / "goal_episode_source.json", {
        "schema": "eai.restaurant.formal_goal_episode_source", "version": 1,
        "episode_id": episode_id,
        "request_sha256": file_sha256(base / "request.json"),
        "source_files": request["source_files"]})
    (base / "isaac.log").write_text("offline synthetic isaac log\n", encoding="utf-8")
    _write(base / "restaurant_goal_episode_report.json", {
        "schema": "eai.restaurant.formal_goal_episode_report", "version": 1,
        "run_id": run_id, "episode_id": episode_id, "status": "completed",
        "business_episode_execution_validated": True,
        "execution_validated": False, "scoreable": False,
        "native_evidence_log_sha256": file_sha256(
            episode_root / "native_evidence_log.json"),
        "readiness_service_snapshot_sha256": file_sha256(
            episode_root / "readiness_service_snapshot.json")})
    _write(base / "process.json", {
        "returncode": 0, "duration_s": 0.0,
        "report_sha256": file_sha256(base / "restaurant_goal_episode_report.json"),
        "isaac_log_sha256": file_sha256(base / "isaac.log"),
        "business_episode_execution_validated": True})
    return base


def _refresh_hashes(base: Path, *, evidence: bool = True, snapshot: bool = False) -> None:
    episode_root = base / "goal_episode"
    report = json.loads((base / "restaurant_goal_episode_report.json").read_text())
    if evidence:
        report["native_evidence_log_sha256"] = file_sha256(
            episode_root / "native_evidence_log.json")
    if snapshot:
        report["readiness_service_snapshot_sha256"] = file_sha256(
            episode_root / "readiness_service_snapshot.json")
    _write(base / "restaurant_goal_episode_report.json", report)
    process = json.loads((base / "process.json").read_text())
    process["report_sha256"] = file_sha256(base / "restaurant_goal_episode_report.json")
    _write(base / "process.json", process)


def _replace_evidence_ref(base: Path, stale_ref: str, fresh_ref: str) -> None:
    receipts_path = base / "goal_episode" / "skill_receipts.json"
    receipts = json.loads(receipts_path.read_text())
    for receipt in receipts:
        receipt["evidence_refs"] = [fresh_ref if ref == stale_ref else ref
                                    for ref in receipt.get("evidence_refs", [])]
    _write(receipts_path, receipts)
    business_path = base / "goal_episode" / "business_evidence.json"
    business = json.loads(business_path.read_text())
    for row in business["outcomes"].values():
        row["evidence_refs"] = [fresh_ref if ref == stale_ref else ref
                                for ref in row.get("evidence_refs", [])]
    _write(business_path, business)
    session_path = base / "goal_episode" / "goal_session_report.json"
    session_report = json.loads(session_path.read_text())
    for row in session_report["report"]["business_outcomes"]:
        row["evidence_refs"] = [fresh_ref if ref == stale_ref else ref
                                for ref in row.get("evidence_refs", [])]
    _write(session_path, session_report)


def test_archive_verifier_accepts_a_complete_synthetic_archive(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    verification = verify_episode_archive(base)
    assert verification["validated"] is True
    assert verification["episode_id"].startswith("restaurant-goal:")
    assert verification["record_count"] == 7
    assert verification["receipt_count"] == 7
    assert verification["execution_validated"] is False
    assert verification["scoreable"] is False


def test_archive_verifier_rejects_a_raw_file_edit(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    log_path = base / "goal_episode" / "native_evidence_log.json"
    log_path.write_text(log_path.read_text().replace(
        "restaurant_staging_to_kitchen", "restaurant_staging_to_kitchen "), encoding="utf-8")
    with pytest.raises(ValueError, match="hash chain"):
        verify_episode_archive(base)


def test_archive_verifier_rejects_self_consistent_causality_tampering(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    log_path = base / "goal_episode" / "native_evidence_log.json"
    log = json.loads(log_path.read_text())
    observation = next(row for row in log["records"]
                       if row["kind"] == "observation"
                       and row["region_id"] == "kitchen_service")
    observation["stamp"]["simulation_step"] = 0
    observation["scene_observation"]["stamp"]["simulation_step"] = 0
    body = {key: value for key, value in observation.items() if key != "evidence_ref"}
    stale_ref = observation["evidence_ref"]
    fresh_ref = "sha256:" + canonical_sha256(body)
    observation["evidence_ref"] = fresh_ref
    _write(log_path, log)
    _replace_evidence_ref(base, stale_ref, fresh_ref)
    _refresh_hashes(base)
    with pytest.raises(ValueError, match="not caused by its arrival"):
        verify_episode_archive(base)


def test_archive_verifier_recomputes_contact_health_after_self_consistent_tamper(
        tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    log_path = base / "goal_episode" / "native_evidence_log.json"
    log = json.loads(log_path.read_text())
    arrival = next(row for row in log["records"] if row["kind"] == "arrival")
    wheel = contact_probe.WHEEL_NAMES[0]
    arrival["wheel_contact_sample_ratios"][wheel] = 0.0
    arrival["contact_snapshots"][0]["wheels"][wheel]["ground_force_N"][2] = 0.0
    stale_ref = arrival["evidence_ref"]
    body = {key: value for key, value in arrival.items() if key != "evidence_ref"}
    fresh_ref = "sha256:" + canonical_sha256(body)
    arrival["evidence_ref"] = fresh_ref
    _write(log_path, log)
    _replace_evidence_ref(base, stale_ref, fresh_ref)
    _refresh_hashes(base)
    with pytest.raises(ValueError, match="contact ratio failed"):
        verify_episode_archive(base)


def test_archive_verifier_recomputes_scene_observation_after_self_consistent_tamper(
        tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    log_path = base / "goal_episode" / "native_evidence_log.json"
    log = json.loads(log_path.read_text())
    observation = next(row for row in log["records"] if row["kind"] == "observation")
    scene = observation["scene_observation"]
    scene["observed_robot_xy_m"] = [999.0, 999.0]
    scene["region_error_m"] = 999.0
    scene["passed_support_samples"] = 0
    for sample in scene["support_samples"]:
        sample["hit"]["hit"] = False
        sample["passed"] = False
    observation["service_record"]["evidence_digest"] = "sha256:" + canonical_sha256({
        "episode_id": observation["episode_id"],
        "region_id": observation["region_id"],
        "producer_id": ROBOT_ID,
        "scene_observation": scene,
    })
    stale_ref = observation["evidence_ref"]
    body = {key: value for key, value in observation.items() if key != "evidence_ref"}
    fresh_ref = "sha256:" + canonical_sha256(body)
    observation["evidence_ref"] = fresh_ref
    _write(log_path, log)
    _replace_evidence_ref(base, stale_ref, fresh_ref)
    snapshot_path = base / "goal_episode" / "readiness_service_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    region = observation["region_id"]
    snapshot["service"]["observations"][region] = observation["service_record"]
    _write(snapshot_path, snapshot)
    _refresh_hashes(base, snapshot=True)
    with pytest.raises(ValueError, match="position binding failed"):
        verify_episode_archive(base)


def test_archive_verifier_rejects_missing_relay_publication(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    snapshot_path = base / "goal_episode" / "readiness_service_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["service"]["relay_publications"] = []
    _write(snapshot_path, snapshot)
    _refresh_hashes(base, snapshot=True)
    with pytest.raises(ValueError, match="exactly one relay publication"):
        verify_episode_archive(base)


def test_archive_verifier_rejects_promoted_release_gates(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    request_path = base / "request.json"
    request = json.loads(request_path.read_text())
    request["release_gates"]["scoreable"] = True
    _write(request_path, request)
    source_path = base / "goal_episode" / "goal_episode_source.json"
    source = json.loads(source_path.read_text())
    source["request_sha256"] = file_sha256(request_path)
    _write(source_path, source)
    with pytest.raises(ValueError, match="invalid Restaurant formal Goal episode request"):
        verify_episode_archive(base)


def test_archive_verifier_rejects_incomplete_archive(tmp_path: Path):
    base = _build_synthetic_archive(tmp_path / "archive")
    (base / "goal_episode" / "team_usage.json").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        verify_episode_archive(base)


def test_restaurant_participant_stops_after_failed_skill():
    public = build_goal_task_package("restaurant")["public_task"]
    participant = RestaurantWitnessParticipant(build_restaurant_episode_plan(public))
    opened = participant.act([{"type": "episode_started", "time_s": 0.0,
                               "payload": {}}])
    assert [row["type"] for row in opened] == [
        "select_team", "submit_plan", "dispatch_skill"]
    assert participant.act([{"type": "action_result", "time_s": 10.0,
                             "payload": {"action_id": "restaurant_witness_dispatch_0",
                                         "status": "failed"}}]) == []
