"""Trusted current PhysX root poses on an episode-relative physical clock.

This reader does not step the simulator, move robots or infer historic poses.
Native owners call it after stepping their existing simulator session.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


class NativeGoalPoseSource:
    def __init__(self, session: Any, robot_bindings: Mapping[str, str], *,
                 episode_step_origin: int, frame_id: str = "world"):
        if type(episode_step_origin) is not int or episode_step_origin < 0:
            raise ValueError("native pose origin must be a nonnegative physics step")
        if session.num_envs != 1:
            raise ValueError("native goal pose source requires one episode environment")
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise ValueError("native pose frame must be nonempty")
        if (not isinstance(robot_bindings, Mapping) or not robot_bindings
                or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                       for k, v in robot_bindings.items())
                or len(set(robot_bindings.values())) != len(robot_bindings)):
            raise ValueError("native pose bindings must map distinct public and runtime identities")
        self.session = session
        self.bindings = dict(robot_bindings)
        self.origin = episode_step_origin
        self.frame_id = frame_id
        self._physics_dt = float(session.base_env.sim.get_physics_dt())
        if not math.isfinite(self._physics_dt) or self._physics_dt <= 0:
            raise ValueError("native pose physics interval must be positive and finite")
        if not set(self.bindings.values()) <= set(session.base_env.scene.articulations):
            raise ValueError("native pose binding is missing its articulation")
        self._last_step = self.origin

    def _step(self) -> int:
        step = int(self.session.base_env.sim.current_time_step_index)
        if step < self._last_step:
            raise ValueError("native physics clock moved backwards; create a new source for a restored branch")
        if float(self.session.base_env.sim.get_physics_dt()) != self._physics_dt:
            raise ValueError("native physics interval changed within this episode")
        self._last_step = step
        return step

    @property
    def time_s(self) -> float:
        return (self._step() - self.origin) * self._physics_dt

    def __call__(self, robot_id: str, time_s: float) -> dict[str, Any]:
        if robot_id not in self.bindings:
            raise ValueError("public robot has no admitted native pose binding")
        step = self._step()
        actual_time = (step - self.origin) * self._physics_dt
        if (isinstance(time_s, bool) or not isinstance(time_s, (int, float))
                or not math.isfinite(time_s)
                or not math.isclose(time_s, actual_time, rel_tol=0, abs_tol=1e-9)):
            raise ValueError("requested pose time differs from current native physics time")
        root = self.session.base_env.scene.articulations[
            self.bindings[robot_id]].data.root_state_w[0].detach().cpu().tolist()
        if len(root) < 7 or any(not math.isfinite(float(value)) for value in root[:7]):
            raise ValueError("native root position/quaternion is missing or nonfinite")
        if self._step() != step:
            raise RuntimeError("simulator stepped during native pose read")
        w, x, y, z = (float(value) for value in root[3:7])
        norm = math.sqrt(w*w + x*x + y*y + z*z)
        if not math.isclose(norm, 1, rel_tol=0, abs_tol=1e-3):
            raise ValueError("native root quaternion must be normalized")
        w, x, y, z = w/norm, x/norm, y/norm, z/norm
        yaw = math.atan2(2*(w*z + x*y), 1-2*(y*y + z*z))
        return {
            "sampled_at_s": float(time_s), "position_m": [float(v) for v in root[:3]],
            "yaw_rad": yaw, "frame_id": self.frame_id, "source": "physx",
            "quality": {"position_error_m": None, "measurement": "physx_root_state_readback",
                        "physics_step": step, "episode_step_origin": self.origin,
                        "physics_dt_s": self._physics_dt},
        }
