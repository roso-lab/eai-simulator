import ast
from pathlib import Path

from EAI.interface_catalog.loader import load_catalog
from EAI.interface_catalog.query import resolve_scene_interfaces


ROOT = Path(__file__).resolve().parents[3]


def _ids(attachments: list[str]) -> set[str]:
    selection = {
        "robots": [{
            "type": "carter",
            "attachments": [{"type": item} for item in attachments],
        }]
    }
    return {
        item.interface_id
        for item in resolve_scene_interfaces(load_catalog(), selection, env_name="lidar-gate")
    }


def test_standalone_lidar_interfaces_require_navigation_io() -> None:
    lidar_ids = {"ros.lidar.point_cloud", "ros.lidar.odometry"}
    assert _ids(["lidar"]).isdisjoint(lidar_ids)
    assert lidar_ids <= _ids(["lidar", "navigation_io"])
    assert _ids(["navigation_io"]).isdisjoint(lidar_ids)


def test_builder_passes_navigation_gate_to_standalone_lidar() -> None:
    source = (ROOT / "source/EAI_hmrs/EAI_hmrs/env_builder.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RosLidarCfg"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    gate = keywords["enable_ros_publish"]
    assert isinstance(gate, ast.Name) and gate.id == "navigation_io_enabled"


def test_lidar_spawn_gate_deactivates_graph_before_ros_setup() -> None:
    path = ROOT / "source/EAI_assets/EAI_assets/sensor/low_sensor/ros_lidar.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    spawn = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "spawn_and_fix_ros_lidar"
    )
    calls = [
        node for node in ast.walk(spawn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert any(node.func.attr == "SetActive" for node in calls)
    spawn_source = ast.get_source_segment(source, spawn)
    assert "if not enable_ros_publish:" in spawn_source
    assert "create_render_product=False" in spawn_source
    gate_branch = spawn_source.split("if not enable_ros_publish:", 1)[1]
    assert gate_branch.index("create_render_product=False") < gate_branch.index("continue")


def test_standalone_lidar_preloads_rtx_extensions_and_rendering() -> None:
    source = (ROOT / "simulator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    launcher_args = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_app_launcher_args"
    )
    launcher_source = ast.get_source_segment(source, launcher_args)
    assert 'has_standalone_lidar = _selection_has_attachment(effective_selection, "lidar")' in launcher_source
    assert "if has_orsus or has_standalone_lidar:" in launcher_source
    assert 'args["enable_cameras"] = True' in launcher_source
    assert "_RTX_LIDAR_PRELOAD_KIT_ARGS" in launcher_source


def test_physical_lidar_creation_can_skip_ros_render_product() -> None:
    path = ROOT / "source/EAI_assets/EAI_assets/sensor/low_sensor/ros_lidar.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    create = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_create_rtx_lidar_render_product"
    )
    create_source = ast.get_source_segment(source, create)
    assert "create_render_product: bool = True" in create_source
    assert "if not create_render_product:" in create_source
    assert create_source.index("if not create_render_product:") < create_source.index("rep.create.render_product(")


def test_runtime_repairs_legacy_odometry_execution_and_context() -> None:
    path = ROOT / "source/EAI_assets/EAI_assets/sensor/low_sensor/ros_lidar.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    repair = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_repair_ros_lidar_odometry_graph"
    )
    repair_source = ast.get_source_segment(source, repair)
    assert 'f"{publish_node}.inputs:execIn": f"{compute_node}.outputs:execOut"' in repair_source
    assert 'f"{publish_node}.inputs:context": f"{context_node}.outputs:context"' in repair_source
    assert "attribute.SetConnections([source])" in repair_source


def test_lidar_cfg_propagates_gate_to_spawn_cfg() -> None:
    source = (ROOT / "source/EAI_assets/EAI_assets/sensor/low_sensor/ros_lidar.py").read_text(encoding="utf-8")
    assert "class RosLidarSpawnCfg" in source
    assert "self.spawn.enable_ros_publish = self.enable_ros_publish" in source
