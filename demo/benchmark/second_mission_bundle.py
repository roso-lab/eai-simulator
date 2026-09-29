"""Closed authoring bundle shared by the second scene missions.

These packages sit beside the frozen GOAL-01 builders. They do not enter the
9x9 suite, and a later decision to admit them to the 405-package matrix is a
separate pre-registration. Their public tasks must not store or depend on a
GOAL-01 content hash. Each required outcome is one externally checkable
business fact. Release gates stay closed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def assemble_unscored_goal_bundle(public: dict[str, Any], witnesses: list[Mapping[str, Any]],
                                  blockers: list[str]) -> dict[str, dict[str, Any]]:
    from demo.benchmark.goal_driven_contract import (
        GOAL_BASELINE_SCHEMA, GOAL_CONTRACT_VERSION, GOAL_HIDDEN_EVALUATOR_SCHEMA,
        build_goal_heterogeneity_certificate, build_goal_participant_api,
        validate_goal_bundle, validate_goal_hidden_evaluator,
    )
    from demo.benchmark.goal_driven_tasks import (
        _apply_factor, _build_hidden_event_schedule, _legacy_measurement_contract,
        review_goal_task_design,
    )
    from demo.benchmark.provenance import canonical_sha256

    _apply_factor(public, "communication_quality", None)
    review = review_goal_task_design(public, witnesses)
    hidden = {
        "schema": GOAL_HIDDEN_EVALUATOR_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "outcome_predicates": [
            {
                "id": outcome["id"],
                "evidence_types": ["business_state", *outcome["evidence_expectations"]],
                "predicate": f"hidden_business_predicate:{outcome['id']}",
                "failure_codes": ["missing_evidence", "inconsistent_identity", "business_state_not_reached"],
            }
            for outcome in public["required_outcomes"]
        ],
        "witness_plans": deepcopy(witnesses),
        "heterogeneity_certificate": build_goal_heterogeneity_certificate(public, witnesses),
        "hidden_event_schedule": _build_hidden_event_schedule(public),
        "factor_effect": {
            "changed_dimension": public["experiment_factor"]["id"],
            "frozen_inputs": list(public["experiment_factor"]["frozen_inputs"]),
            "effect_status": "execution_pending",
        },
        "truth_sources": {
            "scene_objects": "immutable scene/object binding",
            "observations": "runtime frame and message provenance",
            "team_events": "runtime lifecycle callback and capability readback",
            "business_state": "scene-owned identity, service, handoff and access predicates",
            "motion_evidence": "measured pose and object trajectory records",
        },
        "measurement_contract": _legacy_measurement_contract(public),
        "measurement_status": "unclassified",
        "mechanism_template": None,
        "team_necessity_certificate": None,
        "release_gate": {
            "authoring_status": review["authoring_status"],
            "execution_status": review["execution_status"],
            "benchmark_ready": False,
            "scoreable": False,
            "blockers": list(blockers),
        },
    }
    validate_goal_hidden_evaluator(hidden, task_spec=public)
    baseline = {
        "schema": GOAL_BASELINE_SCHEMA,
        "version": GOAL_CONTRACT_VERSION,
        "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "role": "optional participant-visible planning baselines; not normative answers",
        "solvers": [
            {"id": "capability_first", "version": 1, "behavior": "choose a legal team and submit a capability-matched plan"},
            {"id": "message_aware_replan", "version": 1, "behavior": "replan only after visible communication or team events"},
        ],
        "limitations": ["No optimality claim.", "No witness assignment is published."],
    }
    bundle = {"public_task": public, "participant_api": build_goal_participant_api(public),
              "hidden_evaluator": hidden, "baseline_manifest": baseline}
    validate_goal_bundle(bundle)
    return bundle
