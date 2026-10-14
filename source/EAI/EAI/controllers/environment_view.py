"""Explicit one-row views for scalar manipulation state machines.

Reads retain a batch dimension. Simulator writes always select the original
row; the view cannot step or reset the shared environment. Cached controller
attributes belong to the view, rather than the shared articulation.
"""
from __future__ import annotations

import copy
import inspect
from collections.abc import Mapping


def validate_env_id(env, env_id):
    if type(env_id) is not int or not 0 <= env_id < int(env.num_envs):
        raise ValueError('env_id must select an existing environment row')
    return env_id


def environment_path(path, env_id):
    import re
    return re.sub(r'/envs/env_(?:\d+|\.\*)', f'/envs/env_{env_id}',
                  str(path).replace('{ENV_REGEX_NS}', f'/World/envs/env_{env_id}'))


class _DataView:
    def __init__(self, data, env_id, num_envs, origin=None):
        self._data, self._id, self._count, self._origin = data, env_id, num_envs, origin

    def __getattr__(self, name):
        value = getattr(self._data, name)
        if hasattr(value, 'shape') and len(value.shape) and value.shape[0] == self._count:
            value = value[self._id:self._id + 1]
            if self._origin is not None and name in {
                'root_pos_w', 'body_pos_w', 'root_pose_w', 'root_state_w',
                'default_root_state', 'body_state_w', 'body_link_pos_w', 'body_com_pos_w',
                'root_link_pos_w', 'root_com_pos_w', 'root_link_pose_w', 'root_com_pose_w',
            }:
                value = value.clone()
                value[..., :3] -= self._origin
        return value


class _PhysicsView:
    def __init__(self, view, env_id):
        self._view, self._id = view, env_id

    def __getattr__(self, name):
        value = getattr(self._view, name)
        if name in {'link_paths', 'prim_paths'}:
            return value[self._id:self._id + 1]
        if name.startswith('set_'):
            raise RuntimeError('direct PhysX writes must use the row-scoped asset API')
        if callable(value) and name.startswith('get_'):
            def get(*args, **kwargs):
                result = value(*args, **kwargs)
                return result[self._id:self._id + 1]
            return get
        return value


class AssetRowView:
    def __init__(self, asset, env_id, num_envs, origin=None):
        self._asset, self.env_id, self._count, self._origin = asset, env_id, num_envs, origin
        if hasattr(asset, 'data'):
            self.data = _DataView(asset.data, env_id, num_envs, origin)
        if hasattr(asset, 'cfg'):
            self.cfg = copy.copy(asset.cfg)
            if hasattr(self.cfg, 'prim_path'):
                self.cfg.prim_path = environment_path(self.cfg.prim_path, env_id)
        if hasattr(asset, 'root_physx_view'):
            self.root_physx_view = _PhysicsView(asset.root_physx_view, env_id)

    def __getattr__(self, name):
        value = getattr(self._asset, name)
        if name == 'num_instances':
            return 1
        if name == 'get_world_poses':
            def poses(*args, **kwargs):
                pos, quat = value(*args, **kwargs)
                pos, quat = pos[self.env_id:self.env_id + 1], quat[self.env_id:self.env_id + 1]
                if self._origin is not None:
                    pos = pos - self._origin
                return pos, quat
            return poses
        if callable(value) and (name.startswith('write_') or name.startswith('set_') or name == 'reset'):
            signature = inspect.signature(value)
            if 'env_ids' not in signature.parameters:
                raise RuntimeError(f'{name} has no row-scoped env_ids API')
            def write(*args, **kwargs):
                bound = signature.bind_partial(*args, **kwargs)
                local = bound.arguments.get('env_ids')
                if local is not None:
                    ids = local.tolist() if hasattr(local, 'tolist') else list(local)
                    if ids != [0]:
                        raise ValueError('row view accepts only its local environment index 0')
                import torch
                bound.arguments['env_ids'] = torch.tensor([self.env_id], device=self.device, dtype=torch.long)
                if self._origin is not None and name in {'write_root_pose_to_sim', 'write_root_state_to_sim'}:
                    first = next(iter(signature.parameters))
                    pose = bound.arguments[first].clone()
                    pose[..., :3] += self._origin
                    bound.arguments[first] = pose
                return value(*bound.args, **bound.kwargs)
            return write
        return value


class _SceneView:
    def __init__(self, scene, env_id, num_envs, origin):
        self._scene = scene
        for name in ('articulations', 'rigid_objects', 'extras'):
            self.__dict__[name] = {
                key: AssetRowView(asset, env_id, num_envs, origin)
                for key, asset in getattr(scene, name, {}).items()
            }
        # Isaac Lab creates extras views before clone prims exist. Their cached
        # matches can contain only env_0 even after physics cloning completes.
        # Resolve support geometry by concrete path after cloning, rather than
        # assuming the cached view contains one row for every environment.
        for key, asset in getattr(scene, 'extras', {}).items():
            paths = getattr(asset, 'prim_paths', ())
            if paths and getattr(scene, 'stage', None) is not None:
                from isaaclab.sim.views import XformPrimView
                concrete = environment_path(paths[0], env_id)
                row_asset = XformPrimView(concrete, device=asset.device,
                    validate_xform_ops=False, stage=scene.stage)
                self.extras[key] = AssetRowView(row_asset, 0, 1, origin)
        self.env_origins = scene.env_origins[env_id:env_id + 1]
        if origin is not None:
            self.env_origins = self.env_origins * 0

    def __getattr__(self, name):
        if name in {'write_data_to_sim', 'update', 'reset'}:
            raise RuntimeError('the shared scene owner controls scene lifecycle')
        return getattr(self._scene, name)


class SingleEnvironmentView:
    def __init__(self, env, env_id, *, local_coordinates=False):
        self.env_id = validate_env_id(env, env_id)
        self._env = env
        # Legacy runtime helpers unwrap ``base_env``; keep that operation scoped.
        self.base_env = self
        self.num_envs = 1
        origin = env.scene.env_origins[env_id] if local_coordinates else None
        self.scene = _SceneView(env.scene, env_id, env.num_envs, origin)
        self._controllers_dict = {}
        # Auxiliary objects can cache batched target tensors. Scalar runtimes
        # must own separate instances rather than alias those caches.
        self._auxiliary_controllers = {}

    def __getattr__(self, name):
        if name in {'step', 'reset', 'close'}:
            raise RuntimeError('only the shared environment owner may advance its lifecycle')
        if name in {'actions_dict', '_last_computed_actions'}:
            return {key: val[self.env_id:self.env_id + 1]
                    for key, val in getattr(self._env, name, {}).items()}
        return getattr(self._env, name)
