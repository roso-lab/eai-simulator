"""Explicit in-session snapshot adapter for a live Pepper transport handle.

Register under task.checkpoint.SkillRuntimeComponent and a physical replay
backend. This records the ideal attachment state; it is not a PhysX solver-cache
snapshot or support for recovering a completed/released task into a new run.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


class PepperTransportCheckpoint:
    def __init__(self, handle):
        from tools.physics_replay.runtime_state import StateCodec
        self.handle = handle
        runtime = handle.controller['runtime']
        transport = handle.controller['transport']
        self.codec = StateCodec((type(runtime.state), type(transport)))
        self.binding = (handle.plan.request_id, handle.plan.robot_id, id(handle))

    def capture_state(self) -> Mapping[str, Any]:
        h = self.handle
        if h._receipt is not None:
            raise ValueError('terminal handles cannot restore an active skill')
        return {'binding': self.binding,
                'runtime': self.codec.encode(vars(h.controller['runtime'])),
                'transport': self.codec.encode(vars(h.controller['transport'])),
                'controller': self.codec.encode({k:v for k,v in h.controller.items()
                                                if k not in ('runtime','transport')}),
                'handle': {'ticks':h.ticks, 'stable_ticks':h.stable_ticks,
                           'cancel_reason':h.cancel_reason}}

    def _decoded(self, state):
        if tuple(state['binding']) != self.binding or self.handle._receipt is not None:
            raise ValueError('live Pepper handle binding changed or already terminated')
        decoded = {k:self.codec.decode(state[k]) for k in ('runtime','transport','controller')}
        current_controller = {k:v for k,v in self.handle.controller.items() if k not in ('runtime','transport')}
        if set(decoded['controller']) != set(current_controller):
            raise ValueError('Pepper controller field set changed')
        for key in ('robot_type','locomotion_controller','auto_start','collision_filter_installed'):
            if decoded['controller'].get(key) != current_controller.get(key):
                raise ValueError('Pepper controller identity/safety binding changed')
        if decoded['controller'].get('execution_enabled') is not True:
            raise ValueError('active Pepper checkpoint requires enabled execution')
        for key in ('runtime','transport'):
            current = vars(self.handle.controller[key])
            if set(decoded[key]) != set(current):
                raise ValueError('Pepper runtime field set changed')
        if decoded['runtime']['state'].config != self.handle.controller['runtime'].state.config:
            raise ValueError('Pepper grasp geometry changed')
        if decoded['transport']['config'] != self.handle.controller['transport'].config:
            raise ValueError('Pepper transport geometry changed')
        counters = state['handle']
        if set(counters) != {'ticks','stable_ticks','cancel_reason'} or any(
            type(counters[k]) is not int or counters[k] < 0 for k in ('ticks','stable_ticks')
        ) or counters['stable_ticks'] >= self.handle.settle_ticks or (
            counters['cancel_reason'] is not None and (
                not isinstance(counters['cancel_reason'],str) or not counters['cancel_reason'].strip())):
            raise ValueError('invalid Pepper handle counters')
        return decoded

    def validate_state(self, state):
        self._decoded(state)

    def restore_state(self, state):
        decoded = self._decoded(state)
        h = self.handle
        for key in ('runtime','transport'):
            vars(h.controller[key]).clear()
            vars(h.controller[key]).update(decoded[key])
        runtime, transport = h.controller['runtime'], h.controller['transport']
        h.controller.clear()
        h.controller.update(decoded['controller'], runtime=runtime, transport=transport)
        h.ticks, h.stable_ticks = state['handle']['ticks'], state['handle']['stable_ticks']
        h.cancel_reason = state['handle']['cancel_reason']
        h.env.pepper_box_grasp_state = runtime.state.snapshot()
        h.env.box_transport_state = transport.snapshot()

    def attachment_matches_physics(self, *, tolerance_m=.03):
        """Additional held-object gate after physical backend readback passed."""
        if isinstance(tolerance_m, bool) or not math.isfinite(tolerance_m) or tolerance_m <= 0:
            raise ValueError('attachment tolerance must be finite and positive')
        runtime = self.handle.controller['runtime']
        box = self.handle.env.scene.rigid_objects[runtime.state.config.cube_prim_name]
        position = box.data.root_pos_w[0].detach().cpu().tolist()
        if not all(math.isfinite(v) for v in position):
            return False
        if runtime.state.attached:
            return math.dist(position, runtime._world_point(runtime.state.cube_position())) <= tolerance_m
        return True
