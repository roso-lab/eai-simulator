"""CPU-only preparation, tensor mapping and process lifecycle regression tests."""

from copy import deepcopy
from contextlib import contextmanager
import fcntl
import json
import signal
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from demo.benchmark import goal_attachment_probe as probe
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.provenance import canonical_sha256


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    public = build_goal_task_spec("warehouse_nvd")
    counters, robots, objects = {}, [], []
    for robot in public["candidate_robot_pool"]:
        kind = robot["type"]
        counters[kind] = counters.get(kind, 0) + 1
        runtime = f"{kind}_{counters[kind]}"
        robots.append({"public_id": robot["id"], "runtime_id": runtime, "type": kind,
            "public_declaration": robot, "prim_path": f"/World/{runtime}",
            "spawn_binding": {"deployment_directive": {"position_m": [-8., -11.75, .52],
                                                      "rotation_wxyz": [1., 0., 0., 0.]}}})
    for i, obj in enumerate(public["public_world"]["objects"], 1):
        objects.append({"public_id": obj["id"], "runtime_id": f"goal_object_{i}",
            "prim_path": f"/World/goal_object_{i}", "physical_properties": obj["physical_properties"],
            "scene_binding": {"deployment_directive": {"position_m": [-7., -10.05, .15]}}})
    deployment = {"scene_key": "warehouse_nvd", "public_task": public,
                  "robot_instances": robots, "object_instances": objects}
    deployment["sha256"] = canonical_sha256(deployment)
    source = tmp_path / "fixture_source.py"
    source.write_text("# fixture\n")
    monkeypatch.setattr(probe, "_sources", lambda dep: {str(source): probe.file_sha256(source)})
    monkeypatch.setattr(probe.physical, "build_scene_deployment", lambda scene: deepcopy(deployment))
    monkeypatch.setattr(probe.physical, "validate_goal_deployment", lambda dep: None)
    root = tmp_path / "run"
    request = probe.prepare_probe(root, gui=True)
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    return root, request, deployment


def test_only_experimental_m20_spawn_changes_and_full_public_objects_preserved(prepared):
    root, request, deployment = prepared
    _, loaded, selection = probe.load_probe(root)
    assert loaded == deployment
    baseline = probe.physical.build_physical_selection(deployment)
    assert request["spawn_adjustment"]["formal_task_spawn"] is False
    assert request["member_id"] == "warehouse_ground_3"
    changed = [i for i, (before, after) in enumerate(zip(baseline["robots"], selection["robots"])) if before != after]
    assert changed == [3]
    assert selection["robots"][3]["spawn_pose"]["position"] == [-7.50, -10.05, .53]
    assert request["phases"] == probe.PHASES
    assert not request["scoreable"]
    with pytest.raises(FileExistsError):
        probe.prepare_probe(root)


@pytest.mark.parametrize("field,value", [("version", 1), ("formal_binding", {}),
    ("phases", []), ("source_files", {}), ("scoreable", True),
    ("member_id", "warehouse_ground_2"), ("gui", "yes"), ("device", "bad"), ("run_id", "bad")])
def test_request_corruption_rejected(prepared, field, value):
    root, request, _ = prepared
    request[field] = value
    probe.physical.write_json(root / "request.json", request)
    with pytest.raises(ValueError):
        probe.load_probe(root)


def test_selection_source_and_deployment_corruption_rejected(prepared):
    root, _, _ = prepared
    selection = json.loads((root / "selection.json").read_text())
    selection["robots"][0]["spawn_pose"]["position"][0] = 9
    probe.physical.write_json(root / "selection.json", selection)
    with pytest.raises(ValueError, match="selection"):
        probe.load_probe(root)
    dep = json.loads((root / "deployment.json").read_text())
    dep["scene_key"] = "city"
    probe.physical.write_json(root / "deployment.json", dep)
    with pytest.raises(ValueError, match="hash"):
        probe.load_probe(root, rebuild=False)


def test_changed_source_rejected(prepared):
    root, _, _ = prepared
    (root.parent / "fixture_source.py").write_text("# changed\n")
    with pytest.raises(ValueError, match="source"):
        probe.load_probe(root)


