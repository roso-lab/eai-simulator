#!/usr/bin/env bash
set -euo pipefail

readonly CONFIG_PATH="/etc/sysctl.d/90-eai-isaac-sim-inotify.conf"
readonly PROC_ROOT="${EAI_INOTIFY_PROC_ROOT:-/proc/sys/fs/inotify}"
readonly WATCHES_MIN=524288
readonly INSTANCES_MIN=1024
readonly QUEUED_MIN=32768

usage() {
    echo "Usage: $0 [--dry-run]"
}

dry_run=0
case "${1:-}" in
    "") ;;
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

read_limit() {
    local name="$1"
    local value
    value="$(<"${PROC_ROOT}/${name}")"
    if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
        echo "Invalid inotify value in ${PROC_ROOT}/${name}: ${value}" >&2
        exit 1
    fi
    echo "${value}"
}

max_value() {
    local current="$1"
    local minimum="$2"
    if (( current > minimum )); then
        echo "${current}"
    else
        echo "${minimum}"
    fi
}

current_watches="$(read_limit max_user_watches)"
current_instances="$(read_limit max_user_instances)"
current_queued="$(read_limit max_queued_events)"
target_watches="$(max_value "${current_watches}" "${WATCHES_MIN}")"
target_instances="$(max_value "${current_instances}" "${INSTANCES_MIN}")"
target_queued="$(max_value "${current_queued}" "${QUEUED_MIN}")"

render_config() {
    echo "# Managed by eai-simulator tools/configure_inotify_limits.sh"
    echo "fs.inotify.max_user_watches=${target_watches}"
    echo "fs.inotify.max_user_instances=${target_instances}"
    echo "fs.inotify.max_queued_events=${target_queued}"
}

if (( dry_run )); then
    render_config
    exit 0
fi

if [[ "${PROC_ROOT}" != "/proc/sys/fs/inotify" ]]; then
    echo "EAI_INOTIFY_PROC_ROOT is supported only with --dry-run." >&2
    exit 2
fi

root_run=()
if (( EUID != 0 )); then
    if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true; then
        echo "Root access is required. Configure passwordless sudo or run this tool as root." >&2
        exit 1
    fi
    root_run=(sudo -n)
fi

if ! "${root_run[@]}" test -w /etc/sysctl.d; then
    echo "Cannot write /etc/sysctl.d with the available root credentials." >&2
    exit 1
fi

config_tmp="$(mktemp)"
backup_tmp="$(mktemp)"
had_previous=0
cleanup() {
    rm -f "${config_tmp}" "${backup_tmp}"
}
trap cleanup EXIT
render_config >"${config_tmp}"

if "${root_run[@]}" test -e "${CONFIG_PATH}"; then
    "${root_run[@]}" cp "${CONFIG_PATH}" "${backup_tmp}"
    had_previous=1
fi

rollback() {
    if (( had_previous )); then
        "${root_run[@]}" install -m 0644 "${backup_tmp}" "${CONFIG_PATH}"
    else
        "${root_run[@]}" rm -f "${CONFIG_PATH}"
    fi
    "${root_run[@]}" sysctl -q -w "fs.inotify.max_user_watches=${current_watches}" || true
    "${root_run[@]}" sysctl -q -w "fs.inotify.max_user_instances=${current_instances}" || true
    "${root_run[@]}" sysctl -q -w "fs.inotify.max_queued_events=${current_queued}" || true
}

"${root_run[@]}" install -m 0644 "${config_tmp}" "${CONFIG_PATH}"
if ! "${root_run[@]}" sysctl -p "${CONFIG_PATH}"; then
    rollback
    echo "Failed to apply ${CONFIG_PATH}; the previous configuration was restored." >&2
    exit 1
fi

echo "Installed ${CONFIG_PATH}"
echo "fs.inotify.max_user_watches=$(read_limit max_user_watches)"
echo "fs.inotify.max_user_instances=$(read_limit max_user_instances)"
echo "fs.inotify.max_queued_events=$(read_limit max_queued_events)"
