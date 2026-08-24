from pathlib import Path

from EAI.interface_catalog.loader import load_catalog
from EAI.interface_catalog.query import resolve_scene_interfaces


ROOT = Path(__file__).resolve().parents[3]


def _orsus_ids() -> set[str]:
    selection = {
        "robots": [{
            "type": "carter",
            "attachments": [{"type": "orsus"}, {"type": "navigation_io"}],
        }]
    }
    return {
        item.interface_id
        for item in resolve_scene_interfaces(load_catalog(), selection, env_name="orsus-boundary")
    }


def test_orsus_catalog_contains_only_direct_navigation_outputs() -> None:
    ids = _orsus_ids()
    assert {"ros.orsus.point_cloud", "ros.orsus.odometry"} <= ids
    assert "ros.orsus.scan" not in ids
    assert not any(interface_id.endswith(".scan") for interface_id in ids)


def test_scan_is_owned_by_external_nav2_pipeline() -> None:
    launch = (ROOT / "algorithm/nav2/nav2.launch.py").read_text(encoding="utf-8")
    bridge = (ROOT / "algorithm/nav2/tf_bridge.py").read_text(encoding="utf-8")
    assert 'package="pointcloud_to_laserscan"' in launch
    assert '("cloud_in", f"/{robot}/scan_cloud")' in launch
    assert '("scan", f"/{robot}/scan")' in launch
    assert 'cloud_out = f"/{self.robot}/scan_cloud"' in bridge


def test_public_docs_do_not_attribute_scan_to_orsus_runtime() -> None:
    for relative in (
        "docs/source/interface_catalog.md",
        "docs/source/interface_catalog_en.md",
        "docs/source/project_overview.md",
        "docs/source/project_overview_en.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "ros.orsus.scan" not in text
        assert "pointcloud_to_laserscan" in text
