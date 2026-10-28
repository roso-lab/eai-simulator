#!/usr/bin/env bash
# Run from the repository root. Override env roots/endpoints for another host.
set -euo pipefail
adapter_env_root=${ADAPTER_ENV_ROOT:-/home/airs/.cache/heterocoord/envs}
isaac_python=${ISAAC_PYTHON:-/home/airs/miniconda3/envs/env_isaaclab/bin/python}
adapter_output=${1:-tmp/algorithm-regression-$(date -u +%Y%m%dT%H%M%SZ)}
if [[ -e "$adapter_output" ]]; then
    echo 'Refusing to overwrite an existing evidence directory' >&2
    exit 2
fi
mkdir -p "$adapter_output"
export PYTHONPATH=.:source/EAI:${adapter_env_root}/bt-multi-robot-test-deps
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 CUDA_VISIBLE_DEVICES=''
run_suite() {
    local name=$1 interpreter=$2
    shift 2
    "$interpreter" -m pytest -q --import-mode=importlib "$@" \
        --junitxml="$adapter_output/$name.xml" > "$adapter_output/$name.log" 2>&1
    tail -n 2 "$adapter_output/$name.log"
}
run_suite shared_teamweaver "$adapter_env_root/teamweaver-native/bin/python" \
    algorithm/heterocoord/tests algorithm/system/coordination/TeamWeaver/tests \
    benchmark/tests/test_emos.py benchmark/tests/test_emos_case_integration.py \
    benchmark/tests/test_external_algorithms.py benchmark/tests/test_native_algorithms.py \
    benchmark/tests/test_native_source_integrity.py benchmark/tests/test_teamweaver_case_integration.py \
    benchmark/tests/test_native_physical_receipts.py tools/algorithm_adapters/tests
run_suite sadcher "$adapter_env_root/sadcher/bin/python" algorithm/system/coordination/sadcher/tests
run_suite harl "$adapter_env_root/harl/bin/python" algorithm/system/coordination/harl/tests
run_suite fmc_ata "$adapter_env_root/fmc-ata/bin/python" algorithm/system/coordination/fmc_ata/tests
run_suite emos "$isaac_python" algorithm/system/coordination/emos/tests/test_native_adapter.py
# Set HETEROCOORD_BT_NATIVE_ENDPOINT and HETEROCOORD_BT_SOURCE_ROOT to enable
# the 7 real ROS protocol tests. Unset means SKIPPED, never native acceptance.
# Publication used http://172.17.0.2:18766 and ~/.cache/heterocoord/upstream/bt-capabilities.
run_suite bt_host "$isaac_python" algorithm/system/coordination/bt_capabilities/tests \
    tools/algorithm_adapters/tests tools/validation/test_pick_place_clearance.py \
    benchmark/tests/test_native_physical_receipts.py
run_suite merged_registry "$adapter_env_root/teamweaver-native/bin/python" \
    algorithm/heterocoord/tests benchmark/tests/test_formal_matrix_v3.py \
    benchmark/tests/test_emos.py benchmark/tests/test_teamweaver_case_integration.py
