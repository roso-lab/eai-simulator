#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=tools/ros_distro.sh
source "${PROJECT_ROOT}/tools/ros_distro.sh"
ROS_DISTRO_NAME="$(eai_resolve_ros_distro)" || exit $?
ROS_SETUP="/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
ROS2_PYTHON_BIN="${ROS2_PYTHON_BIN:-/usr/bin/python3}"

if [[ ! -f "${ROS_SETUP}" ]]; then
    echo "System ROS2 ${ROS_DISTRO_NAME} not found: ${ROS_SETUP}" >&2
    exit 1
fi
set +u
# shellcheck disable=SC1090
source "${ROS_SETUP}"
set -u

exec "${ROS2_PYTHON_BIN}" "${SCRIPT_DIR}/z1_joint_command.py" "$@"
