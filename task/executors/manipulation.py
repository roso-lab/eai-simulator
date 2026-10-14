"""Shared-tick manipulation bindings for arms, G1 and Pepper.

Task robot IDs include clone identity (``env_1/pepper_1``). The step bridge
assembles those one-row actions into the physical robot's action tensor and
calls the caller-owned environment exactly once. No backend steps physics.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
import math
from typing import Any, Mapping

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.pick_place import PickPlaceProvider, SKILL_ID
from algorithm.common_coordination.contract import FailureCode
from task.contracts import DependencyOutcome
from task.resources import ResourceManager
from .cooperative import CooperativeBinding
from .skills import ExecutionReceipt


def scoped_robot_id(env_id, robot_name):
    if type(env_id) is not int or env_id < 0 or not isinstance(robot_name, str) or not robot_name.strip() or '/' in robot_name:
        raise ValueError('invalid environment or robot identity')
    return f'env_{env_id}/{robot_name}'


@dataclass(frozen=True)
class ManipulationRobotBinding:
    backend: str
    robot_name: str
    env_id: int = 0
    objects: Mapping[str, Any] = field(default_factory=dict)
    robot_path: str | None = None
    config: Any = None
    obstacles: tuple = ()
    grasp_offset_world: tuple[float, float, float] = (0., 0., 0.)
    recovery_height_m: float = .10

    def __post_init__(self):
        if self.backend not in {'arm', 'g1', 'pepper'}:
            raise ValueError('backend must be arm, g1 or pepper')
        scoped_robot_id(self.env_id, self.robot_name)
        if not self.objects:
            raise ValueError('register the permitted objects explicitly')
        if self.backend == 'g1' and not self.robot_path:
            raise ValueError('G1 requires its calibrated robot USD path')
        if len(self.grasp_offset_world) != 3 or any(not math.isfinite(v) for v in self.grasp_offset_world):
            raise ValueError('grasp_offset_world must contain finite XYZ')
        if not math.isfinite(self.recovery_height_m) or self.recovery_height_m <= 0:
            raise ValueError('recovery_height_m must be positive')

    @property
    def task_robot_id(self):
        return scoped_robot_id(self.env_id, self.robot_name)


class _LeaseHandle:
    def __init__(self, runtime, request, lease, keys, handle):
        self.runtime, self.request, self.lease, self.keys, self.handle = runtime, request, lease, keys, handle
        self.finished = False
        self.env_id = runtime.bindings[request.robot_id].env_id
        self.generation = getattr(runtime.env, '_manipulation_reset_generation', [0] * runtime.env.num_envs)[self.env_id]

    def _validate(self):
        current = getattr(self.runtime.env, '_manipulation_reset_generation', [0] * self.runtime.env.num_envs)[self.env_id]
        if current != self.generation:
            raise RuntimeError('environment reset invalidated the manipulation task; reconcile leases')
        self.runtime.resources.validate_lease(self.lease, required_resources=self.keys)
        if self.lease.owner != self.request.request_id:
            raise ValueError('lease owner differs from the admitted request')

    def command(self):
        self._validate()
        return self.handle.command()

    def cancel(self, reason):
        return self.handle.cancel(reason)

    def after_step(self):
        self._validate()
        receipt = self.handle.after_step()
        if receipt is None:
            return None
        self.finished = True
        return replace(receipt, request_id=self.request.request_id, skill_id=self.request.skill_id,
                       robot_id=self.request.robot_id)


class _ArmHandle:
    def __init__(self, request, runtime, binding, view, object_path):
        from EAI.controllers.idealized_manipulation import CylindricalPose
        self.request, self.runtime, self.binding, self.view = request, runtime, binding, view
        self.cancel_reason, self.receipt, self.stable = None, None, 0
        self.phase = 'pick'
        if type(view.cfg.action_spaces[binding.robot_name]) is not int:
            raise ValueError('arm requires a flat numeric action space')
        self.arm = view.scene.articulations[binding.robot_name + '_arm']
        self.object = view.scene.rigid_objects[request.parameters['object_ref']]
        # The per-row runtime binding carries model-specific plane offsets.
        row = runtime._runtime_for_env(binding.env_id)
        arm_binding = row._binding(binding.robot_name)
        if arm_binding is None:
            raise ValueError('arm model has no idealized manipulation binding')
        from EAI.controllers.idealized_manipulation import _quat_rotate, _quat_conjugate, local_position_to_cylindrical
        base_pos, base_quat = row._base_pose(arm_binding)
        def target(world):
            point = tuple(world[i] + binding.grasp_offset_world[i] - base_pos[i] for i in range(3))
            local = _quat_rotate(_quat_conjugate(base_quat), point)
            return local_position_to_cylindrical(local, arm_binding.planar_spec.plane_offset_m)
        pick = target(request.parameters['pick_position'])
        self.place = target(request.parameters['place_position'])
        self.recovery = CylindricalPose(min(pick.x_m, self.place.x_m, .20),
                                        max(pick.z_m, self.place.z_m)+binding.recovery_height_m,
                                        pick.yaw_rad)
        if not runtime.start_pick(binding.robot_name, object_path, pick,
                                  recovery_pose=self.recovery, env_id=binding.env_id):
            raise ValueError(runtime.last_error(binding.robot_name, env_id=binding.env_id))

    def command(self):
        import torch
        size = self.view.cfg.action_spaces[self.binding.robot_name]
        if type(size) is not int:
            raise ValueError('arm requires a flat numeric action space')
        return torch.zeros((1, size), device=self.view.device)

    def cancel(self, reason):
        self.cancel_reason = reason

    def after_step(self):
        from EAI.controllers.idealized_manipulation import ManipulationPhase
        if self.receipt is not None:
            return self.receipt
        name, env_id = self.binding.robot_name, self.binding.env_id
        phase = self.runtime.state(name, env_id=env_id)
        if phase is ManipulationPhase.FAILED:
            raise RuntimeError(self.runtime.last_error(name, env_id=env_id))
        if self.phase == 'pick' and phase is ManipulationPhase.ATTACHED_HOLD:
            if not self.runtime.start_place(name, self.place, recovery_pose=self.recovery, env_id=env_id):
                raise RuntimeError(self.runtime.last_error(name, env_id=env_id))
            self.phase = 'place'
        import torch
        stopped = (self.phase == 'place' and phase is ManipulationPhase.IDLE
                   and self.runtime.held_object(name, env_id=env_id) is None
                   and bool(torch.isfinite(self.arm.data.joint_vel).all())
                   and bool((self.arm.data.joint_vel.abs() < .1).all())
                   and bool(torch.isfinite(self.object.data.root_vel_w).all())
                   and bool((self.object.data.root_vel_w[:, :3].norm(dim=-1) < .05).all())
                   and bool((self.object.data.root_vel_w[:, 3:].norm(dim=-1) < .1).all()))
        self.stable = self.stable + 1 if stopped else 0
        if self.stable >= 10:
            cancelled = self.cancel_reason is not None
            self.receipt = ExecutionReceipt(self.request.request_id, self.request.skill_id, self.request.robot_id,
                DependencyOutcome.TERMINAL_FAILURE if cancelled else DependencyOutcome.COMPLETED, True,
                FailureCode.SAFE_STOP if cancelled else FailureCode.NONE,
                self.cancel_reason or 'arm placed, released, and measured motion settled')
        return self.receipt


class ManipulationSkillRuntime:
    def __init__(self, env, bindings, *, resources=None, stage=None):
        from EAI.controllers.environment_view import SingleEnvironmentView, validate_env_id
        self.env, self.stage = env, stage
        existing_resources = getattr(env, '_manipulation_resource_manager', None)
        if existing_resources is not None and resources is not None and resources is not existing_resources:
            raise ValueError('shared manipulation runtimes must use the same resource manager')
        self.resources = resources if resources is not None else existing_resources
        if self.resources is None:
            self.resources = ResourceManager()
        self.bindings, self.views, self.handles = {}, {}, {}
        from copy import deepcopy
        for original_binding in bindings:
            binding = deepcopy(original_binding)
            validate_env_id(env, binding.env_id)
            if binding.task_robot_id in self.bindings:
                raise ValueError('duplicate manipulation robot binding')
            self.bindings[binding.task_robot_id] = binding
            if binding.backend == 'pepper':
                state = env._controllers_dict[binding.robot_name]
                if 'environment_views' in state:
                    view = state['environment_views'][binding.env_id]
                elif env.num_envs == 1:
                    view = env
                else:
                    raise ValueError('Pepper controller has not been isolated per environment')
            else:
                view = SingleEnvironmentView(env, binding.env_id)
            self.views[binding.task_robot_id] = view
        self.g1_runtime = None
        if any(b.backend == 'g1' for b in self.bindings.values()):
            from .g1_live import G1LiveRuntime
            self.g1_runtime = G1LiveRuntime(env)
        env._manipulation_resource_manager = self.resources

    def _require_scene(self, scene):
        if getattr(scene, 'base_env', scene) is not self.env:
            raise ValueError('manipulation runtime belongs to another scene')

    def _object_path(self, binding, object_ref):
        from EAI.controllers.environment_view import environment_path
        value = binding.objects[object_ref]
        path = value.prim_path if hasattr(value, 'prim_path') else value
        return environment_path(path, binding.env_id)

    def validate(self, request):
        provider = PickPlaceProvider()
        if request.skill_id == SKILL_ID:
            if provider.plan(request).status != 'planned':
                raise ValueError('invalid unified pick/place request')
        elif request.skill_id == 'eai.manipulation.g1_pick_place':
            from algorithm.skills.g1_pick_place import G1PickPlaceProvider
            if G1PickPlaceProvider().plan(request).status != 'planned':
                raise ValueError('invalid G1 pick/place request')
        elif request.skill_id != 'eai.transport.composite':
            raise ValueError('unsupported manipulation skill')
        binding = self.bindings[request.robot_id]
        if type(self.env.cfg.action_spaces[binding.robot_name]) is not int:
            raise ValueError('manipulation requires flat numeric action spaces')
        view = self.views[request.robot_id]
        object_ref = request.parameters.get('object_ref')
        if object_ref not in binding.objects or object_ref not in view.scene.rigid_objects:
            raise ValueError('object must have an explicit live binding')
        path = self._object_path(binding, object_ref)
        if getattr(view.scene.rigid_objects[object_ref].cfg, 'prim_path', None) != path:
            raise ValueError('object identity disagrees with live clone path')
        if request.skill_id == 'eai.manipulation.g1_pick_place' and binding.backend != 'g1':
            raise ValueError('G1 request requires a G1 backend')
        if request.skill_id == 'eai.transport.composite' and binding.backend != 'pepper':
            raise ValueError('Pepper transport request requires a Pepper backend')
        if 'pick_position' in request.parameters:
            measured = self.env.scene.rigid_objects[object_ref].data.root_pos_w[binding.env_id].detach().cpu().tolist()
            if math.dist(measured, request.parameters['pick_position']) > .03:
                raise ValueError('pickup coordinate differs from measured object origin')
        if binding.backend == 'pepper':
            local = self._pepper_request(request, binding)
            from algorithm.skills.pepper_transport.runtime import validate_transport_binding
            validate_transport_binding(scene=view, request=local)
            if 'place_position' in request.parameters:
                state = view._controllers_dict[binding.robot_name]
                transport = state['transport'].config
                grasp = state['runtime'].state.config
                origin = self.env.scene.env_origins[binding.env_id].detach().cpu().tolist()
                place = tuple(grasp.place_position[i] + transport.destination_offset[i] + origin[i] for i in range(3))
                if math.dist(place, request.parameters['place_position']) > .03:
                    raise ValueError('Pepper placement must match its configured transport route')
        return binding, view, path

    def _pepper_request(self, request, binding):
        if request.skill_id == 'eai.transport.composite':
            return replace(request, robot_id=binding.robot_name)
        state = self.views[request.robot_id]._controllers_dict[binding.robot_name]
        cfg = state['transport'].config
        return SkillRequest(request.request_id, 'eai.transport.composite', binding.robot_name, parameters={
            'object_ref': request.parameters['object_ref'], 'source_ref': cfg.source_table_name,
            'destination_ref': cfg.destination_table_name, 'carry_mode': 'bilateral_ideal_attachment'})

    def binding(self, request):
        # Freeze the caller's request and binding before admission.
        from copy import deepcopy
        request = deepcopy(request)
        binding, view, path = self.validate(request)
        if request.skill_id == SKILL_ID:
            provider = PickPlaceProvider()
        elif binding.backend == 'g1':
            from algorithm.skills.g1_pick_place import G1PickPlaceProvider
            provider = G1PickPlaceProvider()
        else:
            from algorithm.skills.pepper_transport import PepperTransportProvider
            provider = PepperTransportProvider()
        keys = ('robot:' + request.robot_id, 'object:' + path)

        def validate(*, scene, request):
            self._require_scene(scene)
            self.validate(request)

        def start(plan, *, scene, lease):
            self._require_scene(scene)
            self.resources.validate_lease(lease, required_resources=keys)
            if lease.owner != request.request_id:
                raise ValueError('lease owner differs from the admitted request')
            if plan.to_mapping() != provider.plan(request).to_mapping():
                raise ValueError('plan differs from its admitted request')
            if request.request_id in self.handles:
                raise ValueError('request already started; use an explicit retry ID')
            active = getattr(self.env, '_active_manipulation_handles', {})
            row_key = (binding.env_id, binding.robot_name)
            previous = active.get(row_key)
            if previous is not None and not previous.finished:
                raise ValueError('robot row has an unresolved manipulation owner')
            self.validate(request)
            if binding.backend == 'g1':
                from .g1_live import G1LiveHandle
                from algorithm.skills.g1_pick_place import G1PickPlaceProvider
                g1_request = replace(request, skill_id='eai.manipulation.g1_pick_place')
                g1_plan = G1PickPlaceProvider().plan(g1_request)
                if self.stage is None:
                    raise ValueError('G1 live planning requires the caller stage')
                handle = G1LiveHandle(g1_plan, view=view, stage=self.stage,
                    robot_name=binding.robot_name, robot_path=binding.robot_path,
                    objects=binding.objects, config=binding.config, obstacles=binding.obstacles)
                handle = self.g1_runtime.add(binding.env_id, binding.robot_name, handle)
            elif binding.backend == 'pepper':
                from algorithm.skills.pepper_transport import PepperTransportProvider
                from algorithm.skills.pepper_transport.runtime import PepperTransportHandle
                handle = PepperTransportHandle(PepperTransportProvider().plan(self._pepper_request(request, binding)), scene=view)
            else:
                arm_runtime = getattr(self.env, '_idealized_manipulation_runtime', None)
                if arm_runtime is None:
                    raise ValueError('arm manipulation runtime is not installed')
                handle = _ArmHandle(request, arm_runtime, binding, view, path)
            result = _LeaseHandle(self, request, lease, keys, handle)
            if binding.backend == 'g1':
                handle._ownership_guard = result._validate
            self.handles[request.request_id] = result
            active[row_key] = result
            self.env._active_manipulation_handles = active
            return result
        return CooperativeBinding(request, provider, start, validate, (keys[1],), start_with_lease=True)

    def neutral_actions(self, scene):
        self._require_scene(scene)
        return {}

    def step(self, scene, actions):
        self._require_scene(scene)
        import torch
        aggregate = {}
        selected = set()
        for key, action in actions.items():
            if not isinstance(action, torch.Tensor) or action.ndim != 2 or not bool(torch.isfinite(action).all()):
                raise ValueError('actions must be finite batched tensors')
            if key not in self.bindings:
                if key not in self.env.cfg.possible_agents or action.shape != (self.env.num_envs, self.env.cfg.action_spaces[key]):
                    raise ValueError('unknown robot action or incorrect action batch')
                if key in aggregate:
                    raise ValueError('action conflicts with row-scoped commands')
                aggregate[key] = action.clone()
                selected.update((i, key) for i in range(self.env.num_envs))
                continue
            binding = self.bindings[key]
            name, row = binding.robot_name, binding.env_id
            if action.shape[1] != self.env.cfg.action_spaces[name]:
                raise ValueError('action width disagrees with controller command space')
            if (row, name) in selected:
                raise ValueError('duplicate command for a robot environment row')
            if not isinstance(action, torch.Tensor) or action.ndim != 2 or action.shape[0] != 1 or not bool(torch.isfinite(action).all()):
                raise ValueError('scoped actions must be finite one-row tensors')
            selected.add((row, name))
            if name not in aggregate:
                aggregate[name] = torch.zeros((self.env.num_envs, action.shape[1]), device=self.env.device, dtype=action.dtype)
            if aggregate[name].shape[1] != action.shape[1]:
                raise ValueError('inconsistent action widths for robot clone rows')
            aggregate[name][row] = action[0]
        # Include idle robots so scalar gated controllers hold their base.
        for name in self.env.cfg.possible_agents:
            if name not in aggregate:
                width = self.env.cfg.action_spaces[name]
                if type(width) is not int:
                    raise ValueError('neutral action requires flat numeric action spaces')
                aggregate[name] = torch.zeros((self.env.num_envs, width), device=self.env.device)
        return self.env.step(aggregate)
