#!/bin/bash
# EAI 仿真器 + Nav2 一键启动脚本
#
# 用法:
#   bash algorithm/ros/nav2/run_nav2.sh            # 启动仿真 + Nav2
#   bash algorithm/ros/nav2/run_nav2.sh --rviz     # 额外启动 RViz 可视化
#
# 注意: 仿真必须 GUI 模式（headless 下 Orsus 不发布传感器），不支持 --headless。
#
# 脚本会:
#   1. 启动 Isaac Sim 仿真 (conda env_isaaclab)
#   2. 等待仿真就绪后启动 Nav2 栈 (系统 ROS2)
#   3. Ctrl+C 时自动清理所有进程（避免残留占用 GPU 内存）

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SIM_LOG=/tmp/eai_nav2_sim.log
NAV2_LOG=/tmp/eai_nav2_stack.log
CONDA_SH="${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
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

cleanup() {
    trap - EXIT INT TERM
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
    echo "✅ 清理完成。GPU 空闲: $(nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>/dev/null)"
}
trap cleanup EXIT INT TERM

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
setsid /bin/bash --noprofile --norc -c '
    source "$1"
    conda activate env_isaaclab
    cd "$2"
    exec python -u simulator.py --env=nav2
' _ "$CONDA_SH" "$REPO_ROOT" > "$SIM_LOG" 2>&1 &
SIM_PID=$!

# 2. 等待仿真就绪
echo "⏳ 等待仿真就绪（约 30-40 秒）..."
SIM_READY=false
for i in $(seq 1 90); do
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
echo "▶ 启动 Nav2 栈（干净的系统 ROS2 Humble + CycloneDDS）..."
setsid env -i \
    HOME="$SYSTEM_ROS_HOME" \
    USER="$SYSTEM_ROS_USER" \
    LOGNAME="$SYSTEM_ROS_USER" \
    PATH=/usr/bin:/bin:/opt/ros/humble/bin \
    LANG=C.UTF-8 \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    DISPLAY="$SYSTEM_ROS_DISPLAY" \
    XAUTHORITY="$SYSTEM_ROS_XAUTHORITY" \
    XDG_RUNTIME_DIR="$SYSTEM_ROS_XDG_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$SYSTEM_ROS_DBUS" \
    /bin/bash --noprofile --norc -c '
        source /opt/ros/humble/setup.bash
        cd "$1"
        exec ros2 launch algorithm/ros/nav2/nav2.launch.py \
            robot_name:=carter_1 robot_type:=Carter scene:=factory "$2"
    ' _ "$REPO_ROOT" "$RVIZ_ARG" > "$NAV2_LOG" 2>&1 &
NAV2_PID=$!

echo "⏳ 等待 Nav2 激活..."
NAV2_READY=false
for i in $(seq 1 45); do
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
echo "   请使用 README 中的 env -i 模板，不能在 Conda 环境中启动 ROS。"
echo "   /usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -5.0 --y -8.0"
echo ""
echo "日志: 仿真=$SIM_LOG  Nav2=$NAV2_LOG"
echo "按 Ctrl+C 停止全部并清理内存"
echo "========================================================"

# 保持运行直到 Ctrl+C
wait "$SIM_PID"
