#!/usr/bin/env python3
"""nav2_setup.py 插件名自动适配的单元测试。

用临时目录伪造 ROS2 安装前缀（share/<pkg>/package.xml + 插件声明 XML），
验证参数文本按本机声明的查找名改写：
  - Humble 风格 <class name="pkg/Name">（斜杠查找名）→ 改写为斜杠名
  - Jazzy 风格 <class type="pkg::Name">（无 name 属性）→ 保持双冒号名
  - 包未安装/声明缺失 → 保持模板原样
  - 非插件参数（如 amcl 运动模型）不受影响

运行：python3 -m pytest algorithm/ros/nav2/test_nav2_plugin_names.py
"""

import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, THIS_DIR)

import nav2_setup  # noqa: E402


def make_prefix(root, package, classes, *, with_name):
    """在 root 下伪造一个 ROS2 包：classes 为 (type, name) 列表。

    with_name=True 模拟 Humble（<class name="pkg/Name">），False 模拟 Jazzy
    （只声明 type 属性）。
    """
    pkg_share = root / "share" / package
    pkg_share.mkdir(parents=True)
    lines = [f'<library path="{package}">']
    for cls_type, cls_name in classes:
        attrs = f'type="{cls_type}"'
        if with_name:
            attrs = f'name="{cls_name}" ' + attrs
        lines.append(f"<class {attrs} base_class_type=\"nav2_core::GlobalPlanner\"></class>")
    lines.append("</library>")
    (pkg_share / "plugins.xml").write_text("\n".join(lines), encoding="utf-8")
    (pkg_share / "package.xml").write_text(
        f'<?xml version="1.0"?><package format="3"><name>{package}</name>'
        f"<version>0.0.0</version><description>t</description>"
        f"<maintainer email='a@b.c'>t</maintainer><license>Apache-2.0</license>"
        f'<export><nav2_core plugin="${{prefix}}/plugins.xml"/></export></package>',
        encoding="utf-8",
    )


def test_humble_slash_names_are_rewritten(tmp_path):
    root = tmp_path / "humble_prefix"
    make_prefix(
        root,
        "nav2_navfn_planner",
        [("nav2_navfn_planner::NavfnPlanner", "nav2_navfn_planner/NavfnPlanner")],
        with_name=True,
    )
    text = 'planner_server:\n  GridBased:\n    plugin: "nav2_navfn_planner::NavfnPlanner"\n'
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    assert 'plugin: "nav2_navfn_planner/NavfnPlanner"' in adapted
    assert overrides == {"nav2_navfn_planner::NavfnPlanner": "nav2_navfn_planner/NavfnPlanner"}


def test_jazzy_colon_names_are_kept(tmp_path):
    root = tmp_path / "jazzy_prefix"
    make_prefix(
        root,
        "nav2_navfn_planner",
        [("nav2_navfn_planner::NavfnPlanner", None)],
        with_name=False,
    )
    text = '    plugin: "nav2_navfn_planner::NavfnPlanner"\n'
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    assert adapted == text
    assert overrides == {}


def test_uninstalled_package_keeps_canonical_name(tmp_path):
    text = '    plugin: "nav2_navfn_planner::NavfnPlanner"\n'
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(tmp_path / "empty")])
    assert adapted == text
    assert overrides == {}


def test_unquoted_yaml_values_are_rewritten(tmp_path):
    root = tmp_path / "humble_prefix"
    make_prefix(
        root,
        "nav2_smac_planner",
        [
            ("nav2_smac_planner::SmacPlannerHybrid", "nav2_smac_planner/SmacPlannerHybrid"),
        ],
        with_name=True,
    )
    text = "    plugin: nav2_smac_planner::SmacPlannerHybrid\n"
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    assert adapted == "    plugin: nav2_smac_planner/SmacPlannerHybrid\n"
    assert overrides == {"nav2_smac_planner::SmacPlannerHybrid": "nav2_smac_planner/SmacPlannerHybrid"}


def test_primary_controller_is_rewritten(tmp_path):
    root = tmp_path / "humble_prefix"
    make_prefix(
        root,
        "dwb_core",
        [("dwb_core::DWBLocalPlanner", "dwb_core/DWBLocalPlanner")],
        with_name=True,
    )
    text = "    primary_controller: dwb_core::DWBLocalPlanner\n"
    adapted, _ = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    assert adapted == "    primary_controller: dwb_core/DWBLocalPlanner\n"


def test_non_plugin_parameters_are_untouched(tmp_path):
    root = tmp_path / "humble_prefix"
    make_prefix(root, "nav2_navfn_planner", [], with_name=False)
    text = (
        "amcl:\n  robot_model_type: nav2_amcl::DifferentialMotionModel\n"
        '    waypoint_task_executor_plugin: "wait_at_waypoint"\n'
        "    controller_plugins: [\"FollowPath\"]\n"
    )
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    assert adapted == text
    assert overrides == {}


def test_behavior_plugins_map_to_declared_names(tmp_path):
    root = tmp_path / "humble_prefix"
    behaviors = [
        ("nav2_behaviors::Spin", "nav2_behaviors/Spin"),
        ("nav2_behaviors::BackUp", "nav2_behaviors/BackUp"),
        ("nav2_behaviors::DriveOnHeading", "nav2_behaviors/DriveOnHeading"),
        ("nav2_behaviors::Wait", "nav2_behaviors/Wait"),
        ("nav2_behaviors::AssistedTeleop", "nav2_behaviors/AssistedTeleop"),
    ]
    make_prefix(root, "nav2_behaviors", behaviors, with_name=True)
    text = "".join(f'    plugin: "{cls_type}"\n' for cls_type, _ in behaviors)
    adapted, overrides = nav2_setup.adapt_plugin_names(text, prefixes=[str(root)])
    for cls_type, cls_name in behaviors:
        assert f'plugin: "{cls_name}"' in adapted
        assert overrides[cls_type] == cls_name
