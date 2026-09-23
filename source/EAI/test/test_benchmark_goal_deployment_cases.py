"""Every authored negative case needs a witness-independent private proof."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_deployment_cases import build_deployment_cases
from demo.benchmark.goal_deployment_feasibility import evaluate_deployment_feasibility
from demo.benchmark.goal_driven_contract import build_goal_participant_api, validate_goal_task_spec
from demo.benchmark.goal_driven_tasks import build_goal_task_package


@pytest.mark.parametrize("scene", ["factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant"])
def test_five_scenes_have_initial_exit_and_degradation_cases(scene):
    original = build_goal_task_package(scene)["public_task"]
    preserved = deepcopy(original)
    package = build_deployment_cases(scene)
    assert len(package["cases"]) == 3
    for case in package["cases"]:
        spec = case["public_task"]
        validate_goal_task_spec(spec)
        api = build_goal_participant_api(spec)
        assert "deployment_assessment" in api["action_types"]
        assert spec["required_outcomes"] == original["required_outcomes"]
        assert "private_evaluation" not in spec
        private = case["private_evaluation"]
        pool = deepcopy(spec["candidate_robot_pool"])
        event = private["external_event"]
        base_args = dict(episode_id="fixture", assessment_id="a0", assessment_epoch=0,
                         roster_version=0, platform_valid=True,
                         platform_evidence_refs=["declared-contract-fixture-check"])
        def evaluate(robots, **kwargs):
            return evaluate_deployment_feasibility(
                spec, current_robot_pool=robots, capability_versions={r["id"]: 0 for r in robots},
                **{**base_args, **kwargs})
        initial = evaluate(pool)
        assert initial["label"] == private["initial_internal_diagnostic"]
        assert private["oracle_status"] == "failed" and private["feasibility_label"] is None
        if private["removed_capability"] == "observe":
            assert evaluate(pool, supplementary_capabilities=["observe"])["label"] == "unverified"
        if event:
            if event["type"] == "member_exit":
                pool = [r for r in pool if r["id"] != event["agent_id"]]
            else:
                next(r for r in pool if r["id"] == event["agent_id"])["public_capabilities"] = event["remaining_capabilities"]
            pending = sorted({r["outcome_id"] for r in private["requirements"]["conditions"]})
            after = evaluate(pool, assessment_id="a1", assessment_epoch=1, roster_version=1,
                             pending_requirement_outcome_ids=pending)
            assert after["label"] == private["post_event_internal_diagnostic"]
        assert case["execution_validated"] is case["business_outcomes_validated"] is case["scoreable"] is False
    assert original == preserved
