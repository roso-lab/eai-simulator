"""End-to-end and fail-closed tests for statistical_report_producer_v1.

The happy-path fixture builds a real 2 systems x 6 dimensions = 12 result-key
request out of the authority reducers' own inputs (borrowing the construction
recipes of test_release_v3/test_organization_v3/test_fixed_catalog_v3/
test_relations_v3), runs the producer, and then proves the P0-1 + P0-2 loop:
producer -> paper_result_index_v1.build_result_rows -> write_result_index ->
paper_release_manifest_v1 build/verify round trip.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import errno
import hashlib
import json
import os
from pathlib import Path

import pytest

from benchmark.analysis_v3 import ScheduledRunV3
from benchmark.fixed_catalog_v3 import FixedRunSpecV3
from benchmark.paper_release_manifest_v1 import (
    build_paper_release_manifest,
    verify_paper_release_manifest,
)
from benchmark.paper_result_index_v1 import (
    CONDITION_REGISTER_SCHEMA,
    IMPLEMENTATION_REGISTER_SCHEMA,
    REQUIRED_DIRECTORY_SCHEMA,
    STATISTICAL_EVENT_LEDGER_SCHEMA,
    STATISTICAL_INPUT_SCHEMA,
    STATISTICAL_REPORT_FIELDS,
    PaperResultIndexError,
    _validate_statistical_report,
    build_result_rows,
    canonical_json_bytes,
    encode_reducer_output,
    result_key,
    result_key_digest,
    reducer_implementation_digest,
    reducer_method_for_score,
    reducer_payload_digest,
    reducer_run_links,
    statistical_input_digest,
    write_result_index,
)
from benchmark.records_v3 import IdentityV3, ObligationOutcomeV3
from benchmark.relations_v3 import relation_run_from_obligations
from benchmark.scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from benchmark.statistical_report_producer_v1 import (
    REQUEST_SCHEMA,
    StatisticalReportProducerError,
    dispatch_reducer,
    estimate_from_report,
    main as producer_main,
    produce_statistical_reports,
    support_from_report,
)
from benchmark.tests.test_organization_v3 import dataset as d1_dataset


TAGGED = ("primitive", "difficulty", "factor_pair", "factor_level_a", "factor_level_b",
          "checkpoint_round", "event_round", "untreated_history_id", "event_target",
          "availability_operation", "notification_policy", "restoration_policy", "event",
          "horizon", "source_direction", "target_direction", "oriented_endpoint_scale",
          "scale_regime", "team_composition", "branch")


def _ref(root: Path, relative: str) -> dict:
    data = (root / relative).read_bytes()
    return {"path": relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _identity(implementation_id: str, condition_id: str, outcome_id: str) -> dict:
    value = {
        "implementation_id": implementation_id,
        "comparison_condition_id": condition_id,
        "task_family": "warehouse",
        "domain": "service",
        "outcome_id": outcome_id,
        "estimator_version": "estimator-v1",
    }
    for field in TAGGED:
        value[field] = {"status": "not_applicable", "reason": "aggregate composite paper result"}
    return value


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _execution(label: str) -> dict:
    return {
        "episode_reset_method": "fresh evaluator process",
        "permitted_history_digest": _digest(f"history-{label}"),
        "initialization_configuration_digest": _digest(f"init-{label}"),
        "execution_audit_version": "audit-v1",
    }


def _registers() -> tuple[dict, dict]:
    implementations = {
        "schema": IMPLEMENTATION_REGISTER_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "implementations": [
            {
                "implementation_id": impl_id,
                "display_name": f"System {tag}",
                "primary_order": order,
                "source_digest": _digest(f"source-{impl_id}"),
                "model_digest": _digest(f"model-{impl_id}"),
                "adapter_digest": _digest(f"adapter-{impl_id}"),
                "runtime_digest": _digest(f"runtime-{impl_id}"),
                "cognition": "heterogeneous coordination",
                "comparison_group": "primary",
                "information_rights": "registered external evaluator observations",
                "training_access": "none",
                "decomposition_policy": "native",
                "supported_task_subsets": ["warehouse"],
                "supported_disturbances": ["nominal"],
            }
            for order, (impl_id, tag) in enumerate((("impl-a", "A"), ("impl-b", "B")))
        ],
    }
    conditions = {
        "schema": CONDITION_REGISTER_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "conditions": [
            {
                "condition_id": condition_id,
                "implementation_id": impl_id,
                "prompt_digest": _digest(f"prompt-{condition_id}"),
                "model_digest": _digest(f"model-{condition_id}"),
                "tool_digest": _digest(f"tool-{condition_id}"),
                "initial_memory_digest": _digest(f"memory-{condition_id}"),
                "adapter_digest": _digest(f"adapter-{condition_id}"),
                "observation_rights": "external evaluator only",
                "budget_policy": "frozen registered budget",
                "runtime_policy": "isolated runtime",
                "reset_policy": "episode reset",
            }
            for impl_id, condition_id in (("impl-a", "cond-a"), ("impl-b", "cond-b"))
        ],
    }
    return implementations, conditions


# --------------------------------------------------------------------------
# Real reducer-input payloads per dimension (recipes from the existing suites).
# --------------------------------------------------------------------------


def _d1_payload() -> dict:
    specs, runs = d1_dataset()
    return {
        "instances": [asdict(spec) for spec in specs],
        "runs": [asdict(run) for run in runs],
        "registered_cells": ["c"],
        "inference": {"resamples": 100},
    }


def _d2_payload(*, residual_unknown: bool) -> dict:
    rows = []
    for i in range(30):
        for repeat in range(3):
            unknown = residual_unknown and i == 0 and repeat == 0
            row = ScheduledRunV3(
                f"{i}/{repeat}", str(i), "cell", str(repeat), "cp", "evidence",
                (None if unknown else 1.0, float(i % 2), float(i % 2), float(i % 2)),
                "pair-invalid" if unknown else "observed",
                paired_block_id="block" if unknown else "",
                paired_attempt_id="attempt" if unknown else "",
                weight=1 / 90,
            )
            rows.append(row)
    return {"runs": [asdict(row) for row in rows], "stratum_weights": {"cell": 1}, "inference": {"resamples": 100}}


def _d2_na_payload() -> dict:
    rows = [ScheduledRunV3(f"a-{repeat}", "a", "cell", str(repeat), "cp", "evidence",
                           (0.0, 1.0, 0.0, 0.0), weight=1 / 3) for repeat in range(3)]
    return {"runs": [asdict(row) for row in rows], "stratum_weights": {"cell": 1}, "inference": {"resamples": 100}}


def _d3_payload(instances: int) -> dict:
    runs = []
    for i in range(instances):
        for repeat in range(3):
            identity = IdentityV3(
                "v3", "score", "catalog", "design", "task", "anchor", "production",
                ("P2", "P3", "P4"), "L1", "nominal", "system", str(i), str(repeat), "fact", "checkpoint")
            obligations = tuple(
                ObligationOutcomeV3(
                    identity, obligation_id=obligation_id, relation=relation, p3_subtype=subtype,
                    participants=("robot",), opportunity=True, accepted_fraction=.5)
                for obligation_id, relation, subtype in (
                    ("p2", "P2", None), ("p3j", "P3", "J"), ("p3s", "P3", "S"), ("p4", "P4", None)))
            runs.append(relation_run_from_obligations(obligations))
    return {
        "runs": [asdict(run) for run in runs],
        "registered_cells": {key: [["production", "L1", "nominal"]] for key in ("P2", "P3J", "P3S", "P4")},
        "partition_mode": "domain_difficulty_condition",
        "inference": {"resamples": 100},
    }


def _d4_payload() -> dict:
    rows = [ScheduledRunV3(f"{i}/{repeat}", str(i), "cell", str(repeat), "cp", "evidence", (.5,), weight=1 / 90)
            for i in range(30) for repeat in range(3)]
    return {"runs": [asdict(row) for row in rows], "stratum_weights": {"cell": 1}, "inference": {"resamples": 100}}


def _d5_payload(*, infrastructure_unknown: bool) -> dict:
    entries, runs = [], []
    for case in range(30):
        for repeat in (1, 2, 3):
            run_id = f"f-c{case}-u-{repeat}"
            entry = FixedRunSpecV3(run_id, f"c{case}-u", "f", str(repeat), "cp", 1 / 90,
                                   f"f-c{case}-{repeat}", "f", f"c{case}", "u")
            entries.append(entry)
            unknown = infrastructure_unknown and case == 0 and repeat == 1
            runs.append(ScheduledRunV3(
                run_id, entry.instance_id, "f", str(repeat), "cp", "evidence",
                (None if unknown else float(case % 2),),
                "infrastructure_unknown" if unknown else "observed",
                weight=1 / 90))
    return {
        "runs": [asdict(run) for run in runs],
        "stratum_weights": {"f": 1},
        "fixed_catalog": {"score_id": "D5", "entries": [asdict(entry) for entry in entries]},
    }


def _d6_payload() -> dict:
    entries, runs = [], []
    for i in range(30):
        for repeat in (1, 2, 3):
            run_id = f"i{i}-r{repeat}"
            entry = FixedRunSpecV3(run_id, f"i{i}", "cell", str(repeat), "cp", 1 / 90, run_id)
            entries.append(entry)
            runs.append(ScheduledRunV3(run_id, f"i{i}", "cell", str(repeat), "cp", "evidence", (.5,), weight=1 / 90))
    return {
        "runs": [asdict(run) for run in runs],
        "stratum_weights": {"cell": 1},
        "fixed_catalog": {"score_id": "D6", "entries": [asdict(entry) for entry in entries]},
    }


def _system_payloads(*, d2_range: bool, d3_instances: int, d5_unknown: bool) -> dict:
    return {
        "D1": _d1_payload(),
        "D2": _d2_payload(residual_unknown=d2_range),
        "D3": _d3_payload(d3_instances),
        "D4": _d4_payload(),
        "D5": _d5_payload(infrastructure_unknown=d5_unknown),
        "D6": _d6_payload(),
    }


def _support_registry(score_id: str, payload: dict) -> dict:
    links = reducer_run_links(payload)
    instance_ids = sorted({item["instance_id"] for item in links})
    by_instance: dict[str, list[dict]] = {}
    for row in payload["runs"]:
        by_instance.setdefault(row["instance_id"], []).append(row)
    known = sorted(
        instance_id for instance_id, rows in by_instance.items()
        if all(row.get("status", "observed") in {"observed", "method_failure"} for row in rows)
    )
    if score_id in {"D2", "D4"}:
        pairing = {"status": "available", "paired_instance_ids": known}
        common = {"status": "available", "common_support_instance_ids": known}
    else:
        pairing = {"status": "not_applicable", "reason": "estimator has no paired-instance design"}
        common = (
            {"status": "available", "common_support_instance_ids": known}
            if score_id in {"D3", "D5", "D6"}
            else {"status": "not_applicable", "reason": "estimator has no common-support comparison"}
        )
    events = (
        {
            "status": "available",
            "event_run_ids": [],
            "competing_event_run_ids": [],
            "censoring_run_ids": [],
        }
        if score_id == "D4"
        else {"status": "not_applicable", "reason": "estimator has no event-time outcome"}
    )
    return {"pairing": pairing, "common_support": common, "events": events}


def _write_frozen_entry_inputs(root: Path, entry: dict, payload: dict, report: dict) -> dict:
    identity = entry["identity"]
    score_id = entry["score_id"]
    method = reducer_method_for_score(score_id)
    implementation_digest = reducer_implementation_digest(score_id)
    payload_digest = reducer_payload_digest(payload)
    registry = _support_registry(score_id, payload)
    ledger_path = entry["event_ledger_ref"]["path"]
    ledger = {
        "schema": STATISTICAL_EVENT_LEDGER_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "score_id": score_id,
        "implementation_id": identity["implementation_id"],
        "comparison_condition_id": identity["comparison_condition_id"],
        "outcome_id": identity["outcome_id"],
        "estimator_version": identity["estimator_version"],
        "result_key_digest": result_key_digest(identity),
        "evidence_scope": "paper_result_candidate",
        "reducer_method": method,
        "reducer_implementation_digest": implementation_digest,
        "reducer_payload_digest": payload_digest,
        "run_links": reducer_run_links(payload),
        "support_registry": registry,
    }
    _write_json(root, ledger_path, ledger)
    entry["event_ledger_ref"] = _ref(root, ledger_path)
    input_path = entry["input_ref"]["path"]
    input_value = {
        "schema": STATISTICAL_INPUT_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "score_id": score_id,
        "implementation_id": identity["implementation_id"],
        "comparison_condition_id": identity["comparison_condition_id"],
        "outcome_id": identity["outcome_id"],
        "estimator_version": identity["estimator_version"],
        "result_key_digest": result_key_digest(identity),
        "evidence_scope": "paper_result_candidate",
        "reducer_method": method,
        "reducer_implementation_digest": implementation_digest,
        "reducer_payload": payload,
        "reducer_payload_digest": payload_digest,
        "reducer_input_digest": report["input_digest"],
        "event_ledger_ref": entry["event_ledger_ref"],
    }
    input_value["input_digest"] = statistical_input_digest(input_value)
    _write_json(root, input_path, input_value)
    entry["input_ref"] = _ref(root, input_path)
    entry["expected_input_digest"] = input_value["input_digest"]
    return registry


# --------------------------------------------------------------------------
# World builder: registers, directory, frozen inputs, request manifest.
# --------------------------------------------------------------------------


SYSTEMS = (
    ("impl-a", "cond-a", {"d2_range": False, "d3_instances": 30, "d5_unknown": False}),
    ("impl-b", "cond-b", {"d2_range": True, "d3_instances": 3, "d5_unknown": True}),
)


def _build_world(tmp_path: Path) -> dict:
    root = tmp_path / "root"
    root.mkdir()
    implementations, conditions = _registers()
    request_entries = []
    directory_entries = []
    for implementation_id, condition_id, flags in SYSTEMS:
        payloads = _system_payloads(**flags)
        for score_id in ("D1", "D2", "D3", "D4", "D5", "D6"):
            payload = payloads[score_id]
            report = dispatch_reducer(score_id, payload)
            identity = _identity(implementation_id, condition_id, f"{score_id}.nominal")
            relative_base = f"evidence/{implementation_id}/{score_id}"
            input_path = f"{relative_base}/input.json"
            ledger_path = f"{relative_base}/ledger.json"
            entry = {
                "identity": identity,
                "score_id": score_id,
                "implementation_id": implementation_id,
                "comparison_condition_id": condition_id,
                "outcome_id": identity["outcome_id"],
                "estimator_version": identity["estimator_version"],
                "expected_input_digest": "0" * 64,
                "input_ref": {"path": input_path, "size": 0, "sha256": "0" * 64},
                "event_ledger_ref": {"path": ledger_path, "size": 0, "sha256": "0" * 64},
                "report_path": f"{relative_base}/report.json",
            }
            registry = _write_frozen_entry_inputs(root, entry, payload, report)
            request_entries.append(entry)
            support = support_from_report(score_id, report, payload, registry)
            directory_entries.append({
                "identity": identity,
                "execution": _execution(implementation_id),
                "support": support,
                "dimension_extension": {"kind": "not_applicable", "reason": "aggregate composite score"},
                "disposition": "required",
                "reason": "registered primary paper result",
                "allow_range_display": True,
            })
    directory_entries.sort(key=lambda entry: result_key(entry["identity"]))
    directory = {"schema": REQUIRED_DIRECTORY_SCHEMA, "paper_commit": PAPER_COMMIT, "entries": directory_entries}
    _write_json(root, "registers/implementation-register.json", implementations)
    _write_json(root, "registers/condition-register.json", conditions)
    _write_json(root, "registers/required-result-directory.json", directory)
    _write_json(root, "scorebook.json", default_scorebook_v3().to_dict())
    _write_json(root, "registers/manuscript-cell-map.json", {"schema": "heterocoord.manuscript-cell-map.v1", "paper_commit": PAPER_COMMIT})
    _write_json(root, "registers/d4-diagnostics.json", {"schema": "heterocoord.d4-diagnostics.v1", "paper_commit": PAPER_COMMIT})
    _write_json(root, "registers/panel-projection-bindings.json", {"schema": "heterocoord.panel-projection-bindings.v1", "paper_commit": PAPER_COMMIT})
    (root / "paper-commit.txt").write_text(PAPER_COMMIT + "\n", encoding="utf-8")
    (root / "index").mkdir()
    request = {
        "schema": REQUEST_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "scorebook_ref": _ref(root, "scorebook.json"),
        "implementation_register_ref": _ref(root, "registers/implementation-register.json"),
        "condition_register_ref": _ref(root, "registers/condition-register.json"),
        "required_directory_ref": _ref(root, "registers/required-result-directory.json"),
        "entries": request_entries,
        "binding_manifest_path": "manifests/statistical-report-binding.v1.json",
        "report_set_path": "manifests/statistical-report-set.v1.json",
    }
    # Persist and reload so every test exercises the byte-driven JSON path
    # exactly like the CLI (tuples become lists, digests stay stable).
    _write_json(root, "request.json", request)
    request = json.loads((root / "request.json").read_bytes())
    return {
        "root": root,
        "request": request,
        "implementations": implementations,
        "conditions": conditions,
        "directory": directory,
    }


def _entry_for(world: dict, *, score_id: str, implementation_id: str = "impl-a") -> dict:
    return next(
        item for item in world["request"]["entries"]
        if item["score_id"] == score_id and item["identity"]["implementation_id"] == implementation_id
    )


def _produced_binding(world: dict) -> dict:
    return json.loads((world["root"] / "manifests/statistical-report-binding.v1.json").read_bytes())


def _frozen_payload(world: dict, entry: dict) -> dict:
    return json.loads((world["root"] / entry["input_ref"]["path"]).read_bytes())["reducer_payload"]


def _replace_entry_payload(world: dict, entry: dict, payload: dict) -> dict:
    report = dispatch_reducer(entry["score_id"], payload)
    registry = _write_frozen_entry_inputs(world["root"], entry, payload, report)
    key = result_key_digest(entry["identity"])
    directory_entry = next(
        item for item in world["directory"]["entries"]
        if result_key_digest(item["identity"]) == key
    )
    directory_entry["support"] = support_from_report(entry["score_id"], report, payload, registry)
    _write_json(world["root"], "registers/required-result-directory.json", world["directory"])
    world["request"]["required_directory_ref"] = _ref(world["root"], "registers/required-result-directory.json")
    return report


# --------------------------------------------------------------------------
# Happy path: 12 result keys through producer -> index -> release manifest.
# --------------------------------------------------------------------------


def test_producer_index_release_round_trip_with_twelve_result_keys(tmp_path: Path):
    world = _build_world(tmp_path)
    summary = produce_statistical_reports(world["request"], root=world["root"])
    assert summary["report_count"] == 12
    assert summary["paper_commit"] == PAPER_COMMIT
    assert summary["formal_gates"]["rankable"] is False
    binding = _produced_binding(world)
    assert len(binding["bindings"]) == 12
    report_set = json.loads((world["root"] / "manifests/statistical-report-set.v1.json").read_bytes())
    assert {item["result_key_digest"] for item in report_set["reports"]} == {
        item["result_key_digest"] for item in binding["bindings"]
    }
    for item in binding["bindings"]:
        report = json.loads((world["root"] / item["report_ref"]["path"]).read_bytes())
        input_object = json.loads((world["root"] / item["input_ref"]["path"]).read_bytes())
        ledger_object = json.loads((world["root"] / item["event_ledger_ref"]["path"]).read_bytes())
        assert set(report) == set(STATISTICAL_REPORT_FIELDS)
        _validate_statistical_report(
            report, binding=item, paper_commit=PAPER_COMMIT,
            scorebook_digest=binding["scorebook_digest"], input_object=input_object,
            ledger_object=ledger_object,
        )
    rows = build_result_rows(
        implementation_register=world["implementations"],
        condition_register=world["conditions"],
        required_directory=world["directory"],
        statistical_report_binding=binding,
        root=world["root"],
    )
    assert len(rows) == 12
    systems_per_score: dict[str, set[str]] = {}
    states: dict[tuple[str, str], str] = {}
    for row in rows:
        score_id = row["identity"]["outcome_id"].split(".")[0]
        systems_per_score.setdefault(score_id, set()).add(row["identity"]["implementation_id"])
        states[(row["identity"]["implementation_id"], score_id)] = row["estimate"]["entry_state"]
    assert set(systems_per_score) == {f"D{i}" for i in range(1, 7)}
    assert all(value == {"impl-a", "impl-b"} for value in systems_per_score.values())
    assert states[("impl-a", "D1")] == "observed"
    assert states[("impl-b", "D1")] == "observed"
    assert states[("impl-a", "D2")] == "observed"
    assert states[("impl-b", "D2")] == "observed_range"
    assert states[("impl-a", "D3")] == "observed"
    assert states[("impl-b", "D3")] == "incomplete"
    assert states[("impl-b", "D5")] == "observed_range"
    assert all(row["evidence"]["source_scope"] == "paper_result_candidate" for row in rows)
    write_result_index(
        rows,
        "index/paper-result-index.v1.jsonl",
        implementation_register=world["implementations"],
        condition_register=world["conditions"],
        required_directory=world["directory"],
        root=world["root"],
    )
    dependencies = {
        "paper_commit": "paper-commit.txt",
        "scorebook": "scorebook.json",
        "implementation_register": "registers/implementation-register.json",
        "comparison_condition_register": "registers/condition-register.json",
        "required_result_directory": "registers/required-result-directory.json",
        "result_index": "index/paper-result-index.v1.jsonl",
        "result_index_manifest": "index/paper-result-index.v1.jsonl.manifest.json",
        "manuscript_cell_map": "registers/manuscript-cell-map.json",
        "statistical_report_binding": "manifests/statistical-report-binding.v1.json",
        "statistical_reports": "manifests/statistical-report-set.v1.json",
        "d4_diagnostics": "registers/d4-diagnostics.json",
        "panel_projection_bindings": "registers/panel-projection-bindings.json",
    }
    manifest = build_paper_release_manifest(
        root=world["root"], paper_commit=PAPER_COMMIT,
        dependencies=dependencies, destination=world["root"] / "release.json",
    )
    assert len(manifest["bound_evidence"]) == 36
    assert verify_paper_release_manifest(world["root"] / "release.json", root=world["root"]) == manifest


def test_second_produce_run_refuses_to_overwrite(tmp_path: Path):
    world = _build_world(tmp_path)
    produce_statistical_reports(world["request"], root=world["root"])
    with pytest.raises(StatisticalReportProducerError, match="refusing to overwrite"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_cli_exit_codes(tmp_path: Path, capsys):
    world = _build_world(tmp_path)
    argv = ["produce-reports", "--root", str(world["root"]), "--request", str(world["root"] / "request.json")]
    assert producer_main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["report_count"] == 12
    assert producer_main(argv) == 2


def test_cli_reports_unexpected_oserror_as_exit_2(tmp_path: Path, capsys, monkeypatch):
    # ENOSPC/EACCES-class failures inside _write_new must reach the same
    # fail-closed exit path instead of a bare traceback (exit 1).
    world = _build_world(tmp_path)
    real_open = os.open

    def denied(path, flags, *args, **kwargs):
        if flags & os.O_CREAT and flags & os.O_EXCL:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    argv = ["produce-reports", "--root", str(world["root"]), "--request", str(world["root"] / "request.json")]
    assert producer_main(argv) == 2
    captured = capsys.readouterr()
    assert "statistical-report-producer:" in captured.err
    assert "writing outputs failed with OS error 13" in captured.err
    assert "Permission denied" in captured.err
    assert not (world["root"] / "manifests/statistical-report-binding.v1.json").exists()
    assert not list((world["root"] / "evidence").rglob("report.json"))


# --------------------------------------------------------------------------
# Fail-closed checklist (one test per independent rejection).
# --------------------------------------------------------------------------


def test_caller_authored_result_key_digest_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"][0]["result_key_digest"] = "0" * 64
    with pytest.raises(StatisticalReportProducerError, match="caller-authored result_key_digest"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_invalid_identity_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    entry["identity"] = {**entry["identity"], "domain": "  "}
    with pytest.raises(StatisticalReportProducerError, match="identity is invalid"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_unregistered_result_key_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    # The digest is always recomputed from the identity, so the only way to
    # attack registration is an identity whose recomputed digest is absent
    # from the frozen required-result directory.
    entry["identity"] = {**entry["identity"], "outcome_id": "D1.unregistered"}
    entry["outcome_id"] = "D1.unregistered"
    with pytest.raises(StatisticalReportProducerError, match="not registered in the required-result directory"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_unregistered_condition_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = _entry_for(world, score_id="D2")
    entry["identity"] = {**entry["identity"], "comparison_condition_id": "cond-z"}
    entry["comparison_condition_id"] = "cond-z"
    with pytest.raises(StatisticalReportProducerError, match="comparison_condition_id is not registered"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_condition_implementation_mismatch_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = _entry_for(world, score_id="D2", implementation_id="impl-b")
    entry["identity"] = {**entry["identity"], "comparison_condition_id": "cond-a"}
    entry["comparison_condition_id"] = "cond-a"
    with pytest.raises(StatisticalReportProducerError, match="implementation and comparison condition do not match"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_entry_field_disagreeing_with_identity_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"][0]["estimator_version"] = "estimator-v2"
    with pytest.raises(StatisticalReportProducerError, match="estimator_version disagrees with the result identity"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_score_id_outside_d1_d6_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"][0]["score_id"] = "D7"
    with pytest.raises(StatisticalReportProducerError, match="outside D1-D6"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_wrong_paper_commit_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["paper_commit"] = "f" * 40
    with pytest.raises(StatisticalReportProducerError, match="authoritative scorebook paper_commit"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_input_digest_mismatch_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"][0]["expected_input_digest"] = "f" * 64
    with pytest.raises(StatisticalReportProducerError, match="input digest identity mismatch"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_event_ledger_byte_mismatch_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    ledger = world["root"] / entry["event_ledger_ref"]["path"]
    ledger.write_bytes(ledger.read_bytes() + b"tampered")
    with pytest.raises(StatisticalReportProducerError, match="reference does not match bytes"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_arbitrary_bytes_cannot_be_resealed_as_event_ledger(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    ledger_path = world["root"] / entry["event_ledger_ref"]["path"]
    ledger_path.write_bytes(b"ordinary string, not a semantic ledger\n")
    entry["event_ledger_ref"] = _ref(world["root"], entry["event_ledger_ref"]["path"])
    input_path = world["root"] / entry["input_ref"]["path"]
    input_value = json.loads(input_path.read_bytes())
    input_value["event_ledger_ref"] = entry["event_ledger_ref"]
    input_value["input_digest"] = statistical_input_digest(input_value)
    input_path.write_bytes(canonical_json_bytes(input_value))
    entry["input_ref"] = _ref(world["root"], entry["input_ref"]["path"])
    entry["expected_input_digest"] = input_value["input_digest"]
    with pytest.raises(StatisticalReportProducerError, match="not strict JSON"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_event_ledger_wrong_implementation_link_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    ledger_path = world["root"] / entry["event_ledger_ref"]["path"]
    ledger = json.loads(ledger_path.read_bytes())
    ledger["implementation_id"] = "impl-b"
    ledger_path.write_bytes(canonical_json_bytes(ledger))
    entry["event_ledger_ref"] = _ref(world["root"], entry["event_ledger_ref"]["path"])
    input_path = world["root"] / entry["input_ref"]["path"]
    input_value = json.loads(input_path.read_bytes())
    input_value["event_ledger_ref"] = entry["event_ledger_ref"]
    input_value["input_digest"] = statistical_input_digest(input_value)
    input_path.write_bytes(canonical_json_bytes(input_value))
    entry["input_ref"] = _ref(world["root"], entry["input_ref"]["path"])
    entry["expected_input_digest"] = input_value["input_digest"]
    with pytest.raises(StatisticalReportProducerError, match="identity/input linkage mismatch"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_inference_change_changes_frozen_input_digest(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = _entry_for(world, score_id="D4")
    original = entry["expected_input_digest"]
    payload = _frozen_payload(world, entry)
    payload["inference"]["alpha"] = 0.1
    report = _replace_entry_payload(world, entry, payload)
    assert report["input_digest"] == json.loads(
        (world["root"] / entry["input_ref"]["path"]).read_bytes()
    )["reducer_input_digest"]
    assert entry["expected_input_digest"] != original


def test_wrong_reducer_implementation_digest_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = _entry_for(world, score_id="D4")
    ledger_path = world["root"] / entry["event_ledger_ref"]["path"]
    ledger = json.loads(ledger_path.read_bytes())
    ledger["reducer_implementation_digest"] = "f" * 64
    ledger_path.write_bytes(canonical_json_bytes(ledger))
    entry["event_ledger_ref"] = _ref(world["root"], entry["event_ledger_ref"]["path"])
    input_path = world["root"] / entry["input_ref"]["path"]
    input_value = json.loads(input_path.read_bytes())
    input_value["reducer_implementation_digest"] = "f" * 64
    input_value["event_ledger_ref"] = entry["event_ledger_ref"]
    input_value["input_digest"] = statistical_input_digest(input_value)
    input_path.write_bytes(canonical_json_bytes(input_value))
    entry["input_ref"] = _ref(world["root"], entry["input_ref"]["path"])
    entry["expected_input_digest"] = input_value["input_digest"]
    with pytest.raises(StatisticalReportProducerError, match="reducer implementation digest mismatch"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_report_retains_raw_reducer_output_and_method(tmp_path: Path):
    world = _build_world(tmp_path)
    produce_statistical_reports(world["request"], root=world["root"])
    item = _produced_binding(world)["bindings"][0]
    stored = json.loads((world["root"] / item["report_ref"]["path"]).read_bytes())
    frozen = json.loads((world["root"] / item["input_ref"]["path"]).read_bytes())
    recomputed = dispatch_reducer(item["score_id"], frozen["reducer_payload"])
    assert stored["reducer_method"] == frozen["reducer_method"]
    assert stored["reducer_implementation_digest"] == frozen["reducer_implementation_digest"]
    assert stored["reducer_output"] == encode_reducer_output(recomputed)
    assert stored["reducer_payload_digest"] == reducer_payload_digest(frozen["reducer_payload"])


def test_reducer_na_status_is_one_honest_not_estimable_key(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = _entry_for(world, score_id="D2")
    payload = _d2_na_payload()
    report = _replace_entry_payload(world, entry, payload)
    assert report["status"] == "na"  # real reducer output: zero nominal reference rate
    summary = produce_statistical_reports(world["request"], root=world["root"])
    assert summary["report_count"] == 12
    rows = build_result_rows(
        implementation_register=world["implementations"],
        condition_register=world["conditions"],
        required_directory=world["directory"],
        statistical_report_binding=_produced_binding(world),
        root=world["root"],
    )
    row = next(item for item in rows if item["identity"] == entry["identity"])
    assert row["estimate"] == {
        "entry_state": "not_estimable", "status": "na", "point": None,
        "lower": None, "upper": None, "interval_method": None,
        "reason": "zero_nominal_reference_rate",
    }
    assert row["support"]["generated_base_instances"] == 1
    assert sum(item["estimate"]["entry_state"] == "not_estimable" for item in rows) == 1


def test_index_layer_rejects_fabricated_na_numbers(tmp_path: Path):
    world = _build_world(tmp_path)
    produce_statistical_reports(world["request"], root=world["root"])
    binding = _produced_binding(world)
    item = binding["bindings"][0]
    report = json.loads((world["root"] / item["report_ref"]["path"]).read_bytes())
    report["estimate"]["status"] = "na"
    report["reducer_output"]["value"]["status"] = "na"
    input_object = json.loads((world["root"] / item["input_ref"]["path"]).read_bytes())
    ledger_object = json.loads((world["root"] / item["event_ledger_ref"]["path"]).read_bytes())
    with pytest.raises(PaperResultIndexError, match="must not contain numerical"):
        _validate_statistical_report(
            report, binding=item, paper_commit=PAPER_COMMIT,
            scorebook_digest=binding["scorebook_digest"], input_object=input_object,
            ledger_object=ledger_object,
        )


def test_duplicate_result_key_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"].append(deepcopy(world["request"]["entries"][0]))
    with pytest.raises(StatisticalReportProducerError, match="duplicate result_key_digest"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_synthetic_scope_in_payload_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    payload = _frozen_payload(world, entry)
    payload["scope"] = "synthetic_engineering_fixture"
    _replace_entry_payload(world, entry, payload)
    with pytest.raises(StatisticalReportProducerError, match="forbidden scope 'synthetic_engineering_fixture'"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_diagnostic_scope_in_input_json_is_refused(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    input_path = world["root"] / entry["input_ref"]["path"]
    input_value = json.loads(input_path.read_bytes())
    input_value["evidence_scope"] = "diagnostic_only"
    input_value["input_digest"] = statistical_input_digest(input_value)
    input_path.write_bytes(canonical_json_bytes(input_value))
    entry["input_ref"] = _ref(world["root"], entry["input_ref"]["path"])
    entry["expected_input_digest"] = input_value["input_digest"]
    with pytest.raises(StatisticalReportProducerError, match="not explicitly scoped as paper_result_candidate"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_missing_scope_does_not_default_to_paper_candidate(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    input_path = world["root"] / entry["input_ref"]["path"]
    input_value = json.loads(input_path.read_bytes())
    input_value.pop("evidence_scope")
    input_value["input_digest"] = statistical_input_digest(input_value)
    input_path.write_bytes(canonical_json_bytes(input_value))
    entry["input_ref"] = _ref(world["root"], entry["input_ref"]["path"])
    entry["expected_input_digest"] = input_value["input_digest"]
    with pytest.raises(StatisticalReportProducerError, match="schema mismatch"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_measured_raw_payload_is_not_rejected_for_upstream_gate_state(tmp_path: Path):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    payload = _frozen_payload(world, entry)
    payload["scope"] = "measured_raw_evidence"
    payload["upstream_formal_gates"] = {"physical": False, "statistical": False}
    _replace_entry_payload(world, entry, payload)
    result = produce_statistical_reports(world["request"], root=world["root"])
    assert result["formal_gates"] == {key: False for key in result["formal_gates"]}


@pytest.mark.parametrize("path", ["results_pipeline/impl-a/input.json", "evidence/impl-a/metrics.csv"])
def test_metrics_pipeline_paths_are_refused(tmp_path: Path, path: str):
    world = _build_world(tmp_path)
    entry = world["request"]["entries"][0]
    source = world["root"] / entry["input_ref"]["path"]
    target = world["root"] / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    entry["input_ref"] = _ref(world["root"], path)
    with pytest.raises(StatisticalReportProducerError, match="refusing to consume"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_scorebook_digest_convention_drift_fails_closed(tmp_path: Path):
    world = _build_world(tmp_path)
    scorebook = json.loads((world["root"] / "scorebook.json").read_bytes())
    scorebook["specs"][0]["title"] = "tampered estimand"
    (world["root"] / "scorebook.json").write_bytes(canonical_json_bytes(scorebook))
    world["request"]["scorebook_ref"] = _ref(world["root"], "scorebook.json")
    with pytest.raises(StatisticalReportProducerError, match="scorebook digest conventions disagree"):
        produce_statistical_reports(world["request"], root=world["root"])


def test_scorebook_digest_conventions_agree_on_default_scorebook():
    # Ruling (a): release-manifest convention equals the reducer-embedded one.
    book = default_scorebook_v3()
    release_side = hashlib.sha256(canonical_json_bytes(book.to_dict()).rstrip(b"\n")).hexdigest()
    assert release_side == book.digest()


def test_failed_request_leaves_no_partial_output(tmp_path: Path):
    world = _build_world(tmp_path)
    world["request"]["entries"][7]["expected_input_digest"] = "f" * 64
    with pytest.raises(StatisticalReportProducerError):
        produce_statistical_reports(world["request"], root=world["root"])
    assert not (world["root"] / "manifests").exists()
    assert not list((world["root"] / "evidence").rglob("report.json"))


def test_downstream_revalidation_failure_rolls_back_new_outputs(
    tmp_path: Path, monkeypatch
):
    world = _build_world(tmp_path)
    destinations = [
        *(world["root"] / entry["report_path"] for entry in world["request"]["entries"]),
        world["root"] / world["request"]["binding_manifest_path"],
        world["root"] / world["request"]["report_set_path"],
    ]

    def reject_downstream(*_args, **_kwargs):
        raise PaperResultIndexError("injected downstream rejection")

    monkeypatch.setattr(
        "benchmark.statistical_report_producer_v1.validate_statistical_report_binding",
        reject_downstream,
    )
    with pytest.raises(
        StatisticalReportProducerError,
        match="produced artifacts failed downstream revalidation",
    ):
        produce_statistical_reports(world["request"], root=world["root"])
    assert all(not path.exists() for path in destinations)


# --------------------------------------------------------------------------
# Estimate and support mapping units (real reducer outputs only).
# --------------------------------------------------------------------------


def test_valid_report_without_point_key_uses_identification():
    rows = [ScheduledRunV3(str(i), "a", "cell", str(i), "cp", "evidence", (None, 1, 0, 0),
                           "infrastructure_unknown", weight=1 / 9) for i in range(3)]
    payload = {"runs": [asdict(row) for row in rows], "stratum_weights": {"cell": 1}, "inference": {"resamples": 100}}
    report = dispatch_reducer("D2", payload)
    assert report["status"] == "valid" and "point" not in report
    estimate = estimate_from_report("D2", report)
    assert estimate["point"] == report["identification"][0] == report["identification"][1]
    assert estimate["status"] == "valid"


def test_unstable_d1_maps_to_numeric_free_incomplete():
    specs, runs = d1_dataset()
    runs = [replace(row, checks=tuple(replace(check, passed=True) for check in row.checks)) for row in runs]
    payload = {"instances": [asdict(spec) for spec in specs], "runs": [asdict(run) for run in runs],
               "registered_cells": ["c"], "inference": {"resamples": 100}}
    report = dispatch_reducer("D1", payload)
    assert report["status"] == "unstable"
    estimate = estimate_from_report("D1", report)
    assert estimate["status"] == "unstable"
    assert estimate["point"] is None and estimate["lower"] is None and estimate["upper"] is None
    assert estimate["interval_method"] is None
    assert estimate["reason"] == "degenerate_cluster_distribution"


def test_range_reports_map_to_identification_range():
    payload = _d2_payload(residual_unknown=True)
    report = dispatch_reducer("D2", payload)
    assert report["status"] == "range"
    estimate = estimate_from_report("D2", report)
    assert estimate["point"] is None
    assert estimate["lower"] == report["interval"][0]
    assert estimate["upper"] == report["interval"][1]
    assert estimate["interval_method"] == "identification_range"
    assert estimate["reason"] == "paired_block_invalid_identification_range"


def test_range_interval_method_is_pinned_by_index_layer(tmp_path: Path):
    # P2-5 fact: _validate_estimate accepts exactly "identification_range"
    # for observed_range, so the producer keeps the pinned string; the
    # authoritative reducer method stays traceable via the bound report bytes.
    world = _build_world(tmp_path)
    produce_statistical_reports(world["request"], root=world["root"])
    binding = _produced_binding(world)
    entry = _entry_for(world, score_id="D5", implementation_id="impl-b")
    reducer_report = dispatch_reducer("D5", _frozen_payload(world, entry))
    assert reducer_report["status"] == "range"
    assert reducer_report["method"] == "fixed_catalog_weighted_hoeffding"
    item = next(
        candidate for candidate in binding["bindings"]
        if candidate["score_id"] == "D5" and candidate["identity"]["implementation_id"] == "impl-b"
    )
    report = json.loads((world["root"] / item["report_ref"]["path"]).read_bytes())
    assert report["estimate"]["status"] == "range"
    assert report["estimate"]["interval_method"] == "identification_range"
    report["estimate"]["interval_method"] = reducer_report["method"]
    input_object = json.loads((world["root"] / item["input_ref"]["path"]).read_bytes())
    ledger_object = json.loads((world["root"] / item["event_ledger_ref"]["path"]).read_bytes())
    with pytest.raises(PaperResultIndexError, match="identification_range"):
        _validate_statistical_report(
            report, binding=item, paper_commit=PAPER_COMMIT,
            scorebook_digest=binding["scorebook_digest"], input_object=input_object,
            ledger_object=ledger_object,
        )


def test_support_counts_reflect_d2_unknown_and_paired_roster():
    payload = _d2_payload(residual_unknown=True)
    report = dispatch_reducer("D2", payload)
    support = support_from_report("D2", report, payload, _support_registry("D2", payload))
    assert support["generated_base_instances"] == 30
    assert support["infrastructure_valid_base_instances"] == 29
    assert support["metric_eligible_base_instances"] == 29
    assert support["paired_instances"] == {"status": "observed", "value": 29}
    assert support["common_support_instances"] == {"status": "observed", "value": 29}
    assert support["repeated_trajectories"] == 90
    assert support["numerator"] == 29 and support["denominator"] == 30
    assert support["observed_coverage_fraction"] == pytest.approx(29 / 30)
    assert support["event_count"]["status"] == "not_applicable"
    assert support["competing_event_count"]["status"] == "not_applicable"
    assert support["censoring_count"]["status"] == "not_applicable"


def test_method_failure_with_known_values_remains_metric_eligible():
    payload = _d2_payload(residual_unknown=False)
    payload["runs"][0]["status"] = "method_failure"
    report = dispatch_reducer("D2", payload)
    assert report["method_failures"] == 1
    support = support_from_report("D2", report, payload, _support_registry("D2", payload))
    assert support["infrastructure_valid_base_instances"] == 30
    assert support["metric_eligible_base_instances"] == 30
    assert support["denominator"] == 30


def test_unavailable_not_applicable_and_real_zero_counts_are_distinct():
    d2_payload = _d2_payload(residual_unknown=False)
    d2_support = support_from_report("D2", dispatch_reducer("D2", d2_payload), d2_payload)
    assert d2_support["paired_instances"]["status"] == "unavailable"
    assert d2_support["event_count"]["status"] == "not_applicable"
    d4_payload = _d4_payload()
    d4_support = support_from_report(
        "D4", dispatch_reducer("D4", d4_payload), d4_payload, _support_registry("D4", d4_payload)
    )
    assert d4_support["event_count"] == {"status": "observed", "value": 0}


def test_d1_support_sums_frozen_coverage_table():
    payload = _d1_payload()
    report = dispatch_reducer("D1", payload)
    support = support_from_report("D1", report, payload)
    assert support["generated_base_instances"] == 60
    assert support["infrastructure_valid_base_instances"] == 60
    assert support["metric_eligible_base_instances"] == 60
    assert support["repeated_trajectories"] == 180
    assert support["planned_coverage_fraction"] == support["observed_coverage_fraction"] == 1.0


def test_d5_support_counts_unknown_unit():
    payload = _d5_payload(infrastructure_unknown=True)
    report = dispatch_reducer("D5", payload)
    assert report["status"] == "range"
    support = support_from_report("D5", report, payload)
    assert support["generated_base_instances"] == 30
    assert support["infrastructure_valid_base_instances"] == 29
    assert support["metric_eligible_base_instances"] == 29
    assert support["repeated_trajectories"] == report["scheduled_attempts"] == 90


def test_d3_incomplete_report_still_gets_honest_support():
    payload = _d3_payload(3)
    report = dispatch_reducer("D3", payload)
    assert report["status"] == "incomplete" and report["reason"] == "relation_cell_coverage_below_floor"
    support = support_from_report("D3", report, payload)
    assert support["generated_base_instances"] == 3
    assert support["metric_eligible_base_instances"] == 3
    assert support["repeated_trajectories"] == 9
    assert estimate_from_report("D3", report)["reason"] == "relation_cell_coverage_below_floor"
