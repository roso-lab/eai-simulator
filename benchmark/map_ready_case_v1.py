"""Fail-closed orchestration from frozen map inputs to paper seams.

This module deliberately composes existing versioned boundaries.  It does not
run Isaac, infer evaluator facts, reuse an artifact directory, or open a formal
qualification gate.  A caller must provide a new output directory, an explicit
binding and the exact plan bytes whose digest the binding names.  A simulator
may hand back JSON terminal material; this module only reconstructs the
benchmark-owned job value and an evaluator class already registered by the
case manifest.  It never deserializes arbitrary Python objects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .case_input_audit_v1 import audit_case_inputs
from .case_manifest_builder_v1 import build_case_manifest
from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT as ENGINEERING_PAPER_COMMIT


ORCHESTRATOR_SCHEMA = "heterocoord.map-ready-case-execution.v1"
ORCHESTRATOR_VERSION = "map-ready-case-v1"
TARGET_PAPER_COMMIT = "dac7b94207a2683d494bdb388fc48533c9219bdf"
FORMAL_GATES = {
    "rankable": False,
    "paper_score_eligible": False,
    "physical_qualified": False,
}


class MapReadyCaseError(ValueError):
    """Raised when an orchestration output target itself is unsafe."""


def _require_closed_gates(value: Mapping[str, Any], name: str) -> None:
    if value.get("formal_gates") != FORMAL_GATES:
        raise ValueError(f"{name}.formal_gates must remain closed")


def _load_object(
    value: Mapping[str, Any] | str | Path, name: str
) -> tuple[dict[str, Any], str, str]:
    if isinstance(value, Mapping):
        result = dict(value)
        return result, canonical_digest(result), "canonical_json_sha256"
    path = Path(value).resolve()
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{name} is not a regular JSON file: {path}")
    try:
        raw = path.read_bytes()
        result = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {name}: {path}") from exc
    if not isinstance(result, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return dict(result), hashlib.sha256(raw).hexdigest(), "file_sha256"


def _paper_commit(value: str | None) -> tuple[str, str | None]:
    """Return the immutable engineering identity and optional caller expectation.

    A caller-provided value is descriptive only.  It can never replace the
    scorebook identity used to decide whether the implementation matches the
    paper.
    """

    if value is not None and (
        not isinstance(value, str)
        or len(value) != 40
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ValueError("paper_commit must be a lowercase 40-character Git commit")
    commit = ENGINEERING_PAPER_COMMIT
    if len(commit) != 40 or any(
        char not in "0123456789abcdef" for char in commit
    ):
        raise ValueError("engineering scorebook PAPER_COMMIT is invalid")
    return commit, value


def _plan_matches_binding(plan: Mapping[str, Any], binding: Mapping[str, Any]) -> str:
    digest = canonical_digest(plan)
    expected = binding.get("plan_digest")
    if expected != digest:
        raise ValueError(
            "explicit plan digest does not match binding.plan_digest "
            f"(expected={expected}, actual={digest})"
        )
    return digest


def _catalog_matches_binding(catalog: Mapping[str, Any], binding: Mapping[str, Any]) -> str:
    digest = canonical_digest(catalog)
    expected = binding.get("catalog_digest")
    if expected != digest:
        raise ValueError(
            "explicit catalog digest does not match binding.catalog_digest "
            f"(expected={expected}, actual={digest})"
        )
    return digest


def _contract_matches_binding(
    contract_artifact: Mapping[str, Any],
    binding: Mapping[str, Any],
    *,
    catalog_input_digest: str,
    catalog_digest_kind: str,
) -> dict[str, Any]:
    """Select and verify the exact anchor contract named by the binding."""

    anchor_id = binding.get("anchor_id")
    if not isinstance(anchor_id, str) or not anchor_id.strip():
        raise ValueError("binding.anchor_id must identify the selected contract")
    rows = contract_artifact.get("contracts")
    mode = "selected_contract"
    source_catalog_sha256 = None
    try:
        from tools.benchmark_branch.pilot_contract import (
            PilotContractError,
            validate_pilot_contract,
            validate_pilot_contract_set,
        )
    except ImportError as exc:
        raise ValueError("pending-contract validator is unavailable") from exc
    if rows is not None:
        mode = "contract_artifact"
        if not isinstance(rows, list):
            raise ValueError("contract_artifact.contracts must be a list")
        try:
            validate_pilot_contract_set(contract_artifact)
        except PilotContractError as exc:
            raise ValueError(
                f"contract artifact failed pending-contract validation: {exc}"
            ) from exc
        matches = [row for row in rows if isinstance(row, Mapping) and row.get("anchor_id") == anchor_id]
        if len(matches) != 1:
            raise ValueError(f"contract artifact must contain exactly one contract for {anchor_id}")
        selected = dict(matches[0])
        source_catalog_sha256 = contract_artifact.get("source_catalog_sha256")
        if catalog_digest_kind != "file_sha256":
            raise ValueError(
                "a whole contract artifact requires catalog as a file so "
                "source_catalog_sha256 can be verified"
            )
        if source_catalog_sha256 != catalog_input_digest:
            raise ValueError("contract artifact source_catalog_sha256 does not match catalog file bytes")
    else:
        selected = dict(contract_artifact)
        if selected.get("anchor_id") != anchor_id:
            raise ValueError("selected contract anchor_id does not match binding.anchor_id")
        try:
            validate_pilot_contract(selected)
        except PilotContractError as exc:
            raise ValueError(
                f"selected contract failed pending-contract validation: {exc}"
            ) from exc

    selected_paper_sha = selected.get("paper_sha")
    artifact_paper_sha = (
        contract_artifact.get("paper_sha") if mode == "contract_artifact" else None
    )
    if selected_paper_sha != ENGINEERING_PAPER_COMMIT:
        raise ValueError("selected contract paper identity is stale")
    if mode == "contract_artifact" and artifact_paper_sha != ENGINEERING_PAPER_COMMIT:
        raise ValueError("contract artifact paper identity is stale")

    binding_contract = binding.get("contract")
    if not isinstance(binding_contract, Mapping):
        raise ValueError("binding.contract must be an object")
    selected_digest = canonical_digest(selected)
    if binding_contract.get("contract_digest") != selected_digest:
        raise ValueError(
            "selected contract digest does not match binding.contract.contract_digest "
            f"(expected={binding_contract.get('contract_digest')}, actual={selected_digest})"
        )
    return {
        "mode": mode,
        "anchor_id": anchor_id,
        "contract_digest": selected_digest,
        "paper_sha": selected_paper_sha,
        "status": selected.get("status", contract_artifact.get("status")),
        "selected_paper_sha": selected_paper_sha,
        "selected_status": selected.get("status"),
        "artifact_paper_sha": artifact_paper_sha,
        "artifact_status": contract_artifact.get("status") if mode == "contract_artifact" else None,
        "source_catalog_sha256": source_catalog_sha256,
        "source_catalog_digest_kind": catalog_digest_kind,
    }


def _emos_initialize(value: Mapping[str, Any]) -> dict[str, Any]:
    """Exercise only the registered ``initialize(H_r, o_r)`` boundary."""

    if set(value) != {"request", "H_r", "o_r"}:
        raise ValueError("emos_input must contain exactly request, H_r and o_r")
    request_value = value["request"]
    if not isinstance(request_value, Mapping):
        raise ValueError("emos_input.request must be an object")
    from .emos import CaseAssignmentRequest, EmosCaseDecisionAdapter

    request = CaseAssignmentRequest(**dict(request_value))
    adapter = EmosCaseDecisionAdapter(request=request, require_initialize=True)
    initialized = adapter.initialize(value["H_r"], value["o_r"])
    return {
        "status": "initialized_dry_run",
        "input_digest": canonical_digest(value),
        "scenario_id": initialized.scenario_id,
        "task_count": len(initialized.task_ids),
        "robot_count": len(initialized.robot_positions),
        "physical_success": None,
    }


def _terminal_chain(
    value: Mapping[str, Any],
    *,
    output_root: Path,
    case_manifest: Mapping[str, Any],
    binding: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal explicitly supplied terminal material and optionally run results."""
    allowed = {"terminals", "parameters", "production", "partial"}
    if set(value) - allowed or not {"terminals", "parameters"}.issubset(value):
        raise ValueError(
            "terminal_payload must contain terminals and parameters; "
            "production or partial is optional"
        )
    terminals = value["terminals"]
    if isinstance(terminals, (str, bytes)) or not isinstance(terminals, Sequence) or not terminals:
        raise ValueError("terminal_payload.terminals must be a non-empty sequence")
    parameters = value["parameters"]
    if not isinstance(parameters, Mapping):
        raise ValueError("terminal parameters must be an object")
    production_requested = "production" in value
    partial_requested = "partial" in value
    if production_requested and partial_requested:
        raise ValueError(
            "terminal_payload.production and terminal_payload.partial are mutually exclusive"
        )
    production = value.get("production")
    partial = value.get("partial")
    if production_requested and not isinstance(production, Mapping):
        raise ValueError("terminal_payload.production must be an object")
    if partial_requested:
        if not isinstance(partial, Mapping):
            raise ValueError("terminal_payload.partial must be an object")
        if set(partial) - {"calibration"}:
            raise ValueError(
                "terminal_payload.partial may contain only optional calibration"
            )
    catalog_digest = _catalog_matches_binding(catalog, binding)

    terminals = _normalize_terminals(
        terminals,
        case_manifest=case_manifest,
        plan=plan,
    )

    # Kept lazy so the audit/manifest dry-run works on checkouts where the
    # terminal runner is awaiting the already-reviewed remote integration.
    from .terminal_case_runner_v1 import seal_terminal_artifacts

    sealed = seal_terminal_artifacts(
        terminals=terminals,
        artifact_root=output_root / "terminal_evidence",
        case_manifest=case_manifest,
        catalog=catalog,
        plan=plan,
        parameters=parameters,
    )
    _require_closed_gates(sealed, "sealed terminal result")
    readiness = sealed.get("readiness")
    if not isinstance(readiness, Mapping):
        raise ValueError("sealed terminal result has no readiness object")
    _require_closed_gates(readiness, "terminal readiness")
    result: dict[str, Any] = {
        "status": "terminal_sealed",
        "catalog_digest": catalog_digest,
        "artifact_manifest_digest": sealed["artifact_manifest_digest"],
        "readiness": sealed["readiness"],
        "formal_gates": dict(FORMAL_GATES),
    }
    if not production_requested and not partial_requested:
        return result
    if partial_requested:
        from .case_partial_results_v1 import run_partial_case

        partial_report = run_partial_case(
            receipts=sealed["receipts"],
            case_manifest=case_manifest,
            artifact_manifest=sealed["artifact_manifest"],
            artifact_root=sealed["artifact_root"],
            calibration=partial.get("calibration"),
            output=output_root / "partial_results",
        )
        _require_closed_gates(partial_report, "partial result")
        result.update({
            "status": "partial_results_verified",
            "partial": partial_report,
            "replay_verified": True,
            "formal_gates": dict(FORMAL_GATES),
        })
        return result

    required = {"calibration", "panel_bindings", "panel_projection_binding"}
    optional = {
        "panel_endpoint_receipts", "panel_bootstrap_resamples",
        "panel_bootstrap_seed", "panel_structured",
    }
    if not required.issubset(production) or set(production) - (required | optional):
        raise ValueError(
            "terminal_payload.production requires calibration, panel_bindings "
            "and panel_projection_binding"
        )
    from .production_results_v1 import run_production_case_from_artifacts

    report = run_production_case_from_artifacts(
        artifact_inputs=sealed["artifact_inputs"],
        case_manifest=case_manifest,
        catalog=catalog,
        plan=plan,
        parameters=parameters,
        calibration=production["calibration"],
        artifact_manifest=sealed["artifact_manifest"],
        artifact_root=sealed["artifact_root"],
        output=output_root / "production_results",
        panel_bindings=production["panel_bindings"],
        panel_endpoint_receipts=production.get("panel_endpoint_receipts"),
        panel_bootstrap_resamples=production.get("panel_bootstrap_resamples", 0),
        panel_bootstrap_seed=production.get("panel_bootstrap_seed", 0),
        panel_projection_binding=production["panel_projection_binding"],
        panel_structured=production.get("panel_structured"),
    )
    _require_closed_gates(report, "production result")
    result.update({
        "status": "production_results_verified",
        "production": report,
        "replay_verified": True,
        "formal_gates": dict(FORMAL_GATES),
    })
    return result


