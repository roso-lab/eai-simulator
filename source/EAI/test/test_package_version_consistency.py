"""Lightweight package metadata version consistency checks."""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]


def _literal_setup_version(setup_path: Path) -> str | None:
    tree = ast.parse(setup_path.read_text(encoding="utf-8"), filename=str(setup_path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "setup":
            continue
        for keyword in node.keywords:
            if keyword.arg == "version" and isinstance(keyword.value, ast.Constant):
                return keyword.value.value
    return None


def _init_version(init_path: Path) -> str:
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise AssertionError(f"No literal __version__ assignment found in {init_path}")


def test_eai_setup_version_matches_package_version() -> None:
    package_root = REPO_ROOT / "source" / "EAI"
    assert _literal_setup_version(package_root / "setup.py") == _init_version(
        package_root / "EAI" / "__init__.py"
    )


def test_eai_hmrs_setup_reports_package_version() -> None:
    package_root = REPO_ROOT / "source" / "EAI_hmrs"
    package_version = _init_version(package_root / "EAI_hmrs" / "__init__.py")

    result = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=package_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == package_version
    assert _literal_setup_version(package_root / "setup.py") is None
