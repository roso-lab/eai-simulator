import ast
from pathlib import Path

from setuptools import find_packages, setup


def _read_package_version() -> str:
    init_path = Path(__file__).parent / "EAI_hmrs" / "__init__.py"
    tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise RuntimeError(f"No literal __version__ assignment found in {init_path}")


PACKAGE_VERSION = _read_package_version()

setup(
    name="EAI_hmrs",
    version=PACKAGE_VERSION,
    description="Large Language Model interface for Embodied AI",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "isaaclab",
    ],
    classifiers=[
        "Natural Language :: English",
        "Programming Language :: Python :: 3.10",
    ],
)
