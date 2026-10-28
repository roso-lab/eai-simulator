from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from tools.validation import room06_livt04_gpu_probe as probe


GPU_UUID = "GPU-11111111-2222-3333-4444-555555555555"
ROBOTS = ("hc-rt-006", "hc-rt-020", "hc-rt-021")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(mode: str = probe.SCENE_MODE) -> dict:
    value = {
        "schema": probe.INPUT_SCHEMA,
        "input_digest": "0" * 64,
        "mode": mode,
        "scene_ref": {"path": "/frozen/room_06.usda", "sha256": "a" * 64},
        "requested_device": "cuda:0",
        "expected_gpu_uuid": GPU_UUID,
        "headless": True,
        "seed": 17,
        "diagnostic_steps": 3,
        "package_ref": None,
        "preflight_inputs": None,
        "runtime_hook_ref": None,
        "formal_gates": copy.deepcopy(probe.FORMAL_GATES),
    }
    if mode == probe.FULL_MODE:
        value["package_ref"] = {
            "path": "/frozen/package.json",
            "sha256": "b" * 64,
            "package_digest": "c" * 64,
        }
        value["preflight_inputs"] = {
            "semantic_root": {"path": "/frozen/map", "sha256": "d" * 64},
            "scene_usd": value["scene_ref"],
            "robot_roster": {"path": "/frozen/roster.json", "sha256": "e" * 64},
            "binding": {"path": "/frozen/binding.json", "sha256": "f" * 64},
            "task_setup": {"path": "/frozen/setup.json", "sha256": "1" * 64},
        }
    value["input_digest"] = probe.probe_input_digest(value)
    return value


def _write_request(tmp_path: Path, value: dict) -> tuple[Path, str]:
    path = tmp_path / "probe-input.json"
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path, _sha(path)


def test_scene_diagnostic_rejects_runtime_source_injection(tmp_path):
    value = _request()
    value["runtime_source_ref"] = {"path": "/source.py", "sha256": "1" * 64, "factory": "create_source"}
    value["input_digest"] = probe.probe_input_digest(value)
    path, digest = _write_request(tmp_path, value)
    with pytest.raises(probe.Room06GpuProbeError, match="cannot contain"):
        probe.load_probe_input(path, expected_file_sha256=digest, scene_load_diagnostic_only=True)


def test_runtime_source_is_bound_into_session_before_launch(monkeypatch):
    observed = {}
    source = object()

    def factory(**kwargs):
        observed["factory_inputs"] = kwargs
        return source

    def open_session(config):
        observed["source"] = config.room06_runtime_hook_source_factory(base_env="live-env")
        raise RuntimeError("stop before actual simulator")

    monkeypatch.setitem(sys.modules, "simulator", SimpleNamespace(
        open_simulator_session=open_session,
        SimulatorLaunchConfig=lambda **kwargs: SimpleNamespace(**kwargs),
    ))
    monkeypatch.setattr(probe, "build_frozen_selection", lambda _: ({"scene_key": "room_06"}, {"robot": "robot_1"}))
    monkeypatch.setattr(probe, "_load_runtime_hook", lambda _: factory)
    prepared = {"request": _request(probe.FULL_MODE), "package": {"bound": [1]}, "runtime_source_ref": {"factory": "create_source"}, "runtime_hook_ref": {"factory": "collect"}}
    with pytest.raises(RuntimeError, match="stop before actual simulator"):
        probe.run_full_task(prepared)
    assert observed["source"] is source
    assert observed["factory_inputs"] == {
        "base_env": "live-env", "package": {"bound": [1]}, "instance_agent_map": {"robot": "robot_1"},
    }
    observed["factory_inputs"]["package"]["bound"].append(2)
    assert prepared["package"] == {"bound": [1]}


def test_runtime_hook_bytes_rechecked_before_import(tmp_path):
    hook = tmp_path / "hook.py"
    hook.write_text("def create_source(**kwargs): return None\n")
    ref = {"path": str(hook), "sha256": _sha(hook), "factory": "create_source"}
    probe._verify_hook_ref(ref)
    hook.write_text("raise AssertionError('changed code must never execute')\n")
    with pytest.raises(probe.Room06GpuProbeError, match="digest mismatch"):
        probe._load_runtime_hook(ref)


