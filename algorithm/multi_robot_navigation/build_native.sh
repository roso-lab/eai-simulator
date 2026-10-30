#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
native_dir="$script_dir/native"
build_dir="$native_dir/build"
repo_root="$(cd "${script_dir}/../.." && pwd)"
# shellcheck source=tools/setup/ros_distro.sh
source "${repo_root}/tools/setup/ros_distro.sh"
ros_distro_name="$(eai_resolve_ros_distro)" || exit $?

if [[ -z "${CONDA_PREFIX:-}" ]]; then
  echo "Activate the EAI env_isaaclab conda environment first." >&2
  exit 2
fi

python "$script_dir/fetch_motion_primitives.py"

python_version="$(python -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
primary_cmeel="$CONDA_PREFIX/lib/$python_version/site-packages/cmeel.prefix"
if [[ ! -d "$primary_cmeel" ]]; then
  echo "Missing cmeel dependencies in $primary_cmeel" >&2
  exit 2
fi

cmeel_prefixes=("$primary_cmeel")
if [[ ! -f "$primary_cmeel/include/crocoddyl/core/fwd.hpp" || \
      ! -f "$primary_cmeel/lib/libcrocoddyl.so" ]]; then
  echo "Missing crocoddyl in the active EAI environment." >&2
  echo "Install it with: python -m pip install --no-deps crocoddyl==2.0.2" >&2
  exit 2
fi

prefix_path="$(IFS=';'; echo "${cmeel_prefixes[*]}")"
prefix_path="$prefix_path;/opt/openrobots;/opt/ros/${ros_distro_name}"
runtime_paths=()
for prefix in "${cmeel_prefixes[@]}"; do
  runtime_paths+=("$prefix/lib")
done
linker_rpaths="$(printf -- '-Wl,-rpath,%s ' "${runtime_paths[@]}")"

cmake -S "$native_dir" -B "$build_dir" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$prefix_path" \
  -DBOOST_ROOT="$primary_cmeel" \
  -DBoost_NO_SYSTEM_PATHS=ON \
  -DCROCODDYL_INCLUDE_DIR="$primary_cmeel/include" \
  -DCROCODDYL_LIBRARY="$primary_cmeel/lib/libcrocoddyl.so" \
  -DCMAKE_EXE_LINKER_FLAGS="$linker_rpaths"
cmake --build "$build_dir" --target db_cbs --parallel "${EAI_DBCBS_BUILD_JOBS:-$(nproc)}"

echo "Built EAI db-CBS: $build_dir/db_cbs"
