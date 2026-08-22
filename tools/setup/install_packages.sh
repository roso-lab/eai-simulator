#!/bin/bash

# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

# EAI Simulator 包安装脚本
# 此脚本会安装 Qt 系统依赖和 source 目录下的所有 Python 包
#
# 用法:
#   ./tools/setup/install_packages.sh            # 安装所有包（静默模式）
#   ./tools/setup/install_packages.sh -v         # 安装所有包（详细输出）
#   ./tools/setup/install_packages.sh -u         # 卸载所有包
#   ./tools/setup/install_packages.sh -u -v      # 卸载所有包（详细输出）
#   ./tools/setup/install_packages.sh --ros-distro jazzy
#   ./tools/setup/install_packages.sh --help     # 显示帮助信息

# 不设置 set -e，允许继续安装其他包即使某个包失败

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 安装 PyQt6 xcb 平台插件所需的 Ubuntu 系统依赖
install_system_dependencies() {
    local package="libxcb-cursor0"
    local -a privilege_command=()

    if command -v dpkg-query > /dev/null 2>&1 \
        && dpkg-query -W -f='${Status}' "${package}" 2> /dev/null \
            | grep -q "ok installed"; then
        print_info "系统依赖 ${package} 已安装"
        return 0
    fi

    if ! command -v apt-get > /dev/null 2>&1; then
        print_error "未找到 apt-get，无法自动安装系统依赖 ${package}"
        return 1
    fi

    if [ "${EUID}" -ne 0 ]; then
        if ! command -v sudo > /dev/null 2>&1; then
            print_error "安装 ${package} 需要 root 权限，但未找到 sudo"
            return 1
        fi
        privilege_command=(sudo)
    fi

    print_info "正在安装系统依赖 ${package}..."
    if ! "${privilege_command[@]}" apt-get update; then
        print_error "更新 apt 软件包索引失败"
        return 1
    fi
    if ! "${privilege_command[@]}" apt-get install -y "${package}"; then
        print_error "系统依赖 ${package} 安装失败"
        return 1
    fi

    print_info "系统依赖 ${package} 安装成功"
}

# 解析命令行参数
VERBOSE=false
UNINSTALL=false
ROS_DISTRO_ARG=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SOURCE_DIR="${PROJECT_ROOT}/source"

# shellcheck source=tools/setup/ros_distro.sh
source "${SCRIPT_DIR}/ros_distro.sh"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -u|--uninstall)
            UNINSTALL=true
            shift
            ;;
        --ros-distro)
            if [[ $# -lt 2 ]]; then
                print_error "--ros-distro 需要参数: humble 或 jazzy"
                exit 2
            fi
            ROS_DISTRO_ARG="$2"
            shift 2
            ;;
        --ros-distro=*)
            ROS_DISTRO_ARG="${1#*=}"
            shift
            ;;
        -h|--help)
            echo "EAI Simulator 包安装/卸载脚本"
            echo ""
            echo "用法:"
            echo "  ./tools/setup/install_packages.sh            # 安装所有包（静默模式）"
            echo "  ./tools/setup/install_packages.sh -v         # 安装所有包（详细输出）"
            echo "  ./tools/setup/install_packages.sh -u         # 卸载所有包"
            echo "  ./tools/setup/install_packages.sh -u -v      # 卸载所有包（详细输出）"
            echo "  ./tools/setup/install_packages.sh --ros-distro humble|jazzy"
            echo "  ./tools/setup/install_packages.sh --help     # 显示帮助信息"
            echo ""
            echo "此脚本会按顺序处理以下包:"
            echo "  - EAI"
            echo "  - EAI_assets"
            echo "  - EAI_hmrs"
            echo ""
            echo "安装模式还会检查系统依赖:"
            echo "  - libxcb-cursor0"
            echo ""
            echo "--ros-distro 会为当前 Python/Conda 环境保存 ROS 2 发行版选择。"
            echo "它不会安装 ROS 2，也不会修改项目源码或 ~/.bashrc。"
            exit 0
            ;;
        *)
            print_warn "忽略未知参数: $1"
            shift
            ;;
    esac
done

if [[ "$UNINSTALL" == true ]]; then
    ACTION="卸载"
else
    ACTION="安装"
    if ! SELECTED_ROS_DISTRO="$(eai_resolve_ros_distro "${ROS_DISTRO_ARG}")"; then
        exit 2
    fi
fi

