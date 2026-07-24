#!/usr/bin/env bash
set -euo pipefail

Z1_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Z1_PROJECT_ROOT="$(cd "${Z1_SCRIPT_DIR}/../../.." && pwd)"

ISAACSIM_ROOT="${ISAACSIM_ROOT:-${HOME}/isaacsim/_build/linux-x86_64/release}"
ISAACSIM_CONDA_ENV="${ISAACSIM_CONDA_ENV:-}"
PYTHON_BIN="${PYTHON_BIN:-}"
Z1_SOURCE_SYSTEM_ROS="${Z1_SOURCE_SYSTEM_ROS:-0}"
export Z1_SOURCE_SYSTEM_ROS

if [[ ! -d "${ISAACSIM_ROOT}" ]]; then
    echo "Isaac Sim root not found: ${ISAACSIM_ROOT}" >&2
    exit 1
fi

filter_colon_var() {
    local var_name="$1"
    local raw_value="${!var_name:-}"
    local filtered=""
    local entry

    IFS=":" read -r -a entries <<< "${raw_value}"
    for entry in "${entries[@]}"; do
        if [[ -n "${entry}" && "${entry}" != /opt/ros/* ]]; then
            if [[ -n "${filtered}" ]]; then
                filtered="${filtered}:${entry}"
            else
                filtered="${entry}"
            fi
        fi
    done

    export "${var_name}=${filtered}"
}
export -f filter_colon_var

source_ros_env() {
    if [[ "${Z1_SOURCE_SYSTEM_ROS}" == "1" && -f /opt/ros/humble/setup.bash ]]; then
        # ROS setup scripts use unset variables internally on some installs.
        set +u
        # shellcheck disable=SC1091
        source /opt/ros/humble/setup.bash
        set -u
    else
        unset ROS_DISTRO
        unset ROS_VERSION
        unset ROS_PYTHON_VERSION
        unset AMENT_PREFIX_PATH
        unset COLCON_PREFIX_PATH
        unset OLD_PYTHONPATH
        filter_colon_var LD_LIBRARY_PATH
        filter_colon_var PYTHONPATH
    fi

    if [[ -f "${ISAACSIM_ROOT}/setup_ros_env.sh" ]]; then
        set +u
        # shellcheck disable=SC1090
        source "${ISAACSIM_ROOT}/setup_ros_env.sh"
        set -u
    fi
}

conda_env_exists() {
    local env_name="$1"
    conda env list | awk '{print $1}' | grep -Fxq "${env_name}"
}

active_conda_env_matches() {
    local env_name="$1"
    [[ "${CONDA_DEFAULT_ENV:-}" == "${env_name}" && -n "${CONDA_PREFIX:-}" ]]
}

detect_conda_env() {
    if [[ -n "${ISAACSIM_CONDA_ENV}" ]]; then
        echo "${ISAACSIM_CONDA_ENV}"
        return
    fi

    if ! command -v conda >/dev/null 2>&1; then
        return
    fi

    for candidate in env_isaable env_isaaclab; do
        if conda_env_exists "${candidate}"; then
            echo "${candidate}"
            return
        fi
    done
}

run_with_active_conda_env() {
    echo "Using active conda env: ${CONDA_DEFAULT_ENV}" >&2
    set +u
    # shellcheck disable=SC1090
    source "${ISAACSIM_ROOT}/setup_conda_env.sh"
    set -u
    source_ros_env
    cd "${Z1_PROJECT_ROOT}"
    exec python "${Z1_SCRIPT_DIR}/run_z1_ros2_bridge.py" "$@"
}

run_with_conda_env() {
    local env_name="$1"
    shift

    if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
        set +u
        # shellcheck disable=SC1091
        source "${HOME}/miniconda3/etc/profile.d/conda.sh"
        set -u
    fi

    if ! conda_env_exists "${env_name}"; then
        echo "Conda env not found: ${env_name}" >&2
        echo "Set ISAACSIM_CONDA_ENV to an existing Python 3.11 Isaac Sim environment." >&2
        exit 1
    fi

    echo "Using conda env: ${env_name}" >&2
    conda run -n "${env_name}" bash -c '
        set -euo pipefail
        ISAACSIM_ROOT="$1"
        Z1_SCRIPT_DIR="$2"
        Z1_PROJECT_ROOT="$3"
        shift 3
        set +u
        source "${ISAACSIM_ROOT}/setup_conda_env.sh"
        if [[ "${Z1_SOURCE_SYSTEM_ROS:-0}" == "1" && -f /opt/ros/humble/setup.bash ]]; then
            source /opt/ros/humble/setup.bash
        else
            unset ROS_DISTRO
            unset ROS_VERSION
            unset ROS_PYTHON_VERSION
            unset AMENT_PREFIX_PATH
            unset COLCON_PREFIX_PATH
            unset OLD_PYTHONPATH
            filter_colon_var LD_LIBRARY_PATH
            filter_colon_var PYTHONPATH
        fi
        source "${ISAACSIM_ROOT}/setup_ros_env.sh"
        set -u
        cd "${Z1_PROJECT_ROOT}"
        python "${Z1_SCRIPT_DIR}/run_z1_ros2_bridge.py" "$@"
    ' bash "${ISAACSIM_ROOT}" "${Z1_SCRIPT_DIR}" "${Z1_PROJECT_ROOT}" "$@"
}

if [[ -n "${PYTHON_BIN}" ]]; then
    source_ros_env
    cd "${Z1_PROJECT_ROOT}"
    exec "${PYTHON_BIN}" "${Z1_SCRIPT_DIR}/run_z1_ros2_bridge.py" "$@"
fi

DETECTED_CONDA_ENV="$(detect_conda_env)"
if [[ -n "${DETECTED_CONDA_ENV}" ]]; then
    if active_conda_env_matches "${DETECTED_CONDA_ENV}"; then
        run_with_active_conda_env "$@"
    else
        run_with_conda_env "${DETECTED_CONDA_ENV}" "$@"
    fi
else
    source_ros_env
    cd "${Z1_PROJECT_ROOT}"
    exec "${ISAACSIM_ROOT}/python.sh" "${Z1_SCRIPT_DIR}/run_z1_ros2_bridge.py" "$@"
fi
