#!/bin/bash

# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

# EAI Simulator 包安装脚本
# 此脚本会安装 source 目录下的所有 Python 包
#
# 用法:
#   ./tools/install_packages.sh            # 安装所有包（静默模式）
#   ./tools/install_packages.sh -v         # 安装所有包（详细输出）
#   ./tools/install_packages.sh -u         # 卸载所有包
#   ./tools/install_packages.sh -u -v      # 卸载所有包（详细输出）
#   ./tools/install_packages.sh --help     # 显示帮助信息

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

# 解析命令行参数
VERBOSE=false
UNINSTALL=false

for arg in "$@"; do
    case "$arg" in
        -v|--verbose)
            VERBOSE=true
            ;;
        -u|--uninstall)
            UNINSTALL=true
            ;;
        -h|--help)
            echo "EAI Simulator 包安装/卸载脚本"
            echo ""
            echo "用法:"
            echo "  ./tools/install_packages.sh            # 安装所有包（静默模式）"
            echo "  ./tools/install_packages.sh -v         # 安装所有包（详细输出）"
            echo "  ./tools/install_packages.sh -u         # 卸载所有包"
            echo "  ./tools/install_packages.sh -u -v      # 卸载所有包（详细输出）"
            echo "  ./tools/install_packages.sh --help     # 显示帮助信息"
            echo ""
            echo "此脚本会按顺序处理以下包:"
            echo "  - EAI"
            echo "  - EAI_assets"
            echo "  - EAI_hmrs"
            exit 0
            ;;
    esac
done

# 仅提示未知参数，不影响执行
for arg in "$@"; do
    case "$arg" in
        -v|--verbose|-u|--uninstall|-h|--help)
            ;;
        *)
            print_warn "忽略未知参数: ${arg}"
            ;;
    esac
done

if [[ "$UNINSTALL" == true ]]; then
    ACTION="卸载"
else
    ACTION="安装"
fi

if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]]; then
    echo "EAI Simulator 包安装脚本"
    echo ""
    echo "用法:"
    echo "  ./tools/install_packages.sh            # 安装所有包（静默模式）"
    echo "  ./tools/install_packages.sh -v         # 安装所有包（详细输出）"
    echo "  ./tools/install_packages.sh -u         # 卸载所有包"
    echo "  ./tools/install_packages.sh -u -v      # 卸载所有包（详细输出）"
    echo "  ./tools/install_packages.sh --help     # 显示帮助信息"
    echo ""
    echo "此脚本会按顺序处理以下包:"
    echo "  - EAI"
    echo "  - EAI_assets"
    echo "  - EAI_hmrs"
    exit 0
fi

# 获取脚本所在目录与项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_DIR="${PROJECT_ROOT}/source"

# 检查 source 目录是否存在
if [ ! -d "${SOURCE_DIR}" ]; then
    print_error "source 目录不存在: ${SOURCE_DIR}"
    exit 1
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
            if pip install -e "${package_dir}"; then
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
                print_warn "运行 './tools/install_packages.sh -u -v' 查看详细错误信息"
                ((FAIL_COUNT++))
            fi
        else
            if pip install -e "${package_dir}" > /dev/null 2>&1; then
                print_info "✓ ${package} 安装成功"
                ((SUCCESS_COUNT++))
            else
                print_error "✗ ${package} 安装失败"
                print_warn "运行 './tools/install_packages.sh -v' 查看详细错误信息"
                ((FAIL_COUNT++))
            fi
        fi
    fi
    echo ""
done

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
