"""Pure preflight and lifecycle tests never launch Isaac."""

from copy import deepcopy
import json
import signal
import fcntl

import pytest

from demo.benchmark import goal_physical_probe as probe
from demo.benchmark.goal_driven_tasks import build_goal_task_spec
from demo.benchmark.provenance import canonical_sha256
from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict


def test_indoor_selection_preserves_distinct_members_and_candidate_poses():
    public = build_goal_task_spec("warehouse_nvd")
    rows = [{"type": robot["type"], "runtime_id": str(i), "spawn_binding": {"deployment_directive": {
        "position_m": [float(i),0.,1.], "rotation_wxyz": [1.,0.,0.,0.]}}}
        for i, robot in enumerate(public["candidate_robot_pool"])]
    declaration = {"scene_key": "warehouse_nvd", "robot_instances": rows}
    original = deepcopy(declaration)
    selection = probe.build_physical_selection(declaration)
    assert len(interactive_selection_from_dict(selection).robots) == 4
    assert [r["type"] for r in selection["robots"]] == ["iris", "m20", "carter", "m20"]
    assert [r["spawn_pose"]["position"][0] for r in selection["robots"]] == [0.,1.,2.,3.]
    assert original == declaration


@pytest.mark.parametrize("steps", [0,-1,True,3001])
def test_invalid_step_budget_rejected_before_scene_read(tmp_path, steps):
    with pytest.raises(ValueError, match="steps"):
        probe.prepare_probe(tmp_path / "unused", scene="warehouse_nvd", steps=steps)
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("scene,steps", [("warehouse_nvd", 1200), ("modern_house_nvd", 60)])
def test_withdrawal_mode_is_house_only_and_includes_motion(tmp_path, scene, steps):
    with pytest.raises(ValueError, match="house withdrawal"):
        probe.prepare_probe(tmp_path / "unused", scene=scene, steps=steps, house_withdrawal=True)
    assert not (tmp_path / "unused").exists()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    deployment = {"sha256": "test-deployment", "scene_key": "warehouse_nvd"}
    selection = {"task_name": "fixture"}
    monkeypatch.setattr(probe, "_probe_sources", lambda dep: {"expected-source": "hash"})
    monkeypatch.setattr(probe, "file_sha256", lambda path: "hash")
    monkeypatch.setattr(probe, "build_physical_selection", lambda dep: selection)
    monkeypatch.setattr(probe, "validate_goal_deployment", lambda dep: None)
    request = {"schema": "eai.benchmark.goal_physical_request", "version": 1, "run_id": "a"*32,
        "scene_key": "warehouse_nvd", "deployment_sha256": deployment["sha256"],
        "selection_sha256": canonical_sha256(selection), "source_files": {"expected-source": "hash"},
        "steps": 2, "gui": False, "requested_device": "cuda:0", "effective_device": "cuda:0", "device_policy": "requested_device",
        "mode": "native_configuration_and_zero_command_settle", "scoreable": False}
    for name,value in (("request.json",request),("deployment.json",deployment),("selection.json",selection)):
        probe.write_json(tmp_path/name,value)
    return tmp_path,request


@pytest.mark.parametrize("field,value", [("steps",0),("steps",True),("scene_key","city"),
    ("source_files",{}),("gui","yes"),("mode","business"),("scoreable",True),("requested_device","other")])
def test_corrupted_request_cannot_bypass_frozen_launch_contract(prepared, field, value):
    root,request = prepared
    request[field] = value
    probe.write_json(root/"request.json",request)
    with pytest.raises(ValueError):
        probe.load_probe(root)


def test_parent_interrupt_terminates_worker_before_releasing_lock(prepared, monkeypatch):
    root,_ = prepared
    monkeypatch.setattr(probe,"ROOT",root)
    killed = []
    class Process:
        pid = 567
        calls = 0
        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt()
            return -signal.SIGTERM
        def poll(self):
            return None
    def spawn(*args, **kwargs):
        assert kwargs["pass_fds"]
        assert kwargs["env"]["EAI_GOAL_PHYSICAL_WORKER_LOCK_FD"] == str(kwargs["pass_fds"][0])
        return Process()
    monkeypatch.setattr(probe.subprocess,"Popen",spawn)
    monkeypatch.setattr(probe.os,"killpg",lambda pid,sig: killed.append((pid,sig)))
    with pytest.raises(KeyboardInterrupt):
        probe.execute_probe(root)
    assert killed == [(567,signal.SIGTERM)]
    assert json.loads((root/"process_result.json").read_text())["returncode"] == -signal.SIGTERM


def test_worker_rejects_unrelated_descriptor_and_contested_canonical_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(probe,"ROOT",tmp_path)
    (tmp_path/"tmp").mkdir()
    lock_path = tmp_path/"tmp/goal_physical_probe.lock"
    lock_path.touch()
    with (tmp_path/"unrelated.txt").open("w") as other:
        monkeypatch.setenv("EAI_GOAL_PHYSICAL_WORKER_LOCK_FD",str(other.fileno()))
        with pytest.raises(RuntimeError,match="canonical"):
            probe.run_worker(tmp_path)
    with lock_path.open("a") as owner, lock_path.open("a") as contender:
        fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monkeypatch.setenv("EAI_GOAL_PHYSICAL_WORKER_LOCK_FD",str(contender.fileno()))
        with pytest.raises(BlockingIOError):
            probe.run_worker(tmp_path)
