import json
import subprocess
import sys

from tools.physics_replay.types import Manifest


def test_inspect_prints_manifest_summary(tmp_path):
    manifest = Manifest(
        environment={"env_name": "test", "num_envs": 1}, targets=[]
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(manifest.to_dict()), encoding="utf-8"
    )
    (tmp_path / "index.json").write_text(
        json.dumps({"format_version": 1, "segments": []}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "tools/physics_replay/cli.py",
            "inspect",
            "--episode",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["environment"]["env_name"] == "test"
    assert payload["segment_count"] == 0


def test_inspect_returns_nonzero_for_missing_episode(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "tools/physics_replay/cli.py",
            "inspect",
            "--episode",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "replay" in result.stderr.lower()
