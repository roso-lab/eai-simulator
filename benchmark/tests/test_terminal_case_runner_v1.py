from __future__ import annotations

from pathlib import Path

import pytest

from benchmark.case_manifest_v1 import bind_case_manifest
from tools.benchmark_branch.models import BranchJob, canonical_digest
from benchmark.terminal_case_runner_v1 import TerminalCaseRunnerError, seal_terminal_artifacts
from benchmark.evaluator.room06_livt04 import (
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    Room06LivT04Evaluator,
    build_diagnostic_runtime_fixture,
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
    plan = {"seed": 20261027, "oracle_obligation_binding": BOUNDING}
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
    plan = {"seed": 20261027, "oracle_obligation_binding": BOUNDING}
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
