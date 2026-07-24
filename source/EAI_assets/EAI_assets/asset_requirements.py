"""Pure Env DIY asset dependency graph.

The module deliberately contains no Isaac Sim imports.  It turns the JSON
selection vocabulary into semantic requirements that can be inspected by the
terminal, the Kit extension, and tests using the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class RequirementState(str, Enum):
    READY = "READY"
    MISSING = "MISSING"
    DOWNLOADING = "DOWNLOADING"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ACCESS_PENDING = "ACCESS_PENDING"
    FAILED = "FAILED"


class RequirementKind(str, Enum):
    SCENE = "scene"
    ROBOT = "robot"
    PAYLOAD = "payload"
    SENSOR = "sensor"
    TOOL = "tool"
    CONTROLLER = "controller"


@dataclass(frozen=True)
class AssetRequirement:
    id: str
    label: str
    kind: RequirementKind
    relative_paths: tuple[str, ...] = ()

    @property
    def remote_paths(self) -> tuple[str, ...]:
        root = "controller" if self.kind is RequirementKind.CONTROLLER else "usd"
        return tuple(f"{root}/{path.lstrip('/')}" for path in self.relative_paths)


@dataclass(frozen=True)
class RequirementGraph:
    requirements: tuple[AssetRequirement, ...]
    selection_id: str = "selection:current"


_SCENE_PATHS = {
    "plane": (),
    "warehouse": ("scene/warehouse/warehouse.usd",),
    "factory": ("scene/factory/factory.usd",),
    "airs": ("scene/airs/airs.usd",),
    "table": ("scene/table/table.usd",),
    "garden": ("scene/garden/garden.usd",),
    "indoor": ("scene/indoor/walls.usd", "scene/indoor/floor.usd"),
    "desert": ("scene/desert/real_dust_scene_tiny.usda",),
    "hospital": (
        "scene/hospital/hospital_local.usda",
        "scene/hospital/Bed_local.usda",
    ),
}

_ROBOT_PATHS = {
    "carter": ("robot/carter/carter.usd",),
    "pepper": ("robot/pepper/pepper.usd",),
    "go2": ("robot/go2/go2.usd", "robot/go2/Props/instanceable_meshes.usd"),
    "b2": ("robot/b2/b2_canonical.usdc",),
    "m20": (
        "robot/m20/M20.usd",
        "robot/m20/configuration/M20_base.usd",
        "robot/m20/configuration/M20_physics.usd",
        "robot/m20/configuration/M20_robot.usd",
        "robot/m20/configuration/M20_sensor.usd",
    ),
    "scout": ("robot/scout/scout_v2.usd",),
    "mushr_v2": ("robot/mushr_v2/mushr_nano_v2.usd",),
    "g1": ("robot/g1/g1_29dof_with_inspire_rev_1_0.usd",),
    "cf2x": ("robot/cf2x/cf2x.usd",),
    "human": ("human/HumanFemale.usd",),
    "lite3": (
        "robot/lite3/Lite3_canonical.usdc",
        "robot/lite3/configuration/Lite3_base.usd",
        "robot/lite3/configuration/Lite3_physics.usd",
        "robot/lite3/configuration/Lite3_robot.usd",
        "robot/lite3/configuration/Lite3_sensor.usd",
    ),
}

_PAYLOAD_PATHS = {
    "ur5": ("payloads/manipulators/ur5/ur5-noroot.usd",),
    "z1": ("payloads/manipulators/z1/z1_description.usda",),
    "gshub": ("payloads/sensors/gs_hub/GS_Hub_fix_type.usd",),
    "lidar": ("payloads/sensors/lidar/ros_lidar.usda",),
}

_CONTROLLER_PATHS = {
    "CARTER_DIFF_CFG": ("traditional/carter_diff/carter_diff.py",),
    "PEPPER_HOLONOMIC_CFG": ("traditional/pepper_holonomic/pepper_holonomic.py",),
    "GO2_VELOCITY_RSL_CFG": ("rl/go2_rsl_rl/model/policy.onnx",),
    "B2_VELOCITY_RSL_CFG": ("rl/b2_rsl_rl/model/policy.onnx",),
    "M20_ROUGH_RSL_CFG": ("rl/m20_rough_rsl/model/m20_rough.onnx",),
    "SCOUT_DIFF_CFG": ("traditional/scout_diff/scout_diff.py",),
    "MUSHR_ACKERMANN_CFG": ("traditional/mushr_ackermann/mushr_ackermann.py",),
    "MUSHR_RWD_ACKERMANN_CFG": ("traditional/mushr_ackermann/mushr_ackermann.py",),
    "G1_SKRL_CFG": ("rl/g1_skrl/model/g1.pt",),
    "QUADCOPTER_GOAL_SKRL_CFG": ("rl/quadcopter_goal_skrl/model/quadcopter.pt",),
    "HUMAN_ANIMATION_CFG": ("traditional/human_animation/human_animation.py",),
    "LITE3_VELOCITY_RSL_CFG": ("rl/lite3_rsl_rl/model/policy.onnx",),
    "UR5_IK_CFG": ("traditional/ur5_ik/ur5_ik.py",),
    "Z1_IK_CFG": ("traditional/z1_ik/z1_ik.py",),
}


def _add(
    entries: dict[str, AssetRequirement],
    requirement: AssetRequirement,
) -> None:
    entries.setdefault(requirement.id, requirement)


def _controller_cfg(value: Any, default: str | None) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("cfg")
    return str(value) if value else default


def _catalog_module(catalog_module=None):
    if catalog_module is not None:
        return catalog_module
    from EAI.hmrs_env.env_diy import catalog

    return catalog


def _controller_requirement(cfg: str, catalog_module) -> AssetRequirement:
    paths = _CONTROLLER_PATHS.get(cfg, ())
    return AssetRequirement(
        id=f"controller:{cfg}",
        label=f"Controller {cfg}",
        kind=RequirementKind.CONTROLLER,
        relative_paths=tuple(path.removeprefix("controller/") for path in paths),
    )


def resolve_selection(selection: Mapping[str, Any], *, catalog_module=None) -> RequirementGraph:
    """Expand one serialized Env DIY selection into deduplicated requirements."""

    if not isinstance(selection, Mapping):
        raise TypeError("selection must be a mapping")
    catalog_module = _catalog_module(catalog_module)
    entries: dict[str, AssetRequirement] = {}
    scene_key = str(selection.get("scene_key") or "").strip().lower()
    if scene_key not in _SCENE_PATHS:
        raise ValueError(f"Unknown scene '{scene_key}'.")
    _add(
        entries,
        AssetRequirement(
            id=f"scene:{scene_key}",
            label=f"Scene {scene_key}",
            kind=RequirementKind.SCENE,
            relative_paths=_SCENE_PATHS[scene_key],
        ),
    )

    for robot in selection.get("robots", ()):
        if not isinstance(robot, Mapping):
            raise ValueError("selection.robots entries must be objects")
        robot_type = str(robot.get("type") or "").strip().lower()
        if robot_type not in _ROBOT_PATHS:
            raise ValueError(f"Unknown robot type '{robot_type}'.")
        _add(
            entries,
            AssetRequirement(
                id=f"robot:{robot_type}",
                label=f"Robot {robot_type}",
                kind=RequirementKind.ROBOT,
                relative_paths=_ROBOT_PATHS[robot_type],
            ),
        )
        default_cfg = catalog_module.default_controller_cfg(robot_type)
        cfg = _controller_cfg(robot.get("controller"), default_cfg)
        if cfg:
            _add(entries, _controller_requirement(cfg, catalog_module))
        for attachment in robot.get("attachments", ()):
            if not isinstance(attachment, Mapping):
                raise ValueError("robot.attachments entries must be objects")
            attachment_type = str(attachment.get("type") or "").strip().lower()
            catalog_entry = catalog_module.attachment_entry(attachment_type)
            category = str(getattr(catalog_entry, "category", "sensor"))
            if category == "tool":
                kind = RequirementKind.TOOL
                prefix = "tool"
                paths = ()
            elif category == "manipulator":
                kind = RequirementKind.PAYLOAD
                prefix = "payload"
                paths = _PAYLOAD_PATHS.get(attachment_type, ())
            else:
                kind = RequirementKind.SENSOR
                prefix = "sensor"
                paths = _PAYLOAD_PATHS.get(attachment_type, ())
            _add(
                entries,
                AssetRequirement(
                    id=f"{prefix}:{attachment_type}@{robot_type}",
                    label=f"{attachment_type} on {robot_type}",
                    kind=kind,
                    relative_paths=paths,
                ),
            )
            attachment_cfg = _controller_cfg(attachment.get("controller"), getattr(catalog_entry, "controller_cfg", None))
            if attachment_cfg:
                _add(entries, _controller_requirement(attachment_cfg, catalog_module))

    return RequirementGraph(tuple(entries.values()))


def requirements_for_selection(selection: Mapping[str, Any], *, catalog_module=None) -> tuple[AssetRequirement, ...]:
    return resolve_selection(selection, catalog_module=catalog_module).requirements


def semantic_ids(graph: RequirementGraph | Iterable[AssetRequirement]) -> tuple[str, ...]:
    requirements = graph.requirements if isinstance(graph, RequirementGraph) else tuple(graph)
    return tuple(item.id for item in requirements)
