from pathlib import Path

from setuptools import find_packages, setup


_VERSION_NS = {}
exec((Path(__file__).parent / "EAI_hmrs" / "__init__.py").read_text(encoding="utf-8"), _VERSION_NS)

setup(
    name="EAI_hmrs",
    version=_VERSION_NS["__version__"],
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
