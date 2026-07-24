#!/bin/bash
# ROS2 Nav2 快速测试脚本

set -e

ROBOT=${1:-carter}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

echo "========================================"
echo "ROS2 Nav2 快速测试"
echo "========================================"
echo "机器人: $ROBOT"
echo "项目根目录: $PROJECT_ROOT"
echo "========================================"
echo ""

# 检查 conda 环境
if [[ -z "$CONDA_DEFAULT_ENV" ]] || [[ "$CONDA_DEFAULT_ENV" != "env_isaaclab" ]]; then
    echo "❌ 错误: 未激活 conda 环境"
    echo "   请运行: conda activate env_isaaclab"
    exit 1
fi

# 检查 ROS2
if ! command -v ros2 &> /dev/null; then
    echo "⚠️  警告: ROS2 未找到"
    echo "   ROS2 功能将不可用"
    echo "   请确保已安装 ROS2 Humble 并 source setup.bash"
    echo ""
fi

# 显示使用说明
echo "📋 测试步骤:"
echo ""
echo "1. 仿真器将在当前终端启动"
echo "2. 等待看到 '✅ Nav2 Bridge 已启动' 消息"
echo "3. 在新终端中测试 ROS2 话题:"
echo ""
echo "   # 查看话题"
echo "   ros2 topic list | grep ${ROBOT}_nav"
echo ""
echo "   # 发送控制命令（前进）"
echo "   python $SCRIPT_DIR/ros2_send_cmd_vel.py --robot ${ROBOT}_nav --linear 0.5 --rate 10"
echo ""
echo "   # 查看传感器"
echo "   python $PROJECT_ROOT/algorithm/ros/tools/vis_sensors.py --namespace /${ROBOT}_nav"
echo ""
echo "========================================"
echo ""
echo "按 Enter 键启动仿真器，或 Ctrl+C 取消..."
read

# 启动仿真器
cd "$PROJECT_ROOT"
python algorithm/ros/tools/ros2_nav2_test.py --robot "$ROBOT"
