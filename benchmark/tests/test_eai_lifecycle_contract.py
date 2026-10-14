"""Execute real EAI lifecycle methods without importing the Isaac app runtime.

These CPU checks validate admission/world-write contracts only, never braking.
"""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any
import pytest
import torch

PATH = Path(__file__).resolve().parents[2] / 'source/EAI/EAI/hmrs_env/multi_robot_direct_env.py'
METHODS = {'set_benchmark_agent_active', 'reconfigure_benchmark_controller',
           '_benchmark_control_enabled', 'cancel_benchmark_commands',
           '_hold_benchmark_agent', 'request_benchmark_stop', 'set_command', 'get_command'}


def environment():
    tree = ast.parse(PATH.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MultiRobotDirectEnv')
    selected = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    module = ast.Module(body=[ast.ClassDef(name='Env', bases=[], keywords=[], body=selected, decorator_list=[])], type_ignores=[])
    ns = dict(torch=torch, Dict=Dict, Any=Any, deepcopy=deepcopy)
    exec(compile(ast.fix_missing_locations(module), str(PATH), 'exec'), ns)
    env = ns['Env']()
    pose = torch.tensor([[4., 5., 6., 1., 0., 0., 0., .8, .1, .2]])
    class Robot:
        data = SimpleNamespace(root_state_w=pose)
        def write_root_pose_to_sim(self, *args):
            pytest.fail('membership/loss must not write a root pose')
        def write_root_velocity_to_sim(self, *args):
            pytest.fail('membership/loss must not pin world velocity')
    env.scene = SimpleNamespace(articulations={'r': Robot()})
    env._controller_configs = {'r': object()}
    env._benchmark_agent_active = {'r': True}
    env._benchmark_park_poses = {}
    env._benchmark_capability_declarations = {'r': {'navigation_interface': True, 'payload': 'unchanged'}}
    env._benchmark_capability_versions = {'r': 1}
    env.actions_dict = {'r': torch.ones(1)}
    env._last_computed_actions = {'r': torch.ones(1)}
    env._goal_position = torch.tensor([999.])
    return env, pose


def test_exit_and_join_preserve_current_world_and_block_stale_commands():
    env, pose = environment()
    before = pose.clone()
    env.set_command('r', 'goal_position', torch.tensor([1., 2., 3.]))
    env.set_benchmark_agent_active('r', False)
    assert env.get_command('r', 'goal_position') is None
    with pytest.raises(RuntimeError, match='disabled'):
        env.set_command('r', 'goal_position', torch.zeros(3))
    env.set_benchmark_agent_active('r', True)
    assert env.get_command('r', 'goal_position') is None  # global fallback cannot resurrect
    env.set_command('r', 'goal_position', torch.tensor([7., 8., 9.]))
    assert torch.equal(env.get_command('r', 'goal_position'), torch.tensor([7., 8., 9.]))
    assert torch.equal(pose, before)


def test_loss_and_restore_clear_actions_keep_payload_and_request_registered_stop():
    env, pose = environment()
    calls = []
    env._benchmark_stop_adapters = {'r': lambda environment, robot: calls.append(robot)}
    before = pose.clone()
    env.set_command('r', 'goal_position', torch.ones(3))
    env.reconfigure_benchmark_controller('r', {'navigation_interface': False, 'payload': 'unchanged'}, 2)
    assert calls == ['r'] and 'r' not in env.actions_dict
    env.reconfigure_benchmark_controller('r', {'navigation_interface': True, 'payload': 'unchanged'}, 3)
    assert env.get_command('r', 'goal_position') is None
    assert torch.equal(pose, before)
    with pytest.raises(ValueError, match='scene adapter'):
        env.reconfigure_benchmark_controller('r', {'navigation_interface': False, 'payload': 'changed'}, 4)
    env.request_benchmark_stop('r')
    assert calls == ['r', 'r']
    assert torch.equal(pose, before)
