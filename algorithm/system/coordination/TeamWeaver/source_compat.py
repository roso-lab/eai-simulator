"""Temporary, source-locked TeamWeaver syntax compatibility overlay.

Only the two audited ``iflen`` syntax defects are repaired. The upstream
checkout is copied in full to ``destination/TeamWeaver`` and is never edited.
Unknown bytes, target-file drift, duplicate matches, copy drift, or a polluted
destination fail closed. Compilation is diagnostic only: additional syntax
errors are reported in the receipt and are never repaired here.
"""
from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
import shutil
import warnings
from typing import Any


PATCHES = (
    {
        "relative": Path("TeamWeaver/planner/HRCS/plan_module/execution_manager.py"),
        "before_sha256": "f4a96b9eb35456865a9dfde3e487353d5d5fcfa79e2afa25d1fbb7e03d671f7a",
        "after_sha256": "706889bf6acfeaba0720c7ac9f8d287ffeb7b91b983af34db61ee82cb19bf3cb",
        "old": "iflen(self.execution_history['actions']) > self.max_history_length:",
        "new": "if len(self.execution_history['actions']) > self.max_history_length:",
    },
    {
        "relative": Path("TeamWeaver/planner/miqp_planner/optimizer/adaptive_optimizer_v2.py"),
        "before_sha256": "a3fa6bcf8587c67a1b686abf80291b0267e8d807e959c027d24878a9c4d3b745",
        "after_sha256": "0450d4d822081021b74596b428335b2cecaa4bc78ed390296a03a9716b6a5824",
        "old": "iflen(x_i) > 2:",
        "new": "if len(x_i) > 2:",
    },
)


class SourceCompatibilityError(RuntimeError):
    """The requested compatibility overlay could not be proven safe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _diff(relative: Path, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"{relative} (original)", tofile=f"{relative} (compatibility overlay)"))


def _compile_report(package_root: Path) -> dict[str, Any]:
    """Compile every Python file for diagnostics, without applying repairs."""
    failures = []
    diagnostics = []
    files = sorted(package_root.rglob("*.py"))
    for path in files:
        try:
            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always")
                compile(path.read_bytes(), str(path), "exec")
            diagnostics.extend({"file": str(path.relative_to(package_root.parent)),
                "category": w.category.__name__, "message": str(w.message), "line": w.lineno}
                for w in captured)
        except (SyntaxError, UnicodeError, OSError) as exc:
            failures.append({
                "file": str(path.relative_to(package_root.parent)),
                "error_type": type(exc).__name__, "error": str(exc),
            })
    return {"python_files": len(files), "syntax_failures": failures,
            "warnings": diagnostics, "all_syntax_valid": not failures, "scope": "diagnostic_only_no_import_or_execution"}


def apply_compatibility_overlay(source_root: str | Path, destination: str | Path) -> dict[str, Any]:
    """Copy and patch a complete TeamWeaver package into a clean destination.

    ``source_root`` is the checkout root containing ``TeamWeaver/``. The
    returned receipt is also written to ``destination/source_compat_receipt.json``.
    No source file is changed, imported, or executed.
    """
    source_root = Path(source_root).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    source_package = source_root / "TeamWeaver"
    target_package = destination / "TeamWeaver"
    if destination == source_root or destination.is_relative_to(source_root) or source_root.is_relative_to(destination):
        raise SourceCompatibilityError("source and destination must be disjoint")
    if source_package.is_symlink() or not source_package.is_dir():
        raise SourceCompatibilityError(f"TeamWeaver package missing: {source_package}")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise SourceCompatibilityError(f"destination already exists and is not empty: {destination}")

    source_files = []
    for path in sorted(source_package.rglob("*")):
        if path.is_symlink():
            raise SourceCompatibilityError(f"source package symlink unsupported: {path}")
        if path.is_file():
            source_files.append(str(path.relative_to(source_root)))
    if not source_files:
        raise SourceCompatibilityError("source TeamWeaver package is empty")
    original_hashes = {name: _sha256(source_root / name) for name in source_files}

    patch_receipts = []
    for patch in PATCHES:
        source_path = source_root / patch["relative"]
        if not source_path.is_file():
            raise SourceCompatibilityError(f"known patch source missing: {patch['relative']}")
        before_hash = _sha256(source_path)
        if before_hash != patch["before_sha256"]:
            raise SourceCompatibilityError(
                f"unknown source for {patch['relative']}: {before_hash} != {patch['before_sha256']}"
            )
        before = source_path.read_text(encoding="utf-8")
        if before.count(patch["old"]) != 1:
            raise SourceCompatibilityError(f"expected exactly one audited syntax defect in {patch['relative']}")
        if patch["new"] in before:
            raise SourceCompatibilityError(f"target already appears repaired in {patch['relative']}")

    destination.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_package, target_package, copy_function=shutil.copy2)
        copied_files = sorted(str(path.relative_to(destination)) for path in target_package.rglob("*") if path.is_file())
        if copied_files != source_files or any(_sha256(destination / name) != digest for name, digest in original_hashes.items()):
            raise SourceCompatibilityError("complete TeamWeaver package copy manifest differs from source")
        for patch in PATCHES:
            target_path = destination / patch["relative"]
            before = target_path.read_bytes().decode("utf-8")
            before_hash = _sha256(target_path)
            if before_hash != patch["before_sha256"]:
                raise SourceCompatibilityError(f"copied target hash mismatch: {patch['relative']}")
            after = before.replace(patch["old"], patch["new"], 1)
            if after.count(patch["old"]) != 0:
                raise SourceCompatibilityError(f"patch did not remove defect: {patch['relative']}")
            target_path.write_bytes(after.encode("utf-8"))
            after_hash = _sha256(target_path)
            if after_hash != patch["after_sha256"]:
                raise SourceCompatibilityError(f"unexpected patched bytes: {patch['relative']}")
            patch_receipts.append({
                "file": str(patch["relative"]), "before_sha256": before_hash,
                "after_sha256": after_hash, "diff": _diff(patch["relative"], before, after),
                "replacement_count": 1, "semantic_scope": "syntax_only_iflen_to_if_len",
            })
        for item in patch_receipts:
            if _sha256(destination / item["file"]) != item["after_sha256"]:
                raise SourceCompatibilityError(f"post-patch hash changed: {item['file']}")
        compile_report = _compile_report(target_package)
        if sorted(str(p.relative_to(source_root)) for p in source_package.rglob("*") if p.is_file()) != source_files or any(
                _sha256(source_root / name) != digest for name, digest in original_hashes.items()):
            raise SourceCompatibilityError("original source changed during overlay construction")
        receipt = {
            "schema": "heterocoord.teamweaver.source_compat_overlay.v1",
            "source_root": str(source_root), "destination": str(destination),
            "package": "TeamWeaver", "source_unchanged": True,
            "source_file_count": len(source_files), "copied_file_count": len(copied_files),
            "patches": patch_receipts, "compile": compile_report,
            "unknown_source_rejected": True,
            "semantic_change": False,
            "scope": "only_two_source_locked_syntax_repairs; algorithm_behavior_not_executed_or_certified",
            "source_manifest_sha256": original_hashes,
            "receipt_path": str(destination / "source_compat_receipt.json"),
        }
        receipt_path = destination / "source_compat_receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        receipt["receipt_path"] = str(receipt_path)
        return receipt
    except Exception:
        shutil.rmtree(target_package, ignore_errors=True)
        raise


__all__ = ["PATCHES", "SourceCompatibilityError", "apply_compatibility_overlay"]