@pytest.mark.parametrize("missing", ["runtime_hook_ref", "runtime_source_ref"])
def test_full_task_missing_provider_stops_before_simulator(monkeypatch, missing):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("must not launch or load any provider")
    monkeypatch.setattr(probe, "_load_runtime_hook", forbidden)
    monkeypatch.setattr(probe, "_verify_hook_ref", lambda value: value)
    request = _request(probe.FULL_MODE)
    request["runtime_hook_ref"] = request["runtime_source_ref"] = {"factory": "test"}
    request[missing] = None
    with pytest.raises(probe.Room06GpuProbeError, match=missing):
        probe.prepare_full_task(request)
    with pytest.raises(probe.Room06GpuProbeError, match=missing):
        probe.run_full_task({"request": request, "runtime_hook_ref": request["runtime_hook_ref"],
                             "runtime_source_ref": request["runtime_source_ref"]})


def test_invalid_provider_factory_stops_before_simulator_import(tmp_path, monkeypatch):
    hook = tmp_path / "hook.py"
    hook.write_text("factory = 42\n")
    ref = {"path": str(hook), "sha256": _sha(hook), "factory": "factory"}
    with pytest.raises(probe.Room06GpuProbeError, match="callable"):
        probe.run_full_task({"request": _request(probe.FULL_MODE),
                             "runtime_hook_ref": ref, "runtime_source_ref": ref})


def test_full_task_rejects_partial_map_ready_handoff_before_simulator(monkeypatch):
    def should_not_import(*_args, **_kwargs):
        raise AssertionError("simulator must not start for an incomplete handoff")

    monkeypatch.setitem(sys.modules, "simulator", SimpleNamespace(
        open_simulator_session=should_not_import
    ))
    with pytest.raises(probe.Room06GpuProbeError, match="must be supplied together"):
        probe.run_full_task({}, terminal_job={})


def _preflight_refs(tmp_path: Path) -> tuple[Path, dict]:
    artifact = tmp_path / "semantic-map"
    current = artifact / "current"
    current.mkdir(parents=True)
    (current / "manifest.json").write_text("{}\n", encoding="utf-8")
    (artifact / "project.json").write_text(
        json.dumps({"current_revision": "current"}), encoding="utf-8"
    )
    files = {}
    for name in ("room_06.usda", "roster.json", "binding.json", "setup.json"):
        path = tmp_path / name
        path.write_text("{}\n", encoding="utf-8")
        files[name] = path
    refs = {
        "semantic_root": {
            "path": str(artifact),
            "sha256": probe.directory_digest(artifact),
        },
        "scene_usd": {"path": str(files["room_06.usda"]), "sha256": _sha(files["room_06.usda"])},
        "robot_roster": {"path": str(files["roster.json"]), "sha256": _sha(files["roster.json"])},
        "binding": {"path": str(files["binding.json"]), "sha256": _sha(files["binding.json"])},
        "task_setup": {"path": str(files["setup.json"]), "sha256": _sha(files["setup.json"])},
    }
    return artifact, refs


def _event() -> dict:
    return {
        "schema": "heterocoord.observed-skill-event.v1",
        "session_id": "observed-pickup",
        "event_type": "relay_pickup_completed",
        "status": "completed",
        "tick": 1,
        "robot_id": ROBOTS[0],
        "object_id": "book_batch_01",
        "target_ref": "floor1-bookshelf",
        "evidence_digest": hashlib.sha256(b"observed-pickup").hexdigest(),
    }


def _capture(observations: list[dict]) -> dict:
    return {
        "schema": probe.HOOK_CAPTURE_SCHEMA,
        "assignment": {
            "tick": 0,
            "assigned_robot_ids": list(ROBOTS),
            "decision_digest": hashlib.sha256(b"decision").hexdigest(),
        },
        "observations": observations,
        "terminal_inputs": {
            "episode": {},
            "d1": {},
            "d4_first_event_observation": {},
            "d6_measurement": {},
        },
        "collector_closed": True,
    }


def test_load_rejects_tampered_or_malformed_frozen_input(tmp_path: Path):
    value = _request()
    path, file_digest = _write_request(tmp_path, value)
    loaded = probe.load_probe_input(
        path,
        expected_file_sha256=file_digest,
        scene_load_diagnostic_only=True,
    )
    assert loaded["input_digest"] == value["input_digest"]

    value["seed"] = 18
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(probe.Room06GpuProbeError, match="input_digest"):
        probe.load_probe_input(
            path,
            expected_file_sha256=_sha(path),
            scene_load_diagnostic_only=True,
        )

    malformed = _request()
    malformed["unexpected"] = True
    malformed["input_digest"] = probe.probe_input_digest(malformed)
    path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(probe.Room06GpuProbeError, match="schema mismatch"):
        probe.load_probe_input(
            path,
            expected_file_sha256=_sha(path),
            scene_load_diagnostic_only=True,
        )


