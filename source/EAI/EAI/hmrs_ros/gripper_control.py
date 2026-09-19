"""Isaac-independent gripper target application helpers."""

from __future__ import annotations

from typing import Any


def apply_gripper_target(articulation: Any, joint_name: str, position: float) -> bool:
    names = tuple(getattr(articulation, "joint_names", ()))
    if joint_name not in names:
        return False
    joint_index = names.index(joint_name)
    lower, upper = 0.0, 0.8
    limits = getattr(getattr(articulation, "data", None), "soft_joint_pos_limits", None)
    if limits is not None:
        try:
            lower, upper = (float(value) for value in limits[0, joint_index])
        except (IndexError, TypeError, ValueError):
            pass
    current = getattr(getattr(articulation, "data", None), "joint_pos", None)
    if current is None:
        return False
    target = max(lower, min(upper, float(position)))
    values = current[:, joint_index].clone()
    values[:] = target
    articulation.set_joint_position_target(values.unsqueeze(-1), joint_ids=[joint_index])
    return True


__all__ = ["apply_gripper_target"]
