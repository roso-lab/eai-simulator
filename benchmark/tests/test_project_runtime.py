from pathlib import Path
import os
from types import ModuleType
import sys

import pytest

from tools.project_runtime import (
    PROJECT_ROOT,
    activate_project_runtime,
    provenance_report,
    runtime_environment,
)


def test_runtime_environment_forces_project_sources_and_assets():
    external = "/opt/other/eai-simulator/source/EAI"
    env = runtime_environment({
        "PYTHONPATH": external,
        "EAI_USD_ROOT": "/tmp/other",
        "EAI_NVD_ASSET_ROOT": "/opt/global/nvd",
        "EAI_RUNTIME_ASSET_CACHE": "/opt/global/runtime-assets",
        "EAI_LIDAR_ASSET_CACHE": "/opt/global/lidar-assets",
    })
    paths = env["PYTHONPATH"].split(":")
    assert paths[:4] == [
        str(PROJECT_ROOT),
        str(PROJECT_ROOT / "source" / "EAI"),
        str(PROJECT_ROOT / "source" / "EAI_assets"),
        str(PROJECT_ROOT / "source" / "EAI_hmrs"),
    ]
    assert external not in paths
    assert env["EAI_USD_ROOT"] == str(PROJECT_ROOT / "usd")
    assert env["EAI_CONTROLLER_ROOT"].startswith(str(PROJECT_ROOT))
    assert env["EAI_ASSETS_AUTO_DOWNLOAD"] == "0"
    assert "EAI_NVD_ASSET_ROOT" not in env
    assert Path(env["HETEROCOORD_ARTIFACT_ROOT"]).is_relative_to(PROJECT_ROOT)
    assert Path(env["TMPDIR"]).is_relative_to(PROJECT_ROOT)
    for key in ("EAI_RUNTIME_ASSET_CACHE", "EAI_LIDAR_ASSET_CACHE"):
        assert Path(env[key]).is_relative_to(PROJECT_ROOT)


def test_generated_asset_cache_cannot_escape_through_symlink(monkeypatch, tmp_path):
    import tools.project_runtime as runtime
    escaped = tmp_path / "outside-link"
    escaped.symlink_to("/opt", target_is_directory=True)
    monkeypatch.setattr(runtime, "ASSET_CACHE_ROOTS", {"EAI_RUNTIME_ASSET_CACHE": escaped})
    with pytest.raises(RuntimeError, match="escapes HeteroCoord"):
        runtime.runtime_environment({})


def test_provenance_report_resolves_all_eai_packages_inside_project(monkeypatch):
    monkeypatch.chdir(PROJECT_ROOT.parent)
    report = provenance_report()
    assert report["cwd"] == str(PROJECT_ROOT)
    assert report["external_editable_environment_modified"] is False
    assert set(report["packages"]) == {"EAI", "EAI_assets", "EAI_hmrs"}
    for origin in report["packages"].values():
        assert Path(origin).is_relative_to(PROJECT_ROOT)


def test_activation_fails_closed_if_external_eai_is_already_loaded(monkeypatch):
    external = ModuleType("EAI")
    external.__file__ = "/opt/other/eai-simulator/source/EAI/EAI/__init__.py"
    monkeypatch.setitem(sys.modules, "EAI", external)
    with pytest.raises(RuntimeError, match="already loaded"):
        activate_project_runtime()


