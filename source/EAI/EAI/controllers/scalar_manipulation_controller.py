"""Run legacy scalar Pepper state machines independently in cloned rows."""
from __future__ import annotations
from .environment_view import SingleEnvironmentView

SCALAR_CONTROLLERS = frozenset({'PepperPickPlaceControllerCfg', 'PepperBoxGraspControllerCfg'})


def load_scalar_controller(cfg, robot_name, task_name, device, env):
    if type(cfg).__name__ not in SCALAR_CONTROLLERS:
        return cfg.load(robot_name, task_name, device, env)
    views, states = [], []
    for env_id in range(env.num_envs):
        # Legacy Pepper geometry is expressed relative to the clone origin.
        # Both measurements and pose writes are translated by this view.
        view = SingleEnvironmentView(env, env_id, local_coordinates=True)
        state = cfg.load(robot_name, task_name, device, view)
        view._controllers_dict[robot_name] = state
        views.append(view)
        states.append(state)
    result = states[0] if env.num_envs == 1 else dict(states[0])
    result['environment_states'], result['environment_views'] = states, views
    return result


def compute_scalar_action(cfg, env, robot_name, command, state):
    if 'environment_states' not in state:
        return cfg.compute_action_from_command(env, robot_name, command, state)
    import torch
    return torch.cat([
        cfg.compute_action_from_command(view, robot_name, command[i:i+1], row)
        for i, (view, row) in enumerate(zip(state['environment_views'], state['environment_states']))
    ], dim=0)


def apply_scalar_action(cfg, env, robot_name, action, state):
    if 'environment_states' not in state:
        return cfg.apply_action(env, robot_name, action, state)
    for i, (view, row) in enumerate(zip(state['environment_views'], state['environment_states'])):
        cfg.apply_action(view, robot_name, action[i:i+1], row)
    # Evidence is keyed by row and robot; scalar aliases would hide one clone.
    evidence = getattr(env, 'manipulation_controller_evidence', {})
    for i, view in enumerate(state['environment_views']):
        evidence[(i, robot_name)] = {
            name: getattr(view, name, None)
            for name in ('pepper_box_grasp_state', 'box_transport_state', 'g1_box_grasp_error')
        }
    env.manipulation_controller_evidence = evidence
    if env.num_envs == 1:
        for name in ('pepper_box_grasp_state', 'box_transport_state', 'g1_box_grasp_error'):
            if hasattr(state['environment_views'][0], name):
                setattr(env, name, getattr(state['environment_views'][0], name))


def reset_scalar_controller(cfg, env, robot_name, state, env_ids):
    if 'environment_states' not in state:
        return cfg.reset(env, robot_name, state, env_ids)
    ids = range(env.num_envs) if env_ids is None else env_ids
    for raw in ids:
        i = int(raw)
        cfg.reset(state['environment_views'][i], robot_name, state['environment_states'][i], [0])
