#!/usr/bin/env bash
# run_sim2sim.sh —— Isaac Sim × 产品导航 Docker 一键闭环（EAI 仓库内自包含）
#
# 用法:
#   run_sim2sim.sh [--lidar c01|mid360|lidar|orsus] [--robot simple_car|carter] [--map rmuc2024] [--gui] [--rviz]
#
# 默认: simple_car + c01 + rmuc2024，headless，无 RViz。
# 顺序固定为 docker 先行（等 healthy+idle）→ 仿真器 → adapter：
#   1. docker 对端集合稳定，规避 Isaac 订阅竞态崩溃；
#   2. 仿真重启 /clock 归零，新容器才不会陷入 TF_OLD_DATA 风暴。
# Ctrl+C 依次收掉 adapter / 仿真器 / docker 容器，无后台残留。
# 发目标: curl -s -X POST -H 'Content-Type: application/json' \
#           -d '{"x":2.5,"y":0.0,"theta":0.0,"frame_id":"map"}' \
#           http://127.0.0.1:7999/v2/navigate_to_pose
set -u

ADAPTER_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
REPO_ROOT=$(cd "$ADAPTER_DIR/../.." && pwd)
CONDA_SH=${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}
MAP=rmuc2024; ROBOT=simple_car; LIDAR=c01; GUI=0; RVIZ=0

while [ $# -gt 0 ]; do
  case "$1" in
    --lidar) LIDAR=${2:?}; shift ;;
    --robot) ROBOT=${2:?}; shift ;;
    --map)   MAP=${2:?}; shift ;;
    --gui) GUI=1 ;;
    --rviz) RVIZ=1 ;;
    *) echo "未知参数: $1（--lidar c01|mid360|lidar|orsus --robot simple_car|carter --map --gui --rviz）" >&2; exit 1 ;;
  esac
  shift
done

case "$LIDAR" in c01|octagon_aio_c01) LIDAR=c01 ;; mid360|lidar|orsus) ;; *) echo "错误: --lidar 可选 c01 / mid360 / lidar / orsus" >&2; exit 1 ;; esac
case "$ROBOT" in simple_car|carter) ;; *) echo "错误: --robot 可选 simple_car / carter" >&2; exit 1 ;; esac
[ "$ROBOT" = carter ] && [ "$LIDAR" = mid360 ] && { echo "错误: carter 只有 +c01 / +lidar / +orsus 组合" >&2; exit 1; }
[ "$ROBOT" = simple_car ] && { case "$LIDAR" in c01|mid360) ;; *) echo "错误: simple_car 只有 +c01 / +mid360 组合" >&2; exit 1 ;; esac; }

ISAAC_ENV=${MAP}_${ROBOT}_${LIDAR}_navigation_io
[ -f "$REPO_ROOT/source/EAI_hmrs/EAI_hmrs/envs/$ISAAC_ENV.json" ] || { echo "错误: env 不存在 $ISAAC_ENV" >&2; exit 1; }

case "$LIDAR,$ROBOT" in
  c01,simple_car)    PROFILE=octagon_aio_c01 ;;
  mid360,simple_car) PROFILE=mid360 ;;
  c01,carter)        PROFILE=octagon_aio_c01_carter ;;
  lidar,carter)      PROFILE=lidar_carter ;;
  orsus,carter)      PROFILE=orsus_carter ;;
  *) echo "错误: 无 $ROBOT+$LIDAR 的 adapter profile" >&2; exit 1 ;;
esac
ROBOT_NS=${ROBOT}_1

GUI_FLAG=--headless; [ "$GUI" = 1 ] && GUI_FLAG=""
LOG_SIM=/tmp/eai_sim2sim_simulator.log
LOG_ADAPTER=/tmp/eai_sim2sim_adapter.log
SIM_PID=""; ADAPTER_PID=""; RVIZ_PID=""

cleanup() {
  trap '' INT TERM
  echo ""
  echo "[sim2sim] 清理中..."
  # setsid 的子进程是会话/进程组长：负 PID 整组终止，避免 wrapper 死了子进程逃逸
  for pid in "$RVIZ_PID" "$ADAPTER_PID" "$SIM_PID"; do
    [ -n "$pid" ] && kill -TERM -- -"$pid" 2>/dev/null
  done
  sleep 6
  for pid in "$RVIZ_PID" "$ADAPTER_PID" "$SIM_PID"; do
    [ -n "$pid" ] && kill -KILL -- -"$pid" 2>/dev/null
  done
  # 兜底清扫（方括号写法避免 pkill 匹配到本脚本自身命令行）
  pkill -KILL -f "adapter_[n]ode.py" 2>/dev/null
  pkill -KILL -f "nav_product_adapter.[l]aunch.py" 2>/dev/null
  pkill -KILL -f "simulator.py --env rmuc2024_" 2>/dev/null
  pkill -KILL -f "rviz2 -d .*nav_sim2sim.[r]viz" 2>/dev/null
  bash "$ADAPTER_DIR/docker/docker_nav.sh" stop
  echo "[sim2sim] 已全部退出。"
  exit 0
}
trap cleanup INT TERM

# 0) 预检：终止上一轮可能残留的 adapter/仿真进程（否则新 adapter 的端点守卫
#    会看到同名节点占用产品话题而 fail-closed 拒绝启动）
for pat in "adapter_[n]ode.py" "nav_product_adapter.[l]aunch.py" "simulator.py --env rmuc2024_"; do
  if pgrep -f "$pat" >/dev/null 2>&1; then
    echo "[sim2sim] 预清理: 终止残留进程 ($pat)"
    pkill -TERM -f "$pat" 2>/dev/null; sleep 4
    pkill -KILL -f "$pat" 2>/dev/null; sleep 1
  fi