def test_simulator_loading_boundary_activates_complete_project_runtime(monkeypatch):
    import simulator

    monkeypatch.chdir(PROJECT_ROOT.parent)
    monkeypatch.setenv("EAI_USD_ROOT", "/tmp/other")
    monkeypatch.setenv("EAI_CONTROLLER_ROOT", "/tmp/other-controller")
    monkeypatch.setenv("EAI_NVD_ASSET_ROOT", "/opt/global/nvd")

    simulator._ensure_repo_sources_on_path(enforce_cwd=True)

    assert Path.cwd() == PROJECT_ROOT
    assert Path(sys.path[0]).resolve() == PROJECT_ROOT.resolve()
    assert Path(sys.path[1]).resolve() == (PROJECT_ROOT / "source" / "EAI").resolve()
    assert Path(sys.path[2]).resolve() == (PROJECT_ROOT / "source" / "EAI_assets").resolve()
    assert Path(sys.path[3]).resolve() == (PROJECT_ROOT / "source" / "EAI_hmrs").resolve()
    assert Path(os.environ["EAI_USD_ROOT"]).resolve() == (PROJECT_ROOT / "usd").resolve()
    assert Path(os.environ["EAI_CONTROLLER_ROOT"]).is_relative_to(PROJECT_ROOT)
    assert Path(os.environ["HETEROCOORD_ARTIFACT_ROOT"]).is_relative_to(PROJECT_ROOT)
    assert Path(os.environ["TMPDIR"]).is_relative_to(PROJECT_ROOT)
    assert "EAI_NVD_ASSET_ROOT" not in os.environ


def test_simulator_loading_boundary_keeps_external_module_and_fails_closed(monkeypatch):
    import simulator

    external = ModuleType("EAI")
    external.__file__ = "/opt/other/eai-simulator/source/EAI/EAI/__init__.py"
    monkeypatch.setitem(sys.modules, "EAI", external)

    with pytest.raises(RuntimeError, match="already loaded"):
        simulator._ensure_repo_sources_on_path()

    assert sys.modules["EAI"] is external


def test_simulator_freezes_relative_scenario_before_runtime_chdir():
    import simulator

    project_input = PROJECT_ROOT / "tmp" / "project_runtime" / "relative-scenario.json"
    relative = project_input.relative_to(PROJECT_ROOT)
    config = simulator.SimulatorLaunchConfig(env="unused", scenario_file=str(relative))

    resolved = simulator._resolve_project_session_inputs(config, caller_cwd=PROJECT_ROOT)

    assert resolved.scenario_file == str(project_input.resolve())
    with pytest.raises(ValueError, match="inside HeteroCoord"):
        simulator._resolve_project_session_inputs(
            config, caller_cwd=Path("/opt/other"),
        )


def test_low_level_simulator_source_helper_preserves_caller_cwd(monkeypatch, tmp_path):
    import simulator

    monkeypatch.chdir(tmp_path)
    simulator._ensure_repo_sources_on_path()
    assert Path.cwd() == tmp_path


def test_session_close_marker_accepts_only_project_local_path(monkeypatch):
    import simulator

    local = PROJECT_ROOT / "tmp" / "project_runtime" / "session-close.json"
    monkeypatch.setenv("HETEROCOORD_SESSION_CLOSE_MARKER", str(local))
    assert simulator._session_close_marker_path() == local.resolve()

    monkeypatch.setenv("HETEROCOORD_SESSION_CLOSE_MARKER", "/tmp/session-close.json")
    assert simulator._session_close_marker_path() is None


def test_session_close_marker_records_cleanup_and_close_boundary(monkeypatch):
    import json
    import simulator

    path = PROJECT_ROOT / "tmp" / "project_runtime" / "marker-test.json"
    simulator._write_session_close_marker(
        path,
        status="cleanup_failed",
        cleanup_errors=[{"callback": "close_sensor", "error": "RuntimeError: boom"}],
        app_close_requested=True,
        app_close_returned=False,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["cleanup_callbacks_complete"] is False
    assert payload["cleanup_errors"][0]["callback"] == "close_sensor"
    assert payload["app_close_requested"] is True
    assert payload["app_close_returned"] is False
    path.unlink()


def test_session_close_marker_write_failure_is_supporting_only(monkeypatch):
    import simulator

    path = PROJECT_ROOT / "tmp" / "project_runtime" / "marker-write-failure.json"
    original = Path.write_text

    def fail_write(self, *args, **kwargs):
        if self == path.with_name(f".{path.name}.{os.getpid()}.tmp"):
            raise OSError("simulated marker I/O failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_write)
    simulator._write_session_close_marker(
        path,
        status="app_close_returned",
        cleanup_errors=[],
        app_close_requested=True,
        app_close_returned=True,
    )
    assert not path.exists()
