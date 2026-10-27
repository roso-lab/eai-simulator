from __future__ import annotations

from pathlib import Path
import hashlib

import pytest

from benchmark.case_manifest_v1 import bind_case_manifest
from tools.benchmark_branch.models import BranchJob, canonical_digest
from benchmark.terminal_case_runner_v1 import TerminalCaseRunnerError, seal_terminal_artifacts
from benchmark.evaluator.room06_livt04 import (
    DIAGNOSTIC_RUNTIME_BINDING,
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    Room06LivT04Evaluator,
    build_diagnostic_runtime_fixture,
)
from benchmark.d4_first_event_v1 import (
    FACT_KEY as D4_FACT_KEY,
    OBSERVATION_SCHEMA as D4_OBSERVATION_SCHEMA,
    PLAN_KEY as D4_PLAN_KEY,
    REGISTRATION_SCHEMA as D4_REGISTRATION_SCHEMA,
    VERSION as D4_VERSION,
)


BOUNDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1",
    "anchor_id": "liv-t-04",
    "obligations": [{
        "obligation_id": "relay",
        "relation": "P2",
        "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"],
    }],
}


def _job() -> BranchJob:
    return BranchJob(
        run_id="room06-diagnostic-run",
        system_id="emos-zero",
        scene_key="room_06",
        task_id="liv-t-04",
        condition_id="C0",
        slot=0,
        branch_type="fact",
        branch_id="room06-diagnostic-r1",
        pair_group_id="room06-diagnostic-r1",
        repeat_id="r1",
        execution_scope="measured_diagnostic",
    )


def _d4_job() -> BranchJob:
    job = _job()
    return BranchJob(
        run_id=job.run_id,
        system_id=job.system_id,
        scene_key=job.scene_key,
        task_id=job.task_id,
        condition_id="task_function_loss",
        slot=job.slot,
        branch_type=job.branch_type,
        branch_id="room06-diagnostic-d4-r1",
        pair_group_id="room06-diagnostic-d4-r1",
        repeat_id=job.repeat_id,
        execution_scope=job.execution_scope,
    )


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _d4_plan() -> dict:
    registration = {
        "schema": D4_REGISTRATION_SCHEMA,
        "version": D4_VERSION,
        "panels": [{
            "panel_id": "room06-task-loss",
            "registration_id": "room06-task-loss-v1",
            "system_id": "emos-zero",
            "event_id": "task-function-loss",
            "condition_id": "task_function_loss",
            "onset_tick": 100,
            "horizon_tick": 200,
            "evaluator_stride_ticks": 5,
            "cells": [{
                "cell_id": "room06-diagnostic-cell",
                "weight": 1.0,
                "response_required": True,
                "response_deadline_tick": 130,
                "expected_trajectories": [{
                    "base_instance_id": "room06-livt04-diagnostic/base",
                    "repeat_id": "r1",
                }],
            }],
        }],
    }
    registration["registration_digest"] = canonical_digest({
        key: value for key, value in registration.items() if key != "registration_digest"
    })
    return {
        "seed": 20261027,
        "oracle_obligation_binding": BOUNDING,
        "room06_runtime_binding": dict(DIAGNOSTIC_RUNTIME_BINDING),
        D4_PLAN_KEY: registration,
    }


class _ExplicitMaterialEvaluator:
    @staticmethod
    def collect_receipt_material(runtime, job):
        return runtime


def _d4_material(*, include_observation: bool = True) -> dict:
    job = _d4_job()
    runtime = build_diagnostic_runtime_fixture()
    runtime["episode"]["condition_id"] = job.condition_id
    material = Room06LivT04Evaluator().collect_receipt_material(runtime, job)
    material["episode"]["system_id"] = job.system_id
    if include_observation:
        material["facts"][D4_FACT_KEY] = {
            "schema": D4_OBSERVATION_SCHEMA,
            "registration_id": "room06-task-loss-v1",
            "panel_id": "room06-task-loss",
            "cell_id": "room06-diagnostic-cell",
            "event_id": "task-function-loss",
            "event_eligible": True,
            "eligibility_evidence_digest": _sha("eligible"),
            "response": {"tick": 120, "evidence_digest": _sha("response")},
            "work_confirmation": {"tick": 115, "evidence_digest": _sha("work")},
            "terminal": None,
            "safety": {"flag": False, "evidence_digest": None},
            "observed_through_tick": 200,
            "timeline_digest": _sha("timeline"),
        }
    return material


