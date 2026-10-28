"""CPU-only preflight against the shared host's pure contract functions.

Never call the host main(), AppLauncher, or a physical executor. Native policy
and CLI are real; a dispatch tripwire is only a negative boundary assertion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import torch

from algorithm.system.coordination.native_runtime import sha256_file
from algorithm.system.coordination.sadcher.tests.test_native_adapter import ROOT, COMPATIBLE
from benchmark.native_algorithms import build_native_adapter
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner
from tools.algorithm_adapters.isaac_pick_place import physical_options, native_decision_from_report


REPO = Path(__file__).resolve().parents[5]
FIXTURES = REPO / "records/algorithm_adapters/sadcher/physical_preflight"
READ_ONLY = (
    "tools/algorithm_adapters/isaac_pick_place.py",
    "tools/algorithm_adapters/run.py",
    "benchmark/native_algorithms.py",
    "algorithm/system/coordination/sadcher/native_adapter.py",
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


@pytest.fixture
def output(tmp_path):
    target = Path(os.getenv("HETEROCOORD_SADCHER_PREFLIGHT_OUTPUT", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    return target


def test_physical_contract_preserves_original_and_explicit_alignment(output):
    results = {}
    for name, expected_error in (
        ("original_exact", "target_ref=/World/Object"),
        ("original_with_host_metadata", "declared task position XY disagrees"),
        ("host_aligned", None),
    ):
        path = FIXTURES / f"task_{name}.json"
        task = EaiTaskPackage.from_json(path)
        if expected_error:
            with pytest.raises(ValueError, match=expected_error) as exc:
                physical_options(task, output / "not_executed.json")
            results[name] = {"accepted": False, "error": str(exc.value)}
        else:
            options = physical_options(task, output / "not_executed.json")
            assert options.object_position == [.45, 0, .475]
            assert options.place_position == [.425, .01, .475]
            assert options.timeout_s == 100 and options.approach_degrees == 15
            results[name] = {"accepted": True, "options": {
                k: str(v) if isinstance(v, Path) else v for k, v in vars(options).items()
            }}
        results[name]["task_sha256"] = sha256_file(path)
    original = json.loads((FIXTURES / "task_original_with_host_metadata.json").read_text())
    aligned = json.loads((FIXTURES / "task_host_aligned.json").read_text())
    original["tasks"][0]["metadata"]["position"][1] = 0.0
    assert original == aligned  # Alignment changes only declared task Y.
    write_json(output / "host_contract_check.json", {
        "scope": "pure_host_contract_only", "cases": results,
        "isaac_started": False, "physical_success": None,
        "host_sha256": sha256_file(REPO / READ_ONLY[0]),
    })


@pytest.mark.parametrize("case", ["original_with_host_metadata", "host_aligned"])
def test_native_factory_cli_and_host_gate_preserve_idle(case, output):
    if not (ROOT / COMPATIBLE).is_file():
        pytest.skip("real native assets missing; preflight not verified")
    torch.set_num_threads(1)
    hashes_before = {p: sha256_file(REPO / p) for p in READ_ONLY}
    task_path = FIXTURES / f"task_{case}.json"
    config_path = FIXTURES / "adapter_config.json"
    task = EaiTaskPackage.from_json(task_path)
    config = json.loads(config_path.read_text())
    provider = build_native_adapter("sadcher", source_root=ROOT, **config)

    class DispatchTripwire:
        calls = 0

        def execute(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("idle decision must never reach an executor")

    executor = DispatchTripwire()
    result = TaskRunner(executor=executor).run(TaskRunRequest(task), provider)
    assert result.decision.provenance["native_assignments"] == {"0": 2}
    assert result.decision.provenance["idle_robot_ids"] == ["ur5_1"]
    assert not result.decision.assignments and not result.feedback and executor.calls == 0
    assert result.decision.unassigned_tasks == ("pick_place",)
    assert result.verdict["status"] == "N/A"
    # SADCHER selects its native torch device from the upstream runtime. The
    # adapter must preserve that choice; CPU-only execution is covered by the
    # subprocess preflight, while this in-process test may run with CUDA.
    assert provider.last_rewards.device.type in {"cpu", "cuda"}
    assert provider.last_checkpoint_audit["strict_complete"]
    assert provider.last_checkpoint_audit["audit_mutates_model"] is False

    # Independently construct upstream records without using adapter translation.
    from simulation_environment.simulator_2D import Simulation
    from schedulers.sadcher import SadcherScheduler
    y = .05 if case == "original_with_host_metadata" else 0.0
    locations = np.array([[0., 0.], [.45, y], [0., 0.]])
    sim = Simulation({
        "task_locations": locations, "T_e": np.array([0., 30., 0.]),
        "T_t": np.linalg.norm(locations[:, None, :] - locations[None, :, :], axis=-1),
        "Q": np.array([[True, False, False]]),
        "R": np.array([[False, False, False], [True, False, False], [False, False, False]]),
        "precedence_constraints": [],
    }, scheduler_name="sadcher", use_idle=True)
    native = SadcherScheduler(False, str(ROOT / COMPATIBLE), sim.duration_normalization, sim.location_normalization)
    from algorithm.system.coordination.sadcher.native_adapter import audit_checkpoint
    audit_checkpoint(native, ROOT / COMPATIBLE)
    rewards, schedule = native.calculate_robot_assignment(sim)
    assert schedule.robot_assignments == provider.last_schedule.robot_assignments == {0: 2}
    assert torch.equal(rewards, provider.last_rewards)
    assert all(torch.equal(a, b) for a, b in zip(native.extract_task_robot_features(sim),
               provider.last_scheduler.extract_task_robot_features(provider.last_simulation)))

    report_path = output / f"{case}.native_decision.json"
    argv = [sys.executable, "-m", "tools.algorithm_adapters.run", "--algorithm", "sadcher",
            "--source-root", str(ROOT), "--task", str(task_path), "--config", str(config_path),
            "--report", str(report_path)]
    process = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, timeout=60,
                             env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"})
    (output / f"{case}.cli.stdout.txt").write_text(process.stdout)
    (output / f"{case}.cli.stderr.txt").write_text(process.stderr)
    assert process.returncode == 0, process.stderr
    report = json.loads(report_path.read_text())
    assert report["status"] == "native_decision_returned"
    assert report["result"]["assignments"] == []
    assert report["provenance"]["native_assignments"] == {"0": 2}
    assert report["physical_success"] is None and not report["simulation_acceptance_passed"]
    with pytest.raises(RuntimeError, match="no skill substituted") as exc:
        native_decision_from_report(report)
    assert "isaaclab.app" not in sys.modules and "isaacsim" not in sys.modules
    hashes_after = {p: sha256_file(REPO / p) for p in READ_ONLY}
    assert hashes_before == hashes_after
    write_json(output / f"{case}.preflight.json", {
        "scope": "cpu_native_decision_and_pure_host_gate_only", "status": "blocked_native_idle",
        "result": result.to_dict(), "provenance": result.decision.provenance,
        "predicted_rewards_including_sentinels": rewards.tolist(),
        "independent_native_parity": {"assignments": True, "features": True, "rewards": True},
        "host_gate_error": str(exc.value), "executor_dispatches": executor.calls,
        "isaac_started": False, "physical_success": None, "physical_feedback_count": 0,
        "cli_command": argv, "cli_returncode": process.returncode,
        "environment_overrides": {"CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1"},
        "read_only_sha256_before": hashes_before, "read_only_sha256_after": hashes_after,
    })
