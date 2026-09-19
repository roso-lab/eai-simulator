from copy import deepcopy
import json

import pytest

from demo.benchmark.stage17_evidence_gate import review_stage17_evidence


def _city():
    return {"status": "diagnostic_completed", "sources_unchanged": True,
            "full_city_service": "pending", "incident_trace": {
                "receipt": {"status": "received"},
                "service_review": {"scoreable": False}}}


def _house():
    return {"status": "diagnostic_completed", "resident_path": {"status": "blocked"},
            "final_objects": {"objects": [{"public_id": "resident_bookstack_01",
                "physx": {"position_m": [4.5, 12.3, .375]},
                "usd": {"world_mesh_bounds_m": {"size": [.3, .25, .25]}}}]}}


def _warehouse():
    return {"status": "blocked", "physical_task_execution_validated": False}


def test_gate_classifies_diagnostic_only_without_promoting_release():
    result = review_stage17_evidence({"city": _city(), "house": _house(), "warehouse": _warehouse()})
    assert result["scenes"]["city"]["status"] == "diagnostic_only"
    assert result["scenes"]["house"]["status"] == "diagnostic_only"
    assert result["scenes"]["warehouse"]["status"] == "blocked"
    assert result["release_status"] == "pending"
    assert result["benchmark_ready"] is False and result["scoreable"] is False


@pytest.mark.parametrize("mutation,reason", [
    (lambda report: report.__setitem__("sources_unchanged", False), "city_source_hashes_changed_or_missing"),
    (lambda report: report["incident_trace"]["receipt"].clear(), "city_independent_receipt_missing"),
])
def test_city_gate_fails_closed_on_missing_provenance(mutation, reason):
    report = _city()
    mutation(report)
    result = review_stage17_evidence({"city": report, "house": _house(), "warehouse": _warehouse()})
    assert reason in result["scenes"]["city"]["blockers"]


def test_house_gate_catches_new_buried_object():
    report = _house()
    report["final_objects"]["objects"][0]["physx"]["position_m"][2] = .1
    result = review_stage17_evidence({"city": _city(), "house": report, "warehouse": _warehouse()})
    assert "house_bookstack_below_floor" in result["scenes"]["house"]["blockers"]


def test_warehouse_gate_requires_actual_transport_and_dock_receipt():
    report = {"status": "diagnostic_completed", "physical_task_execution_validated": False,
              "carry_readback": {"object_displacement_m": .001},
              "dock_receipt": {"state": "ready"}}
    result = review_stage17_evidence({"city": _city(), "house": _house(), "warehouse": report})
    assert result["scenes"]["warehouse"]["status"] == "blocked"
    assert "warehouse_transport_displacement_insufficient" in result["scenes"]["warehouse"]["blockers"]


def test_report_paths_are_supported_and_inputs_are_detached(tmp_path):
    reports = {"city": _city(), "house": _house(), "warehouse": _warehouse()}
    paths = {}
    for name, value in reports.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value))
        paths[name] = path
    result = review_stage17_evidence(paths)
    reports["city"]["status"] = "changed"
    assert result["scenes"]["city"]["status"] == "diagnostic_only"


def test_exact_scene_set_is_required():
    with pytest.raises(ValueError, match="exactly city, house and warehouse"):
        review_stage17_evidence({"city": _city(), "house": _house()})
