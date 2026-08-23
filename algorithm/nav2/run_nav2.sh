#!/bin/bash
# EAI 仿真器 + Nav2 一键启动脚本
#
# 用法:
#   bash algorithm/nav2/run_nav2.sh            # 启动仿真 + Nav2
#   bash algorithm/nav2/run_nav2.sh --rviz     # 额外启动 RViz 可视化
#
# 注意: 仿真必须 GUI 模式（headless 下 Orsus 不发布传感器），不支持 --headless。
#
# 脚本会:
#   1. 启动 Isaac Sim 仿真 (conda env_isaaclab)
#   2. 等待仿真就绪后启动 Nav2 栈 (系统 ROS2)
#   3. Ctrl+C 时自动清理所有进程（避免残留占用 GPU 内存）

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=tools/setup/ros_distro.sh
source "${REPO_ROOT}/tools/setup/ros_distro.sh"
ROS_DISTRO_NAME="$(eai_resolve_ros_distro)" || exit $?
ROS_ROOT="/opt/ros/${ROS_DISTRO_NAME}"
if [[ ! -f "${ROS_ROOT}/setup.bash" ]]; then
    echo "❌ 未安装系统 ROS2 ${ROS_DISTRO_NAME}: ${ROS_ROOT}/setup.bash" >&2
    echo "   重新安装配置可运行: ./tools/setup/install_packages.sh --ros-distro humble|jazzy" >&2
    exit 1
fi
SIM_LOG=/tmp/eai_nav2_sim.log
NAV2_LOG=/tmp/eai_nav2_stack.log
CONDA_SH="${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
EAI_NAV2_SIM_READY_TIMEOUT="${EAI_NAV2_SIM_READY_TIMEOUT:-90}"
EAI_NAV2_STACK_READY_TIMEOUT="${EAI_NAV2_STACK_READY_TIMEOUT:-45}"

validate_positive_integer() {
    local name="$1"
    local value="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        echo "❌ $name 必须是正整数秒数，当前值: $value" >&2
        exit 1
    fi
}

validate_positive_integer EAI_NAV2_SIM_READY_TIMEOUT "$EAI_NAV2_SIM_READY_TIMEOUT"
validate_positive_integer EAI_NAV2_STACK_READY_TIMEOUT "$EAI_NAV2_STACK_READY_TIMEOUT"

RVIZ_ARG="rviz:=false"
[ "${1:-}" = "--rviz" ] && RVIZ_ARG="rviz:=true"
SYSTEM_ROS_HOME="$HOME"
SYSTEM_ROS_USER="$(id -un)"
SYSTEM_ROS_DISPLAY="${DISPLAY:-}"
SYSTEM_ROS_XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
SYSTEM_ROS_XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SYSTEM_ROS_DBUS="${DBUS_SESSION_BUS_ADDRESS:-}"
SIM_PID=""
NAV2_PID=""
LAUNCH_IN_PROGRESS=false
PENDING_SIGNAL_STATUS=""

append_ros_discovery_environment() {
    local output_name="$1"
    local -n output="$output_name"
    local variable
    for variable in \
        ROS_DOMAIN_ID \
        ROS_LOCALHOST_ONLY \
        ROS_AUTOMATIC_DISCOVERY_RANGE \
        ROS_STATIC_PEERS \
        CYCLONEDDS_URI; do
        if [[ -v "$variable" ]]; then
            output+=("$variable=${!variable}")
        fi
    done
}

SYSTEM_ROS_ENV=(
    "HOME=$SYSTEM_ROS_HOME"
    "USER=$SYSTEM_ROS_USER"
    "LOGNAME=$SYSTEM_ROS_USER"
    "PATH=/usr/bin:/bin:${ROS_ROOT}/bin"
    "LANG=C.UTF-8"
    "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"
    "DISPLAY=$SYSTEM_ROS_DISPLAY"
    "XAUTHORITY=$SYSTEM_ROS_XAUTHORITY"
    "XDG_RUNTIME_DIR=$SYSTEM_ROS_XDG_RUNTIME_DIR"
    "DBUS_SESSION_BUS_ADDRESS=$SYSTEM_ROS_DBUS"
)
append_ros_discovery_environment SYSTEM_ROS_ENV

