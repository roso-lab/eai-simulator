"""Physical evidence rejection tests with isolated native-readback fixtures."""

from copy import deepcopy
import json
import sys

import pytest

from demo.benchmark import goal_physical_review as review
from demo.benchmark.goal_physical_probe import ROOT, _LOCAL_SOURCES, _probe_sources
from demo.benchmark.provenance import canonical_sha256, file_sha256


@pytest.fixture
def archive(tmp_path, monkeypatch):
    source = tmp_path / "source.py"
    source.write_text("# source\n")
    request = {"run_id": "test-run", "scene_key": "warehouse_nvd", "steps": 2, "gui": False,
               "requested_device": "cuda:0", "effective_device": "cuda:0", "device_policy": "requested_device"}
    robots = [{"runtime_id": f"m20_{i}", "public_id": f"member_{i}",
               "prim_path": f"/World/envs/env_0/m20_{i}", "public_declaration": {"mobility": "ground"}} for i in (1,2)]
    deployment = {"sha256": "fixture", "robot_instances": robots}
    state = [0.,0.,.52,1.,0.,0.,0.,0.,0.,0.,0.,0.,0.]
    rows = [{**r, "status": "native_articulation_read_back", "actual_articulation_paths": [r["prim_path"]],
             "root_state_w": deepcopy(state), "controller_state_identity": r["runtime_id"],
             "body_masses_kg": [20.], "body_names": ["base"]} for r in robots]
    from demo.benchmark.goal_physical_probe import ROOT, _LOCAL_SOURCES
    sources = {str(ROOT/"demo/benchmark"/name): file_sha256(ROOT/"demo/benchmark"/name) for name in _LOCAL_SOURCES}
    sources[str(ROOT/"simulator.py")] = file_sha256(ROOT/"simulator.py")
    sources[str(source)] = file_sha256(source)
    layer = tmp_path/"native.usda"
    layer.write_text("#usda 1.0\n")
    sources[str(layer)] = file_sha256(layer)
    manifest = {"run_id": request["run_id"], "source_files": deepcopy(sources),
                "stage": {"root_layer_identifier": "anon:test", "up_axis": "Z", "meters_per_unit": 1., "used_file_layers": [str(layer)]}}
    report = {"run_id": request["run_id"], "request_sha256": canonical_sha256(request),
        "status": "diagnostic_completed", "scene_key": request["scene_key"], "deployment_sha256": "fixture",
        "physical_task_execution_validated": False, "business_outcomes_validated": False, "scoreable": False,
        "runtime_source_files": sources, "possible_agents": [r["runtime_id"] for r in robots],
        "actual_device": "cuda:0", "timing": {"initial_simulation_time_s": 0., "physics_dt_s": .005,
                                              "control_decimation": 4, "control_step_dt_s": .02},
        "initial_robots": deepcopy(rows), "final_robots": deepcopy(rows), "native_step_count": 2,
        "samples": [{"step": i, "time_s": (i+1)*.02,
                     "robot_root_state_w": {r["runtime_id"]: deepcopy(state) for r in robots},
                     "commands": {r["runtime_id"]: [0.,0.,0.] for r in robots}} for i in range(2)], "captures": []}
    process = {"run_id": request["run_id"], "request_sha256": canonical_sha256(request), "returncode": 0, "timed_out": False}
    monkeypatch.setattr(review, "load_probe", lambda root: (request, deployment, {}))
    def save():
        manifest["request_sha256"] = canonical_sha256(request)
        (tmp_path/"runtime_manifest.json").write_text(json.dumps(manifest))
        report["runtime_manifest_sha256"] = process["runtime_manifest_sha256"] = file_sha256(tmp_path/"runtime_manifest.json")
        launch = {"request_sha256": canonical_sha256(request), "run_id": request["run_id"],
                  "command": [sys.executable,"-m","demo.benchmark.goal_physical_probe","--worker","--output-root",str(tmp_path)]}
        (tmp_path/"launch.json").write_text(json.dumps(launch))
        process["launch_sha256"] = file_sha256(tmp_path/"launch.json")
        (tmp_path / "physical_report.json").write_text(json.dumps(report))
        process["physical_report_sha256"] = file_sha256(tmp_path / "physical_report.json")
        (tmp_path / "process_result.json").write_text(json.dumps(process))
    save()
    return tmp_path, report, process, request, save


def test_native_steps_prove_only_instance_readback_and_bounded_settle(archive):
    result = review.audit_physical_probe(archive[0])
    assert result["native_instance_and_step_readback_validated"]
    assert result["all_robots_bounded_settle"]
    assert result["physical_task_execution_validated"] is result["scoreable"] is False


def test_warehouse_late_query_source_is_frozen_without_precontrol_load_requirement():
    path = str(ROOT / "demo/benchmark/goal_scene_query_calibration.py")
    deployment = {"scene_key": "factory", "scene_overlay": {"source_files": {}}}
    assert "goal_scene_query_calibration.py" not in _LOCAL_SOURCES
    assert path not in _probe_sources(deployment)
    deployment["scene_key"] = "warehouse_nvd"
    assert _probe_sources(deployment)[path] == file_sha256(ROOT / "demo/benchmark/goal_scene_query_calibration.py")


@pytest.mark.parametrize("mutation", ["alias", "controller", "time", "negative_time", "dt", "device", "source_coverage",
                                     "count", "command", "final", "source", "score", "run", "gui"])
def test_invalid_evidence_rejected_with_refreshed_archive_hash(archive, mutation):
    root, report, process, request, save = archive
    if mutation == "alias":
        report["final_robots"][1]["actual_articulation_paths"] = report["final_robots"][0]["actual_articulation_paths"]
    elif mutation == "controller":
        report["final_robots"][1]["controller_state_identity"] = report["final_robots"][0]["controller_state_identity"]
    elif mutation == "time":
        report["samples"][1]["time_s"] = report["samples"][0]["time_s"]
    elif mutation == "negative_time":
        report["samples"][0]["time_s"] = -.5
    elif mutation == "dt":
        report["samples"][1]["time_s"] = .5
    elif mutation == "device":
        report["actual_device"] = "cpu"
    elif mutation == "source_coverage":
        report["runtime_source_files"].pop(str(root/"native.usda"))
    elif mutation == "count":
        report["samples"].pop()
    elif mutation == "command":
        report["samples"][0]["commands"]["m20_1"] = [1.,0.,0.]
    elif mutation == "final":
        report["final_robots"][0]["root_state_w"][0] = 2.
    elif mutation == "source":
        (root / "source.py").write_text("# changed")
    elif mutation == "score":
        report["scoreable"] = True
    elif mutation == "run":
        process["run_id"] = "other"
    else:
        request["gui"] = True
        report["request_sha256"] = process["request_sha256"] = canonical_sha256(request)
    save()
    with pytest.raises(ValueError):
        review.audit_physical_probe(root)


def test_fallen_robot_retains_native_execution_but_fails_health(archive):
    root, report, _, _, save = archive
    report["samples"][-1]["robot_root_state_w"]["m20_1"][2] = -1.
    report["final_robots"][0]["root_state_w"][2] = -1.
    save()
    result = review.audit_physical_probe(root)
    assert result["native_instance_and_step_readback_validated"]
    assert result["all_robots_bounded_settle"] is False
