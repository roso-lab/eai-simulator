"""Static extraction of actuator literals from EAI robot config sources.

This intentionally uses AST literals instead of importing Isaac Lab. It reports
what the source config declares, not what a provider-backed runtime loaded.
"""
from __future__ import annotations

import ast
import hashlib
import math
from pathlib import Path
from typing import Any

ROBOT_CONFIG_SOURCES = {
    "go2": "source/EAI_assets/EAI_assets/robots/go2.py",
    "b2": "source/EAI_assets/EAI_assets/robots/b2.py",
    "m20": "source/EAI_assets/EAI_assets/robots/deeprobotics.py",
    "lite3": "source/EAI_assets/EAI_assets/robots/deeprobotics.py",
}
ROBOT_CONFIG_SYMBOLS = {"go2": "GO2_CFG", "b2": "B2_CFG", "m20": "DEEPROBOTICS_M20_CFG",
                        "lite3": "DEEPROBOTICS_LITE3_CFG"}


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = _literal(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        return None


def _numeric(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def inspect_robot_config(robot_type: str, *, repo_root: str | Path = ".") -> dict[str, Any]:
    if robot_type not in ROBOT_CONFIG_SOURCES:
        raise ValueError(f"no static config extractor for {robot_type!r}")
    path = Path(repo_root) / ROBOT_CONFIG_SOURCES[robot_type]
    content = path.read_bytes()
    tree = ast.parse(content, filename=str(path))
    symbol = ROBOT_CONFIG_SYMBOLS[robot_type]
    assignment = next((node for node in ast.walk(tree)
                       if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == symbol for target in node.targets)), None)
    if assignment is None or not isinstance(assignment.value, ast.Call):
        raise ValueError(f"{symbol}: ArticulationCfg assignment not found")
    call = assignment.value
    actuator_keyword = next((keyword for keyword in call.keywords if keyword.arg == "actuators"), None)
    if actuator_keyword is None or not isinstance(actuator_keyword.value, ast.Dict):
        raise ValueError(f"{symbol}: literal actuators mapping not found")
    groups = {}
    for key_node, value_node in zip(actuator_keyword.value.keys, actuator_keyword.value.values):
        group = _literal(key_node)
        if not isinstance(group, str) or not isinstance(value_node, ast.Call):
            continue
        keywords = {keyword.arg: _literal(keyword.value) for keyword in value_node.keywords}
        groups[group] = {
            key: (_numeric(value) if key not in {"joint_names_expr"} else value)
            for key, value in keywords.items()
            if key in {"joint_names_expr", "effort_limit", "saturation_effort", "velocity_limit",
                       "effort_limit_sim", "velocity_limit_sim", "stiffness", "damping", "min_delay", "max_delay"}
        }
    if not groups:
        raise ValueError(f"{symbol}: no actuator groups extracted")
    return {
        "schema_id": "eai.robot_config_inspection", "schema_version": "1.0",
        "robot_key": robot_type, "config_symbol": symbol,
        "source": {"locator": str(path), "sha256": hashlib.sha256(content).hexdigest(),
                   "size_bytes": len(content)},
        "actuators": groups,
        "evidence": {"tier": "E1", "status": "source_literal_extracted",
                     "limitations": ["does not import/load Isaac Lab", "does not prove USD or runtime controller availability"]},
    }


__all__ = ["ROBOT_CONFIG_SOURCES", "inspect_robot_config"]