cleanup() {
    # A second Ctrl+C must not interrupt TERM -> KILL escalation.
    trap '' INT TERM
    trap - EXIT
    echo ""
    echo "🧹 正在清理本脚本启动的进程..."
    for pid in "$NAV2_PID" "$SIM_PID"; do
        if [ -n "$pid" ] && kill -0 -- "-$pid" 2>/dev/null; then
            kill -TERM -- "-$pid" 2>/dev/null || true
        fi
    done
    for _ in $(seq 1 20); do
        alive=false
        for pid in "$NAV2_PID" "$SIM_PID"; do
            if [ -n "$pid" ] && kill -0 -- "-$pid" 2>/dev/null; then
                alive=true
            fi
        done
        [ "$alive" = false ] && break
        sleep 0.1
    done
    for pid in "$NAV2_PID" "$SIM_PID"; do
        if [ -n "$pid" ] && kill -0 -- "-$pid" 2>/dev/null; then
            kill -KILL -- "-$pid" 2>/dev/null || true
        fi
        [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
    done
    if [ -n "$SIM_PID" ]; then
        # 使用系统 Python 运行纯标准库清理，避免加载 Isaac Conda 或 ROS Python 环境。
        if ! PYTHONPATH="${REPO_ROOT}/source/EAI${PYTHONPATH:+:$PYTHONPATH}" \
            /usr/bin/python3 - "$REPO_ROOT/tmp/runtime_interfaces.json" "$SIM_PID" <<'PY'
import sys
from pathlib import Path
from EAI.interface_catalog.snapshot import remove_stale_snapshot

remove_stale_snapshot(Path(sys.argv[1]), pid=int(sys.argv[2]))
PY
        then
            echo "⚠️ 无法清理已停止 simulator 的 runtime snapshot，将由下次启动时兜底处理。" >&2
        fi
    fi
    echo "✅ 清理完成。GPU 空闲: $(nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>/dev/null)"
}

handle_signal() {
    local exit_status="$1"
    if [ "$LAUNCH_IN_PROGRESS" = true ]; then
        if [ -z "$PENDING_SIGNAL_STATUS" ]; then
            PENDING_SIGNAL_STATUS="$exit_status"
        fi
        return
    fi
    cleanup
    exit "$exit_status"
}

begin_process_group_launch() {
    PENDING_SIGNAL_STATUS=""
    LAUNCH_IN_PROGRESS=true
}

complete_process_group_launch() {
    local output_name="$1"
    local launched_pid="$2"
    local pending_status

    printf -v "$output_name" '%s' "$launched_pid"
    LAUNCH_IN_PROGRESS=false
    pending_status="$PENDING_SIGNAL_STATUS"
    PENDING_SIGNAL_STATUS=""
    if [ -n "$pending_status" ]; then
        handle_signal "$pending_status"
    fi
}

monitor_runtime() {
    local exited_pid=""
    local exit_status=0

    wait -n -p exited_pid "$SIM_PID" "$NAV2_PID" || exit_status=$?
    if [ -z "$exited_pid" ]; then
        return "$exit_status"
    fi
    if [ "$exited_pid" = "$NAV2_PID" ]; then
        echo "❌ Nav2 进程意外退出，查看 $NAV2_LOG"
        return 1
    fi
    if [ "$exit_status" -ne 0 ]; then
        echo "❌ 仿真进程意外退出，查看 $SIM_LOG"
    fi
    return "$exit_status"
}

trap cleanup EXIT
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

echo "========================================================"
RVIZ_NOTE=""; [ "$RVIZ_ARG" = "rviz:=true" ] && RVIZ_NOTE="（含 RViz）"
echo "EAI 仿真器 + Nav2 一键启动 ${RVIZ_NOTE}"
echo "========================================================"

# 1. 启动仿真
echo "▶ 启动 Isaac Sim 仿真..."
if [ ! -f "$CONDA_SH" ]; then
    echo "❌ 找不到 conda.sh: $CONDA_SH"
    echo "   可通过 CONDA_SH=/path/to/conda.sh 覆盖"
    exit 1
fi
begin_process_group_launch
setsid /bin/bash --noprofile --norc -c '
    source "$1"
    conda activate env_isaaclab
    cd "$2"
    exec python -u simulator.py --env=nav2
' _ "$CONDA_SH" "$REPO_ROOT" > "$SIM_LOG" 2>&1 &
complete_process_group_launch SIM_PID "$!"

# 2. 等待仿真就绪
echo "⏳ 等待仿真就绪（最多 ${EAI_NAV2_SIM_READY_TIMEOUT} 秒，可用 EAI_NAV2_SIM_READY_TIMEOUT 覆盖）..."
SIM_READY=false
for i in $(seq 1 "$EAI_NAV2_SIM_READY_TIMEOUT"); do
    if grep -Fq "[EAI Simulator] cmd_vel enabled: /carter_1/cmd_vel" "$SIM_LOG" 2>/dev/null; then
        echo "✅ 仿真就绪"
        SIM_READY=true
        break
    fi
    if ! kill -0 "$SIM_PID" 2>/dev/null; then
        echo "❌ 仿真进程退出，查看 $SIM_LOG"; exit 1
    fi
    sleep 1
done
if [ "$SIM_READY" != true ]; then
    echo "❌ 等待仿真就绪超时，查看 $SIM_LOG"; exit 1
fi

# 3. 启动 Nav2
echo "▶ 启动 Nav2 栈（干净的系统 ROS2 ${ROS_DISTRO_NAME} + CycloneDDS）..."
begin_process_group_launch
setsid env -i "${SYSTEM_ROS_ENV[@]}" \
    /bin/bash --noprofile --norc -c '
        source "$1/setup.bash"
        cd "$2"
        exec ros2 launch algorithm/nav2/nav2.launch.py \
            robot_name:=carter_1 robot_type:=Carter scene:=factory "$3"
    ' _ "$ROS_ROOT" "$REPO_ROOT" "$RVIZ_ARG" > "$NAV2_LOG" 2>&1 &
complete_process_group_launch NAV2_PID "$!"

echo "⏳ 等待 Nav2 激活（最多 ${EAI_NAV2_STACK_READY_TIMEOUT} 秒，可用 EAI_NAV2_STACK_READY_TIMEOUT 覆盖）..."
NAV2_READY=false
for i in $(seq 1 "$EAI_NAV2_STACK_READY_TIMEOUT"); do
    if ! kill -0 "$NAV2_PID" 2>/dev/null; then
        echo "❌ Nav2 进程退出，查看 $NAV2_LOG"; exit 1
    fi
    if grep -q "velocity_smoother" "$NAV2_LOG" 2>/dev/null && \
       grep -q "Creating bond timer" "$NAV2_LOG" 2>/dev/null; then
        echo "✅ Nav2 已激活"
        NAV2_READY=true
        break
    fi
    sleep 1
done
if [ "$NAV2_READY" != true ]; then
    echo "❌ 等待 Nav2 激活超时，查看 $NAV2_LOG"; exit 1
fi

echo "========================================================"
echo "✅ 全部就绪！发送导航目标示例（新终端，系统 ROS2 环境）:"
echo "   请按 README 的 Manual Launch 指引使用未激活 Conda 的系统 ROS2 终端。"
echo "   export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp"
echo "   /usr/bin/python3 algorithm/nav2/send_goal.py --x -5.0 --y -8.0"
echo ""
echo "日志: 仿真=$SIM_LOG  Nav2=$NAV2_LOG"
echo "按 Ctrl+C 停止全部并清理内存"
echo "========================================================"

# 任一核心进程退出都会结束本脚本，并由 EXIT trap 清理另一进程。
monitor_runtime
