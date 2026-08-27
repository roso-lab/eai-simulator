# Copyright (c) 2022-2025, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Package containing asset and sensor configurations."""

import os

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback
    tomllib = None
    import toml

# Conveniences to other module directories via relative paths
# 修改点 1: 变量名从 ISAACLAB_ASSETS_EXT_DIR 改为 EAI_ASSETS_EXT_DIR
EAI_ASSETS_EXT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
"""Path to the extension source directory."""

# 修改点 2: 变量名同步修改
EAI_ASSETS_DATA_DIR = os.path.join(EAI_ASSETS_EXT_DIR, "data")
"""Path to the extension data directory."""

# 修改点 3: 变量名同步修改
_metadata_path = os.path.join(EAI_ASSETS_EXT_DIR, "config", "extension.toml")
if tomllib is not None:
    with open(_metadata_path, "rb") as _metadata_file:
        EAI_ASSETS_METADATA = tomllib.load(_metadata_file)
else:  # Python 3.10 uses the text-mode third-party toml parser.
    EAI_ASSETS_METADATA = toml.load(_metadata_path)
"""Extension metadata dictionary parsed from the extension.toml file."""

# Configure the module-level variables
# 修改点 4: 使用新的变量名获取版本号
__version__ = EAI_ASSETS_METADATA["package"]["version"]
