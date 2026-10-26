"""End-to-end diagnostic contract for the production results seam.

The fixture below is deliberately small, evaluator-owned, and content
addressed.  It exercises the same boundaries used by a future simulator run;
it is not a qualification or paper-data fixture.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from benchmark.case_manifest_v1 import bind_case_manifest
from benchmark.paper_panel_v1 import _digest as panel_digest
from benchmark.paper_projection_v1 import apply_panel_projection, project_panel_package
from benchmark.production_results_v1 import ProductionRunError, build_paper_panel_package, run_production_case
from benchmark.receipt_producer_v1 import build_raw_receipt
from benchmark.records_v3 import canonical_digest
from benchmark.receipt_adapter_v3 import normalize_receipts
from benchmark.results_pipeline_v3 import compute_results, export_results, verify_export


ANCHOR = "liv-p2-01"
BASE = "liv-p2-01-diagnostic-base"
REPEATS = ("r1", "r2", "r3")
CONDITIONS = ("C0", "C1", "C2", "C3")
ORACLE_BINDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1",
    "anchor_id": ANCHOR,
    "obligations": [
        {"obligation_id": "assembly", "relation": "P2", "predicate_ids": ["move_after_assembly"]},
        {"obligation_id": "receipt", "relation": "P3", "subtype": "J", "predicate_ids": ["receive_after_release"]},
        {"obligation_id": "dock", "relation": "P3", "subtype": "S", "predicate_ids": ["dock_slot_capacity_one"]},
        {"obligation_id": "accept", "relation": "P4", "predicate_ids": ["accept_after_receipt"]},
    ],
}


def _case(catalog: dict, plan: dict) -> dict:
    return bind_case_manifest(
        {
            "schema": "heterocoord.case-manifest.v1",
            "case_id": "production-results-diagnostic-liv-p2-01",
            "task_id": ANCHOR,
            "anchor_id": ANCHOR,
            "domain": "living_public",
            "relation_signature": ["P2", "P3-J"],
            "base_instance_id": BASE,
            "scene": {
                "scene_id": "diagnostic-home-entry",
                "scene_digest": "1" * 64,
                "semantic_map_digest": "2" * 64,
                "topology_digest": "3" * 64,
                "frame": "world",
                "units": "meters+ticks",
                "entity_ids": ["pallet"],
                "zone_ids": ["home_entry_handoff_dock"],
                "horizon": 7500,
                "tau_stable": 25,
            },
            "robot_pool": {
                "pool_id": "diagnostic-pool",
                "roster_digest": "4" * 64,
                "robots": [
                    {
                        "robot_id": "carrier-a",
                        "asset_digest": "5" * 64,
                        "capability_digest": "6" * 64,
                        "initial_state_digest": "7" * 64,
                    },
                ],
            },
            "contract": {
                "contract_digest": "8" * 64,
                "oracle_id": "liv-p2-01-oracle-v3",
                "contract_revision": "diagnostic-2026-10-27",
            },
            "catalog_digest": canonical_digest(catalog),
            "plan_digest": canonical_digest(plan),
            "replay": {
                "serializer_version": "diagnostic-v1",
                "rng_stream_key": "production-results-diagnostic",
                "checkpoint_schema": "diagnostic-v1",
            },
            "evaluator": {
                "evaluator_id": "diagnostic-evaluator-v1",
                "evaluator_version": "diagnostic-evaluator-v1",
            },
        }
    )


def _streams() -> dict:
    return {
        "zone_intervals": [
            {
                "zone_id": "home_entry_handoff_dock",
                "holder_id": "carrier-a",
                "enter_tick": 40,
                "exit_tick": 80,
            }
        ],
        "hold_intervals": [],
        "milestones": [
            {"label": "ready_for_handoff", "tick": 20},
            {"label": "released", "tick": 30},
            {"label": "received", "tick": 90},
            {"label": "accepted", "tick": 100},
        ],
        "task_events": [
            {"name": "assemble_pallet", "subject": "pallet", "tick": 10},
        ],
        "identity_records": {},
        "integrity_confirmed": True,
    }


def _facts() -> dict:
    return {
        "d1": {"validity": 0.9, "organization": 0.8, "infeasible": 0.0, "complete_plan": True},
        "success": True,
        "obligations": [
            {"relation": "P2", "outcome": "completed"},
            {"relation": "P3", "subtype": "J", "outcome": "completed"},
            {"relation": "P3", "subtype": "S", "outcome": "completed"},
            {"relation": "P4", "outcome": "completed"},
        ],
        "d4_items": [{"kind": "deadline", "e": 1.0}],
        "d5_attempt": {"family_id": "demo-family", "case_id": "demo-case", "unit_id": "demo-unit", "success": True},
        "d6": {"success": True, "bits": 120.0, "time": 1.2},
    }


def _panel_binding(endpoint: str, value_path: str, *, condition: str | None = None) -> dict:
    cell = {
        "weight": 1.0,
        "planned_instances": 1,
        "infrastructure_valid_instances": [BASE],
        "min_valid_instances": 1,
        "min_coverage_fraction": 1.0,
    }
    if condition is not None:
        cell["receipt_filter"] = {"condition_id": condition}
    return {
        "schema": "heterocoord.paper-panel-binding.v1",
        "endpoint": endpoint,
        "repeat_ids": list(REPEATS),
        "value_path": value_path,
        "cells": {"diagnostic-cell": cell},
    }


def _build_fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    catalog = {"anchors": [ANCHOR]}
    plan = {"seed": 20261027, "oracle_obligation_binding": ORACLE_BINDING}
    parameters = {"alpha": 0.05, "resamples": 0, "d5_min_cases": 1}
    case = _case(catalog, plan)

    artifact_manifest = {}
    for condition in CONDITIONS:
        for repeat in REPEATS:
            label = f"evidence-{condition}-{repeat}"
            payload = json.dumps(
                {"condition_id": condition, "repeat_id": repeat, "evaluator": "diagnostic"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            relative = f"{label}.json"
            (tmp_path / relative).write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            artifact_manifest[label] = {"path": relative, "sha256": digest, "size_bytes": len(payload)}

    manifest_digest = canonical_digest(artifact_manifest)
    receipts = []
    for condition in CONDITIONS:
        for repeat in REPEATS:
            label = f"evidence-{condition}-{repeat}"
            digest = artifact_manifest[label]["sha256"]
            receipts.append(
                build_raw_receipt(
                    case_manifest=case,
                    catalog=catalog,
                    plan=plan,
                    parameters=parameters,
                    episode={
                        "status": "valid_terminal",
                        "condition_id": condition,
                        "branch": "fact",
                        "repeat_id": repeat,
                        "checkpoint_id": "cp-diagnostic",
                        "system_id": "diagnostic-system",
                        "evaluator_version": "diagnostic-evaluator-v1",
                        "evidence_refs": [label],
                        "evidence_hash": digest,
                        "artifact_manifest_digest": manifest_digest,
                    },
                    streams=_streams(),
                    facts=(
                        _facts()
                        if condition == "C0"
                        else {key: value for key, value in _facts().items() if key != "d5_attempt"}
                    ),
                    receipt_id=f"receipt-{condition}-{repeat}",
                )
            )

    # The projection owns the D6 binding.  Its digest is embedded in the
    # evaluator-owned structured section before the panel package is sealed.
    calibration_rows = [{"instance_id": BASE, "reference_bits": 100.0, "reference_time": 1.0}]
    d6_runs = [{"instance_id": BASE} for _ in receipts]
    d6_binding = {"calibration": calibration_rows, "runs": d6_runs}
    structured = {
        "d6": {
            "binding_digest": panel_digest(d6_binding),
            "calibration": calibration_rows,
            "runs": [
                {"instance_id": BASE, "success": True, "bits": 120.0, "time": 1.2}
                for _ in receipts
            ],
        }
    }

    # Every fixed endpoint receives one copy of each registered repeat.  The
    # D2 endpoints partition by condition; other endpoints use C0 as their
    # fixed diagnostic cell so the same receipt is never double-mapped.
    unfiltered = lambda endpoint, path: _panel_binding(endpoint, path, condition="C0")
    panel_bindings = {
        "d1_validity": unfiltered("d1_validity", "facts.d1.validity"),
        "d1_organization": unfiltered("d1_organization", "facts.d1.organization"),
        "d1_infeasible": unfiltered("d1_infeasible", "facts.d1.infeasible"),
        "d2_nominal": _panel_binding("d2_nominal", "facts.success", condition="C0"),
        "d2_c1": _panel_binding("d2_c1", "facts.success", condition="C1"),
        "d2_c2": _panel_binding("d2_c2", "facts.success", condition="C2"),
        "d2_c3": _panel_binding("d2_c3", "facts.success", condition="C3"),
        "d3_p2": unfiltered("d3_p2", "facts.obligations:P2"),
        "d3_p3j": unfiltered("d3_p3j", "facts.obligations:P3:J"),
        "d3_p3s": unfiltered("d3_p3s", "facts.obligations:P3:S"),
        "d3_p4": unfiltered("d3_p4", "facts.obligations:P4"),
        "d4_deadline": unfiltered("d4_deadline", "facts.d4_items[0].e"),
        "d5_demo": _panel_binding("d5_demo", "facts.d5_attempt.success", condition="C0"),
    }
    projection_binding = {
        "schema": "heterocoord.paper-projection-binding.v1",
        # Filled after the panel package is constructed by the production seam;
        # this placeholder is replaced in the test helper below.
        "paper_panel_package_digest": "",
        "dimensions": {
            "D1": {"validity": "d1_validity", "organization": "d1_organization", "infeasible": "d1_infeasible", "complete_plan": True},
            "D2": {"nominal": "d2_nominal", "restricted": ["d2_c1", "d2_c2", "d2_c3"]},
            "D3": {"p2": "d3_p2", "p3j": "d3_p3j", "p3s": "d3_p3s", "p4": "d3_p4"},
            "D4": {"items": [{"kind": "deadline", "e": "d4_deadline"}]},
            "D5": {"min_cases_per_family": 1, "families": {"demo-family": {"cases": {"demo-case": {"units": {"demo-unit": "d5_demo"}}}}}},
            "D6": d6_binding,
        },
    }
    calibration = {"calibration": calibration_rows, "calibration_digest": "a" * 64}
    return receipts, calibration, case, artifact_manifest, panel_bindings, projection_binding, structured


def _merge_endpoint_packages(receipts, panel_bindings, structured):
    """Build one endpoint at a time, then seal the registered panel package.

    This is the currently deployable composition for a mixed fixed-panel
    directory: D2 needs all four condition subsets while D5 needs one
    base-instance/three-repeat subset.  The one-shot production helper cannot
    express those disjoint receipt directories (see the companion regression
    test below), so this helper makes the boundary explicit and auditable.
    """
    endpoints = {}
    for endpoint, binding in panel_bindings.items():
        condition = endpoint in {"d2_nominal", "d2_c1", "d2_c2", "d2_c3"}
        condition_id = {"d2_nominal": "C0", "d2_c1": "C1", "d2_c2": "C2", "d2_c3": "C3"}.get(endpoint, "C0")
        subset = [row for row in receipts if row["identity"]["condition_id"] == condition_id]
        package = build_paper_panel_package(
            receipts=subset,
            panel_bindings={endpoint: binding},
            structured=structured if endpoint == "d1_validity" else None,
        )
        endpoints[endpoint] = package["endpoints"][endpoint]
    package = {
        "schema": "heterocoord.paper-panel-package.v1",
        "panel_version": "paper-panel-v1",
        "status": "ready",
        "endpoints": endpoints,
        "formal_gates": {"rankable": False, "paper_score_eligible": False, "physical_qualified": False},
        "structured": structured,
    }
    package["package_digest"] = panel_digest({k: v for k, v in package.items() if k != "package_digest"})
    return package


def test_production_results_e2e_is_content_addressed_and_replayable(tmp_path):
    fixture = _build_fixture(tmp_path / "evidence")
    evidence_root = tmp_path / "evidence"
    output = tmp_path / "production-output"
    receipts, calibration, case, artifacts, panel_bindings, projection_binding, structured = fixture
    normalized = normalize_receipts(
        receipts,
        calibration=calibration,
        case_manifest=case,
        artifact_manifest=artifacts,
        artifact_root=evidence_root,
        require_artifact_evidence=True,
        scope="measured_unqualified",
    )
    panel_package = _merge_endpoint_packages(receipts, panel_bindings, structured)
    projection_binding["paper_panel_package_digest"] = panel_package["package_digest"]
    projection = project_panel_package(panel_package, projection_binding)
    assert projection["status"] == "ready"
    projected = apply_panel_projection(normalized, projection)
    report = compute_results(projected)
    outputs = export_results(
        report,
        output,
        input_payload=projected,
        additional_json={"paper_panel_package.json": panel_package, "paper_projection.json": projection},
    )
    manifest = verify_export(output)
    assert report["scope"] == "measured_unqualified"
    assert report["formal_gates"] == {"rankable": False, "paper_score_eligible": False, "physical_qualified": False}
    assert {row["score_id"] for row in report["dimensions"]} == {"D1", "D2", "D3", "D4", "D5", "D6"}
    assert manifest["schema"] == "heterocoord.results-output-manifest.v1"
    assert len(outputs) >= 9
    assert (output / "paper_panel_package.json").is_file()
    assert (output / "paper_projection.json").is_file()


def test_one_shot_runner_exposes_fixed_panel_directory_shape_gap(tmp_path):
    fixture = _build_fixture(tmp_path / "evidence")
    receipts, calibration, case, artifacts, panel_bindings, projection_binding, structured = fixture
    with pytest.raises(ProductionRunError, match="maps to 0 panel cells|duplicate panel receipt"):
        run_production_case(
            receipts=receipts,
            calibration=calibration,
            case_manifest=case,
            artifact_manifest=artifacts,
            artifact_root=tmp_path / "evidence",
            output=tmp_path / "one-shot-output",
            panel_bindings=panel_bindings,
            panel_projection_binding=projection_binding,
            panel_structured=structured,
        )


def test_one_shot_runner_accepts_endpoint_receipt_directories(tmp_path):
    """The production seam can consume disjoint fixed-panel receipt sets."""
    fixture = _build_fixture(tmp_path / "evidence")
    evidence_root = tmp_path / "evidence"
    output = tmp_path / "one-shot-output"
    receipts, calibration, case, artifacts, panel_bindings, projection_binding, structured = fixture
    endpoint_receipts = {}
    condition_for_endpoint = {
        "d2_nominal": "C0",
        "d2_c1": "C1",
        "d2_c2": "C2",
        "d2_c3": "C3",
    }
    for endpoint in panel_bindings:
        condition_id = condition_for_endpoint.get(endpoint, "C0")
        endpoint_receipts[endpoint] = [
            row for row in receipts if row["identity"]["condition_id"] == condition_id
        ]
    panel_package = build_paper_panel_package(
        receipts=receipts,
        panel_bindings=panel_bindings,
        endpoint_receipts=endpoint_receipts,
        structured=structured,
    )
    projection_binding["paper_panel_package_digest"] = panel_package["package_digest"]

    result = run_production_case(
        receipts=receipts,
        calibration=calibration,
        case_manifest=case,
        artifact_manifest=artifacts,
        artifact_root=evidence_root,
        output=output,
        panel_bindings=panel_bindings,
        panel_endpoint_receipts=endpoint_receipts,
        panel_projection_binding=projection_binding,
        panel_structured=structured,
    )
    assert result["paper_projection"]["status"] == "ready"
    assert result["report"]["scope"] == "measured_unqualified"
    assert result["formal_gates"] == {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }
    assert (output / "paper_panel_package.json").is_file()
