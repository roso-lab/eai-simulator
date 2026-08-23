"""Lightweight package metadata version consistency checks."""

from __future__ import annotations

import ast
from pathlib import Path


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


def test_eai_hmrs_setup_uses_package_version_source() -> None:
    package_root = REPO_ROOT / "source" / "EAI_hmrs"
    setup_text = (package_root / "setup.py").read_text(encoding="utf-8")

    assert 'EAI_hmrs" / "__init__.py"' in setup_text
    assert 'version=_VERSION_NS["__version__"]' in setup_text
    assert _literal_setup_version(package_root / "setup.py") is None
    assert _init_version(package_root / "EAI_hmrs" / "__init__.py") == "1.0.0"
