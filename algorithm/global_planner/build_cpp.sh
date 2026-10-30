#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/csrc/build"
OUTPUT_DIR="${SCRIPT_DIR}"

echo "=== Building C++ planner extension ==="

PYBIND11_CMAKE_DIR=$(python -c "import pybind11; print(pybind11.get_cmake_dir())")
echo "  pybind11 cmake dir: ${PYBIND11_CMAKE_DIR}"

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"

cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -Dpybind11_DIR="${PYBIND11_CMAKE_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${OUTPUT_DIR}"

make -j"$(nproc)"

cp _planner_cpp*.so "${OUTPUT_DIR}/"
echo "=== Done: $(ls ${OUTPUT_DIR}/_planner_cpp*.so) ==="