# 检查 source 目录是否存在
if [ ! -d "${SOURCE_DIR}" ]; then
    print_error "source 目录不存在: ${SOURCE_DIR}"
    exit 1
fi

if [ "${UNINSTALL}" = false ]; then
    print_info "ROS 2 发行版: ${SELECTED_ROS_DISTRO}"
    if [[ ! -f "/opt/ros/${SELECTED_ROS_DISTRO}/setup.bash" ]]; then
        print_warn "未检测到 /opt/ros/${SELECTED_ROS_DISTRO}/setup.bash；仅配置 Isaac Sim 内置 bridge"
    fi
    if ! install_system_dependencies; then
        print_error "系统依赖安装未完成，终止安装"
        exit 1
    fi
    echo ""
fi

print_info "开始${ACTION} source 目录下的所有包..."
echo ""

# 定义要安装的包（按依赖顺序）
PACKAGES=(
    "EAI"
    "EAI_assets"
    "EAI_hmrs"
)

# 安装每个包
SUCCESS_COUNT=0
FAIL_COUNT=0

for package in "${PACKAGES[@]}"; do
    package_dir="${SOURCE_DIR}/${package}"
    
    if [ ! -d "${package_dir}" ]; then
        print_warn "跳过 ${package}: 目录不存在"
        continue
    fi
    
    if [ ! -f "${package_dir}/setup.py" ]; then
        print_warn "跳过 ${package}: 未找到 setup.py"
        continue
    fi
    
    print_info "正在${ACTION} ${package}..."
    
    if [ "$VERBOSE" = true ]; then
        # 详细模式：显示完整输出
        if [ "$UNINSTALL" = true ]; then
            if pip uninstall -y "${package}"; then
                print_info "✓ ${package} 卸载成功"
                ((SUCCESS_COUNT++))
            else
                print_error "✗ ${package} 卸载失败"
                ((FAIL_COUNT++))
            fi
        else
            if python -m pip install --no-deps -e "${package_dir}"; then
                print_info "✓ ${package} 安装成功"
                ((SUCCESS_COUNT++))
            else
                print_error "✗ ${package} 安装失败"
                ((FAIL_COUNT++))
            fi
        fi
    else
        # 静默模式：隐藏输出，失败时显示错误
        if [ "$UNINSTALL" = true ]; then
            if pip uninstall -y "${package}" > /dev/null 2>&1; then
                print_info "✓ ${package} 卸载成功"
                ((SUCCESS_COUNT++))
            else
                print_error "✗ ${package} 卸载失败"
                print_warn "运行 './tools/setup/install_packages.sh -u -v' 查看详细错误信息"
                ((FAIL_COUNT++))
            fi
        else
            if python -m pip install --no-deps -e "${package_dir}" > /dev/null 2>&1; then
                print_info "✓ ${package} 安装成功"
                ((SUCCESS_COUNT++))
            else
                print_error "✗ ${package} 安装失败"
                print_warn "运行 './tools/setup/install_packages.sh -v' 查看详细错误信息"
                ((FAIL_COUNT++))
            fi
        fi
    fi
    echo ""
done

if [[ "${UNINSTALL}" = false && ${FAIL_COUNT} -eq 0 ]]; then
    PYTHON_PREFIX="$(python -c 'import sys; print(sys.prefix)')"
    if ROS_CONFIG_PATH="$(eai_write_ros_distro_config "${SELECTED_ROS_DISTRO}" "${PYTHON_PREFIX}")"; then
        print_info "已保存 ROS 2 发行版配置: ${ROS_CONFIG_PATH}"
        if [[ -n "${ROS_DISTRO:-}" && "${ROS_DISTRO,,}" != "${SELECTED_ROS_DISTRO}" ]]; then
            print_warn "当前 shell 的 ROS_DISTRO=${ROS_DISTRO} 会覆盖该配置；运行前请 unset ROS_DISTRO 或改为 ${SELECTED_ROS_DISTRO}"
        fi
    else
        print_error "无法保存 ROS 2 发行版配置"
        ((FAIL_COUNT++))
    fi
fi

# 总结
echo "=========================================="
print_info "${ACTION}完成！"
echo "  成功: ${SUCCESS_COUNT} 个包"
if [ ${FAIL_COUNT} -gt 0 ]; then
    print_error "  失败: ${FAIL_COUNT} 个包"
fi
echo "=========================================="

# 如果有失败的包，返回非零退出码
if [ ${FAIL_COUNT} -gt 0 ]; then
    exit 1
fi
