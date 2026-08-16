"""Pure state model shared by the Isaac extension and non-Isaac tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from EAI.hmrs_env.env_diy import catalog
from EAI.hmrs_env.env_diy.flow import (
    AttachmentSelection,
    ControllerChoice,
    InteractiveSelection,
    RobotSelection,
    interactive_selection_from_dict,
    interactive_selection_to_dict,
)


@dataclass
class AuthoringAttachment:
    type: str
    controller: ControllerChoice | None = None


@dataclass
class AuthoringRobot:
    id: str
    type: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    controller: ControllerChoice
    attachments: list[AuthoringAttachment] = field(default_factory=list)


class AuthoringModel:
    def __init__(self, scene_key: str | None = None) -> None:
        self.scene_key: str | None = None
        self.robots: list[AuthoringRobot] = []
        self._next_robot_number = 1
        if scene_key is not None:
            self.set_scene(scene_key)

    def set_scene(self, scene_key: str) -> None:
        key = str(scene_key).strip().lower()
        if key not in {item[0] for item in catalog.scene_choices()}:
            raise ValueError(f"Unknown scene '{scene_key}'.")
        self.scene_key = key

    def add_robot(
        self,
        robot_type: str,
        *,
        position=(0.0, 0.0, 0.0),
        rotation=(1.0, 0.0, 0.0, 0.0),
    ) -> str:
        key = catalog.canonical_robot_type(robot_type)
        if key not in catalog.robot_keys():
            raise ValueError(f"Unknown robot type '{robot_type}'.")
        pose = catalog.normalize_spawn_pose({"position": position, "rotation": rotation})
        assert pose is not None
        robot_id = f"robot_{self._next_robot_number}"
        self._next_robot_number += 1
        self.robots.append(
            AuthoringRobot(
                id=robot_id,
                type=key,
                position=pose["position"],
                rotation=pose["rotation"],
                controller=ControllerChoice("default", catalog.default_controller_cfg(key)),
            )
        )
        return robot_id

    def delete_robot(self, robot_id: str) -> None:
        self.robots = [robot for robot in self.robots if robot.id != robot_id]

    def robot(self, robot_id: str) -> AuthoringRobot:
        robot = next((item for item in self.robots if item.id == robot_id), None)
        if robot is None:
            raise KeyError(robot_id)
        return robot

    def set_robot_pose(self, robot_id: str, position, rotation) -> None:
        pose = catalog.normalize_spawn_pose({"position": position, "rotation": rotation})
        assert pose is not None
        robot = self.robot(robot_id)
        robot.position = pose["position"]
        robot.rotation = pose["rotation"]

    def attach(self, robot_id: str, attachment_type: str) -> None:
        robot = self.robot(robot_id)
        key = str(attachment_type).strip().lower()
        normalized = catalog.validate_attachment_types(
            robot.type,
            [item.type for item in robot.attachments] + [key],
        )
        if any(item.type == key for item in robot.attachments):
            return
        entry = catalog.attachment_entry(key)
        controller = (
            ControllerChoice("default", entry.controller_cfg)
            if entry.controller_cfg is not None
            else None
        )
        robot.attachments = [
            *(item for item in robot.attachments if item.type in normalized),
            AuthoringAttachment(key, controller),
        ]

    def detach(self, robot_id: str, attachment_type: str) -> None:
        key = str(attachment_type).strip().lower()
        robot = self.robot(robot_id)
        robot.attachments = [item for item in robot.attachments if item.type != key]
        if key in {"orsus", "realsense_d455"} and robot.type not in catalog.BUILTIN_CAMERA_ROBOTS:
            robot.attachments = [item for item in robot.attachments if item.type != "camera"]

    def set_robot_controller(self, robot_id: str, mode: str, cfg: str | None) -> None:
        self.robot(robot_id).controller = _controller_choice(mode, cfg)

    def set_attachment_controller(
        self,
        robot_id: str,
        attachment_type: str,
        mode: str,
        cfg: str | None,
    ) -> None:
        key = str(attachment_type).strip().lower()
        attachment = next(
            (item for item in self.robot(robot_id).attachments if item.type == key),
            None,
        )
        if attachment is None:
            raise KeyError(f"{robot_id}:{key}")
        if catalog.attachment_entry(key).controller_cfg is None:
            raise ValueError(f"Attachment '{key}' does not have a controller cfg.")
        attachment.controller = _controller_choice(mode, cfg)

    def to_selection(self) -> InteractiveSelection:
        if self.scene_key is None:
            raise ValueError("A scene must be selected before export.")
        if not self.robots:
            raise ValueError("At least one robot is required before export.")
        selection = InteractiveSelection(
            scene_key=self.scene_key,
            robots=tuple(
                RobotSelection(
                    type=robot.type,
                    controller=robot.controller,
                    visual={},
                    attachments=tuple(
                        AttachmentSelection(item.type, item.controller)
                        for item in robot.attachments
                    ),
                    spawn_pose={
                        "position": robot.position,
                        "rotation": robot.rotation,
                    },
                )
                for robot in self.robots
            ),
        )
        # Reuse the canonical parser as the final compatibility check.
        return interactive_selection_from_dict(interactive_selection_to_dict(selection))

    def to_selection_dict(self) -> dict[str, Any]:
        return interactive_selection_to_dict(self.to_selection())

    @classmethod
    def from_selection_dict(cls, data: dict[str, Any]) -> "AuthoringModel":
        selection = interactive_selection_from_dict(data)
        model = cls(selection.scene_key)
        for selected in selection.robots:
            pose = selected.spawn_pose or {
                "position": (0.0, 0.0, 0.0),
                "rotation": (1.0, 0.0, 0.0, 0.0),
            }
            robot_id = model.add_robot(
                selected.type,
                position=pose["position"],
                rotation=pose["rotation"],
            )
            robot = model.robot(robot_id)
            robot.controller = selected.controller
            robot.attachments = [
                AuthoringAttachment(item.type, item.controller)
                for item in selected.attachments
            ]
        return model


def _controller_choice(mode: str, cfg: str | None) -> ControllerChoice:
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"default", "manual"}:
        raise ValueError("Controller mode must be 'default' or 'manual'.")
    if cfg is not None and cfg not in catalog.controller_cfg_names():
        raise ValueError(f"Unknown controller cfg '{cfg}'.")
    return ControllerChoice(normalized_mode, cfg)
