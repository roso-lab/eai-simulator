#!/usr/bin/env bash

# Shared ROS 2 distribution selection for installer and operational scripts.

eai_ros_distro_config_path() {
    local python_prefix="${1:-}"

    if [[ -z "${python_prefix}" ]]; then
        if [[ -n "${CONDA_PREFIX:-}" ]]; then
            python_prefix="${CONDA_PREFIX}"
        elif command -v python >/dev/null 2>&1; then
            python_prefix="$(python -c 'import sys; print(sys.prefix)')" || return 1
        else
            return 1
        fi
    fi

    printf '%s/share/eai-simulator/ros_distro\n' "${python_prefix%/}"
}

eai_validate_ros_distro() {
    case "${1:-}" in
        humble|jazzy)
            return 0
            ;;
        *)
            printf 'Unsupported ROS 2 distribution: %s (expected humble or jazzy)\n' "${1:-<empty>}" >&2
            return 2
            ;;
    esac
}

eai_resolve_ros_distro() {
    local requested="${1:-}"
    local config_path=""

    if [[ -z "${requested}" ]]; then
        requested="${ROS_DISTRO:-}"
    fi
    if [[ -z "${requested}" ]]; then
        config_path="$(eai_ros_distro_config_path 2>/dev/null || true)"
        if [[ -n "${config_path}" && -f "${config_path}" ]]; then
            IFS= read -r requested < "${config_path}"
        fi
    fi
    requested="${requested:-humble}"
    requested="${requested,,}"
    eai_validate_ros_distro "${requested}" || return
    printf '%s\n' "${requested}"
}

eai_write_ros_distro_config() {
    local distro="$1"
    local python_prefix="${2:-}"
    local config_path config_dir temp_path

    eai_validate_ros_distro "${distro}" || return
    config_path="$(eai_ros_distro_config_path "${python_prefix}")" || return
    config_dir="$(dirname "${config_path}")"
    mkdir -p "${config_dir}" || return
    temp_path="$(mktemp "${config_dir}/ros_distro.XXXXXX")" || return
    if ! printf '%s\n' "${distro}" > "${temp_path}"; then
        rm -f "${temp_path}"
        return 1
    fi
    mv "${temp_path}" "${config_path}"
    printf '%s\n' "${config_path}"
}
