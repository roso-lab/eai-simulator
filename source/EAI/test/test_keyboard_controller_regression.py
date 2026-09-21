from __future__ import annotations

import torch

from algorithm.keyboard.keyboard import command_for_key, topic_for_robot
from simulator import _apply_cmd_vel_bridge_commands


class _FakeBridge:
    def __init__(self, command: tuple[float, float, float]):
        self.command = command

    def get_cmd_vel(self) -> tuple[float, float, float]:
        return self.command


def test_keyboard_quadruped_keys_keep_velocity_contract():
    assert topic_for_robot("/b2_1/") == "/b2_1/cmd_vel"
    assert command_for_key("w", linear_speed=0.5, angular_speed=0.8) == command_for_key(
        "W", linear_speed=0.5, angular_speed=0.8
    )
    assert command_for_key("w", linear_speed=0.5, angular_speed=0.8).linear_x == 0.5
    assert command_for_key("s", linear_speed=0.5, angular_speed=0.8).linear_x == -0.5
    assert command_for_key("c", linear_speed=0.5, angular_speed=0.8).angular_z == 0.8
    assert command_for_key("v", linear_speed=0.5, angular_speed=0.8).angular_z == -0.8
    assert command_for_key("k", linear_speed=0.5, angular_speed=0.8).linear_x == 0.0


def test_cmd_vel_bridge_feeds_b2_controller_command_tensor():
    robot_commands = {"b2_1": torch.zeros((1, 3))}
    _apply_cmd_vel_bridge_commands(
        bridges={"b2_1": _FakeBridge((0.5, 0.0, -0.8))},
        robot_commands=robot_commands,
        robot_types={"b2_1": "B2"},
        goal_controlled_robots=set(),
    )

    assert torch.equal(robot_commands["b2_1"], torch.tensor([[0.5, 0.0, -0.8]]))
