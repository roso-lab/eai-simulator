#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROS2_PYTHON_BIN="${ROS2_PYTHON_BIN:-/usr/bin/python3}"

if [[ -f /opt/ros/humble/setup.bash ]]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    set -u
fi

exec "${ROS2_PYTHON_BIN}" "${SCRIPT_DIR}/z1_joint_command.py" "$@"