done

# 1) docker 先行
bash "$ADAPTER_DIR/docker/docker_nav.sh" start --robot "$ROBOT" --lidar "$LIDAR" --map "$MAP" --no-follow || exit 1

# 2) Isaac 仿真器（conda env_isaaclab）
setsid /bin/bash --noprofile --norc -c '
  source "$1"; conda activate env_isaaclab; cd "$2"
  unset EAI_ROS_LOADER_REEXEC ISAAC_ROS_PATH AMENT_PREFIX_PATH EAI_SIMPLE_CAR_LIDAR_VARIANT
  export LD_LIBRARY_PATH="" PYTHONUNBUFFERED=1
  export PYTHONPATH="$2/source/EAI:$2/source/EAI_assets:$2/source/EAI_hmrs"
  export EAI_USD_ROOT="$2/usd" EAI_ASSETS_AUTO_DOWNLOAD=0
  export ROS_DISTRO=humble ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
  export FASTRTPS_DEFAULT_PROFILES_FILE="$3"
  exec python simulator.py --env "$4" --device cuda:0 $5
' _ "$CONDA_SH" "$REPO_ROOT" "$ADAPTER_DIR/docker/fastdds_udp_only.xml" "$ISAAC_ENV" "$GUI_FLAG" > "$LOG_SIM" 2>&1 &
SIM_PID=$!

# 3) 等仿真就绪
# 大场景（如 factory，1.4GB 资产 + 数百材质）首次加载需编译着色器，
# 可能超过 5 分钟；READY_TIMEOUT 可覆盖（秒）。着色器缓存会保留，第二次启动明显加快。
READY_TIMEOUT=${READY_TIMEOUT:-480}
printf '[sim2sim] 等待仿真就绪（READY_TIMEOUT=%ss）' "$READY_TIMEOUT"
ok=""
for i in $(seq 1 "$READY_TIMEOUT"); do
  grep -q "cmd_vel enabled" "$LOG_SIM" 2>/dev/null && ok=1 && break
  kill -0 "$SIM_PID" 2>/dev/null || { echo ""; echo "[sim2sim] 仿真进程退出，见 $LOG_SIM" >&2; cleanup; }
  printf '.'; sleep 2
done
echo ""
[ -n "$ok" ] || { echo "[sim2sim] 仿真就绪超时，见 $LOG_SIM" >&2; cleanup; }
grep -E "lidar variant|cmd_vel enabled" "$LOG_SIM" | head -2

# 4) 产品 adapter（系统 ROS；带幽灵端点重试看护）
: > "$LOG_ADAPTER"
ADAPTER_PID=""

start_adapter() {
  setsid /bin/bash --noprofile --norc -c '
    cd "$2"; source /opt/ros/humble/setup.bash
    export PYTHONUNBUFFERED=1 ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    export FASTRTPS_DEFAULT_PROFILES_FILE="$3"
    exec ros2 launch algorithm/nav_product_adapter/nav_product_adapter.launch.py       profile_name:="$4" robot_name:="$5" nav_hub_id:=GSXXXXXXXX
  ' _ "$CONDA_SH" "$REPO_ROOT" "$ADAPTER_DIR/docker/fastdds_udp_only.xml" "$PROFILE" "$ROBOT_NS" >> "$LOG_ADAPTER" 2>&1 &
  ADAPTER_PID=$!
}

ADAPTER_OK=""
for attempt in 1 2 3; do
  start_adapter
  # 前 20 秒存活即视为通过启动守卫（幽灵端点冲突会在数秒内退出）
  for i in $(seq 1 20); do
    kill -0 "$ADAPTER_PID" 2>/dev/null || break
    sleep 1
  done
  if kill -0 "$ADAPTER_PID" 2>/dev/null; then ADAPTER_OK=1; break; fi
  echo "[sim2sim] adapter 第 ${attempt}/3 次启动失败（常见原因: 上一轮参与者残留的 DDS 幽灵端点）"
  tail -2 "$LOG_ADAPTER" | sed 's/^/    /'
  [ "$attempt" = 3 ] && break
  echo "[sim2sim] 等待 45 秒让幽灵参与者过期后重试..."
  sleep 45
done
[ -n "$ADAPTER_OK" ] || { echo "[sim2sim] adapter 三次启动均失败，见 $LOG_ADAPTER" >&2; cleanup; }

# 5) RViz（产品话题视图）
if [ "$RVIZ" = 1 ]; then
  setsid /bin/bash --noprofile --norc -c '
    source /opt/ros/humble/setup.bash
    export ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_fastrtps_cpp
    export FASTRTPS_DEFAULT_PROFILES_FILE="$2"
    exec rviz2 -d "$3"
  ' _ x "$ADAPTER_DIR/docker/fastdds_udp_only.xml" "$ADAPTER_DIR/docker/rviz/nav_sim2sim.rviz" >/tmp/eai_sim2sim_rviz.log 2>&1 &
  RVIZ_PID=$!
fi

echo "=============================================================="
echo " sim2sim 就绪 | env=$ISAAC_ENV adapter=$PROFILE ns=$ROBOT_NS"
echo " 发目标(2.5,0): curl -s -X POST -H 'Content-Type: application/json' \\"
echo "   -d '{\"x\":2.5,\"y\":0,\"theta\":0,\"frame_id\":\"map\"}' http://127.0.0.1:7999/v2/navigate_to_pose"
echo " 状态: $ADAPTER_DIR/docker/docker_nav.sh status"
echo " 日志: $LOG_SIM / $LOG_ADAPTER"
echo " Ctrl+C 全部退出"
echo "=============================================================="

wait "$SIM_PID" 2>/dev/null
echo "[sim2sim] 仿真进程退出。"
cleanup
