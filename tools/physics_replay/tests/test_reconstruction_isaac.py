"""Opt-in uninterrupted-original versus fresh-process physical acceptance."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get("EAI_RUN_NATIVE_REPLAY") != "1", reason="requires Isaac/GPU integration")
@pytest.mark.parametrize("scenario,steps,checkpoint", [("locomotion", 64, 40), ("carry", 160, 80)])
def test_factory_fresh_process_reconstruction(tmp_path, scenario, steps, checkpoint):
    directory = tmp_path / "audit"
    result = subprocess.run(
        [sys.executable, "-m", "demo.fire_rescue.checks.reconstruct_physics", "--output", str(directory),
         "--scenario", scenario, "--steps", str(steps), "--checkpoint-step", str(checkpoint)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1200,
    )
    (tmp_path / "supervisor.log").write_text(result.stdout, encoding="utf-8")
    assert (directory / "comparison.json").is_file(), result.stdout[-5000:]
    report = json.loads((directory / "comparison.json").read_text(encoding="utf-8"))
    assert report["passed"], report
    assert result.returncode == 0, result.stdout[-5000:]
    original, reconstructed = report["runs"]
    assert original["pid"] != reconstructed["pid"]
    assert reconstructed["peak_contact_force_N"] > 0
    verification = reconstructed["verification"]
    assert verification["compared_steps"] == steps
    assert verification["prefix_verified"]
    assert verification["original_continuation_verified"]
    assert all(value == 0.0 for value in verification["max_errors"].values())
    assert any("/observation" in name for name in verification["max_errors"])
    assert any("after/rng/torch" in name for name in verification["max_errors"])
    if scenario == "carry":
        assert reconstructed["fixture"]["payload_physics_unchanged"]
        assert reconstructed["fixture"]["payload_mass_kg"] > 0
        assert reconstructed["payload_motion_after_checkpoint_m"] > 1e-3
        assert reconstructed["peak_weld_anchor_error_m"] < 0.01
        live_directory = tmp_path / "live"
        result = subprocess.run(
            [sys.executable, "-m", "demo.fire_rescue.checks.reconstruct_physics", "--mode", "reconstruct",
             "--reference", str(directory / "record" / "journal"), "--output", str(live_directory),
             "--release-at-checkpoint", "--live-speed", "0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600,
        )
        (tmp_path / "live_supervisor.log").write_text(result.stdout, encoding="utf-8")
        live_report = json.loads((live_directory / "comparison.json").read_text(encoding="utf-8"))
        assert result.returncode == 0 and live_report["passed"], live_report
        live_run = live_report["runs"][0]
        assert live_run["live_steps_after_release"] == steps - checkpoint
        assert live_run["live_command_speed"] == 0.1
        assert live_run["verification"]["compared_steps"] == checkpoint
        assert live_run["verification"]["released_for_live_continuation"]
        assert not live_run["verification"]["original_continuation_verified"]