def _registered_evaluator(
    case_manifest: Mapping[str, Any], plan: Mapping[str, Any]
) -> Any:
    """Instantiate only the evaluator named by the validated case manifest."""

    evaluator_binding = case_manifest.get("evaluator")
    if not isinstance(evaluator_binding, Mapping):
        raise ValueError("case manifest evaluator binding is missing")
    evaluator_id = evaluator_binding.get("evaluator_id")
    evaluator_version = evaluator_binding.get("evaluator_version")

    from .evaluator.room06_livt04 import (
        EVALUATOR_ID,
        EVALUATOR_VERSION,
        Room06LivT04Evaluator,
    )

    if (evaluator_id, evaluator_version) != (EVALUATOR_ID, EVALUATOR_VERSION):
        raise ValueError(
            "case manifest evaluator is not registered for serialized terminal input: "
            f"{evaluator_id}@{evaluator_version}"
        )
    obligation_binding = plan.get("oracle_obligation_binding")
    if not isinstance(obligation_binding, Mapping):
        raise ValueError(
            "serialized terminal input requires plan.oracle_obligation_binding"
        )
    runtime_binding = plan.get("room06_runtime_binding")
    if not isinstance(runtime_binding, Mapping):
        raise ValueError(
            "serialized terminal input requires plan.room06_runtime_binding"
        )
    return Room06LivT04Evaluator(
        case_manifest=case_manifest,
        obligation_binding=obligation_binding,
        runtime_binding=runtime_binding,
    )


