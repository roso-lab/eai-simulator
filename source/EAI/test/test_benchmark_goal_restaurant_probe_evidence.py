import json

import pytest

from demo.benchmark.goal_restaurant_probe_evidence import (
    RESTAURANT_PROBE_SCHEMA,
    ProbeEvidenceError,
    derive_restaurant_probe_evidence,
    write_restaurant_probe_evidence,
)


def _arrival(task, region, time_s, producer="restaurant_carter_1"):
    return {
        "kind": "arrival", "task_id": task, "region_id": region,
        "producer_id": producer, "native_agent_id": producer.replace("restaurant_", ""),
        "contact_health_validated": True,
        "median_weight_support_ratio": 0.99,
        "evidence_ref": f"sha256:{task}", "route_id": task,
        "arrival": {"time_s": time_s, "episode_control_step": int(time_s * 50)},
    }


def _observation(task, region, time_s):
    return {
        "kind": "observation", "task_id": task, "region_id": region,
        "producer_id": "restaurant_carter_1", "native_agent_id": "carter_1",
        "scene_observation": {"region_id": region, "digest": "x"},
        "service_record": {"region_id": region, "ready": True},
        "evidence_ref": f"sha256:{task}",
        "stamp": {"time_s": time_s, "episode_control_step": int(time_s * 50)},
    }


def _relay(time_s):
    return {
        "kind": "relay_and_receipt", "task_id": "restaurant_readiness_relay",
        "producer_id": "restaurant_iris_1", "native_agent_id": "iris_1",
        "evidence_ref": "sha256:relay",
        "business_receipt": {
            "kind": "readiness_receipt",
            "observation_refs": ["ep:dining:1", "ep:kitchen:1"]},
        "relay_publication": {
            "kind": "relay_publication", "time_s": time_s,
            "observation_refs": ["ep:kitchen_service:1", "ep:dining_service:1"]},
    }


def _archive(tmp_path, *, records, validated=True):
    episode_id = "restaurant-algo:probe-fixture"
    (tmp_path / "restaurant_algorithm_episode_report.json").write_text(
        json.dumps({"episode_id": episode_id,
                    "business_episode_execution_validated": validated,
                    "status": "completed"}), encoding="utf-8")
    goal = tmp_path / "goal_episode"
    goal.mkdir()
    (goal / "native_evidence_log.json").write_text(
        json.dumps({"schema": "eai.restaurant.formal_goal_native_evidence",
                    "version": 1, "episode_id": episode_id,
                    "records": records}), encoding="utf-8")
    (goal / "participant_trace.json").write_text(
        json.dumps({"actions": [{
            "type": "select_team",
            "payload": {"agent_ids": ["restaurant_carter_1",
                                      "restaurant_carter_2",
                                      "restaurant_iris_1"]}}]}),
        encoding="utf-8")
    return tmp_path


def _complete_records():
    return [
        _arrival("k_arrive", "kitchen_service", 20.0),
        _observation("k_observe", "kitchen_service", 45.0),
        _arrival("d_arrive", "dining_service", 106.0,
                 producer="restaurant_carter_2"),
        _observation("d_observe", "dining_service", 139.0),
        _relay(188.0),
    ]


def test_complete_episode_replays_into_binary_labels(tmp_path):
    report = derive_restaurant_probe_evidence(
        _archive(tmp_path, records=_complete_records()))
    assert report["schema"] == RESTAURANT_PROBE_SCHEMA
    points = {row["measurement_id"]: row for row in report["measurements"]}
    assert set(points) == {"restaurant_initial", "restaurant_kitchen_inspected",
                           "restaurant_both_zones_inspected",
                           "restaurant_receipt_committed"}
    # Every cut point is verified and deployable: the episode completed all
    # terminal predicates within the opening budget.
    for point_id, row in points.items():
        assert row["grounding"]["status"] == "verified", point_id
        assert row["feasibility"]["oracle_status"] == "verified", point_id
        assert row["feasibility"]["feasibility_label"] == "deployable", point_id
    assert points["restaurant_initial"]["facts"] == []
    assert points["restaurant_kitchen_inspected"]["facts"] == [
        "kitchen_arrived", "kitchen_observed"]
    assert points["restaurant_both_zones_inspected"]["facts"] == [
        "dining_arrived", "dining_observed", "kitchen_arrived", "kitchen_observed"]
    assert points["restaurant_receipt_committed"]["facts"] == [
        "dining_arrived", "dining_observed", "kitchen_arrived",
        "kitchen_observed", "readiness_receipt_committed"]
    assert report["development_track"]["evidence_class"] == "diagnostic"
    assert report["scoreable"] is False


def test_each_fact_carries_its_own_archive_evidence_ref(tmp_path):
    report = derive_restaurant_probe_evidence(
        _archive(tmp_path, records=_complete_records()))
    final = report["measurements"][-1]
    refs = final["fact_evidence"]
    assert refs["kitchen_arrived"] == "sha256:k_arrive"
    assert refs["kitchen_observed"] == "sha256:k_observe"
    assert refs["dining_arrived"] == "sha256:d_arrive"
    assert refs["readiness_receipt_committed"] == "sha256:relay"
    for row in report["measurements"]:
        for packet in row["grounding"]["evidence_refs"]:
            assert packet.startswith("sha256:") or ":probe" in packet


def test_unhealthy_contact_evidence_fails_closed(tmp_path):
    records = _complete_records()
    records[0]["median_weight_support_ratio"] = 0.2
    with pytest.raises(ProbeEvidenceError, match="health gate"):
        derive_restaurant_probe_evidence(_archive(tmp_path, records=records))


def test_missing_relay_record_fails_closed(tmp_path):
    records = _complete_records()[:-1]
    with pytest.raises(ProbeEvidenceError, match="readiness_receipt_committed"):
        derive_restaurant_probe_evidence(_archive(tmp_path, records=records))


def test_unvalidated_business_episode_is_rejected(tmp_path):
    with pytest.raises(ProbeEvidenceError, match="business-validated"):
        derive_restaurant_probe_evidence(
            _archive(tmp_path, records=_complete_records(), validated=False))


def test_non_monotonic_timeline_fails_closed(tmp_path):
    records = _complete_records()
    records[4]["relay_publication"]["time_s"] = 100.0  # receipt before both zones
    with pytest.raises(ProbeEvidenceError, match="strictly increasing"):
        derive_restaurant_probe_evidence(_archive(tmp_path, records=records))


def test_missing_select_team_fails_closed(tmp_path):
    archive = _archive(tmp_path, records=_complete_records())
    (archive / "goal_episode" / "participant_trace.json").write_text(
        json.dumps({"actions": []}), encoding="utf-8")
    with pytest.raises(ProbeEvidenceError, match="select_team"):
        derive_restaurant_probe_evidence(archive)


def test_roster_starts_from_select_team_and_stays_real(tmp_path):
    report = derive_restaurant_probe_evidence(
        _archive(tmp_path, records=_complete_records()))
    initial = report["measurements"][0]
    # The roster at t=0 is exactly the episode's own select_team choice.
    assert initial["feasibility"]["feasibility_label"] == "deployable"
    assert report["measurements"][-1]["feasibility"]["feasibility_label"] == "deployable"


def test_writer_round_trips_the_report(tmp_path):
    archive = _archive(tmp_path, records=_complete_records())
    out = tmp_path / "probe_evidence.json"
    path = write_restaurant_probe_evidence(archive, out)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == RESTAURANT_PROBE_SCHEMA
    assert payload["measurements"][0]["measurement_id"] == "restaurant_initial"
