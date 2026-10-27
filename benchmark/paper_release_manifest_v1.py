"""Content-addressed paper release manifest for the indexed result layer."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .paper_result_index_v1 import (
    BINDING_ENTRY_FIELDS,
    FORMAL_GATES_FALSE,
    INDEX_MANIFEST_SCHEMA,
    PaperResultIndexError,
    STATISTICAL_REPORT_FIELDS,
    STATISTICAL_REPORT_SCHEMA,
    canonical_json_bytes,
    canonical_digest,
    result_key_digest,
    validate_identity,
    _commit,
    _exact_keys,
    _json_object,
    _safe_destination,
    _sha256,
    _text,
    _validate_statistical_report,
    validate_statistical_event_ledger,
    validate_statistical_input,
)


RELEASE_MANIFEST_SCHEMA = "heterocoord.paper-release-manifest.v2"
STATISTICAL_REPORT_SET_SCHEMA = "heterocoord.statistical-report-set.v2"
REQUIRED_ROLES = frozenset({
    "paper_commit",
    "scorebook",
    "implementation_register",
    "comparison_condition_register",
    "required_result_directory",
    "result_index",
    "result_index_manifest",
    "manuscript_cell_map",
    "statistical_report_binding",
    "statistical_reports",
    "d4_diagnostics",
    "panel_projection_bindings",
})


class PaperReleaseManifestError(PaperResultIndexError):
    """Raised when a release manifest is unsafe or stale."""


def _safe_relative(path: str, name: str = "path") -> Path:
    _text(path, name)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in {"", "."}:
        raise PaperReleaseManifestError(f"{name} must be root-relative without traversal")
    return candidate


def _file_entry(root: Path, relative: str) -> dict[str, Any]:
    candidate = _safe_relative(relative)
    root = root.resolve(strict=True)
    path = root / candidate
    if path.is_symlink() or not path.is_file():
        raise PaperReleaseManifestError(f"release dependency is not a regular non-symlink file: {relative}")
    resolved = path.resolve(strict=True)
    if resolved != path.absolute() or root not in resolved.parents:
        raise PaperReleaseManifestError(f"release dependency escapes root: {relative}")
    data = path.read_bytes()
    return {"path": candidate.as_posix(), "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write_new(path: Path, data: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise PaperReleaseManifestError(f"refusing to overwrite existing file: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_manifest_shape(manifest: Mapping[str, Any]) -> None:
    if set(manifest) != {"schema", "paper_commit", "dependencies", "bound_evidence", "formal_gates"}:
        raise PaperReleaseManifestError("release manifest schema mismatch")
    if manifest["schema"] != RELEASE_MANIFEST_SCHEMA:
        raise PaperReleaseManifestError("unsupported release manifest schema")
    _commit(manifest["paper_commit"], "release manifest paper_commit")
    if manifest["formal_gates"] != FORMAL_GATES_FALSE:
        raise PaperReleaseManifestError("release manifest formal gates must remain false")
    dependencies = manifest["dependencies"]
    if not isinstance(dependencies, Mapping) or set(dependencies) != REQUIRED_ROLES:
        raise PaperReleaseManifestError("release manifest must contain exactly the frozen dependency roles")
    for role, entry in dependencies.items():
        _text(role, f"dependency role {role}")
        if not isinstance(entry, Mapping) or set(entry) != {"path", "size", "sha256"}:
            raise PaperReleaseManifestError(f"dependency {role} has an invalid file entry")
        _safe_relative(entry["path"], f"dependency {role}.path")
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise PaperReleaseManifestError(f"dependency {role}.size is invalid")
        _sha256(entry["sha256"], f"dependency {role}.sha256")
    evidence = manifest["bound_evidence"]
    if not isinstance(evidence, Mapping):
        raise PaperReleaseManifestError("bound_evidence must be an object")
    for role, entry in evidence.items():
        _text(role, f"evidence role {role}")
        if not isinstance(entry, Mapping) or set(entry) != {"path", "size", "sha256"}:
            raise PaperReleaseManifestError(f"bound evidence {role} has an invalid file entry")
        _safe_relative(entry["path"], f"bound evidence {role}.path")
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise PaperReleaseManifestError(f"bound evidence {role}.size is invalid")
        _sha256(entry["sha256"], f"bound evidence {role}.sha256")


def _json_dependency(root: Path, entry: Mapping[str, Any], role: str) -> Mapping[str, Any]:
    path = root / entry["path"]
    value = _json_object(path.read_bytes(), role)
    if canonical_json_bytes(value) != path.read_bytes():
        raise PaperReleaseManifestError(f"dependency {role} is not canonical JSON")
    return value


def _collect_bound_evidence(
    root: Path,
    *,
    paper_commit: str,
    dependency_entries: Mapping[str, Mapping[str, Any]],
    scorebook_digest: str,
) -> dict[str, Mapping[str, Any]]:
    binding = _json_dependency(root, dependency_entries["statistical_report_binding"], "statistical_report_binding")
    _exact_keys(binding, {"schema", "paper_commit", "scorebook_digest", "bindings"}, "statistical report binding")
    if binding["schema"] != "heterocoord.statistical-report-binding.v2" or binding["paper_commit"] != paper_commit:
        raise PaperReleaseManifestError("statistical report binding identity mismatch")
    if binding["scorebook_digest"] != scorebook_digest:
        raise PaperReleaseManifestError("statistical binding scorebook digest mismatch")
    bindings = binding.get("bindings")
    if not isinstance(bindings, list):
        raise PaperReleaseManifestError("statistical report bindings must be a list")
    report_set = _json_dependency(root, dependency_entries["statistical_reports"], "statistical_reports")
    if set(report_set) != {"schema", "paper_commit", "reports"} or report_set["schema"] != STATISTICAL_REPORT_SET_SCHEMA or report_set["paper_commit"] != paper_commit:
        raise PaperReleaseManifestError("statistical report set identity mismatch")
    reports = report_set["reports"]
    if not isinstance(reports, list):
        raise PaperReleaseManifestError("statistical report set must contain a list of reports")
    report_refs: dict[str, Mapping[str, Any]] = {}
    bound: dict[str, Mapping[str, Any]] = {}
    seen_report_refs: set[tuple[str, int, str]] = set()
    for index, item in enumerate(bindings):
        _exact_keys(item, BINDING_ENTRY_FIELDS, f"bindings[{index}]")
        key = item["result_key_digest"]
        _sha256(key, f"bindings[{index}].result_key_digest")
        if key in report_refs:
            raise PaperReleaseManifestError("statistical bindings contain duplicate result keys")
        if item["score_id"] not in {f"D{i}" for i in range(1, 7)}:
            raise PaperReleaseManifestError("statistical binding score_id is outside D1-D6")
        validate_identity(item["identity"])
        if result_key_digest(item["identity"]) != key:
            raise PaperReleaseManifestError("statistical binding result-key digest is stale")
        for kind in ("report_ref", "input_ref", "event_ledger_ref"):
            reference = item.get(kind)
            if not isinstance(reference, Mapping) or set(reference) != {"path", "size", "sha256"}:
                raise PaperReleaseManifestError(f"bindings[{index}].{kind} is invalid")
            observed = _file_entry(root, reference["path"])
            if observed != reference:
                raise PaperReleaseManifestError(f"bindings[{index}].{kind} does not match bytes")
            role = f"{kind[:-4]}:{key}"
            if role in bound:
                raise PaperReleaseManifestError("duplicate bound evidence role")
            bound[role] = observed
        report_identity = (item["report_ref"]["path"], item["report_ref"]["size"], item["report_ref"]["sha256"])
        if report_identity in seen_report_refs:
            raise PaperReleaseManifestError("one statistical report cannot bind multiple result keys")
        seen_report_refs.add(report_identity)
        input_object = _json_dependency(root, item["input_ref"], f"statistical input {item['score_id']}")
        ledger_object = _json_dependency(root, item["event_ledger_ref"], f"statistical event ledger {item['score_id']}")
        if input_object.get("paper_commit") != paper_commit:
            raise PaperReleaseManifestError("statistical input paper commit mismatch")
        try:
            validate_statistical_input(input_object, binding=item)
            validate_statistical_event_ledger(ledger_object, binding=item, input_object=input_object)
        except PaperResultIndexError as exc:
            raise PaperReleaseManifestError(str(exc)) from exc
        report = _json_dependency(root, item["report_ref"], f"statistical report {item['score_id']}")
        _exact_keys(report, STATISTICAL_REPORT_FIELDS, f"statistical report {item['score_id']}")
        try:
            _validate_statistical_report(
                report,
                binding=item,
                paper_commit=paper_commit,
                scorebook_digest=scorebook_digest,
                input_object=input_object,
                ledger_object=ledger_object,
            )
        except PaperResultIndexError as exc:
            raise PaperReleaseManifestError(str(exc)) from exc
        if report["event_ledger_digest"] != item["event_ledger_ref"]["sha256"]:
            raise PaperReleaseManifestError("statistical report event-ledger digest mismatch")
        identity = item["identity"]
        for field in ("implementation_id", "comparison_condition_id", "outcome_id", "estimator_version"):
            if identity.get(field) != item[field]:
                raise PaperReleaseManifestError("statistical binding full result identity mismatch")
        report_refs[key] = item["report_ref"]
    expected_scores = {f"D{i}" for i in range(1, 7)}
    # Per-system completeness is enforced by the required-result directory and
    # the result-index layer; the release layer only checks global D1-D6 coverage.
    if not expected_scores.issubset({item["score_id"] for item in bindings}):
        raise PaperReleaseManifestError("statistical bindings must cover D1-D6 score IDs")
    set_report_refs: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(reports):
        if not isinstance(item, Mapping) or set(item) != {"result_key_digest", "report_ref"}:
            raise PaperReleaseManifestError("statistical report set entry schema mismatch")
        key = _sha256(item["result_key_digest"], f"reports[{index}].result_key_digest")
        if key in set_report_refs:
            raise PaperReleaseManifestError("statistical report set contains duplicate result keys")
        set_report_refs[key] = item["report_ref"]
    if set(set_report_refs) != set(report_refs):
        raise PaperReleaseManifestError("statistical report set and binding manifest disagree")
    for key, reference in set_report_refs.items():
        if reference != report_refs[key]:
            raise PaperReleaseManifestError("statistical report set and binding manifest disagree")
    return bound


def _validate_dependency_semantics(
    root: Path,
    *,
    paper_commit: str,
    dependency_entries: Mapping[str, Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    dependency_values: dict[str, Mapping[str, Any]] = {}
    for role in REQUIRED_ROLES - {"paper_commit", "result_index"}:
        value = _json_dependency(root, dependency_entries[role], role)
        dependency_values[role] = value
        if value.get("paper_commit") != paper_commit:
            raise PaperReleaseManifestError(f"dependency {role} binds a different paper commit")
    scorebook = _json_dependency(root, dependency_entries["scorebook"], "scorebook")
    # ScorebookV3.digest hashes canonical JSON without a trailing newline; the
    # release dependency SHA independently binds the exact file bytes.
    scorebook_digest = hashlib.sha256(canonical_json_bytes(scorebook).rstrip(b"\n")).hexdigest()
    index_manifest = _json_dependency(root, dependency_entries["result_index_manifest"], "result_index_manifest")
    _exact_keys(
        index_manifest,
        {"schema", "paper_commit", "index", "row_count", "required_directory_digest", "formal_gates"},
        "result index manifest",
    )
    if index_manifest["schema"] != INDEX_MANIFEST_SCHEMA or index_manifest["formal_gates"] != FORMAL_GATES_FALSE:
        raise PaperReleaseManifestError("result index manifest schema/gates mismatch")
    result_entry = dependency_entries["result_index"]
    expected_index = {
        "path": result_entry["path"],
        "size": result_entry["size"],
        "sha256": result_entry["sha256"],
    }
    if index_manifest["index"] != expected_index:
        raise PaperReleaseManifestError("result index and index manifest disagree")
    if index_manifest["required_directory_digest"] != canonical_digest(dependency_values["required_result_directory"]):
        raise PaperReleaseManifestError("required directory and result index manifest disagree")
    return _collect_bound_evidence(
        root,
        paper_commit=paper_commit,
        dependency_entries=dependency_entries,
        scorebook_digest=scorebook_digest,
    )


def build_paper_release_manifest(
    *, root: str | Path, paper_commit: str, dependencies: Mapping[str, str | Path],
    destination: str | Path, no_overwrite: bool = True,
) -> Mapping[str, Any]:
    if no_overwrite is not True:
        raise PaperReleaseManifestError("release manifests are always no-overwrite")
    _commit(paper_commit, "paper_commit")
    if not isinstance(dependencies, Mapping) or set(dependencies) != REQUIRED_ROLES:
        missing = sorted(REQUIRED_ROLES - set(dependencies)) if isinstance(dependencies, Mapping) else sorted(REQUIRED_ROLES)
        extra = sorted(set(dependencies) - REQUIRED_ROLES) if isinstance(dependencies, Mapping) else []
        raise PaperReleaseManifestError(f"dependency role mismatch; missing={missing}, extra={extra}")
    raw_root = Path(root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise PaperReleaseManifestError("release root must be an existing non-symlink directory")
    root_path = raw_root.resolve(strict=True)
    entries = {role: _file_entry(root_path, str(path)) for role, path in sorted(dependencies.items())}
    commit_text = (root_path / entries["paper_commit"]["path"]).read_text(encoding="utf-8").strip()
    if commit_text != paper_commit:
        raise PaperReleaseManifestError("paper_commit dependency contents do not match paper_commit")
    bound_evidence = _validate_dependency_semantics(
        root_path,
        paper_commit=paper_commit,
        dependency_entries=entries,
    )
    manifest = {
        "schema": RELEASE_MANIFEST_SCHEMA,
        "paper_commit": paper_commit,
        "dependencies": entries,
        "bound_evidence": bound_evidence,
        "formal_gates": dict(FORMAL_GATES_FALSE),
    }
    _validate_manifest_shape(manifest)
    try:
        destination_path = _safe_destination(root_path, destination)
    except PaperResultIndexError as exc:
        raise PaperReleaseManifestError(str(exc)) from exc
    if destination_path.exists():
        raise PaperReleaseManifestError(f"refusing to overwrite existing file: {destination_path}")
    _write_new(destination_path, canonical_json_bytes(manifest))
    return manifest


def verify_paper_release_manifest(path: str | Path, *, root: str | Path) -> Mapping[str, Any]:
    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise PaperReleaseManifestError("release manifest must be a regular non-symlink file")
    raw_root = Path(root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise PaperReleaseManifestError("release root must be an existing non-symlink directory")
    root_path = raw_root.resolve(strict=True)
    resolved_manifest = manifest_path.resolve(strict=True)
    if manifest_path.absolute() != resolved_manifest or root_path not in resolved_manifest.parents:
        raise PaperReleaseManifestError("release manifest must be inside root")
    manifest_data = manifest_path.read_bytes()
    manifest = _json_object(manifest_data, "paper release manifest")
    if canonical_json_bytes(manifest) != manifest_data:
        raise PaperReleaseManifestError("paper release manifest is not canonical JSON")
    _validate_manifest_shape(manifest)
    for role, entry in manifest["dependencies"].items():
        observed = _file_entry(root_path, entry["path"])
        if observed != entry:
            raise PaperReleaseManifestError(f"dependency changed after manifest creation: {role}")
    commit_text = (root_path / manifest["dependencies"]["paper_commit"]["path"]).read_text(encoding="utf-8").strip()
    if commit_text != manifest["paper_commit"]:
        raise PaperReleaseManifestError("paper_commit dependency mismatch")
    expected_evidence = _validate_dependency_semantics(
        root_path,
        paper_commit=manifest["paper_commit"],
        dependency_entries=manifest["dependencies"],
    )
    if manifest["bound_evidence"] != expected_evidence:
        raise PaperReleaseManifestError("bound evidence set or bytes changed")
    return manifest


def _load_dependencies(path: str | Path) -> Mapping[str, str]:
    value = _json_object(Path(path).read_bytes(), "release dependencies")
    if set(value) != REQUIRED_ROLES or any(not isinstance(item, str) for item in value.values()):
        raise PaperReleaseManifestError("release dependency map has wrong roles or values")
    return dict(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-release-manifest")
    build.add_argument("--root", required=True)
    build.add_argument("--paper-commit", required=True)
    build.add_argument("--dependencies", required=True, help="JSON object role -> root-relative path")
    build.add_argument("--output", required=True)
    build.set_defaults(handler=lambda args: build_paper_release_manifest(
        root=args.root,
        paper_commit=args.paper_commit,
        dependencies=_load_dependencies(args.dependencies),
        destination=args.output,
    ))
    verify = sub.add_parser("verify-release-manifest")
    verify.add_argument("--root", required=True)
    verify.add_argument("--manifest", required=True)
    verify.set_defaults(handler=lambda args: verify_paper_release_manifest(args.manifest, root=args.root))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        sys.stdout.buffer.write(canonical_json_bytes(args.handler(args)))
        return 0
    except PaperReleaseManifestError as exc:
        print(f"paper-release-manifest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
