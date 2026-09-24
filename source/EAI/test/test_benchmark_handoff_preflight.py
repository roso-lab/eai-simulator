import json

import pytest

from demo.benchmark.handoff_preflight import run_handoff_preflight


def test_preflight_builds_five_scene_public_boundary(tmp_path):
    root = tmp_path / "preflight"
    report = run_handoff_preflight(root)
    assert report["scene_keys"] == [
        "factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant"]
    assert report["suite"]["package_count"] == 45
    assert report["suite"]["factor_pair_count"] == 35
    assert all(row["accepted"] for row in report["public_adapter_smoke"].values())
    assert report["isaac_started"] is False
    saved = json.loads((root / "preflight.json").read_text())
    assert saved["execution_validated"] is False
    assert saved["business_outcomes_validated"] is False
    assert saved["scoreable"] is False


def test_preflight_refuses_existing_output_and_cleans_failed_output(tmp_path, monkeypatch):
    root = tmp_path / "preflight"
    root.mkdir()
    with pytest.raises(FileExistsError):
        run_handoff_preflight(root)

    import demo.benchmark.handoff_preflight as module
    broken = tmp_path / "broken"
    monkeypatch.setattr(module, "write_goal_suite", lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("source drift")))
    with pytest.raises(RuntimeError, match="source drift"):
        run_handoff_preflight(broken)
    assert not broken.exists()

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        run_handoff_preflight(alias / "through-link")
