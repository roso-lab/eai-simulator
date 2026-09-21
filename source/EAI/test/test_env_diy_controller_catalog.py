from pathlib import Path

from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import resolve_selection


def _selection(robot_type: str, cfg: str) -> dict:
    return {
        "task_name": "controller_catalog_test",
        "scene_key": "factory",
        "robots": [
            {
                "type": robot_type,
                "controller": {"mode": "manual", "cfg": cfg},
                "attachments": [],
                "visual": {"x": 0.0, "y": 0.0},
            }
        ],
    }


def test_quadruped_controller_choices_are_robot_scoped():
    assert catalog.controller_cfgs_for_robot("b2") == (
        "B2_VELOCITY_RSL_CFG",
        "B2_MIXED_STAIRS_CFG",
    )
    assert catalog.controller_cfgs_for_robot("go2") == (
        "GO2_VELOCITY_RSL_CFG",
        "GO2_MIXED_STAIRS_CFG",
    )


def test_b2_mixed_controller_resolves_232d_policy_asset():
    graph = resolve_selection(_selection("b2", "B2_MIXED_STAIRS_CFG"))
    requirement = next(item for item in graph.requirements if item.id == "controller:B2_MIXED_STAIRS_CFG")
    assert requirement.relative_paths[-1] == "rl/b2_rsl_rl/model/policy.mixed_stairs.onnx"
    model = Path(__file__).resolve().parents[3] / "source" / "EAI_assets" / "EAI_assets" / "controller" / requirement.relative_paths[-1]
    assert model.is_file()


def test_mixed_stairs_scanner_binds_each_robot_trunk_link():
    """The height scanner must attach to each robot's own trunk prim.

    go2 names its trunk body ``base`` while b2 names it ``base_link``; a
    hardcoded ``/base`` prim path made the B2 mixed-stairs build fail inside
    Isaac Lab's RayCaster at runtime. Importing env_builder requires Isaac, so
    this guards the wiring at the source level.
    """
    source = Path(__file__).resolve().parents[3] / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py"
    text = source.read_text(encoding="utf-8")
    assert 'trunk_link="base"' in text
    assert 'trunk_link="base_link"' in text
    assert 'prim_path=f"{{ENV_REGEX_NS}}/{name}/{robot.trunk_link}"' in text
    assert 'prim_path=f"{{ENV_REGEX_NS}}/{name}/base"' not in text
