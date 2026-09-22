"""Offline checks for the four-scene M0 mechanism-template matrix."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_contract import validate_goal_bundle
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    generate_canonical_goal_task_packages,
)
from demo.benchmark.goal_suite import verify_goal_mechanism_suite, write_goal_mechanism_suite
from demo.benchmark.mechanism_templates import (
    M0_MECHANISMS,
    build_mechanism_template,
    build_team_necessity_certificate,
    canonical_template_id,
    registered_mechanism_templates,
    validate_mechanism_template,
    validate_team_necessity_certificate,
)


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city")


def test_registry_has_one_validated_authoring_template_per_scene_and_m0_mechanism():
    rows = registered_mechanism_templates()
    assert len(rows) == 16
    assert {row["template_id"] for row in rows} == {
        canonical_template_id(scene, mechanism)
        for scene in SCENES for mechanism in M0_MECHANISMS
    }
    assert all(row["execution_status"] == "external_pending" for row in rows)
    assert all(row["scoreable"] is False for row in rows)
    assert all(validate_mechanism_template(row)["validated"] for row in rows)


@pytest.mark.parametrize("scene", SCENES)
@pytest.mark.parametrize("mechanism", M0_MECHANISMS)
def test_generated_bundle_is_classified_and_bound_to_template(scene, mechanism):
    bundle = build_goal_task_package(scene, mechanism_family=mechanism)
    assert validate_goal_bundle(bundle)["validated"]
    hidden = bundle["hidden_evaluator"]
    template = hidden["mechanism_template"]
    assert hidden["measurement_status"] == "classified"
    assert template["template_id"] == canonical_template_id(scene, mechanism)
    assert hidden["team_necessity_certificate"]["status"] == "unknown"
    assert hidden["team_necessity_certificate"]["whole_task_necessity_validated"] is False
    assert template["measurement_contract"] == hidden["measurement_contract"]


def test_canonical_generation_returns_sixteen_seed_and_factor_independent_ids():
    packages = generate_canonical_goal_task_packages()
    assert set(packages) == {
        canonical_template_id(scene, mechanism)
        for scene in SCENES for mechanism in M0_MECHANISMS
    }
    immediate = generate_canonical_goal_task_packages(factor_level="immediate")
    delayed = generate_canonical_goal_task_packages(factor_level="delayed")
    assert set(immediate) == set(delayed)
    for template_id in immediate:
        assert immediate[template_id]["hidden_evaluator"]["mechanism_template"]["template_id"] == template_id
        assert immediate[template_id]["hidden_evaluator"]["measurement_contract"] != {}


def test_necessity_certificate_is_three_state_and_fails_closed():
    template_id = canonical_template_id("factory", "role_allocation")
    unknown = build_team_necessity_certificate(
        scene_key="factory", template_id=template_id, mechanism_family="role_allocation")
    assert unknown["status"] == "unknown"
    assert validate_team_necessity_certificate(unknown)["validated"]

    with pytest.raises(ValueError, match="evidence_refs"):
        build_team_necessity_certificate(
            scene_key="factory", template_id=template_id,
            mechanism_family="role_allocation", status="validated")

    disproven = build_team_necessity_certificate(
        scene_key="factory", template_id=template_id, mechanism_family="role_allocation",
        status="disproven", evidence_refs=["counterexample-1"],
        counterexample={"team": ["ground_only"], "outcome": "completed"},
        unknown_reason="")
    assert disproven["status"] == "disproven"
    forged = deepcopy(disproven)
    forged["physical_execution_validated"] = True
    with pytest.raises(ValueError, match="physical"):
        validate_team_necessity_certificate(forged)


def test_mechanism_suite_publishes_and_roundtrips_sixteen_templates(tmp_path):
    root = tmp_path / "m0"
    result = write_goal_mechanism_suite(root)
    assert result == {
        "validated": True,
        "canonical_template_count": 16,
        "execution_validated": False,
        "scoreable": False,
    }
    assert verify_goal_mechanism_suite(root)["canonical_template_count"] == 16

