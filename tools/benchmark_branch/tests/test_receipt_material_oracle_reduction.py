import copy
import hashlib

import pytest

from benchmark.d4_first_event_v1 import (
    FACT_KEY as D4_FIRST_EVENT_FACT_KEY,
    OBSERVATION_SCHEMA as D4_OBSERVATION_SCHEMA,
)
from benchmark.case_manifest_v1 import (
    TASK_OBJECT_BINDINGS_SCHEMA,
    bind_case_manifest,
)
from benchmark.case_readiness_v1 import CaseReadinessError, audit_case_receipts
from benchmark.oracle_obligation_v1 import reduce_obligations
from benchmark.receipt_adapter_v3 import ReceiptAdapterError, normalize_receipts
from benchmark.receipt_producer_v1 import ReceiptProducerError, build_raw_receipt
from benchmark.records_v3 import canonical_digest
from tools.benchmark_branch.receipt_material import (
    ReceiptMaterialError, assemble_oracle_receipt_material,
)


def _streams(*, late=False, holds=2):
    return {
        "zone_intervals": [],
        "hold_intervals": [
            {"object_id": "heavy_object", "robot_id": f"carrier-{i}",
             "start_tick": 10, "end_tick": 20}
            for i in range(holds)
        ],
        "milestones": [
            {"label": "relay_started", "tick": 1},
            {"label": "relay_complete", "tick": 3},
            {"label": "heavy_carry_complete", "tick": 20},
            {"label": "accepted", "tick": 15001 if late else 100},
        ],
        "task_events": [
            {"name": "transition_handoff_observed", "subject": "transition_node", "tick": 2},
            {"name": "bilateral_support_established", "subject": "heavy_object", "tick": 10},
        ],
        "identity_records": {}, "integrity_confirmed": True,
    }


BOUNDING = {
    "schema": "heterocoord.oracle-obligation-binding.v1", "anchor_id": "liv-t-04",
    "obligations": [{"obligation_id": "relay", "relation": "P2",
                     "predicate_ids": ["upper_shelf_delivery_after_transition_handoff"]}],
}


def _base():
    return {"d1": {"complete_plan": True, "validity": 1.0,
                    "organization": 1.0, "infeasible": 0.0},
            "base_task_success": True, "d4_items": [], "d6": {"bits": 1.0, "time": 1.0}}


def _d4_observation():
    return {
        "schema": D4_OBSERVATION_SCHEMA,
        "registration_id": "room06-loss-v1",
        "panel_id": "room06-loss",
        "cell_id": "room06-cell",
        "event_id": "task-function-loss",
        "event_eligible": True,
        "eligibility_evidence_digest": "1" * 64,
        "response": {"tick": 120, "evidence_digest": "2" * 64},
        "work_confirmation": {"tick": 115, "evidence_digest": "3" * 64},
        "terminal": None,
        "safety": {"flag": False, "evidence_digest": None},
        "observed_through_tick": 200,
        "timeline_digest": "4" * 64,
    }


def test_terminal_material_derives_obligation_and_safety_success_from_streams():
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING)
    assert material["facts"]["obligations"] == [{"relation": "P2", "outcome": "completed"}]
    assert material["facts"]["success"] is True
    assert material["facts"]["d6"]["success"] is True


def test_terminal_material_preserves_detached_d4_diagnostic_without_primary_items():
    base = _base()
    observation = _d4_observation()
    base[D4_FIRST_EVENT_FACT_KEY] = observation
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "task_function_loss",
                 "branch": "fact", "repeat_id": "r1", "checkpoint_id": "cp"},
        streams=_streams(), base_facts=base, anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    observation["response"]["tick"] = 199

    assert material["facts"][D4_FIRST_EVENT_FACT_KEY]["response"]["tick"] == 120
    assert material["facts"][D4_FIRST_EVENT_FACT_KEY]["work_confirmation"]["tick"] == 115
    assert material["facts"]["d4_items"] == []


def test_terminal_material_rejects_malformed_d4_diagnostic():
    base = _base()
    observation = _d4_observation()
    observation["response"]["tick"] = 201
    base[D4_FIRST_EVENT_FACT_KEY] = observation
    with pytest.raises(ReceiptMaterialError, match="exceeds observation coverage"):
        assemble_oracle_receipt_material(
            episode={"status": "valid_terminal", "condition_id": "task_function_loss",
                     "branch": "fact", "repeat_id": "r1", "checkpoint_id": "cp"},
            streams=_streams(), base_facts=base, anchor_id="liv-t-04",
            obligation_binding=BOUNDING,
        )