def test_input_file_sha_is_checked_before_internal_json(tmp_path: Path):
    path, digest = _write_request(tmp_path, _request())
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(probe.Room06GpuProbeError, match="file digest mismatch"):
        probe.load_probe_input(
            path,
            expected_file_sha256=digest,
            scene_load_diagnostic_only=True,
        )


def test_scene_mode_requires_explicit_flag_and_has_no_task_fields(tmp_path: Path):
    value = _request()
    path, digest = _write_request(tmp_path, value)
    with pytest.raises(probe.Room06GpuProbeError, match="does not match requested mode"):
        probe.load_probe_input(
            path,
            expected_file_sha256=digest,
            scene_load_diagnostic_only=False,
        )
    value["runtime_hook_ref"] = {
        "path": "/tmp/hook.py",
        "sha256": "a" * 64,
        "factory": "run",
    }
    value["input_digest"] = probe.probe_input_digest(value)
    path, digest = _write_request(tmp_path, value)
    with pytest.raises(probe.Room06GpuProbeError, match="cannot contain task"):
        probe.load_probe_input(
            path,
            expected_file_sha256=digest,
            scene_load_diagnostic_only=True,
        )


def test_cpu_or_different_cuda_actual_device_is_rejected():
    with pytest.raises(probe.Room06GpuProbeError, match="not CUDA"):
        probe.normalize_device_identity(
            requested_device="cuda:0",
            actual_device="cpu",
            expected_gpu_uuid=GPU_UUID,
            actual_gpu_uuid=GPU_UUID,
        )
    with pytest.raises(probe.Room06GpuProbeError, match="CUDA device mismatch"):
        probe.normalize_device_identity(
            requested_device="cuda:0",
            actual_device="cuda:1",
            expected_gpu_uuid=GPU_UUID,
            actual_gpu_uuid=GPU_UUID,
        )


