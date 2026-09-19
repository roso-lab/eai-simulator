"""Reference probes exercise real admission/effects without native scene assets."""

from copy import deepcopy

import pytest

from demo.benchmark import goal_deployment_conformance as probe
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark.contracts import build_public_task


@pytest.fixture(params=probe.SCENES)
def deployment(request, monkeypatch):
    if request.param == "city":
        public = build_public_task({"regions": [{"id": "road_surface", "kind": "road_network",
            "public_description": "Symbolic test road, no physical claim",
            "access_modes": ["ground_navigation", "aerial_navigation"], "resource_capacity": 4}]})
    else:
        public = build_goal_task_spec(request.param)
    value = {"scene_key": request.param, "public_task": public, "sha256": canonical_sha256(public)}
    # This test isolates reference execution. Source-bound deployment rebuilding
    # is tested independently and in the four-scene CLI integration check.
    def validate(actual):
        if actual != value:
            raise ValueError("unexpected deployment fixture")
    monkeypatch.setattr(probe, "validate_goal_deployment", validate)
    return value


def test_four_scene_diagnostics_hold_then_complete_without_business_claim(deployment):
    report = probe.run_reference_conformance(deployment)
    assert probe.verify_reference_conformance(report, deployment)["validated"]
    assert report["missing_occupancy"]["status"] == "pending"
    assert report["blocked_occupancy"]["status"] == "held"
    assert report["completion"]["status"] == "succeeded"
    assert report["physical_execution_validated"] is report["business_outcomes_validated"] is report["scoreable"] is False
    assert report["origin"] == "deterministic_reference_not_simulator"
    assert len(report["backend_report"]["evidence"]) == 1
    assert all(row["status"] == "unassessed" for row in report["backend_report"]["ledger"]["business_outcomes"])


@pytest.mark.parametrize("mutation", ["physical", "receipt", "initial_state"])
def test_rehash_does_not_repair_forged_reference_result(deployment, mutation):
    report = deepcopy(probe.run_reference_conformance(deployment))
    if mutation == "physical":
        report["physical_execution_validated"] = True
    elif mutation == "receipt":
        report["backend_report"]["evidence"][0]["origin"] = "isaac"
    else:
        report["initial_state"]["robot_regions"].clear()
    report["sha256"] = canonical_sha256({k: v for k, v in report.items() if k != "sha256"})
    with pytest.raises(ValueError, match="differs"):
        probe.verify_reference_conformance(report, deployment)
