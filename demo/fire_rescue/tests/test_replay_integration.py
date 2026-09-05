"""Opt-in end-to-end Factory Fire Rescue replay verification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


if os.environ.get("EAI_RUN_FIRE_RESCUE_REPLAY_TEST") != "1":
    pytest.skip(
        "set EAI_RUN_FIRE_RESCUE_REPLAY_TEST=1 in env_isaaclab to run Factory replay",
        allow_module_level=True,
    )


def test_factory_replay_writes_a_diagnostic_report(tmp_path):
    output_dir = tmp_path / "fire_rescue"
    command = [
        sys.executable,
        "-m",
        "demo.fire_rescue.main",
        "--env",
        "EAI-Factory-v0",
        "--headless",
        "--trials",
        "1",
        "--auto-fire-delay",
        "0",
        "--replay-verify-dir",
        str(output_dir),
        "--replay-verify-steps",
        "1",
        "--replay-local-fallback",
        "--device",
        "cpu",
    ]
    environment = dict(os.environ)
    environment["EAI_RUN_FIRE_RESCUE_REPLAY"] = "1"
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300,
        env=environment,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    report_path = output_dir / "trial_01" / "verification.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["tape_coverage"] == 1.0
    assert report["noise_coverage"] == 1.0
    assert report["restore"]["ok"] is True
    assert "drift" in report