def test_gpu_uuid_mismatch_is_rejected():
    with pytest.raises(probe.Room06GpuProbeError, match="GPU UUID mismatch"):
        probe.normalize_device_identity(
            requested_device="cuda:0",
            actual_device="cuda:0",
            expected_gpu_uuid=GPU_UUID,
            actual_gpu_uuid="GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


def test_pytorch_cuuid_object_is_normalized_without_index_only_trust():
    class CudaUuid:
        def __str__(self):
            return "aa40060e-cb9d-b04a-e70c-4c91cbb67fb0"

    assert probe._normalize_cuda_uuid(CudaUuid()) == (
        "GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0"
    )
    assert probe._normalize_cuda_uuid(GPU_UUID.upper()) == GPU_UUID
    with pytest.raises(probe.Room06GpuProbeError, match="canonical GPU UUID"):
        probe._normalize_cuda_uuid(object())


def test_absent_runtime_hooks_and_absent_events_fail_closed():
    with pytest.raises(probe.Room06GpuProbeError, match="provider is unavailable"):
        probe.normalize_runtime_hook_capture(None, selected_robot_ids=ROBOTS)
    with pytest.raises(probe.Room06GpuProbeError, match="no observer records"):
        probe.normalize_runtime_hook_capture(
            _capture([]), selected_robot_ids=ROBOTS
        )
    only_snapshot = _capture(
        [{"kind": "held_object_snapshot", "tick": 1, "held_objects": {robot: None for robot in ROBOTS}}]
    )
    with pytest.raises(probe.Room06GpuProbeError, match="no completed skill-session"):
        probe.normalize_runtime_hook_capture(only_snapshot, selected_robot_ids=ROBOTS)


def test_noncompleted_skill_event_is_not_evidence():
    event = _event()
    event["status"] = "commanded"
    capture = _capture(
        [
            {"kind": "skill_event", "event": event},
            {"kind": "held_object_snapshot", "tick": 1, "held_objects": {robot: None for robot in ROBOTS}},
        ]
    )
    with pytest.raises(probe.Room06GpuProbeError, match="only completed"):
        probe.normalize_runtime_hook_capture(capture, selected_robot_ids=ROBOTS)


def test_incomplete_held_object_snapshot_is_rejected():
    capture = _capture(
        [
            {"kind": "skill_event", "event": _event()},
            {
                "kind": "held_object_snapshot",
                "tick": 1,
                "held_objects": {ROBOTS[0]: None, ROBOTS[1]: None},
            },
        ]
    )
    with pytest.raises(probe.Room06GpuProbeError, match="exactly cover"):
        probe.normalize_runtime_hook_capture(capture, selected_robot_ids=ROBOTS)


def test_completed_event_and_full_snapshot_are_retained_without_success_inference():
    capture = _capture(
        [
            {"kind": "skill_event", "event": _event()},
            {
                "kind": "held_object_snapshot",
                "tick": 1,
                "held_objects": {robot: None for robot in ROBOTS},
            },
        ]
    )
    normalized = probe.normalize_runtime_hook_capture(
        capture, selected_robot_ids=ROBOTS
    )
    assert normalized["observations"][0]["event"]["status"] == "completed"
    assert set(normalized["observations"][1]["held_objects"]) == set(ROBOTS)
    assert "success" not in normalized


def test_output_directory_must_be_new(tmp_path: Path):
    target = tmp_path / "new-attempt"
    assert probe.reserve_output_directory(target) == target.resolve()
    with pytest.raises(probe.Room06GpuProbeError, match="already exists"):
        probe.reserve_output_directory(target)


def test_formal_gates_are_invariant_and_scene_report_is_task_free():
    request = _request()
    report = probe.build_scene_success_report(
        request=request,
        device_identity={
            "requested_device": "cuda:0",
            "actual_device": "cuda:0",
            "expected_gpu_uuid": GPU_UUID,
            "actual_gpu_uuid": GPU_UUID,
        },
        scene_readback={"finite_steps": 3, "physics_dt": 1 / 60, "simulation_time": 0.05},
    )
    assert report["status"] == "diagnostic_only"
    assert report["formal_gates"] == probe.FORMAL_GATES
    assert not any(report["formal_gates"].values())
    for forbidden in ("task_success", "runtime_evidence", "terminal_payload", "receipt"):
        assert forbidden not in report

    request["formal_gates"]["rankable"] = True
    request["input_digest"] = probe.probe_input_digest(request)
    with pytest.raises(probe.Room06GpuProbeError, match="formal gates"):
        probe._validate_common_input(request)


def test_scene_report_is_sealed_before_native_context_close(
    monkeypatch: pytest.MonkeyPatch,
):
    session = object()

    class NativeClosingContext:
        def __enter__(self):
            return session

        def __exit__(self, *_args):
            raise SystemExit(0)

    monkeypatch.setitem(
        sys.modules,
        "simulator",
        SimpleNamespace(open_simulator_session=lambda _config: NativeClosingContext()),
    )
    monkeypatch.setattr(probe, "_session_config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        probe,
        "_query_device_identity",
        lambda _request: {
            "requested_device": "cuda:0",
            "actual_device": "cuda:0",
            "expected_gpu_uuid": GPU_UUID,
            "actual_gpu_uuid": GPU_UUID,
        },
    )
    monkeypatch.setattr(
        probe,
        "_finite_scene_readback",
        lambda *_args, **_kwargs: {
            "finite_steps": 3,
            "physics_dt": 0.005,
            "simulation_time": 0.015,
        },
    )
    written = []
    with pytest.raises(SystemExit):
        probe.run_scene_diagnostic(
            {"request": _request(), "scene_path": Path("/frozen/room_06.usda")},
            report_writer=written.append,
        )
    assert len(written) == 1
    assert written[0]["status"] == "diagnostic_only"
    assert written[0]["formal_gates"] == probe.FORMAL_GATES


def test_current_cross_floor_preflight_block_is_preserved_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    request = _request(probe.FULL_MODE)
    path, digest = _write_request(tmp_path, request)
    launched = False

    def block(_request):
        raise probe.Room06GpuProbeError(
            "fresh room06 preflight blocked: ground topology has no real cross-floor edge"
        )

    def would_launch(_prepared, **_kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("simulation must not start")

    monkeypatch.setattr(probe, "prepare_full_task", block)
    monkeypatch.setattr(probe, "run_full_task", would_launch)
    output = tmp_path / "attempt"
    assert probe.execute(
        input_path=path,
        input_sha256=digest,
        output_dir=output,
        scene_load_diagnostic_only=False,
    ) == 2
    assert launched is False
    diagnostic = json.loads((output / "diagnostic.json").read_text(encoding="utf-8"))
    assert diagnostic["phase"] == "pre_simulation"
    assert diagnostic["reason"].endswith("ground topology has no real cross-floor edge")
    assert diagnostic["formal_gates"] == probe.FORMAL_GATES


def test_full_preparation_orders_fresh_preflight_before_package_verification(monkeypatch):
    request = _request(probe.FULL_MODE)
    request["runtime_hook_ref"] = request["runtime_source_ref"] = {"factory": "test"}
    monkeypatch.setattr(probe, "_verify_hook_ref", lambda ref: ref)
    monkeypatch.setattr(probe, "_verify_scene_ref", lambda _value: Path("/frozen/room_06.usda"))
    package_checked = False

    def blocked(_value):
        raise probe.Room06GpuProbeError(
            "fresh room06 preflight blocked: ground topology has no real cross-floor edge"
        )

    def package(_value):
        nonlocal package_checked
        package_checked = True
        raise AssertionError("package verification must not follow a blocked fresh preflight")

    monkeypatch.setattr(probe, "_fresh_preflight", blocked)
    monkeypatch.setattr(probe, "_verify_package_ref", package)
    with pytest.raises(probe.Room06GpuProbeError, match="no real cross-floor edge"):
        probe.prepare_full_task(request)
    assert package_checked is False


def test_semantic_artifact_digest_binds_parent_project_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, refs = _preflight_refs(tmp_path)
    project_path = artifact / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["save_id"] = "changed-after-freeze"
    project_path.write_text(json.dumps(project), encoding="utf-8")
    monkeypatch.setattr(
        probe,
        "audit_room06_runtime_preflight",
        lambda **_kwargs: pytest.fail("audit must not run after digest drift"),
    )
    with pytest.raises(probe.Room06GpuProbeError, match="semantic_root digest mismatch"):
        probe._fresh_preflight(refs)


@pytest.mark.parametrize(
    ("current_revision", "message"),
    (
        ("/absolute/revision", "must be relative"),
        ("../escape", "without '\\.\\.'"),
        ("missing-revision", "not a regular revision directory"),
    ),
)
def test_project_current_revision_is_strictly_confined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_revision: str,
    message: str,
):
    artifact, refs = _preflight_refs(tmp_path)
    (artifact / "project.json").write_text(
        json.dumps({"current_revision": current_revision}), encoding="utf-8"
    )
    refs["semantic_root"]["sha256"] = probe.directory_digest(artifact)
    monkeypatch.setattr(
        probe,
        "audit_room06_runtime_preflight",
        lambda **_kwargs: pytest.fail("audit must not run for unsafe current_revision"),
    )
    with pytest.raises(probe.Room06GpuProbeError, match=message):
        probe._fresh_preflight(refs)


def test_project_current_revision_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, refs = _preflight_refs(tmp_path)
    current = artifact / "current"
    stored = tmp_path / "outside-revision"
    current.rename(stored)
    current.symlink_to(stored, target_is_directory=True)
    monkeypatch.setattr(
        probe,
        "audit_room06_runtime_preflight",
        lambda **_kwargs: pytest.fail("audit must not follow current_revision symlink"),
    )
    with pytest.raises(probe.Room06GpuProbeError, match="contains a symlink"):
        probe._fresh_preflight(refs)


def test_semantic_artifact_root_symlink_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, refs = _preflight_refs(tmp_path)
    linked_artifact = tmp_path / "semantic-map-link"
    linked_artifact.symlink_to(artifact, target_is_directory=True)
    refs["semantic_root"]["path"] = str(linked_artifact)
    monkeypatch.setattr(
        probe,
        "audit_room06_runtime_preflight",
        lambda **_kwargs: pytest.fail("audit must not follow semantic root symlink"),
    )
    with pytest.raises(probe.Room06GpuProbeError, match="path is a symlink"):
        probe._fresh_preflight(refs)


def test_fresh_preflight_receives_project_selected_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    artifact, refs = _preflight_refs(tmp_path)
    observed = {}

    def audit(**kwargs):
        observed.update(kwargs)
        return {
            "preflight_passed": True,
            "formal_gates": copy.deepcopy(probe.FORMAL_GATES),
        }

    monkeypatch.setattr(probe, "audit_room06_runtime_preflight", audit)
    paths, report = probe._fresh_preflight(refs)
    assert paths["semantic_root"] == artifact.resolve()
    assert paths["semantic_revision"] == (artifact / "current").resolve()
    assert observed["semantic_root"] == (artifact / "current").resolve()
    assert report["preflight_passed"] is True
