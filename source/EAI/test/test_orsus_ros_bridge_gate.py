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

    assert "setup_pending_orsus_ros_graphs" in gated_calls
    simulator_text = SIMULATOR_SOURCE.read_text(encoding="utf-8")
    assert "OrsusOdometryManager" not in simulator_text
    assert "attach_orsus_odometry_manager" not in simulator_text


def test_bridge_disabled_sets_import_time_orsus_ros_guard_before_env_load() -> None:
    tree = ast.parse(SIMULATOR_SOURCE.read_text(encoding="utf-8"))
    session = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "open_simulator_session"
    )
    guard = next(
        node
        for node in session.body
        if isinstance(node, ast.If)
        and any(
            isinstance(child, ast.Assign)
            and any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and isinstance(target.value.value, ast.Name)
                and target.value.value.id == "os"
                and target.value.attr == "environ"
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "EAI_DISABLE_ORSUS_ROS_ENV"
                for target in child.targets
            )
            for child in node.body
        )
    )
    assert ast.unparse(guard.test) == (
        "config.disable_orsus_ros_env or not config.enable_ros_bridge_extension"
    )
    load_index = next(
        index
        for index, node in enumerate(session.body)
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "_load_env_cfg"
            for child in ast.walk(node)
        )
    )
    assert session.body.index(guard) < load_index


def test_bridge_disabled_propagates_orsus_ros_guard_to_preflight() -> None:
    tree = ast.parse(SIMULATOR_SOURCE.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_session_preflight_args"
    )
    returned = next(node.value for node in helper.body if isinstance(node, ast.Return))
    assert isinstance(returned, ast.Call)
    keyword = next(
        item for item in returned.keywords if item.arg == "disable_orsus_ros_env"
    )
    assert ast.unparse(keyword.value) == (
        "config.disable_orsus_ros_env or not config.enable_ros_bridge_extension"
    )
