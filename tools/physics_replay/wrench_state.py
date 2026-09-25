"""Checkpoint adapter for articulation external-wrench composers."""

from __future__ import annotations


class WrenchState:
    """Capture and restore public external-wrench composer state."""

    def __init__(self, scene):
        self.scene = scene

    def capture_replay_state(self):
        return {
            name: {
                kind: {
                    "force": getattr(entity, kind).composed_force_as_torch.clone(),
                    "torque": getattr(entity, kind).composed_torque_as_torch.clone(),
                    "active": getattr(entity, kind).active,
                }
                for kind in (
                    "permanent_wrench_composer",
                    "instantaneous_wrench_composer",
                )
            }
            for name, entity in self.scene.articulations.items()
        }

    def validate_replay_state(self, state):
        if set(state) != set(self.scene.articulations):
            raise ValueError("wrench owners changed")

    def restore_replay_state(self, state):
        self.validate_replay_state(state)
        for name, values in state.items():
            for kind, value in values.items():
                composer = getattr(self.scene.articulations[name], kind)
                composer.reset()
                if value["active"]:
                    composer.set_forces_and_torques(
                        forces=value["force"],
                        torques=value["torque"],
                    )


__all__ = ["WrenchState"]
