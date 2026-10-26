"""One-shot production seam from evaluator receipts to a replayable report.

This module is intentionally CPU-only.  It composes the already versioned
boundaries instead of reimplementing any paper arithmetic:

``raw receipts + case manifest + evidence``
    -> ``receipt_adapter_v3.normalize_receipts``
    -> ``results_pipeline_v3.compute_results``
    -> ``export_results``
    -> ``verify_export``

The production entry point requires a bound case and content-addressed
evidence.  It always emits a diagnostic/unqualified report; formal gates are
owned by the release process and remain closed here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .receipt_adapter_v3 import ReceiptAdapterError, normalize_receipts
from .receipt_bridge_v1 import ReceiptBridgeError, build_raw_receipt_from_artifact
from .paper_panel_v1 import PanelAggregationError, build_panel_estimates_from_receipts
from .paper_panel_v1 import _digest as _panel_digest
from .paper_projection_v1 import PaperProjectionError, apply_panel_projection, project_panel_package
from .results_pipeline_v3 import (
    ResultsPipelineError,
    compute_results,
    export_results,
    verify_export,
)
from .records_v3 import canonical_digest


RUNNER_SCHEMA = "heterocoord.production-results-run.v1"
RUNNER_VERSION = "production-results-v1"


class ProductionRunError(ValueError):
    """Raised when the production seam cannot complete a verified run."""


def build_paper_panel_package(
    *,
    receipts: Sequence[Mapping[str, Any]],
    panel_bindings: Mapping[str, Mapping[str, Any]],
    endpoint_receipts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    bootstrap_resamples: int = 0,
    bootstrap_seed: int = 0,
    structured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build fixed-cell paper estimates from evaluator-owned raw receipts.

    This helper is intentionally separate from raw receipt validation.  By
    itself the package is an auditable statistical view; when a
    ``panel_projection_binding`` is supplied to ``run_production_case``, the
    validated projection becomes the authoritative D1--D6 input.  Neither
    path opens a qualification gate.
    """

    try:
        if endpoint_receipts is None:
            package = build_panel_estimates_from_receipts(
                receipts,
                panel_bindings,
                bootstrap_resamples=bootstrap_resamples,
                bootstrap_seed=bootstrap_seed,
            )
        else:
            if not isinstance(endpoint_receipts, Mapping):
                raise ProductionRunError("endpoint_receipts must be an object keyed by panel endpoint")
            expected = set(panel_bindings)
            actual = set(endpoint_receipts)
            if actual != expected:
                missing = sorted(expected - actual)
                unknown = sorted(actual - expected)
                raise ProductionRunError(
                    f"endpoint_receipts keys must match panel_bindings (missing={missing}, unknown={unknown})"
                )
            endpoints: dict[str, Any] = {}
            for endpoint, binding in panel_bindings.items():
                subset = endpoint_receipts[endpoint]
                if isinstance(subset, (str, bytes)) or not isinstance(subset, Sequence):
                    raise ProductionRunError(f"endpoint_receipts[{endpoint}] must be a receipt sequence")
                endpoint_package = build_panel_estimates_from_receipts(
                    subset,
                    {endpoint: binding},
                    bootstrap_resamples=bootstrap_resamples,
                    bootstrap_seed=bootstrap_seed,
                )
                endpoints[endpoint] = endpoint_package["endpoints"][endpoint]
            package = {
                "schema": "heterocoord.paper-panel-package.v1",
                "panel_version": "paper-panel-v1",
                "status": "ready",
                "endpoints": endpoints,
            }
    except (PanelAggregationError, TypeError, ValueError) as exc:
        raise ProductionRunError(f"paper panel construction failed: {exc}") from exc
    package["formal_gates"] = {
        "rankable": False,
        "paper_score_eligible": False,
        "physical_qualified": False,
    }
    if structured is not None:
        if not isinstance(structured, Mapping):
            raise ProductionRunError("paper panel structured section must be an object")
        package["structured"] = dict(structured)
    # The gate declaration is part of the archived package identity.  Rebind
    # the digest after adding it so projection/replay can verify the exact
    # bytes rather than validating a pre-gate intermediate object.
    package["package_digest"] = _panel_digest({k: v for k, v in package.items() if k != "package_digest"})
    return package


