#!/bin/bash
# EAI 仿真器 + Nav2 一键启动脚本
#
# 用法:
#   bash algorithm/ros/nav2/run_nav2.sh            # 启动仿真 + Nav2
#   bash algorithm/ros/nav2/run_nav2.sh --rviz     # 额外启动 RViz 可视化
#
# 注意: 仿真必须 GUI 模式（headless 下 GSHub 不发布传感器），不支持 --headless。
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

cleanup() {
    echo ""
    echo "🧹 正在清理所有进程..."
    pkill -9 -f "nav2.launch" 2>/dev/null
    pkill -9 -f "carter_nav2.launch" 2>/dev/null
    pkill -9 -f "nav2/tf_bridge.py" 2>/dev/null
    pkill -9 -f "pointcloud_to_laserscan_node" 2>/dev/null
    pkill -9 -f "rviz2 -d.*view.rviz" 2>/dev/null
    for n in map_server amcl controller_server planner_server bt_navigator \
             behavior_server smoother_server waypoint_follower velocity_smoother \
             lifecycle_manager; do
        pkill -9 -f "$n" 2>/dev/null
    done
    pkill -9 -f "simulator.py --env=nav2" 2>/dev/null
    sleep 2
    echo "✅ 清理完成。GPU 空闲: $(nvidia-smi --query-gpu=memory.free --format=csv,noheader 2>/dev/null)"
}
trap cleanup EXIT INT TERM

echo "========================================================"
RVIZ_NOTE=""; [ "$RVIZ_ARG" = "rviz:=true" ] && RVIZ_NOTE="（含 RViz）"
echo "EAI 仿真器 + Nav2 一键启动 ${RVIZ_NOTE}"
echo "========================================================"

# 1. 启动仿真
echo "▶ 启动 Isaac Sim 仿真..."
(
    if [ ! -f "$CONDA_SH" ]; then
        echo "❌ 找不到 conda.sh: $CONDA_SH"
        echo "   可通过 CONDA_SH=/path/to/conda.sh 覆盖"
        exit 1
    fi
    source "$CONDA_SH"
    conda activate env_isaaclab
    cd "$REPO_ROOT"
    exec python -u simulator.py --env=nav2
) > "$SIM_LOG" 2>&1 &
SIM_PID=$!

# 2. 等待仿真就绪
echo "⏳ 等待仿真就绪（约 30-40 秒）..."
for i in $(seq 1 90); do
    if grep -q "Nav2 控制已启用" "$SIM_LOG" 2>/dev/null; then
        echo "✅ 仿真就绪"
        break
    fi
    if ! kill -0 $SIM_PID 2>/dev/null; then
        echo "❌ 仿真进程退出，查看 $SIM_LOG"; exit 1
    fi
    sleep 1
done

# 3. 启动 Nav2
echo "▶ 启动 Nav2 栈..."
(
    source /opt/ros/humble/setup.bash
    cd "$REPO_ROOT"
    exec ros2 launch algorithm/ros/nav2/nav2.launch.py \
        robot_name:=carter_1 robot_type:=Carter scene:=factory "$RVIZ_ARG"
) > "$NAV2_LOG" 2>&1 &

echo "⏳ 等待 Nav2 激活..."
for i in $(seq 1 45); do
    if grep -q "velocity_smoother" "$NAV2_LOG" 2>/dev/null && \
       grep -q "Creating bond timer" "$NAV2_LOG" 2>/dev/null; then
        echo "✅ Nav2 已激活"
        break
    fi
    sleep 1
done

echo "========================================================"
echo "✅ 全部就绪！发送导航目标示例（新终端，系统 ROS2 环境）:"
echo "   source /opt/ros/humble/setup.bash"
echo "   /usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -5.0 --y -8.0"
echo ""
echo "日志: 仿真=$SIM_LOG  Nav2=$NAV2_LOG"
echo "按 Ctrl+C 停止全部并清理内存"
echo "========================================================"

# 保持运行直到 Ctrl+C
wait $SIM_PID
