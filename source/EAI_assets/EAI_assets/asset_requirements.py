"""Pure Env DIY asset dependency graph.

The module deliberately contains no Isaac Sim imports.  It turns the JSON
selection vocabulary into semantic requirements that can be inspected by the
terminal, the Kit extension, and tests using the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from EAI_assets.scene_maps import SCENE_MAP_PATHS


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
    "plane": SCENE_MAP_PATHS["plane"],
    "warehouse": ("scene/warehouse/warehouse.usd", *SCENE_MAP_PATHS["warehouse"]),
    "factory": ("scene/factory/factory.usd", *SCENE_MAP_PATHS["factory"]),
    "airs": ("scene/airs/airs.usd", *SCENE_MAP_PATHS["airs"]),
    "desert": ("scene/desert/real_dust_scene_tiny.usda", *SCENE_MAP_PATHS["desert"]),
    "hospital": (
        "scene/hospital/hospital_local.usda",
        "scene/hospital/Bed_local.usda",
        *SCENE_MAP_PATHS["hospital"],
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
    "coco": (
        "robot/coco_one/coco_airs.usda",
        "robot/coco_one/coco_one.usd",
        "robot/coco_one/materials/coco-white.png",
        "robot/coco_one/materials/coco-white.png.001.png",
    ),
    "g1": ("robot/g1/g1_29dof_with_inspire_rev_1_0.usd",),
    "cf2x": ("robot/cf2x/cf2x.usd",),
    "iris": (
        "robot/pegasus/iris/iris.usd",
        "robot/pegasus/iris/iris_thumbnail.png",
    ),
    "pegasus": (
        "robot/pegasus/pegasus/pegasus_optimized.usdc",
        "robot/pegasus/pegasus/pegasus.usd",
        "robot/pegasus/pegasus/pegasus_thumbnail.png",
        "robot/pegasus/pegasus/Materials/Base/Metals/Aluminum_Anodized/Aluminum_Anodized_BaseColor.png",
        "robot/pegasus/pegasus/Materials/Base/Metals/Aluminum_Anodized/Aluminum_Anodized_Normal.png",
        "robot/pegasus/pegasus/Materials/Base/Metals/Aluminum_Anodized/Aluminum_Anodized_ORM.png",
        "robot/pegasus/pegasus/Materials/Base/Metals/Aluminum_Anodized_Black.mdl",
        "robot/pegasus/pegasus/Materials/Base/Plastics/Plastic_ABS.mdl",
        "robot/pegasus/pegasus/Materials/Base/Plastics/Plastic_ABS_1.mdl",
        "robot/pegasus/pegasus/Materials/Base/Plastics/Plastic_ABS_1_1.mdl",
    ),
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
    "orsus": (
        "payloads/sensors/orsus/Orsus_fix_type.usd",
        "payloads/sensors/orsus/orsus_mid360_rtx.usda",
    ),
    "realsense_d455": (
        "payloads/sensors/realsense_d455/rsd455_d455.usd",
        "payloads/sensors/realsense_d455/rsd455.usd",
    ),
    "lidar": ("payloads/sensors/lidar/ros_lidar.usda",),
}

_CONTROLLER_PATHS = {
    # ── Traditional controllers ──────────────────────────────────────────
    "CARTER_DIFF_CFG": (
        "traditional/carter_diff/__init__.py",
        "traditional/carter_diff/carter_diff.py",
    ),
    "PEPPER_HOLONOMIC_CFG": (
        "traditional/pepper_holonomic/__init__.py",
        "traditional/pepper_holonomic/pepper_holonomic.py",
    ),
    "SCOUT_DIFF_CFG": (
        "traditional/scout_diff/__init__.py",
        "traditional/scout_diff/scout_diff.py",
    ),
    "MUSHR_ACKERMANN_CFG": (
        "traditional/mushr_ackermann/__init__.py",
        "traditional/mushr_ackermann/mushr_ackermann.py",
        "traditional/mushr_ackermann/kinematics.py",
    ),
    "MUSHR_RWD_ACKERMANN_CFG": (
        "traditional/mushr_ackermann/__init__.py",
        "traditional/mushr_ackermann/mushr_ackermann.py",
        "traditional/mushr_ackermann/kinematics.py",
    ),
    "COCO_ACKERMANN_CFG": (
        "traditional/coco_ackermann/__init__.py",
        "traditional/coco_ackermann/coco_ackermann.py",
        "traditional/coco_ackermann/kinematics.py",
    ),
    "PEGASUS_IRIS_POSITION_CFG": (
        "traditional/pegasus_multirotor/__init__.py",
        "traditional/pegasus_multirotor/controller.py",
        "traditional/pegasus_multirotor/multirotor.py",
        "traditional/pegasus_multirotor/pegasus_multirotor.py",
    ),
    "PEGASUS_IRIS_ROTOR_CFG": (
        "traditional/pegasus_multirotor/__init__.py",
        "traditional/pegasus_multirotor/controller.py",
        "traditional/pegasus_multirotor/multirotor.py",
        "traditional/pegasus_multirotor/pegasus_multirotor.py",
    ),
    "PEGASUS_X4_POSITION_CFG": (
        "traditional/pegasus_multirotor/__init__.py",
        "traditional/pegasus_multirotor/controller.py",
        "traditional/pegasus_multirotor/multirotor.py",
        "traditional/pegasus_multirotor/pegasus_multirotor.py",
    ),
    "PEGASUS_X4_ROTOR_CFG": (
        "traditional/pegasus_multirotor/__init__.py",
        "traditional/pegasus_multirotor/controller.py",
        "traditional/pegasus_multirotor/multirotor.py",
        "traditional/pegasus_multirotor/pegasus_multirotor.py",
    ),
    "UR5_IK_CFG": (
        "traditional/manipulator_ik/__init__.py",
        "traditional/manipulator_ik/manipulator_ik.py",
        "traditional/ur5_ik/__init__.py",
        "traditional/ur5_ik/ur5_ik.py",
    ),
    "Z1_IK_CFG": (
        "traditional/manipulator_ik/__init__.py",
        "traditional/manipulator_ik/manipulator_ik.py",
        "traditional/z1_ik/__init__.py",
        "traditional/z1_ik/z1_ik.py",
    ),
    # ── RSL (ONNX) controllers ───────────────────────────────────────────
    "GO2_VELOCITY_RSL_CFG": (
        "rl/go2_rsl_rl/__init__.py",
        "rl/go2_rsl_rl/go2_rsl_rl.py",
        "rl/go2_rsl_rl/model/policy.onnx",
    ),
    "B2_VELOCITY_RSL_CFG": (
        "rl/b2_rsl_rl/__init__.py",
        "rl/b2_rsl_rl/b2_rsl_rl.py",
        "rl/b2_rsl_rl/model/policy.onnx",
        "rl/b2_rsl_rl/model/policy.pt",
    ),
    "M20_ROUGH_RSL_CFG": (
        "rl/m20_rough_rsl/__init__.py",
        "rl/m20_rough_rsl/m20_rough_rsl.py",
        "rl/m20_rough_rsl/model/m20_rough.onnx",
    ),
    "LITE3_VELOCITY_RSL_CFG": (
        "rl/lite3_rsl_rl/__init__.py",
        "rl/lite3_rsl_rl/lite3_rsl_rl.py",
        "rl/lite3_rsl_rl/model/policy.onnx",
    ),
    # ── SKRL controllers ─────────────────────────────────────────────────
    "G1_SKRL_CFG": (
        "rl/g1_skrl/__init__.py",
        "rl/g1_skrl/g1_skrl.py",
        "rl/g1_skrl/model/g1.pt",
        "rl/rl_cfg/__init__.py",
        "rl/rl_cfg/g1_skrl_flat_ppo_cfg.yaml",
    ),
    "QUADCOPTER_GOAL_SKRL_CFG": (
        "rl/quadcopter_goal_skrl/__init__.py",
        "rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py",
        "rl/quadcopter_goal_skrl/model/quadcopter.pt",
        "rl/rl_cfg/__init__.py",
        "rl/rl_cfg/quadcopter_goal_skrl_ppo_cfg.yaml",
    ),
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


def attachment_requirement_id(
    attachment_type: str,
    *,
    robot_type: str | None = None,
    catalog_module=None,
) -> str:
    catalog_module = _catalog_module(catalog_module)
    key = str(attachment_type).strip().lower()
    entry = catalog_module.attachment_entry(key)
    if getattr(entry, "visual_only", False):
        raise ValueError(f"Unknown attachment '{attachment_type}'.")
    if robot_type is not None and not entry.supports(robot_type):
        raise ValueError(
            f"Attachment '{key}' is not supported by robot "
            f"'{str(robot_type).strip().lower()}'."
        )
    category = str(getattr(entry, "category", "sensor"))
    if category == "manipulator":
        return f"payload:{key}"
    if category != "tool":
        return f"sensor:{key}"
    if not robot_type:
        raise ValueError(f"Tool '{key}' requires a host robot.")
    return f"tool:{key}@{str(robot_type).strip().lower()}"


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
        attachments = tuple(robot.get("attachments", ()) or ())
        for attachment in attachments:
            if not isinstance(attachment, Mapping):
                raise ValueError("robot.attachments entries must be objects")
        catalog_module.validate_attachment_types(
            robot_type,
            tuple(str(attachment.get("type") or "") for attachment in attachments),
        )
        for attachment in attachments:
            attachment_type = str(attachment.get("type") or "").strip().lower()
            catalog_entry = catalog_module.attachment_entry(attachment_type)
            category = str(getattr(catalog_entry, "category", "sensor"))
            if category == "tool":
                kind = RequirementKind.TOOL
                paths = ()
                label = f"{attachment_type} on {robot_type}"
            elif category == "manipulator":
                kind = RequirementKind.PAYLOAD
                paths = _PAYLOAD_PATHS.get(attachment_type, ())
                label = f"Payload {attachment_type}"
            else:
                kind = RequirementKind.SENSOR
                paths = _PAYLOAD_PATHS.get(attachment_type, ())
                label = f"Sensor {attachment_type}"
            _add(
                entries,
                AssetRequirement(
                    id=attachment_requirement_id(
                        attachment_type,
                        robot_type=robot_type,
                        catalog_module=catalog_module,
                    ),
                    label=label,
                    kind=kind,
                    relative_paths=paths,
                ),
            )
            attachment_cfg = _controller_cfg(attachment.get("controller"), getattr(catalog_entry, "controller_cfg", None))
            if attachment_cfg:
                _add(entries, _controller_requirement(attachment_cfg, catalog_module))

    return RequirementGraph(tuple(entries.values()))


def resolve_card_requirement(
    requirement_id: str,
    *,
    scene_key: str = "plane",
    catalog_module=None,
) -> AssetRequirement:
    catalog_module = _catalog_module(catalog_module)
    item_id = str(requirement_id).strip().lower()
    if item_id.startswith("scene:"):
        selection = {"scene_key": item_id.split(":", 1)[1], "robots": []}
    elif item_id.startswith("robot:"):
        robot_type = item_id.split(":", 1)[1]
        selection = {
            "scene_key": scene_key,
            "robots": [{"type": robot_type, "attachments": []}],
        }
    elif item_id.startswith(("payload:", "sensor:")):
        attachment_type = item_id.split(":", 1)[1]
        entry = catalog_module.attachment_entry(attachment_type)
        expected_id = attachment_requirement_id(
            attachment_type,
            catalog_module=catalog_module,
        )
        if item_id != expected_id:
            raise ValueError(f"Unknown asset requirement '{requirement_id}'.")
        if not entry.supported_robots:
            raise ValueError(f"Attachment '{attachment_type}' has no supported host robot.")
        selection = {
            "scene_key": scene_key,
            "robots": [
                {
                    "type": entry.supported_robots[0],
                    "attachments": [{"type": attachment_type}],
                }
            ],
        }
    else:
        raise ValueError(f"Unsupported asset requirement '{requirement_id}'.")

    graph = resolve_selection(selection, catalog_module=catalog_module)
    try:
        return next(item for item in graph.requirements if item.id == item_id)
    except StopIteration as exc:
        raise ValueError(f"Unknown asset requirement '{requirement_id}'.") from exc


def requirements_for_selection(selection: Mapping[str, Any], *, catalog_module=None) -> tuple[AssetRequirement, ...]:
    return resolve_selection(selection, catalog_module=catalog_module).requirements


def semantic_ids(graph: RequirementGraph | Iterable[AssetRequirement]) -> tuple[str, ...]:
    requirements = graph.requirements if isinstance(graph, RequirementGraph) else tuple(graph)
    return tuple(item.id for item in requirements)