def _case(catalog: dict, plan: dict) -> dict:
    return bind_case_manifest({
        "schema": "heterocoord.case-manifest.v1",
        "case_id": "room06-livt04-diagnostic",
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "domain": "living_public",
        "relation_signature": ["P2", "P3-J"],
        "base_instance_id": "room06-livt04-diagnostic/base",
        "scene": {
            "scene_id": "room_06",
            "scene_digest": "1" * 64,
            "semantic_map_digest": "2" * 64,
            "topology_digest": "3" * 64,
            "frame": "world",
            "units": "meters+ticks",
            "entity_ids": ["book_batch_01", "heavy_object"],
            "zone_ids": [],
            "horizon": 15000,
            "tau_stable": 25,
        },
        "robot_pool": {
            "pool_id": "diagnostic-pool",
            "roster_digest": "4" * 64,
            "robots": [
                {
                    "robot_id": "carrier-0",
                    "asset_digest": "5" * 64,
                    "capability_digest": "6" * 64,
                    "initial_state_digest": "7" * 64,
                },
                {
                    "robot_id": "carrier-1",
                    "asset_digest": "5" * 64,
                    "capability_digest": "6" * 64,
                    "initial_state_digest": "7" * 64,
                },
            ],
        },
        "contract": {
            "contract_digest": "8" * 64,
            "oracle_id": "liv-t-04-oracle-v3",
            "contract_revision": "diagnostic-2026-10-27",
        },
        "catalog_digest": canonical_digest(catalog),
        "plan_digest": canonical_digest(plan),
        "replay": {
            "serializer_version": "diagnostic-v1",
            "rng_stream_key": "room06-diagnostic",
            "checkpoint_schema": "diagnostic-v1",
        },
        "evaluator": {"evaluator_id": EVALUATOR_ID, "evaluator_version": EVALUATOR_VERSION},
    })


def test_seal_terminal_artifact_builds_readiness_and_preserves_gates(tmp_path: Path) -> None:
    catalog = {"anchors": ["liv-t-04"]}
    plan = {
        "seed": 20261027,
        "oracle_obligation_binding": BOUNDING,
        "room06_runtime_binding": dict(DIAGNOSTIC_RUNTIME_BINDING),
    }
    case = _case(catalog, plan)
    result = {
        "status": "valid_terminal",
        "completed": True,
        "physical_success": True,
        "terminal_predicate": True,
        "safety_predicate": True,
        "feasibility_predicate": True,
    }
    sealed = seal_terminal_artifacts(
        terminals=[{
            "job": _job(),
            "evaluator": Room06LivT04Evaluator(),
            "runtime": {**build_diagnostic_runtime_fixture(), "obligation_binding": BOUNDING},
            "evaluator_result": result,
        }],
        artifact_root=tmp_path,
        case_manifest=case,
        catalog=catalog,
        plan=plan,
        parameters={"alpha": 0.05},
    )
    assert sealed["readiness"]["case_receipt_ready"] is True
    assert sealed["readiness"]["paper_support_ready"] is False
    assert sealed["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }
    assert len(sealed["artifact_manifest"]) == 1
    assert len(sealed["receipts"]) == 1
    assert (tmp_path / sealed["artifact_manifest"]["evaluator-room06-diagnostic-r1"]["path"]).is_file()


def test_seal_does_not_infer_missing_evaluator_verdict(tmp_path: Path) -> None:
    catalog = {"anchors": ["liv-t-04"]}
    plan = {
        "seed": 20261027,
        "oracle_obligation_binding": BOUNDING,
        "room06_runtime_binding": dict(DIAGNOSTIC_RUNTIME_BINDING),
    }
    with pytest.raises(TerminalCaseRunnerError, match="terminal evidence sealing failed"):
        seal_terminal_artifacts(
            terminals=[{
                "job": _job(),
                "evaluator": Room06LivT04Evaluator(),
                "runtime": build_diagnostic_runtime_fixture(),
                "evaluator_result": {"status": "valid_terminal"},
            }],
            artifact_root=tmp_path,
            case_manifest=_case(catalog, plan),
            catalog=catalog,
            plan=plan,
            parameters={},
        )


def test_terminal_material_passes_explicit_d4_observation_without_inference(tmp_path: Path) -> None:
    catalog = {"anchors": ["liv-t-04"]}
    plan = _d4_plan()
    case = _case(catalog, plan)
    sealed = seal_terminal_artifacts(
        terminals=[{
            "job": _d4_job(),
            "evaluator": _ExplicitMaterialEvaluator(),
            "runtime": _d4_material(),
            "evaluator_result": {
                "status": "valid_terminal",
                "completed": True,
                "physical_success": True,
                "terminal_predicate": True,
                "safety_predicate": True,
                "feasibility_predicate": True,
            },
        }],
        artifact_root=tmp_path,
        case_manifest=case,
        catalog=catalog,
        plan=plan,
        parameters={"alpha": 0.05},
    )
    assert sealed["receipts"][0]["facts"][D4_FACT_KEY]["response"]["tick"] == 120
    assert sealed["readiness"]["d4_first_event_registered"] is True
    assert sealed["readiness"]["d4_first_event_eligible_trajectory_count"] == 1
    assert sealed["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }


def test_terminal_registration_rejects_missing_explicit_d4_observation(tmp_path: Path) -> None:
    catalog = {"anchors": ["liv-t-04"]}
    plan = _d4_plan()
    with pytest.raises(TerminalCaseRunnerError, match="exact roster is incomplete"):
        seal_terminal_artifacts(
            terminals=[{
                "job": _d4_job(),
                "evaluator": _ExplicitMaterialEvaluator(),
                "runtime": _d4_material(include_observation=False),
                "evaluator_result": {
                    "status": "valid_terminal",
                    "completed": True,
                    "physical_success": True,
                    "terminal_predicate": True,
                    "safety_predicate": True,
                    "feasibility_predicate": True,
                },
            }],
            artifact_root=tmp_path,
            case_manifest=_case(catalog, plan),
            catalog=catalog,
            plan=plan,
            parameters={},
        )