def test_deadline_or_hold_violation_forces_success_false():
    for streams in (_streams(late=True), _streams(holds=1)):
        material = assemble_oracle_receipt_material(
            episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                     "repeat_id": "r1", "checkpoint_id": "cp"},
            streams=streams, base_facts=_base(), anchor_id="liv-t-04",
            obligation_binding=BOUNDING)
        assert material["facts"]["success"] is False
        assert material["facts"]["d6"]["success"] is False


def test_incomplete_non_unreached_oracle_stream_rejects_before_seal():
    streams = _streams()
    streams["hold_intervals"] = []
    streams["integrity_confirmed"] = False
    try:
        assemble_oracle_receipt_material(
            episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                     "repeat_id": "r1", "checkpoint_id": "cp"},
            streams=streams, base_facts=_base(), anchor_id="liv-t-04",
            obligation_binding=BOUNDING)
    except ReceiptMaterialError:
        return
    raise AssertionError("missing evaluator evidence must fail closed")


def _production_case(catalog, plan):
    scene_digest = "1" * 64
    return bind_case_manifest({
        "schema": "heterocoord.case-manifest.v1",
        "case_id": "room06-livt04-oracle-seam",
        "task_id": "liv-t-04",
        "anchor_id": "liv-t-04",
        "domain": "living_public",
        "relation_signature": ["P2", "P3-J"],
        "base_instance_id": "room06-livt04-base",
        "scene": {
            "scene_id": "room_06",
            "scene_digest": scene_digest,
            "semantic_map_digest": "2" * 64,
            "topology_digest": "3" * 64,
            "frame": "world",
            "units": "meters+ticks",
            "entity_ids": ["book_batch", "heavy_object"],
            "zone_ids": [],
            "horizon": 15000,
            "tau_stable": 25,
            "object_bindings": {
                "schema": TASK_OBJECT_BINDINGS_SCHEMA,
                "scene_digest": scene_digest,
                "objects": [
                    {
                        "entity_id": entity_id,
                        "origin": "task_generated",
                        "definition_ref": f"task-setup:liv-t-04/{entity_id}",
                        "definition_digest": digest,
                    }
                    for entity_id, digest in (
                        ("book_batch", "4" * 64),
                        ("heavy_object", "5" * 64),
                    )
                ],
            },
        },
        "robot_pool": {
            "pool_id": "diagnostic-pool",
            "roster_digest": "6" * 64,
            "robots": [
                {
                    "robot_id": robot_id,
                    "asset_digest": asset_digest,
                    "capability_digest": capability_digest,
                    "initial_state_digest": initial_digest,
                }
                for robot_id, asset_digest, capability_digest, initial_digest in (
                    ("carrier-0", "7" * 64, "8" * 64, "9" * 64),
                    ("carrier-1", "a" * 64, "b" * 64, "c" * 64),
                )
            ],
        },
        "contract": {
            "contract_digest": "d" * 64,
            "oracle_id": "liv-t-04-oracle-v3",
            "contract_revision": "2026-10-26-v3",
        },
        "catalog_digest": canonical_digest(catalog),
        "plan_digest": canonical_digest(plan),
        "replay": {
            "serializer_version": "diagnostic-v1",
            "rng_stream_key": "room06-livt04-r1",
            "checkpoint_schema": "diagnostic-v1",
        },
        "evaluator": {
            "evaluator_id": "oracle-reducer-v1",
            "evaluator_version": "oracle-reducer-v1",
        },
    })


def _sealed_receipts(catalog, plan, case, facts):
    receipts = []
    for condition in ("C0", "C1", "C2", "C3"):
        receipts.append(build_raw_receipt(
            case_manifest=case,
            catalog=catalog,
            plan=plan,
            parameters={"alpha": 0.05, "resamples": 10},
            episode={
                "status": "valid_terminal",
                "condition_id": condition,
                "branch": "fact",
                "repeat_id": "r1",
                "checkpoint_id": "cp0",
                "system_id": "diagnostic-system",
                "evaluator_version": "oracle-reducer-v1",
                "evidence_refs": [f"evidence-{condition}"],
                "evidence_hash": hashlib.sha256(
                    (condition + "\n").encode()
                ).hexdigest(),
            },
            streams=_streams(),
            facts=copy.deepcopy(facts),
            receipt_id=f"room06-livt04-{condition}",
        ))
    return receipts


