from setuptools import setup, find_packages

setup(
    name="EAI_hmrs",
    version="0.1.0",
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