def _normalize_terminals(
    terminals: Sequence[Mapping[str, Any]],
    *,
    case_manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Rehydrate JSON jobs without widening the executable trust boundary.

    Live Python callers may continue to supply an explicit evaluator object.
    JSON callers omit it; the evaluator is selected from a closed registry by
    the already validated manifest identity.
    """

    from dataclasses import fields
    from tools.benchmark_branch.models import BranchJob

    job_fields = {field.name for field in fields(BranchJob)}
    required = {"job", "runtime", "evaluator_result"}
    allowed = required | {"evaluator", "label", "receipt_id"}
    normalized: list[dict[str, Any]] = []
    for index, raw_item in enumerate(terminals):
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"terminal_payload.terminals[{index}] must be an object")
        if not required.issubset(raw_item) or set(raw_item) - allowed:
            raise ValueError(
                f"terminal_payload.terminals[{index}] must contain "
                "job/runtime/evaluator_result and only registered optional fields"
            )
        item = dict(raw_item)
        serialized_job = isinstance(item["job"], Mapping)
        if serialized_job:
            unknown = sorted(set(item["job"]) - job_fields)
            if unknown:
                raise ValueError(
                    f"terminal_payload.terminals[{index}].job has unsupported fields: {unknown}"
                )
            try:
                item["job"] = BranchJob.from_mapping(item["job"])
            except (TypeError, ValueError, KeyError) as exc:
                raise ValueError(
                    f"terminal_payload.terminals[{index}].job is invalid: {exc}"
                ) from exc
            if not isinstance(item["runtime"], Mapping):
                raise ValueError(
                    f"terminal_payload.terminals[{index}].runtime must be a JSON object"
                )
            runtime_payload = item["runtime"].get(
                "room06_livt04", item["runtime"]
            )
            if not isinstance(runtime_payload, Mapping) or not isinstance(
                runtime_payload.get("episode"), Mapping
            ):
                raise ValueError(
                    f"terminal_payload.terminals[{index}].runtime must contain an explicit episode"
                )
            episode = runtime_payload["episode"]
            expected_identity = {
                "condition_id": item["job"].condition_id,
                "branch": item["job"].branch_type,
                "repeat_id": item["job"].repeat_id,
                "system_id": item["job"].system_id,
                "base_instance_id": item["job"].base_instance_id,
            }
            for key, expected in expected_identity.items():
                if episode.get(key) != expected:
                    raise ValueError(
                        f"terminal_payload.terminals[{index}].runtime.episode.{key} "
                        "does not match the serialized BranchJob"
                    )
        elif not isinstance(item["job"], BranchJob):
            raise ValueError(
                f"terminal_payload.terminals[{index}].job must be a BranchJob or mapping"
            )

        if not isinstance(item["evaluator_result"], Mapping):
            raise ValueError(
                f"terminal_payload.terminals[{index}].evaluator_result must be an object"
            )
        evaluator = item.get("evaluator")
        if evaluator is None:
            evaluator = _registered_evaluator(case_manifest, plan)
            item["evaluator"] = evaluator
        else:
            manifest_evaluator = case_manifest.get("evaluator")
            if not isinstance(manifest_evaluator, Mapping) or (
                getattr(evaluator, "evaluator_id", None),
                getattr(evaluator, "evaluator_version", None),
            ) != (
                manifest_evaluator.get("evaluator_id"),
                manifest_evaluator.get("evaluator_version"),
            ):
                raise ValueError(
                    f"terminal_payload.terminals[{index}].evaluator does not match case manifest"
                )
        normalized.append(item)
    return normalized


def run_map_ready_case(
    *,
    semantic_root: str | Path,
    robot_pool: str | Path,
    binding: Mapping[str, Any] | str | Path,
    catalog: Mapping[str, Any] | str | Path,
    contract_artifact: Mapping[str, Any] | str | Path,
    plan: Mapping[str, Any] | str | Path,
    output_dir: str | Path,
    paper_commit: str | None = None,
    emos_input: Mapping[str, Any] | str | Path | None = None,
    terminal_payload: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Execute the map-ready seam and write one immutable execution receipt.

    Scientific-input failures are represented in the returned receipt.  An
    existing output directory is the one hard error because writing into it
    could overwrite or silently mix evidence from another attempt.
    """

    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise MapReadyCaseError(f"output_dir already exists; use a new directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()

    receipt: dict[str, Any] = {
        "schema": ORCHESTRATOR_SCHEMA,
        "orchestrator_version": ORCHESTRATOR_VERSION,
        "status": "failed_closed",
        "paper_commit": None,
        "paper_commit_source": "engineering_scorebook",
        "requested_paper_commit": None,
        "requested_paper_aligned": None,
        "target_paper_commit": TARGET_PAPER_COMMIT,
        "paper_aligned": False,
        "inputs": {},
        "audit": None,
        "manifest": None,
        "contract": None,
        "emos": None,
        "terminal": None,
        "failures": [],
        "next_action": [],
        "formal_gates": dict(FORMAL_GATES),
    }
    try:
        commit, requested_commit = _paper_commit(paper_commit)
        receipt["paper_commit"] = commit
        receipt["requested_paper_commit"] = requested_commit
        receipt["paper_aligned"] = ENGINEERING_PAPER_COMMIT == TARGET_PAPER_COMMIT
        receipt["requested_paper_aligned"] = (
            None if requested_commit is None else requested_commit == commit
        )
        if requested_commit is not None and requested_commit != commit:
            raise ValueError(
                "requested paper_commit is stale and cannot override the "
                "engineering scorebook identity"
            )
        binding_value, binding_input_digest, binding_digest_kind = _load_object(binding, "binding")
        catalog_value, catalog_input_digest, catalog_digest_kind = _load_object(catalog, "catalog")
        contract_value, contract_input_digest, contract_digest_kind = _load_object(
            contract_artifact, "contract_artifact"
        )
        plan_value, plan_input_digest, plan_digest_kind = _load_object(plan, "plan")
        receipt["inputs"].update({
            "semantic_root": str(Path(semantic_root).resolve()),
            "robot_pool": str(Path(robot_pool).resolve()),
            "binding_input_digest": binding_input_digest,
            "binding_input_digest_kind": binding_digest_kind,
            "binding_canonical_digest": canonical_digest(binding_value),
            "catalog_input_digest": catalog_input_digest,
            "catalog_input_digest_kind": catalog_digest_kind,
            "catalog_canonical_digest": canonical_digest(catalog_value),
            "contract_input_digest": contract_input_digest,
            "contract_input_digest_kind": contract_digest_kind,
            "plan_input_digest": plan_input_digest,
            "plan_input_digest_kind": plan_digest_kind,
        })

        # The explicit audit is intentionally first.  build_case_manifest
        # re-audits defensively, but this receipt retains the original result.
        audit = audit_case_inputs(
            semantic_root,
            robot_pool,
            semantic_requirements=binding_value.get("semantic_requirements"),
        )
        receipt["audit"] = audit
        receipt["inputs"].update({
            "semantic_audit_digest": canonical_digest(audit.get("semantic_map")),
            "robot_pool_digest": audit.get("robot_pool", {}).get("pool_digest"),
        })
        if audit.get("case_ready") is not True:
            raise ValueError("case input audit is not ready: " + "; ".join(audit.get("failures", [])))
        catalog_digest = _catalog_matches_binding(catalog_value, binding_value)
        plan_digest = _plan_matches_binding(plan_value, binding_value)
        receipt["inputs"]["catalog_canonical_digest"] = catalog_digest
        receipt["inputs"]["plan_canonical_digest"] = plan_digest
        receipt["contract"] = _contract_matches_binding(
            contract_value,
            binding_value,
            catalog_input_digest=catalog_input_digest,
            catalog_digest_kind=catalog_digest_kind,
        )
        manifest = build_case_manifest(
            semantic_root=semantic_root,
            robot_pool_path=robot_pool,
            binding=binding_value,
        )
        receipt["manifest"] = {
            "case_id": manifest.get("case_id"),
            "manifest_digest": manifest.get("manifest_digest"),
            "status": "ready_for_terminal_python_api",
        }

        if emos_input is not None:
            emos_value, emos_digest, emos_digest_kind = _load_object(emos_input, "emos_input")
            receipt["inputs"]["emos_input_digest"] = emos_digest
            receipt["inputs"]["emos_input_digest_kind"] = emos_digest_kind
            receipt["emos"] = _emos_initialize(emos_value)

        if terminal_payload is None:
            receipt["status"] = "ready_for_terminal_python_api"
            receipt["next_action"].extend([
                "provide_serialized_terminal_payload_or_python_api",
                "ready_for_terminal_python_api",
            ])
        else:
            terminal_value, terminal_digest, terminal_digest_kind = _load_object(
                terminal_payload, "terminal_payload"
            )
            receipt["inputs"]["terminal_payload_digest"] = terminal_digest
            receipt["inputs"]["terminal_payload_digest_kind"] = terminal_digest_kind
            receipt["terminal"] = _terminal_chain(
                terminal_value,
                output_root=output,
                case_manifest=manifest,
                binding=binding_value,
                catalog=catalog_value,
                plan=plan_value,
            )
            if receipt["terminal"]["status"] == "production_results_verified":
                receipt["status"] = "production_results_verified"
                receipt["next_action"].append("formal_release_matrix_pending")
            elif receipt["terminal"]["status"] == "partial_results_verified":
                receipt["status"] = "partial_results_verified"
                receipt["next_action"].append(
                    "complete_registered_matrix_for_paper_results"
                )
            else:
                receipt["status"] = "terminal_sealed"
                receipt["next_action"].append(
                    "select_partial_export_or_provide_explicit_fixed_panel_production_inputs"
                )
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        detail = str(exc)
        code = "MAP_READY_EXECUTION_FAILED"
        receipt["failures"].append({"code": code, "detail": detail})
        receipt["next_action"].append("correct_explicit_inputs_and_use_a_new_output_directory")

    if receipt["paper_aligned"] is not True:
        receipt["next_action"].append("paper_alignment_pending")
    receipt["execution_receipt_digest"] = canonical_digest(receipt)
    (output / "execution_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic-root", required=True, type=Path)
    parser.add_argument("--robot-pool", required=True, type=Path)
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--contract-artifact", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--paper-commit")
    parser.add_argument("--emos-input", type=Path)
    parser.add_argument("--terminal-payload", type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = run_map_ready_case(
            semantic_root=args.semantic_root,
            robot_pool=args.robot_pool,
            binding=args.binding,
            catalog=args.catalog,
            contract_artifact=args.contract_artifact,
            plan=args.plan,
            output_dir=args.output_dir,
            paper_commit=args.paper_commit,
            emos_input=args.emos_input,
            terminal_payload=args.terminal_payload,
        )
    except MapReadyCaseError as exc:
        parser.error(str(exc))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt["status"] != "failed_closed" else 2


__all__ = [
    "FORMAL_GATES",
    "MapReadyCaseError",
    "ORCHESTRATOR_SCHEMA",
    "ORCHESTRATOR_VERSION",
    "TARGET_PAPER_COMMIT",
    "run_map_ready_case",
]


if __name__ == "__main__":
    raise SystemExit(main())