def _artifact_manifest(tmp_path):
    result = {}
    for condition in ("C0", "C1", "C2", "C3"):
        relative = f"evidence-{condition}.json"
        payload = (condition + "\n").encode()
        (tmp_path / relative).write_bytes(payload)
        result[f"evidence-{condition}"] = {
            "path": relative,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    return result


def _normalize_production(receipts, case, artifacts, root):
    return normalize_receipts(
        receipts,
        calibration={
            "reference_bits": 1.0,
            "reference_time": 1.0,
            "calibration_digest": "e" * 64,
        },
        case_manifest=case,
        artifact_manifest=artifacts,
        artifact_root=root,
    )


def test_resealed_obligation_tamper_is_rejected_by_production_reduction(tmp_path):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    # This is a stronger attack than editing sealed JSON: producer creates a
    # fresh valid envelope hash around a semantically forged D3 outcome.
    forged = copy.deepcopy(material["facts"])
    forged["obligations"][0]["outcome"] = "violated"
    receipts = _sealed_receipts(catalog, plan, case, forged)
    with pytest.raises(ReceiptAdapterError, match="disagree with frozen oracle reduction"):
        _normalize_production(receipts, case, _artifact_manifest(tmp_path), tmp_path)


def test_production_case_without_frozen_obligation_binding_fails_closed(tmp_path):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    receipts = _sealed_receipts(catalog, plan, case, material["facts"])
    with pytest.raises(ReceiptAdapterError, match="requires plan.oracle_obligation_binding"):
        _normalize_production(receipts, case, _artifact_manifest(tmp_path), tmp_path)


def test_room06_case_receipt_can_pass_before_paper_matrix_is_complete(tmp_path):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    # One receipt is sufficient to prove this case-level seam, but never to
    # claim the paper-wide C0--C3 and P2/P3-J/P3-S/P4 support matrix.
    receipt = _sealed_receipts(catalog, plan, case, material["facts"])[0]
    report = audit_case_receipts(
        [receipt], case_manifest=case,
        artifact_manifest={"evidence-C0": _artifact_manifest(tmp_path)["evidence-C0"]},
        artifact_root=tmp_path,
    )
    assert report["case_receipt_ready"] is True
    assert report["receipt_support_surface_present"] is False
    assert report["paper_support_ready"] is False
    assert report["observed_obligation_relations"] == ["P2"]
    assert {row["code"] for row in report["paper_gaps"]} >= {
        "PAPER_D2_CONDITION_SUPPORT_MISSING",
        "PAPER_D3_RELATION_SUPPORT_MISSING",
        "PAPER_D5_ATTEMPT_SUPPORT_MISSING",
        "PAPER_FIXED_PANEL_PACKAGE_MISSING",
    }
    assert not any(report["formal_gates"].values())


def test_case_readiness_reuses_frozen_oracle_tamper_guard(tmp_path):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    forged = copy.deepcopy(material["facts"])
    forged["obligations"][0]["outcome"] = "violated"
    receipt = _sealed_receipts(catalog, plan, case, forged)[0]
    with pytest.raises(CaseReadinessError, match="frozen oracle reduction"):
        audit_case_receipts(
            [receipt], case_manifest=case,
            artifact_manifest={"evidence-C0": _artifact_manifest(tmp_path)["evidence-C0"]},
            artifact_root=tmp_path,
        )


def test_case_readiness_rejects_substituted_artifact_manifest(tmp_path):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    receipt = _sealed_receipts(catalog, plan, case, material["facts"])[0]
    payload = b"substituted evaluator bytes\n"
    (tmp_path / "replacement.json").write_bytes(payload)
    forged_manifest = {
        "evidence-C0": {
            "path": "replacement.json",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    }
    with pytest.raises(CaseReadinessError, match="not bound to the sealed receipt"):
        audit_case_receipts(
            [receipt], case_manifest=case,
            artifact_manifest=forged_manifest, artifact_root=tmp_path,
        )


@pytest.mark.parametrize("status", ["safe_stop", "team_state_infeasible"])
def test_special_terminal_status_requires_certified_proof(status):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    with pytest.raises(ReceiptProducerError, match="safe_stop requires|infeasibility_proof_hash"):
        build_raw_receipt(
            case_manifest=case, catalog=catalog, plan=plan,
            parameters={"alpha": 0.05},
            episode={
                "status": status, "condition_id": "C0", "branch": "fact",
                "repeat_id": "r1", "checkpoint_id": "cp0",
                "evaluator_version": "oracle-reducer-v1",
                "evidence_refs": ["evidence-C0"],
                "evidence_hash": hashlib.sha256(b"C0\n").hexdigest(),
            },
            streams=_streams(), facts=material["facts"], receipt_id=f"bad-{status}",
        )


@pytest.mark.parametrize(
    ("status", "proof_field"),
    [
        ("safe_stop", "safe_stop_certificate_digest"),
        ("team_state_infeasible", "infeasibility_proof_hash"),
    ],
)
@pytest.mark.parametrize("proof_present", [True, False])
def test_special_terminal_proof_must_match_verified_artifact(
    tmp_path, status, proof_field, proof_present,
):
    catalog = {"anchors": ["liv-t-04"]}
    plan = {"seed": 20261026, "oracle_obligation_binding": BOUNDING}
    case = _production_case(catalog, plan)
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp0"},
        streams=_streams(), base_facts=_base(), anchor_id="liv-t-04",
        obligation_binding=BOUNDING,
    )
    facts = copy.deepcopy(material["facts"])
    facts["success"] = False
    facts["d6"]["success"] = False
    if status == "safe_stop":
        facts["safety_predicate"] = True

    artifacts = _artifact_manifest(tmp_path)
    proof_payload = f"certified proof for {status}\n".encode()
    proof_digest = hashlib.sha256(proof_payload).hexdigest()
    facts[proof_field] = proof_digest if proof_present else "f" * 64
    if proof_present:
        proof_path = f"{status}-proof.json"
        (tmp_path / proof_path).write_bytes(proof_payload)
        artifacts[f"{status}-proof"] = {
            "path": proof_path,
            "sha256": proof_digest,
            "size_bytes": len(proof_payload),
        }

    receipt = build_raw_receipt(
        case_manifest=case, catalog=catalog, plan=plan,
        parameters={"alpha": 0.05},
        episode={
            "status": status, "condition_id": "C0", "branch": "fact",
            "repeat_id": "r1", "checkpoint_id": "cp0",
            "evaluator_version": "oracle-reducer-v1",
            "evidence_refs": ["evidence-C0"],
            "evidence_hash": hashlib.sha256(b"C0\n").hexdigest(),
            "artifact_manifest_digest": canonical_digest(artifacts),
        },
        streams=_streams(), facts=facts, receipt_id=f"certified-{status}",
    )
    if proof_present:
        report = audit_case_receipts(
            [receipt], case_manifest=case,
            artifact_manifest=artifacts, artifact_root=tmp_path,
        )
        assert report["case_receipt_ready"] is True
    else:
        with pytest.raises(CaseReadinessError, match=f"{proof_field} does not match any verified artifact"):
            audit_case_receipts(
                [receipt], case_manifest=case,
                artifact_manifest=artifacts, artifact_root=tmp_path,
            )


def test_pilot_joint_hold_safety_is_applied_before_receipt_seal():
    binding = {
        "schema": "heterocoord.oracle-obligation-binding.v1",
        "anchor_id": "liv-p3-01",
        "obligations": [
            {"obligation_id": "sync", "relation": "P3", "subtype": "J",
             "predicate_ids": ["carriers_start_within_sync_window"]},
            {"obligation_id": "release", "relation": "P2",
             "predicate_ids": ["release_after_joint_carry"]},
            {"obligation_id": "accept", "relation": "P2",
             "predicate_ids": ["accept_after_release"]},
        ],
    }
    streams = {
        "zone_intervals": [],
        "hold_intervals": [
            {"object_id": "pallet", "robot_id": "only-carrier",
             "start_tick": 0, "end_tick": 10},
        ],
        "milestones": [
            {"label": "joint_carry_complete", "tick": 10},
            {"label": "released", "tick": 11},
            {"label": "accepted", "tick": 12},
        ],
        "task_events": [
            {"name": "arrive_in_position", "subject": "left_carrier", "tick": 0},
            {"name": "arrive_in_position", "subject": "right_carrier", "tick": 0},
        ],
        "identity_records": {}, "integrity_confirmed": True,
    }
    material = assemble_oracle_receipt_material(
        episode={"status": "valid_terminal", "condition_id": "C0", "branch": "fact",
                 "repeat_id": "r1", "checkpoint_id": "cp"},
        streams=streams, base_facts=_base(), anchor_id="liv-p3-01",
        obligation_binding=binding,
    )
    assert material["facts"]["success"] is False
    assert material["facts"]["d6"]["success"] is False
