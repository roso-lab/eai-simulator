#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EAI_SIM_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

ISAACSIM_ROOT="${ISAACSIM_ROOT:-${HOME}/isaacsim/_build/linux-x86_64/release}"
ISAACSIM_CONDA_ENV="${ISAACSIM_CONDA_ENV:-}"
BUILD_ROOT="${BUILD_ROOT:-${EAI_SIM_ROOT}/usd/payloads/manipulators/z1/build}"
Z1_PACKAGE_DIR="${Z1_PACKAGE_DIR:-${EAI_SIM_ROOT}/usd/payloads/manipulators/z1/source_description}"
FINAL_USD="${FINAL_USD:-${EAI_SIM_ROOT}/usd/payloads/manipulators/z1/z1_description.usda}"

XACRO_IN="${Z1_PACKAGE_DIR}/xacro/robot.xacro"
XACRO_TMP="${BUILD_ROOT}/xacro/robot.abs.xacro"
URDF_OUT="${EAI_SIM_ROOT}/usd/payloads/manipulators/z1/urdf/z1_with_gripper.urdf"
USD_OUT_DIR="${BUILD_ROOT}/usd"

if [[ ! -f "${XACRO_IN}" ]]; then
    echo "Z1 xacro not found: ${XACRO_IN}" >&2
    exit 1
fi

mkdir -p "$(dirname "${XACRO_TMP}")" "$(dirname "${URDF_OUT}")" "${USD_OUT_DIR}"

if [[ -f /opt/ros/humble/setup.bash ]]; then
    set +u
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    set -u
fi

XACRO_BIN="${XACRO_BIN:-$(command -v xacro || true)}"
if [[ -z "${XACRO_BIN}" ]]; then
    echo "xacro was not found. Source ROS2 or set XACRO_BIN=/path/to/xacro." >&2
    exit 1
fi

python3 - "${XACRO_IN}" "${XACRO_TMP}" "${Z1_PACKAGE_DIR}" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
pkg = sys.argv[3]
text = src.read_text(encoding="utf-8")
text = text.replace("$(find z1_description)", pkg)
dst.write_text(text, encoding="utf-8")
PY

"${XACRO_BIN}" "${XACRO_TMP}" UnitreeGripper:=true > "${URDF_OUT}"

python3 - "${URDF_OUT}" "${Z1_PACKAGE_DIR}" "${BUILD_ROOT}" <<'PY'
from pathlib import Path
import sys

urdf = Path(sys.argv[1])
pkg = sys.argv[2].rstrip("/")
text = urdf.read_text(encoding="utf-8")
text = text.replace("package://z1_description/", f"{pkg}/")
urdf.write_text(text, encoding="utf-8")
PY

if command -v xmllint >/dev/null 2>&1; then
    xmllint --noout "${URDF_OUT}"
fi

echo "Generated URDF: ${URDF_OUT}"

source_conda_sh() {
    if [[ -f "${HOME}/miniconda3/etc/profile.d/conda.sh" ]]; then
        set +u
        # shellcheck disable=SC1091
        source "${HOME}/miniconda3/etc/profile.d/conda.sh"
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

detect_isaac_conda_env() {
    if [[ -n "${ISAACSIM_CONDA_ENV}" ]]; then
        return
    fi

    if ! command -v conda >/dev/null 2>&1; then
        return
    fi

    for candidate in env_isaable env_isaaclab; do
        if conda_env_exists "${candidate}"; then
            ISAACSIM_CONDA_ENV="${candidate}"
            return
        fi
    done
}

run_with_active_conda_env() (
    set +u
    # shellcheck disable=SC1090
    source "${ISAACSIM_ROOT}/setup_conda_env.sh"
    set -u
    python "$@"
)

run_with_conda_env() {
    conda run -n "${ISAACSIM_CONDA_ENV}" bash -c '
        set -euo pipefail
        ISAACSIM_ROOT="$1"
        shift
        set +u
        source "${ISAACSIM_ROOT}/setup_conda_env.sh"
        set -u
        python "$@"
    ' bash "${ISAACSIM_ROOT}" "$@"
}

source_conda_sh
detect_isaac_conda_env

if [[ -n "${ISAACSIM_CONDA_ENV}" ]]; then
    if ! command -v conda >/dev/null 2>&1 || ! conda_env_exists "${ISAACSIM_CONDA_ENV}"; then
        echo "Conda env not found: ${ISAACSIM_CONDA_ENV}" >&2
        echo "Set ISAACSIM_CONDA_ENV to an existing Python 3.11 Isaac Sim environment." >&2
        exit 1
    fi

    run_isaac_python() {
        if active_conda_env_matches "${ISAACSIM_CONDA_ENV}"; then
            run_with_active_conda_env "$@"
        else
            run_with_conda_env "$@"
        fi
    }
else
    run_isaac_python() {
        "${ISAACSIM_ROOT}/python.sh" "$@"
    }
fi

run_isaac_python "${SCRIPT_DIR}/import_z1_urdf.py" \
    --urdf "${URDF_OUT}" \
    --usd-dir "${USD_OUT_DIR}"

python3 - "${URDF_OUT}" "${Z1_PACKAGE_DIR}" <<'PY'
from pathlib import Path
import sys

urdf = Path(sys.argv[1])
package_dir = Path(sys.argv[2]).resolve().as_posix().rstrip("/") + "/"
build_dir = Path(sys.argv[3]).resolve().as_posix().rstrip("/") + "/"
text = urdf.read_text(encoding="utf-8")
text = text.replace(package_dir, "../source_description/")
text = text.replace(build_dir, "../build/")
urdf.write_text(text, encoding="utf-8")
PY

run_isaac_python "${SCRIPT_DIR}/apply_z1_materials.py" \
    --usd "${USD_OUT_DIR}/z1_description/z1_description.usda" \
    --isaacsim-root "${ISAACSIM_ROOT}"

mkdir -p "$(dirname "${FINAL_USD}")"
cp -a "${USD_OUT_DIR}/z1_description/z1_description.usda" "${FINAL_USD}"

echo "Generated build USD: ${USD_OUT_DIR}/z1_description/z1_description.usda"
echo "Updated project USD: ${FINAL_USD}"
