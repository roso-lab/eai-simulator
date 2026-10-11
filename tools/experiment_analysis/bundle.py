"""Exclusive result publication and read-only, detached snapshot verification."""

import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import stat

from . import ANALYSIS_VERSION
from .batch import _validate_handoff, assemble
from .collector import bind_attempts, make_dataset
from .engine import analyze
from .planning import readiness
from .schema import AnalysisError, Dataset, _pairs, canonical, digest, number, obj, sha, strings, text


BASE_FILES = {"design.json", "data.json", "report.json", "results.csv", "report.md"}
EXTRA_FILES = {
    "compute": {"readiness.json"},
    "synthetic": {"readiness.json", "analytic_checks.json"},
    "bound": {"evaluated_outcomes.json", "handoff.json"},
    "batch": {"registration.json", "sources.json", "batch_audit.json", "readiness.json"},
}


def write_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def output_directory(path: str | Path) -> Path:
    target = Path(path).resolve()
    target.mkdir(parents=True, exist_ok=False)
    return target


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_exports(report: dict) -> dict[str, str]:
    """Render secondary views deterministically so verification covers them."""
    fields = ["estimand_id", "system_id", "kind", "status", "estimate", "ci_low", "ci_high", "interval_status", "reasons", "provenance", "publishable"]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in report["results"]:
        out = {key: row[key] for key in fields[:5]}
        out.update(ci_low=row["interval"]["low"], ci_high=row["interval"]["high"], interval_status=row["interval"]["status"],
                   reasons=";".join(row["reasons"]), provenance=report["qualification"], publishable=False)
        writer.writerow({k: "'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v for k, v in out.items()})
    lines = ["# Descriptive calculation report", "", f"Source: {report['qualification']}. Publishable: false.",
             "External evaluator and protocol release verification remain required.", "",
             f"Design SHA-256: `{report['design_digest']}`", f"Dataset SHA-256: `{report['dataset_digest']}`", "",
             "| Estimand | System | Status | Estimate | 95% cluster interval | Reason |", "|---|---|---|---:|---|---|"]
    for row in report["results"]:
        ci = row["interval"]
        interval = "N/A: " + str(ci["reason"]) if ci["status"] != "computed" else f"[{ci['low']:.6g}, {ci['high']:.6g}]"
        point = "N/A" if row["estimate"] is None else f"{row['estimate']:.6g}"
        lines.append("| " + " | ".join(_md(v) for v in (row["estimand_id"], row["system_id"], row["status"], point, interval, ";".join(row["reasons"]))) + " |")
    return {"results.csv": stream.getvalue(), "report.md": "\n".join(lines) + "\n"}


def export_report(root: Path, report: dict, *, bundle_type: str) -> None:
    write_json(root / "report.json", report)
    for name, content in render_exports(report).items():
        with (root / name).open("x", encoding="utf-8", newline="") as stream:
            stream.write(content)
    expected = BASE_FILES | EXTRA_FILES[bundle_type]
    if {p.name for p in root.iterdir()} != expected:
        raise AnalysisError("output does not have the exact bundle file set")
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(expected)}
    # Written last: an interrupted export has no complete bundle manifest.
    write_json(root / "output_manifest.json", {"schema": ANALYSIS_VERSION + ".output_manifest",
               "bundle_type": bundle_type, "files": files, "publishable": False})


def export_batch(path: str | Path, design: dict, registration: dict, sources: list[dict]) -> Path:
    data, audit = assemble(design, registration, sources)
    report = analyze(design, data)
    root = output_directory(path)
    payloads = {"design": design, "data": data, "registration": registration,
                "sources": {"schema": ANALYSIS_VERSION + ".source_snapshots",
                            "sources": sorted(sources, key=lambda s: s["handoff"]["run_id"])},
                "batch_audit": audit, "readiness": readiness(design)}
    for name, value in payloads.items():
        write_json(root / (name + ".json"), value)
    export_report(root, report, bundle_type="batch")
    return root


def _read_regular(root_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AnalysisError("bundle members must be regular files")
        return stream.read()


def _parse(payload: bytes):
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
    canonical(value)  # Reject NaN/Infinity, including overflowed numeric literals.
    return value


def verify_bundle(path: str | Path, *, recompute: bool = False) -> dict:
    """Verify a single byte snapshot; never run archived code or change inputs.

    Handoffs are detached snapshots, not archived collector ledgers. Passing
    proves consistency of their contents, not physical truth or trusted time.
    """
    root = Path(path).absolute()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        manifest = _parse(_read_regular(root_fd, "output_manifest.json"))
        obj(manifest, "schema bundle_type files publishable")
        if manifest["schema"] != ANALYSIS_VERSION + ".output_manifest" or manifest["publishable"] is not False:
            raise AnalysisError("invalid output manifest")
        kind = manifest["bundle_type"]
        if not isinstance(kind, str) or kind not in EXTRA_FILES:
            raise AnalysisError("unsupported bundle type")
        expected = BASE_FILES | EXTRA_FILES[kind]
        if not isinstance(manifest["files"], dict) or set(manifest["files"]) != expected:
            raise AnalysisError("bundle manifest has an incorrect file set")
        if set(os.listdir(root_fd)) != expected | {"output_manifest.json"}:
            raise AnalysisError("bundle has missing or extra members")
        blobs = {name: _read_regular(root_fd, name) for name in sorted(expected)}
    finally:
        os.close(root_fd)
    for name, payload in blobs.items():
        if hashlib.sha256(payload).hexdigest() != sha(manifest["files"][name]):
            raise AnalysisError(f"bundle checksum mismatch: {name}")
    parsed = {name: _parse(payload) for name, payload in blobs.items() if name.endswith(".json")}
    design, data, report = (parsed[name + ".json"] for name in ("design", "data", "report"))
    obj(report, "schema design_id design_digest dataset_digest protocol_digest authority provenance qualification "
                "analysis_environment publishable release_blockers records unreferenced_records results results_digest")
    dataset = Dataset.parse(data, design)
    qualification = "synthetic_validation" if dataset.provenance["kind"] == "synthetic" else "measured_unverified"
    expected_metadata = {"schema": ANALYSIS_VERSION + ".report", "design_id": dataset.design.id,
                         "design_digest": digest(design), "dataset_digest": digest(data),
                         "protocol_digest": dataset.design.protocol_digest, "authority": dataset.design.authority,
                         "provenance": dataset.provenance, "qualification": qualification,
                         "publishable": False, "records": len(dataset.records),
                         "release_blockers": ["external_evaluator_and_protocol_release_verification_required"]}
    if any(canonical(report.get(k)) != canonical(v) for k, v in expected_metadata.items()):
        raise AnalysisError("report identity or qualification mismatch")
    if report.get("results_digest") != digest(report["results"]):
        raise AnalysisError("report results digest mismatch")
    if not isinstance(report["results"], list):
        raise AnalysisError("report results must be an array")
    result_keys = set()
    planned_results = {(e.id, s): e.kind for e in dataset.design.estimands
                       for s in (("registered_contrast",) if e.terms[0].system_id else dataset.design.systems)}
    for row in report["results"]:
        obj(row, "estimand_id system_id kind unit status estimate reasons coverage family_coverage units interval qualification publishable", "components")
        key = (text(row["estimand_id"]), text(row["system_id"]))
        if key in result_keys or planned_results.get(key) != row["kind"]:
            raise AnalysisError("duplicate or unregistered report result")
        result_keys.add(key)
        if row["qualification"] != "descriptive_only" or row["publishable"] is not False:
            raise AnalysisError("result qualification mismatch")
        text(row["unit"])
        strings(row["reasons"], empty=True)
        if row["status"] == "computed":
            number(row["estimate"])
        elif row["status"] != "N/A" or row["estimate"] is not None:
            raise AnalysisError("invalid result status/estimate")
        obj(row["interval"], "status reason low high", "resamples undefined_resamples cluster independent_clusters method confidence seed")
    if result_keys != set(planned_results):
        raise AnalysisError("report omits registered results")
    environment = obj(report["analysis_environment"], "python source_files code_digest")
    if not isinstance(environment["source_files"], dict) or environment["code_digest"] != digest(environment["source_files"]):
        raise AnalysisError("report analysis source digest mismatch")
    for name, content in render_exports(report).items():
        if content.encode("utf-8") != blobs[name]:
            raise AnalysisError(f"derived export differs from report: {name}")
    if "readiness.json" in parsed and canonical(parsed["readiness.json"]) != canonical(readiness(design)):
        raise AnalysisError("readiness differs from frozen design")
    if kind == "batch":
        snapshots = obj(parsed["sources.json"], "schema sources")
        if snapshots["schema"] != ANALYSIS_VERSION + ".source_snapshots":
            raise AnalysisError("invalid source snapshots")
        rebound, audit = assemble(design, parsed["registration.json"], snapshots["sources"])
        if canonical(data) != canonical(rebound) or canonical(audit) != canonical(parsed["batch_audit.json"]):
            raise AnalysisError("batch selection or audit differs from preserved sources")
    elif kind == "bound":
        handoff, outcomes = parsed["handoff.json"], parsed["evaluated_outcomes.json"]
        _validate_handoff(handoff)
        attempts = bind_attempts(design, outcomes, handoff)
        rebound = make_dataset(design, [a["record"] for a in attempts], handoff["provenance"], outcomes["evaluator"])
        if canonical(data) != canonical(rebound):
            raise AnalysisError("bound dataset differs from preserved evidence")
    elif kind == "synthetic":
        checks = parsed["analytic_checks.json"]
        obj(checks, "checks passed publishable")
        if checks["publishable"] is not False or dataset.provenance["kind"] != "synthetic":
            raise AnalysisError("invalid synthetic qualification")
        results = {(r["estimand_id"], r["system_id"]): r["estimate"] for r in report["results"]}
        seen = set()
        for check in checks["checks"]:
            obj(check, "estimand system expected actual passed")
            key = (check["estimand"], check["system"])
            if key in seen or key not in results or canonical(check["actual"]) != canonical(results[key]):
                raise AnalysisError("analytic check/result mismatch")
            seen.add(key)
            passed = check["actual"] is not None and math.isclose(check["expected"], check["actual"], abs_tol=1e-12)
            if check["passed"] is not passed:
                raise AnalysisError("incorrect analytic check verdict")
        if seen != set(results) or checks["passed"] is not all(c["passed"] for c in checks["checks"]):
            raise AnalysisError("incomplete analytic checks")
    if recompute:
        current_sources = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob("*.py"))}
        if environment["python"] != platform.python_version() or environment["source_files"] != current_sources:
            raise AnalysisError("analysis environment differs; recompute requires the pinned Python and source files")
        if canonical(analyze(design, data)) != canonical(report):
            raise AnalysisError("recomputed report differs from bundle")
    return {"schema": ANALYSIS_VERSION + ".bundle_verification", "status": "passed", "bundle_type": kind,
            "qualification": "detached_content_consistency_only", "publishable": False,
            "files_checked": len(blobs), "selection_replayed": kind in {"batch", "bound"},
            "calculation_recomputed": recompute, "raw_collector_seal_reverified": False,
            "registration_time_verified": False}
