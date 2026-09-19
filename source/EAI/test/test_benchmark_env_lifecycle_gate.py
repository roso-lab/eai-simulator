"""Isolated controller hot-path checks without booting Isaac Kit."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import types

import pytest
import torch


def _environment_class(monkeypatch):
    modules = {
        "isaaclab.envs": types.ModuleType("isaaclab.envs"),
        "EAI.controllers": types.ModuleType("EAI.controllers"),
        "EAI.controllers.base": types.ModuleType("EAI.controllers.base"),
        "EAI.hmrs_env.multi_robot_direct_env_cfg": types.ModuleType(
            "EAI.hmrs_env.multi_robot_direct_env_cfg"),
    }
    modules["isaaclab.envs"].DirectMARLEnv = type("DirectMARLEnv", (), {})
    modules["EAI.controllers"].ControllerCfg = type("ControllerCfg", (), {})
    modules["EAI.controllers"].load_all_controllers = lambda *args, **kwargs: []
    modules["EAI.controllers.base"].normalize_controller_entry = (
        lambda entry: (entry, ()))
    modules["EAI.hmrs_env.multi_robot_direct_env_cfg"].MultiRobotDirectEnvCfg = (
        type("MultiRobotDirectEnvCfg", (), {}))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).resolve().parents[1] / "EAI/hmrs_env/multi_robot_direct_env.py"
    spec = importlib.util.spec_from_file_location(
        "EAI.hmrs_env.multi_robot_direct_env", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module.MultiRobotDirectEnv


class _Articulation:
    def __init__(self, name):
        self.root_physx_view = types.SimpleNamespace(
            prim_paths=[f"/World/envs/env_0/{name}"])
        self.data = types.SimpleNamespace(
            root_state_w=torch.tensor([[0., 0., .2, 1., 0., 0., 0.]]),
            joint_vel=torch.zeros((1, 2)))
        self.pose_writes = []
        self.velocity_writes = []
        self.joint_velocity_targets = []

    def write_root_pose_to_sim(self, pose, *, env_ids):
        self.pose_writes.append(pose.clone())

    def write_root_velocity_to_sim(self, velocity, *, env_ids):
        self.velocity_writes.append(velocity.clone())

    def set_joint_velocity_target(self, velocity, *, env_ids):
        self.joint_velocity_targets.append(velocity.clone())


class _Controller:
    def __init__(self):
        self.computed = []
        self.applied = []

    def compute_action_from_command(self, env, name, command, state):
        self.computed.append(name)
        return command.clone()

    def apply_action(self, env, name, action, state):
        self.applied.append(name)


def _configured_env(monkeypatch):
    cls = _environment_class(monkeypatch)
    env = cls.__new__(cls)
    controllers = {name: _Controller() for name in ("active_1", "reserve_1")}
    env.cfg = types.SimpleNamespace(possible_agents=list(controllers), action_spaces={})
    env.scene = types.SimpleNamespace(articulations={
        name: _Articulation(name) for name in controllers})
    env.num_envs = 1
    env.device = "cpu"
    env._controller_configs = controllers
    env._auxiliary_controllers = {name: () for name in controllers}
    env._controllers_dict = {name: {"name": name} for name in controllers}
    env._controllers_loaded = True
    env._benchmark_agent_active = {name: True for name in controllers}
    env._benchmark_capability_declarations = {}
    env._benchmark_capability_versions = {name: 1 for name in controllers}
    env._benchmark_park_poses = {}
    env.actions_dict = {}
    env._last_computed_actions = {}
    env._replay_precomputed_actions = None
    env._replay_precomputed_used = False
    declarations = {name: {"navigation_interface": True,
                           "attachment_types": ["z1"] if name == "active_1" else []}
                    for name in controllers}
    env.configure_benchmark_lifecycle(
        active_agent_ids={"active_1"}, capability_declarations=declarations)
    return env, controllers


def test_inactive_reserve_is_held_and_cannot_receive_controller_actions(monkeypatch):
    env, controllers = _configured_env(monkeypatch)
    commands = {name: torch.ones((1, 3)) for name in controllers}

    env._pre_physics_step(commands)
    env._apply_action()

    assert controllers["active_1"].computed == ["active_1"]
    assert controllers["active_1"].applied == ["active_1"]
    assert controllers["reserve_1"].computed == []
    assert controllers["reserve_1"].applied == []
    assert env.scene.articulations["reserve_1"].pose_writes
    assert env.read_benchmark_agent_state("reserve_1")["active"] is False

    env.set_benchmark_agent_active("reserve_1", True)
    env._pre_physics_step(commands)
    env._apply_action()
    assert controllers["reserve_1"].applied == ["reserve_1"]


def test_navigation_loss_blocks_replay_and_rejects_invented_attachment(monkeypatch):
    env, controllers = _configured_env(monkeypatch)
    old = env.read_benchmark_controller_state("active_1")["capability_declaration"]
    invented = deepcopy(old)
    invented["attachment_types"] = ["z1", "ur5"]
    with pytest.raises(ValueError, match="physical attachments"):
        env.reconfigure_benchmark_controller("active_1", invented, 2)
    assert env.read_benchmark_controller_state("active_1")["capability_version"] == 1

    disabled = deepcopy(old)
    disabled["navigation_interface"] = False
    env.reconfigure_benchmark_controller("active_1", disabled, 2)
    env._replay_precomputed_actions = {
        name: torch.ones((1, 3)) for name in controllers}
    env._pre_physics_step(env._replay_precomputed_actions)
    env._apply_action()
    assert env.actions_dict == {}
    assert controllers["active_1"].applied == []
    assert env.read_benchmark_controller_state("active_1")["capability_version"] == 2
    assert env.scene.articulations["active_1"].pose_writes
