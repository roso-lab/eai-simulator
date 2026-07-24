from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from . import catalog
from .paths import REPO_ROOT

ENV_CONFIG_RELATIVE_DIR = Path("source/EAI_hmrs/EAI_hmrs/envs")
TASK_SCHEMA_VERSION = 1
_TASK_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_task_name(name: str) -> str:
    stripped = name.strip()
    if not stripped:
        raise ValueError("Env name cannot be empty.")
    if not _TASK_NAME_PATTERN.fullmatch(stripped):
        raise ValueError("Env name may only contain letters, numbers, '_' and '-'.")
    if stripped.endswith(".json"):
        raise ValueError("Env name should not include the .json suffix.")
    return stripped


def task_path(task_name: str, *, repo_root: Path = REPO_ROOT) -> Path:
    return repo_root / ENV_CONFIG_RELATIVE_DIR / f"{validate_task_name(task_name)}.json"


def saved_task_exists(task_name: str, *, repo_root: Path = REPO_ROOT) -> bool:
    try:
        path = task_path(task_name, repo_root=repo_root)
    except ValueError:
        return False
    return path.is_file()


def save_task(
    task_name: str,
    task_data: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    overwrite: bool = True,
) -> Path:
    clean_name = validate_task_name(task_name)
    payload = _normalize_task_payload(clean_name, task_data)
    path = task_path(clean_name, repo_root=repo_root)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def save_task_with_payload(
    task_name: str,
    task_data: dict[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    overwrite: bool = True,
) -> tuple[Path, dict[str, Any]]:
    path = save_task(
        task_name,
        task_data,
        repo_root=repo_root,
        overwrite=overwrite,
    )
    return path, json.loads(path.read_text(encoding="utf-8"))


def load_task(task_name: str, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    path = task_path(task_name, repo_root=repo_root)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_task_payload(validate_task_name(task_name), data)


def task_from_visual_state(task_name: str, visual_state: dict[str, Any]) -> dict[str, Any]:
    clean_name = validate_task_name(task_name)
    scene = visual_state.get("scene")
    robots = []
    for robot in visual_state.get("robots", []):
        normalized = {
            "type": _canonical_robot_type(str(robot["type"])),
            "controller": _controller_dict(robot.get("controller")),
            "visual": {
                "x": float(robot.get("x", 0.0)),
                "y": float(robot.get("y", 0.0)),
            },
            "attachments": _deduplicate_attachments(robot.get("attachments", [])),
        }
        spawn_pose = _spawn_pose_dict(robot.get("spawn_pose"))
        if spawn_pose is not None:
            normalized["spawn_pose"] = spawn_pose
        robots.append(normalized)
    return _normalize_task_payload(
        clean_name,
        {
            "scene_key": scene,
            "robots": robots,
        },
    )


def _normalize_task_payload(task_name: str, task_data: dict[str, Any]) -> dict[str, Any]:
    scene_key = task_data.get("scene_key") or task_data.get("scene")
    if not scene_key:
        raise ValueError("Env data must include scene_key.")
    robots = []
    for robot in task_data.get("robots", []):
        visual = robot.get("visual") or {}
        normalized = {
            "type": _canonical_robot_type(str(robot["type"])),
            "controller": _controller_dict(robot.get("controller")),
            "visual": {
                "x": float(visual.get("x", robot.get("x", 0.0))),
                "y": float(visual.get("y", robot.get("y", 0.0))),
            },
            "attachments": _deduplicate_attachments(robot.get("attachments", [])),
        }
        spawn_pose = _spawn_pose_dict(robot.get("spawn_pose"))
        if spawn_pose is not None:
            normalized["spawn_pose"] = spawn_pose
        robots.append(normalized)
    if not robots:
        raise ValueError("Env data must include at least one robot.")
    return {
        "version": int(task_data.get("version", TASK_SCHEMA_VERSION)),
        "task_name": task_name,
        "scene_key": str(scene_key),
        "robots": robots,
    }


def _canonical_robot_type(robot_type: str) -> str:
    return catalog.canonical_robot_type(robot_type)


def _deduplicate_attachments(attachments: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    manipulator_type: str | None = None
    for attachment in attachments or []:
        attachment_type = str(attachment["type"]).strip().lower()
        if attachment_type in {"ur5", "z1"}:
            if manipulator_type is not None and manipulator_type != attachment_type:
                raise ValueError("A robot cannot attach both UR5 and Z1.")
            manipulator_type = attachment_type
        if attachment_type in seen:
            continue
        seen.add(attachment_type)
        normalized.append(
            {
                "type": attachment_type,
                "controller": _controller_dict(attachment.get("controller")),
            }
        )
    return normalized


def _controller_dict(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    mode = str(value.get("mode", "default"))
    cfg = value.get("cfg")
    if mode == "manual":
        return {"mode": "manual", "cfg": str(cfg or "manual")}
    return {"mode": "default", "cfg": str(cfg) if cfg is not None else ""}


def _spawn_pose_dict(value: Any) -> dict[str, list[float]] | None:
    return catalog.spawn_pose_to_dict(value)