class Tensor:
    def __init__(self, value):
        self.value = value
    def __getitem__(self, key):
        value = self.value
        for i in key if isinstance(key, tuple) else (key,):
            value = value[i]
        return Tensor(value)
    def detach(self):
        return self
    def cpu(self):
        return self
    def tolist(self):
        return deepcopy(self.value)


def test_native_body_mapping_uses_physx_link_paths_xyzw_and_fresh_reads(prepared):
    _, _, dep = prepared
    transform = [1., 2., 3., 0., 0., 1., 0.]
    class View:
        def get_link_transforms(self):
            return Tensor([[transform]])
        def get_root_transforms(self):
            return Tensor([transform])
        def get_transforms(self):
            return Tensor([transform])
        def get_masses(self):
            return Tensor([[3.]])
    class Scene(dict):
        articulations = {}
    scene = Scene()
    for row in dep["robot_instances"]:
        view = View()
        view.link_paths = [[row["prim_path"] + "/real_root"]]
        scene.articulations[row["runtime_id"]] = SimpleNamespace(root_physx_view=view)
    for row in dep["object_instances"]:
        view = View()
        view.prim_paths = [row["prim_path"]]
        scene[row["runtime_id"]] = SimpleNamespace(root_physx_view=view)
    live = probe.LiveBodies(scene, dep)
    path = live.bindings["members"][probe.MEMBER]["body_path"]
    assert path.endswith("m20_2/real_root")
    assert live(path)["rotation_wxyz"] == [0., 0., 0., 1.]
    transform[0] = 8.
    live.step = 123
    assert live(path)["position_m"][0] == 8.
    assert live(path)["physics_step"] == 123
    assert live.snapshot()["robot_roots"][probe.MEMBER]["root_transform_xyzw"] == transform
    scene[dep["object_instances"][0]["runtime_id"]].root_physx_view.prim_paths = ["/Other"]
    with pytest.raises(ValueError, match="path mismatch"):
        probe.LiveBodies(scene, dep)


def test_anchor_quality_reports_solver_drift_without_claiming_pass():
    receipt = {"joint_readback": [{"prim_path": "/Joint", "body0": ["/Robot"], "body1": ["/Box"],
        "local_pos0": [1., 0., 0.], "local_pos1": [0., 0., 0.],
        "local_rot0_wxyz": [1., 0., 0., 0.], "local_rot1_wxyz": [1., 0., 0., 0.]}]}
    poses = {"/Robot": {"position_m": [0., 0., 0.], "rotation_wxyz": [1., 0., 0., 0.]},
             "/Box": {"position_m": [1.2, 0., 0.], "rotation_wxyz": [0., 0., 0., 1.]}}
    row = probe.anchor_quality(receipt, poses)[0]
    assert row["anchor_error_m"] == pytest.approx(.2)
    assert row["orientation_error_rad"] == pytest.approx(3.141592653589793)
    assert "passed" not in row


@pytest.mark.parametrize("status", ["unknown", "mismatch", "blocked"])
def test_native_configuration_must_be_measured_before_attachment(status):
    with pytest.raises(ValueError, match="unresolved"):
        probe.require_native_objects({"objects": [{"public_id": probe.OBJECT, "status": status}]})


