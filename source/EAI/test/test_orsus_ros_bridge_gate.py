from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SIMULATOR_SOURCE = ROOT / "simulator.py"


def test_orsus_ros_setup_is_nested_under_bridge_gate() -> None:
    tree = ast.parse(SIMULATOR_SOURCE.read_text(encoding="utf-8"))
    session = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "open_simulator_session"
    )
    gated_calls: set[str] = set()
    for node in ast.walk(session):
        if not isinstance(node, ast.If):
            continue
        if not (
            isinstance(node.test, ast.Attribute)
            and isinstance(node.test.value, ast.Name)
            and node.test.value.id == "config"
            and node.test.attr == "enable_ros_bridge_extension"
        ):
            continue
        for child in node.body:
            for descendant in ast.walk(child):
                if isinstance(descendant, ast.Call):
                    if isinstance(descendant.func, ast.Name):
                        gated_calls.add(descendant.func.id)
                    elif isinstance(descendant.func, ast.Attribute):
                        gated_calls.add(descendant.func.attr)

    assert "OrsusOdometryManager" in gated_calls
    assert "setup_pending_orsus_ros_graphs" in gated_calls
    assert "attach_orsus_odometry_manager" in gated_calls
