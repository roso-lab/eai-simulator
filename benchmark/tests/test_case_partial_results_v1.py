from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.case_manifest_v1 import bind_case_manifest
from benchmark.case_partial_results_v1 import (
    CasePartialResultsError,
    compute_partial_results,
    normalize_partial_case_receipts,
    run_partial_case,
    verify_partial_export,
)
from benchmark.evaluator.room06_livt04 import (
    DIAGNOSTIC_RUNTIME_BINDING,
    EVALUATOR_ID,
    EVALUATOR_VERSION,
    Room06LivT04Evaluator,
    build_diagnostic_runtime_fixture,
)
from benchmark.receipt_adapter_v3 import ReceiptAdapterError, normalize_receipts
from benchmark.records_v3 import canonical_digest
from benchmark.terminal_case_runner_v1 import seal_terminal_artifacts
from tools.benchmark_branch.models import BranchJob


OBLIGATION_BINDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1",
    "anchor_id": "liv-t-04",
    "obligations": [
        {
            "obligation_id": "relay",
            "relation": "P2",
            "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"],
        }
    ],
}


def _job() -> BranchJob:
    return BranchJob(
        run_id="room06-partial-run",
        system_id="emos-zero",
        scene_key="room_06",
        task_id="liv-t-04",
        condition_id="C0",
        slot=0,
        branch_type="fact",
        branch_id="room06-partial-r1",
        pair_group_id="room06-partial-r1",
        repeat_id="r1",
        execution_scope="measured_diagnostic",
    )


def _case(catalog: dict, plan: dict) -> dict:
    return bind_case_manifest(
        {
            "schema": "heterocoord.case-manifest.v1",
            "case_id": "room06-livt04-partial",
            "task_id": "liv-t-04",
            "anchor_id": "liv-t-04",
            "domain": "living_public",
            "relation_signature": ["P2", "P3-J"],
            "base_instance_id": "room06-partial/base",
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
                        "robot_id": robot_id,
                        "asset_digest": "5" * 64,
                        "capability_digest": "6" * 64,
                        "initial_state_digest": "7" * 64,
                    }
                    for robot_id in ("carrier-0", "carrier-1")
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
                "rng_stream_key": "room06-partial",
                "checkpoint_schema": "diagnostic-v1",
            },
            "evaluator": {
                "evaluator_id": EVALUATOR_ID,
                "evaluator_version": EVALUATOR_VERSION,
            },
        }
    )


def _sealed(tmp_path: Path) -> tuple[dict, dict, dict, dict]:
    catalog = {"anchors": ["liv-t-04"]}
    plan = {
        "seed": 20261027,
        "oracle_obligation_binding": OBLIGATION_BINDING,
        "room06_runtime_binding": dict(DIAGNOSTIC_RUNTIME_BINDING),
    }
    parameters = {"alpha": 0.05, "seed": 20261027, "resamples": 0, "d5_min_cases": 30}
    case = _case(catalog, plan)
    sealed = seal_terminal_artifacts(
        terminals=[
            {
                "job": _job(),
                "evaluator": Room06LivT04Evaluator(
                    case_manifest=case,
                    obligation_binding=OBLIGATION_BINDING,
                    runtime_binding=DIAGNOSTIC_RUNTIME_BINDING,
                ),
                "runtime": {
                    **build_diagnostic_runtime_fixture(),
                    "obligation_binding": OBLIGATION_BINDING,
                },
                "evaluator_result": {
                    "status": "valid_terminal",
                    "completed": True,
                    "physical_success": True,
                    "terminal_predicate": True,
                    "safety_predicate": True,
                    "feasibility_predicate": True,
                },
            }
        ],
        artifact_root=tmp_path / "evidence",
        case_manifest=case,
        catalog=catalog,
        plan=plan,
        parameters=parameters,
    )
    calibration = {
        "calibration": [
            {
                "instance_id": case["base_instance_id"],
                "reference_bits": 10.0,
                "reference_time": 1.0,
            }
        ],
        "calibration_digest": "a" * 64,
    }
    return sealed, case, calibration, parameters