@pytest.mark.parametrize("failure", ["interrupt", "sigterm", "timeout"])
def test_parent_reaps_worker_with_lock_held_and_escalates_timeout(prepared, monkeypatch, failure):
    root, _, _ = prepared
    kills = []
    class Process:
        pid = 654
        calls = 0
        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                if failure == "sigterm":
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                if failure == "interrupt":
                    raise KeyboardInterrupt()
                raise subprocess.TimeoutExpired("fixture", timeout)
            if failure == "timeout" and self.calls == 2:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return -signal.SIGKILL if failure == "timeout" else -signal.SIGTERM
        def poll(self):
            return None
    def spawn(command, **kwargs):
        assert "demo.benchmark.goal_attachment_probe" in command
        assert kwargs["start_new_session"]
        fd = kwargs["pass_fds"][0]
        assert kwargs["env"]["EAI_GOAL_PHYSICAL_WORKER_LOCK_FD"] == str(fd)
        return Process()
    def kill(pid, sig):
        with (root.parent / "tmp/goal_physical_probe.lock").open("a") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        kills.append(sig)
    monkeypatch.setattr(probe.subprocess, "Popen", spawn)
    monkeypatch.setattr(probe.os, "killpg", kill)
    if failure == "timeout":
        result = probe.execute_probe(root, timeout_s=1.)
        assert result["timed_out"] and not result["diagnostic_completed"]
        assert kills == [signal.SIGTERM, signal.SIGKILL]
    else:
        with pytest.raises(KeyboardInterrupt):
            probe.execute_probe(root)
        assert kills == [signal.SIGTERM]
    assert (root / "process_result.json").exists()
    with pytest.raises(ValueError, match="already launched"):
        probe.execute_probe(root)


def test_worker_requires_canonical_uncontested_lock_before_loading_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "ROOT", tmp_path)
    (tmp_path / "tmp").mkdir()
    path = tmp_path / "tmp/goal_physical_probe.lock"
    path.touch()
    with (tmp_path / "other").open("w") as other:
        monkeypatch.setenv("EAI_GOAL_PHYSICAL_WORKER_LOCK_FD", str(other.fileno()))
        with pytest.raises(RuntimeError, match="canonical"):
            probe.run_worker(tmp_path)
    with path.open("a") as owner, path.open("a") as contender:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        monkeypatch.setenv("EAI_GOAL_PHYSICAL_WORKER_LOCK_FD", str(contender.fileno()))
        with pytest.raises(BlockingIOError):
            probe.run_worker(tmp_path)