def run_production_case(
    *,
    receipts: Sequence[Mapping[str, Any]],
    calibration: Mapping[str, Any],
    case_manifest: Mapping[str, Any],
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    output: str | Path,
    panel_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    panel_endpoint_receipts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    panel_bootstrap_resamples: int = 0,
    panel_bootstrap_seed: int = 0,
    panel_projection_binding: Mapping[str, Any] | None = None,
    panel_structured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize, calculate, export, and replay-verify one production case.

    All scientific facts must already be explicit in ``receipts``.  The
    function rejects legacy/diagnostic-only paths by requiring the case and
    evidence arguments, then delegates schema and semantic checks to the
    existing adapter and results pipeline.
    """

    if case_manifest is None:
        raise ProductionRunError("case_manifest is required for production runs")
    if artifact_manifest is None or artifact_root is None:
        raise ProductionRunError("artifact manifest and root are required for production runs")
    # A bound production case must use the registered fixed-cell estimand.
    # The receipt adapter's row aggregates remain useful for diagnostics, but
    # allowing them to reach the production report would silently reintroduce
    # the pre-projection estimand and make the panel package an attachment.
    if panel_bindings is None or panel_projection_binding is None:
        raise ProductionRunError(
            "production case requires panel_bindings and panel_projection_binding; "
            "receipt-row aggregation is diagnostic-only"
        )
    try:
        normalized = normalize_receipts(
            receipts,
            calibration=calibration,
            case_manifest=case_manifest,
            artifact_manifest=artifact_manifest,
            artifact_root=artifact_root,
            require_artifact_evidence=True,
            scope="measured_unqualified",
        )
        additional_json = None
        panel_package = None
        panel_projection = None
        if panel_bindings is not None:
            panel_package = build_paper_panel_package(
                receipts=receipts,
                panel_bindings=panel_bindings,
                endpoint_receipts=panel_endpoint_receipts,
                bootstrap_resamples=panel_bootstrap_resamples,
                bootstrap_seed=panel_bootstrap_seed,
                structured=panel_structured,
            )
            additional_json = {"paper_panel_package.json": panel_package}
            if panel_projection_binding is not None:
                panel_projection = project_panel_package(panel_package, panel_projection_binding)
                normalized = apply_panel_projection(normalized, panel_projection)
                additional_json["paper_projection.json"] = panel_projection
        report = compute_results(normalized)
        output_path = Path(output)
        output_files = export_results(
            report,
            output_path,
            input_payload=normalized,
            additional_json=additional_json,
        )
        output_manifest = verify_export(output_path)
    except (ReceiptAdapterError, ResultsPipelineError, PaperProjectionError, OSError, TypeError, ValueError, KeyError) as exc:
        raise ProductionRunError(str(exc)) from exc

    return {
        "schema": RUNNER_SCHEMA,
        "runner_version": RUNNER_VERSION,
        "scope": report["scope"],
        "qualification": report["qualification"],
        "formal_gates": dict(report["formal_gates"]),
        "input_digest": normalized["input_digest"],
        "output_manifest": output_manifest,
        "output_files": [str(path) for path in output_files],
        "paper_panel_package": panel_package,
        "paper_projection": panel_projection,
        "d4_first_event_diagnostics": report.get("d4_first_event_diagnostics"),
        "report": report,
    }


def run_production_case_from_artifacts(
    *,
    artifact_inputs: Sequence[Mapping[str, Any]],
    case_manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    plan: Mapping[str, Any],
    parameters: Mapping[str, Any],
    calibration: Mapping[str, Any],
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    output: str | Path,
    panel_bindings: Mapping[str, Mapping[str, Any]] | None = None,
    panel_endpoint_receipts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    panel_bootstrap_resamples: int = 0,
    panel_bootstrap_seed: int = 0,
    panel_projection_binding: Mapping[str, Any] | None = None,
    panel_structured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the production pipeline directly from verified evaluator artifacts.

    Each item in ``artifact_inputs`` contains exactly ``job`` (the immutable
    :class:`BranchJob` identity used by the branch artifact verifier) and
    ``evaluator_envelope`` (the explicit evaluator verdict envelope).  The
    bridge verifies the content-addressed artifact before the producer builds
    a raw receipt.  Scientific facts are therefore still supplied only by the
    evaluator's explicit ``receipt_material``; this helper merely removes the
    error-prone manual raw-receipt handoff.

    The result remains ``measured_unqualified`` and all formal gates remain
    closed.  A missing, duplicated, malformed, or unverifiable artifact fails
    before an output directory is created.
    """

    if not isinstance(artifact_inputs, Sequence) or isinstance(artifact_inputs, (str, bytes)) or not artifact_inputs:
        raise ProductionRunError("artifact_inputs must be a non-empty sequence")
    if artifact_manifest is None or artifact_root is None:
        raise ProductionRunError("artifact manifest and root are required for artifact runs")

    receipts: list[Mapping[str, Any]] = []
    seen_jobs: set[str] = set()
    try:
        for index, item in enumerate(artifact_inputs):
            if not isinstance(item, Mapping) or set(item) != {"job", "evaluator_envelope"}:
                raise ProductionRunError(
                    f"artifact_inputs[{index}] must contain exactly job and evaluator_envelope"
                )
            job = item["job"]
            # BranchJob.identity() is the stable branch binding used by the
            # evaluator artifact verifier.  Avoid accepting an arbitrary
            # mapping that could be mistaken for a branch identity.
            identity = getattr(job, "identity", None)
            if not callable(identity):
                raise ProductionRunError(f"artifact_inputs[{index}].job must be a BranchJob")
            job_identity = identity()
            job_digest = json.dumps(job_identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            if job_digest in seen_jobs:
                raise ProductionRunError(f"duplicate artifact job at index {index}")
            seen_jobs.add(job_digest)
            receipts.append(
                build_raw_receipt_from_artifact(
                    artifact_root=str(artifact_root),
                    job=job,
                    evaluator_envelope=item["evaluator_envelope"],
                    case_manifest=case_manifest,
                    catalog=catalog,
                    plan=plan,
                    parameters=parameters,
                    artifact_manifest_digest=canonical_digest(artifact_manifest),
                )
            )
    except (ReceiptBridgeError, TypeError, ValueError, KeyError) as exc:
        raise ProductionRunError(f"artifact-to-receipt bridge failed: {exc}") from exc

    result = run_production_case(
        receipts=receipts,
        calibration=calibration,
        case_manifest=case_manifest,
        artifact_manifest=artifact_manifest,
        artifact_root=artifact_root,
        output=output,
        panel_bindings=panel_bindings,
        panel_endpoint_receipts=panel_endpoint_receipts,
        panel_bootstrap_resamples=panel_bootstrap_resamples,
        panel_bootstrap_seed=panel_bootstrap_seed,
        panel_projection_binding=panel_projection_binding,
        panel_structured=panel_structured,
    )
    return {**result, "receipt_count": len(receipts), "source": "verified_evaluator_artifacts"}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductionRunError(f"cannot read JSON: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True, type=Path, help="JSON array of raw evaluator receipts")
    parser.add_argument("--calibration", required=True, type=Path)
    parser.add_argument("--case-manifest", required=True, type=Path)
    parser.add_argument("--artifact-manifest", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--panel-bindings", type=Path, help="registered fixed-panel bindings JSON")
    parser.add_argument(
        "--panel-endpoint-receipts",
        type=Path,
        help="JSON object mapping each fixed-panel endpoint to its own receipt sequence",
    )
    parser.add_argument("--panel-bootstrap-resamples", type=int, default=0)
    parser.add_argument("--panel-bootstrap-seed", type=int, default=0)
    parser.add_argument("--panel-projection-binding", type=Path, help="registered binding that makes the fixed panel authoritative")
    try:
        args = parser.parse_args(argv)
        result = run_production_case(
            receipts=_load_json(args.receipts),
            calibration=_load_json(args.calibration),
            case_manifest=_load_json(args.case_manifest),
            artifact_manifest=_load_json(args.artifact_manifest),
            artifact_root=args.artifact_root,
            output=args.output,
            panel_bindings=_load_json(args.panel_bindings) if args.panel_bindings else None,
            panel_endpoint_receipts=(
                _load_json(args.panel_endpoint_receipts) if args.panel_endpoint_receipts else None
            ),
            panel_bootstrap_resamples=args.panel_bootstrap_resamples,
            panel_bootstrap_seed=args.panel_bootstrap_seed,
            panel_projection_binding=_load_json(args.panel_projection_binding) if args.panel_projection_binding else None,
        )
        print(json.dumps({
            "schema": result["schema"],
            "runner_version": result["runner_version"],
            "scope": result["scope"],
            "qualification": result["qualification"],
            "formal_gates": result["formal_gates"],
            "input_digest": result["input_digest"],
            "output_manifest": result["output_manifest"],
        }, ensure_ascii=False, sort_keys=True))
    except (ProductionRunError, OSError, TypeError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RUNNER_SCHEMA",
    "RUNNER_VERSION",
    "ProductionRunError",
    "build_paper_panel_package",
    "run_production_case",
    "run_production_case_from_artifacts",
]
