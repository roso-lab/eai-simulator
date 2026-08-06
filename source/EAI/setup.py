# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Installation script for the 'EAI' python package."""

from setuptools import setup, find_packages

setup(
    name="EAI",
    version="0.1.0",
    description="EAI shared utilities and dispatcher",
    packages=find_packages(),
    package_data={
        "EAI.interface_catalog": [
            "interfaces/robots/*.yaml",
            "interfaces/sensors/*.yaml",
        ],
        "EAI.hmrs_env.env_diy": [
            "env_diy_app.html",
        ],
    },
    python_requires=">=3.10",
    install_requires=[
        "isaaclab",
        "skrl",
        "torch",
        "pyyaml",
        "pywebview[qt]; platform_system == 'Linux'",
        "pywebview; platform_system != 'Linux'",
    ],
    classifiers=[
        "Natural Language :: English",
        "Programming Language :: Python :: 3.10",
    ],
    zip_safe=False,
)
