# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""Generic arm / kinematics pipeline hooks for DirectMARLEnv `post_apply_action`.

This module stays **robot-agnostic**: no UR5/M20/Carter joint names or factory agent ids.

Naming note (review): "ik_controller" denotes the **pipeline extension point** for applying
arm joint targets after per-robot controllers run. Concrete tasks may use file-based joint
commands, analytic IK, or numerical IK in subclasses under `EAI_assets.controller.*`.

See also: `EAI_assets.controller.traditional.ur5_ik` for the factory UR5 implementation.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple


def eai_simulator_repo_root() -> Path:
    """`eai-simulator` repo root when this file lives under `source/EAI/EAI/controllers/`."""
    return Path(__file__).resolve().parents[4]


def ensure_dir(path: Path | str) -> None:
    os.makedirs(str(path), exist_ok=True)


def read_first_line_floats(path: str, count: int) -> Optional[List[float]]:
    """Read first non-empty line from *path* and parse *count* floats (comma or space separated)."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.read().strip()
        if not line or line.startswith("#"):
            return None
        parts = [x.strip() for x in line.replace(",", " ").split()]
        vals = [float(x) for x in parts[:count]]
        if len(vals) < count:
            return None
        return vals
    except (OSError, ValueError):
        return None


def read_control_target_token(
    path: str,
    *,
    valid_tokens: frozenset[str],
    default: str,
) -> str:
    """Read a single token from *path*; return *default* if missing or invalid."""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read().strip().lower()
        if t in valid_tokens:
            return t
    except OSError:
        pass
    return default


def read_pick_xyz_after_seq(
    trigger_path: str,
    target_path: str,
    last_seq: int,
) -> Tuple[int, Optional[Tuple[float, float, float]]]:
    """If trigger file seq increased, read XYZ from *target_path*."""
    if not (os.path.isfile(trigger_path) and os.path.isfile(target_path)):
        return last_seq, None
    try:
        with open(trigger_path, "r", encoding="utf-8") as f:
            seq = int(f.read().strip() or "0")
    except (OSError, ValueError):
        return last_seq, None
    if seq <= last_seq:
        return last_seq, None
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            line = f.read().strip()
        if not line:
            return last_seq, None
        parts = [float(x) for x in line.replace(",", " ").split()[:3]]
        if len(parts) != 3:
            return last_seq, None
        return seq, (parts[0], parts[1], parts[2])
    except (OSError, ValueError):
        return last_seq, None


class ArmPostApplyPipeline(ABC):
    """Abstract hook invoked at the end of `MultiRobotDirectEnv._apply_action`."""

    @abstractmethod
    def run_post_apply(self, env: Any) -> None:
        """Task-specific arm / kinematics follow-up (file bridge, IK, grasp, etc.)."""


def noop_post_apply(_env: Any) -> None:
    """Placeholder `post_apply_action` that does nothing."""