def test_single_room06_case_exports_only_observed_local_estimates(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    result = run_partial_case(
        receipts=sealed["receipts"],
        case_manifest=case,
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
        calibration=calibration,
        output=tmp_path / "partial-results",
    )
    report = result["report"]
    assert result["qualification"] == "measured_unqualified"
    assert result["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }
    assert report["composite"] == {
        "status": "not_computed",
        "point": None,
        "reason": "partial_case_export_forbids_composite",
    }
    dimensions = {row["score_id"]: row for row in report["dimensions"]}
    assert all(row["paper_point"] is None for row in dimensions.values())
    assert dimensions["D1"]["case_local_point"] == pytest.approx(80.0)
    assert dimensions["D2"]["status"] == "incomplete"
    assert dimensions["D2"]["components"] == {
        "condition_rates": {"C0": 1.0},
        "conditions_missing": ["C1", "C2", "C3"],
    }
    assert dimensions["D3"]["components"] == {
        "relation_rates": {"P2": 1.0},
        "relations_missing": ["P3J", "P3S", "P4"],
    }
    assert dimensions["D4"]["status"] == "na"
    assert dimensions["D5"]["status"] == "na"
    assert dimensions["D6"]["case_local_point"] == pytest.approx(100.0)
    assert verify_partial_export(tmp_path / "partial-results")["schema"] == (
        "heterocoord.case-partial-output-manifest.v1"
    )


def test_missing_calibration_preserves_raw_d6_and_marks_incomplete(tmp_path: Path) -> None:
    sealed, case, _, _ = _sealed(tmp_path)
    payload = normalize_partial_case_receipts(
        sealed["receipts"],
        case_manifest=case,
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
    )
    report = compute_partial_results(payload)
    d6 = {row["score_id"]: row for row in report["dimensions"]}["D6"]
    assert d6["status"] == "incomplete"
    assert d6["case_local_point"] is None
    assert d6["reason"] == "independent_instance_calibration_missing"
    assert d6["components"]["runs"] == [
        {
            "instance_id": case["base_instance_id"],
            "success": True,
            "bits": 10.0,
            "time": 1.0,
        }
    ]


def test_strict_full_matrix_normalizer_still_rejects_partial_case(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    with pytest.raises(ReceiptAdapterError, match="D2 fixed directory missing conditions"):
        normalize_receipts(
            sealed["receipts"],
            calibration=calibration,
            case_manifest=case,
            artifact_manifest=sealed["artifact_manifest"],
            artifact_root=sealed["artifact_root"],
            scope="measured_unqualified",
        )


def test_tampered_external_evidence_is_rejected_before_output(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    entry = next(iter(sealed["artifact_manifest"].values()))
    evidence_path = Path(sealed["artifact_root"]) / entry["path"]
    evidence_path.write_text("tampered", encoding="utf-8")
    output = tmp_path / "partial-results"
    with pytest.raises(CasePartialResultsError, match="artifact manifest contains invalid evidence"):
        run_partial_case(
            receipts=sealed["receipts"],
            case_manifest=case,
            artifact_manifest=sealed["artifact_manifest"],
            artifact_root=sealed["artifact_root"],
            calibration=calibration,
            output=output,
        )
    assert not output.exists()


def test_partial_replay_rejects_resized_tampered_report(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    output = tmp_path / "partial-results"
    run_partial_case(
        receipts=sealed["receipts"],
        case_manifest=case,
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
        calibration=calibration,
        output=output,
    )
    report_path = output / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["dimensions"][0]["case_local_point"] = 99.0
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(CasePartialResultsError, match="size mismatch"):
        verify_partial_export(output)


def test_partial_replay_rejects_same_size_tampered_report(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    output = tmp_path / "partial-results"
    run_partial_case(
        receipts=sealed["receipts"],
        case_manifest=case,
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
        calibration=calibration,
        output=output,
    )
    report_path = output / "report.json"
    original = report_path.read_text(encoding="utf-8")
    tampered = original.replace(
        '"case_local_point": 80.0',
        '"case_local_point": 81.0',
        1,
    )
    assert tampered != original
    assert len(tampered.encode("utf-8")) == len(original.encode("utf-8"))
    report_path.write_text(tampered, encoding="utf-8")
    with pytest.raises(CasePartialResultsError, match="digest mismatch"):
        verify_partial_export(output)


def test_partial_input_digest_and_gate_tamper_fail_closed(tmp_path: Path) -> None:
    sealed, case, calibration, _ = _sealed(tmp_path)
    payload = normalize_partial_case_receipts(
        sealed["receipts"],
        case_manifest=case,
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
        calibration=calibration,
    )
    payload["coverage"]["relations_missing"] = []
    with pytest.raises(CasePartialResultsError, match="input digest mismatch"):
        compute_partial_results(payload)
    payload["input_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "input_digest"}
    )
    payload["formal_gates"]["paper_score_eligible"] = True
    payload["input_digest"] = canonical_digest(
        {key: value for key, value in payload.items() if key != "input_digest"}
    )
    with pytest.raises(CasePartialResultsError, match="formal gates must remain closed"):
        compute_partial_results(payload)