def test_fresh_worker_import_does_not_load_openusd_or_isaac():
    result = subprocess.run([sys.executable, "-c", "import sys; import demo.benchmark.goal_attachment_probe; "
        "assert not any(n == 'pxr' or n.startswith(('pxr.', 'isaaclab.', 'omni.')) for n in sys.modules)"],
        cwd=probe.ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", [None, ValueError, KeyboardInterrupt])
def test_worker_phase_order_capture_and_native_step_clock_with_injected_session(prepared, monkeypatch, failure):
    """Exercise all 440 dispatches without starting an application or a solver."""
    import torch
    import simulator
    from demo.benchmark import goal_native_attachment as attachment
    from demo.benchmark import goal_native_objects as objects
    root, request, _ = prepared
    probe.physical.write_json(root / "launch.json", {"command": probe._command(root),
        "run_id": request["run_id"], "request_sha256": canonical_sha256(request)})
    monkeypatch.setattr(probe, "_worker_lock", lambda: None)
    # A fresh worker has no pxr; pytest may already have collected USD tests.
    for name in list(sys.modules):
        if name == "pxr" or name.startswith("pxr."):
            monkeypatch.delitem(sys.modules, name)
    omni = ModuleType("omni")
    omni.usd = ModuleType("omni.usd")
    omni.usd.get_context = lambda: SimpleNamespace(get_stage=lambda: object())
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.usd", omni.usd)
    calls, frames = [], []
    sim = SimpleNamespace(current_time_step_index=12, current_time=.12, get_physics_dt=lambda: .01)
    def step(actions):
        calls.append(actions["m20_2"][0].tolist())
        sim.current_time_step_index += 2
        sim.current_time += .02
    session = SimpleNamespace(device="cpu", possible_agents=["m20_2", "iris_1"],
        simulation_app=SimpleNamespace(is_running=lambda: True), env=SimpleNamespace(step=step),
        env_cfg=SimpleNamespace(decimation=2), base_env=SimpleNamespace(sim=sim, step_dt=.02,
            set_command=lambda *args: None,
            scene=SimpleNamespace(articulations={"iris_1": SimpleNamespace(
                data=SimpleNamespace(root_pos_w=torch.zeros((1, 3))))})))
    @contextmanager
    def open_session(config):
        assert config.headless is False and config.num_envs == 1
        try:
            yield session
        finally:
            if failure is not None:
                # Emulate Kit shutdown ending the worker. The original failure
                # must already be on disk before session __exit__ starts.
                archived = json.loads((root / "attachment_report.json").read_text())
                assert archived["status"] == "failed"
                assert archived["error"] == repr(failure("injected positive prepare failure"))
                assert "injected positive prepare failure" in archived["traceback"]
                assert archived["native_step_count"] == 240
                assert len(archived["negative_checks"]) == 3
                raise SystemExit(0)
    monkeypatch.setattr(simulator, "open_simulator_session", open_session)
    class Live:
        bindings = {"members": {probe.MEMBER: {"body_path": "/Robot"},
            "warehouse_ground_1": {"body_path": "/Far"}, "warehouse_aerial_1": {"body_path": "/Iris"}},
            "objects": {probe.OBJECT: {"body_path": "/Box"}}}
        step = 0
        def snapshot(self):
            return {"bodies": {path: {"position_m": [0., 0., 0.], "rotation_wxyz": [1., 0., 0., 0.],
                "physics_step": self.step} for path in ("/Robot", "/Box", "/Iris")}, "robot_roots": {}}
    monkeypatch.setattr(probe, "LiveBodies", lambda *args: Live())
    monkeypatch.setattr(probe.physical, "runtime_sources", lambda stage: {})
    monkeypatch.setattr(objects, "read_goal_objects", lambda *args: {"objects": [
        {"public_id": name, "status": "measured"} for name in (probe.OBJECT, "order_crate_01")]})
    monkeypatch.setattr(probe, "negative_checks", lambda *args: [
        {"expected_rejection": True, "zero_stage_effect": True} for _ in range(3)])
    def prepare(*args, **kwargs):
        assert len(calls) == 240
        if failure is not None:
            raise failure("injected positive prepare failure")
        return {}
    monkeypatch.setattr(attachment, "prepare_attachment", prepare)
    receipt = {"joint_readback": [{"prim_path": "/Joint", "body0": ["/Robot"], "body1": ["/Box"],
        "local_pos0": [0., 0., 0.], "local_pos1": [0., 0., 0.],
        "local_rot0_wxyz": [1., 0., 0., 0.], "local_rot1_wxyz": [1., 0., 0., 0.]}]}
    def commit(*args, **kwargs):
        assert len(calls) == 240
        return receipt
    def detach(*args):
        assert len(calls) == 340
        return {"already_detached": len(frames) > 1}
    monkeypatch.setattr(attachment, "commit_attachment", commit)
    monkeypatch.setattr(attachment, "detach_attachment", detach)
    omni.usd.get_context = lambda: SimpleNamespace(get_stage=lambda: SimpleNamespace(GetPrimAtPath=lambda path: True))
    def capture(*args, **kwargs):
        assert len(calls) == 340
        frames.append(args[3])
        return {"path": "mock.png"}
    monkeypatch.setattr(probe.physical, "_camera_capture", capture)
    if failure is not None:
        with pytest.raises(SystemExit) as exc:
            probe.run_worker(root)
        assert exc.value.code == 0
        archived = json.loads((root / "attachment_report.json").read_text())
        assert archived["status"] == "failed"
        assert archived["error"] == repr(failure("injected positive prepare failure"))
        assert "attachment_receipt" not in archived
        assert len(calls) == 240
        return
    report = probe.run_worker(root)
    assert report["status"] == "diagnostic_completed"
    assert report["native_step_count"] == 440
    assert all(cmd == [0., 0., 0.] for cmd in calls[:240])
    assert all(cmd == pytest.approx([-.1, 0., 0.]) for cmd in calls[240:])
    assert [sum(s["phase"] == name for s in report["samples"]) for name, _, _ in probe.PHASES] == [240, 100, 100]
    assert report["samples"][-1]["physics_step"] == 892
    assert len(frames) == 1 and len(report["captures"][0]["body_paths"]) == 3
    assert report["solver_response_validated"] is False and report["scoreable"] is False
