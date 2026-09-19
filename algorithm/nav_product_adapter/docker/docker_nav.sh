#!/usr/bin/env bash
# docker_nav.sh —— EAI 算法包内的产品导航 Docker 对接入口（Isaac 链）
#
# 用法:
#   docker_nav.sh start [--lidar c01|mid360|lidar|orsus] [--robot simple_car|carter] [--map rmuc2024] [--no-follow] [--dry-run]
#   docker_nav.sh stop      # 关闭并删除容器
#   docker_nav.sh status    # 查看健康/状态
#
# 对接物料全部在本目录（nav.env / fastdds_udp_only.xml / device_info.json /
# system_parameters.yaml.* / maps/），不依赖 /home/pupil/nav-loop。
# 换雷达/换机器人不需要重编译镜像：差异只通过 SENSOR_VARIANT 环境变量 +
# 单文件只读挂载的 system_parameters 变体生效（rm+run 毫秒级重建）。
# 产品镜像 docker.gs-symbiosis.com/internal/nav:runtime-0.2.10 视为不可改黑盒，
# 本脚本只用其官方外置配置口（env-file / /workspace/.info / /maps / /tmp/roslog）。
#
# Isaac 链顺序约束：先跑本脚本（等 idle），再启动仿真器与 adapter——
# 仿真重启后 /clock 归零，沿用旧容器会陷入 TF_OLD_DATA 风暴。
set -u

DOCK_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
NAV_ENV_BASE=$DOCK_DIR/nav.env
FASTDDS_XML=$DOCK_DIR/fastdds_udp_only.xml
INFO_MOUNT=$DOCK_DIR/info_runtime        # 容器可写的 .info（设备信息 + 参数落盘）
MAPS_DIR=$DOCK_DIR/maps
NAV_IMAGE=docker.gs-symbiosis.com/internal/nav:runtime-0.2.10
CONTAINER=nav_loop
API=http://127.0.0.1:7999

CMD=${1:-}; [ $# -gt 0 ] && shift
ROBOT=simple_car; LIDAR=c01; MAP=rmuc2024; FOLLOW=1; DRY_RUN=0

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
[ "$CMD" = "-h" ] || [ "$CMD" = "--help" ] && usage 0
case "$CMD" in start|stop|status) ;; *) echo "错误: 未知子命令 '$CMD'（start / stop / status）" >&2; usage 1 ;; esac

while [ $# -gt 0 ]; do
  case "$1" in
    --robot) ROBOT=${2:?}; shift ;;
    --lidar) LIDAR=${2:?}; shift ;;
    --map)   MAP=${2:?}; shift ;;
    --no-follow) FOLLOW=0 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "未知参数: $1" >&2; usage 1 ;;
  esac
  shift
done

docker_cmd() {  # 当前用户已在 docker 组；个别 shell 需要刷新组时走 sg
  if docker info >/dev/null 2>&1; then docker "$@"; else sg docker -c "docker $*"; fi
}

resolve_variant() {
  case "$LIDAR" in
    c01|octagon_aio_c01) LIDAR=c01; SENSOR_VARIANT=octagon_aio_c01 ;;
    mid360) LIDAR=mid360; SENSOR_VARIANT=mid360 ;;
    # EAI 原生雷达/Orsus 复用产品的 octagon_aio_c01 传感器变体：
    # 产品侧只看到一份 3D 点云 + odometry + /clock 的标准 navigation I/O，
    # 挂载差异完全由 system_parameters 的 lidar_height/pitch 表达。
    lidar) LIDAR=lidar; SENSOR_VARIANT=octagon_aio_c01 ;;
    orsus) LIDAR=orsus; SENSOR_VARIANT=octagon_aio_c01 ;;
    *) echo "错误: 未知雷达 '$LIDAR'（可选 c01 / mid360 / lidar / orsus）" >&2; exit 1 ;;
  esac
  case "$ROBOT" in
    simple_car)
      case "$LIDAR" in
        c01) YAML_VARIANT=$DOCK_DIR/system_parameters.yaml.c01 ;;
        mid360) YAML_VARIANT=$DOCK_DIR/system_parameters.yaml.mid360 ;;
        *) echo "错误: simple_car 只有 +c01 / +mid360 组合" >&2; exit 1 ;;
      esac ;;
    carter)
      case "$LIDAR" in
        c01) YAML_VARIANT=$DOCK_DIR/system_parameters.yaml.carter_c01 ;;
        lidar) YAML_VARIANT=$DOCK_DIR/system_parameters.yaml.carter_lidar ;;
        orsus) YAML_VARIANT=$DOCK_DIR/system_parameters.yaml.carter_orsus ;;
        *) echo "错误: carter 只有 +c01 / +lidar / +orsus 组合" >&2; exit 1 ;;
      esac ;;
    *) echo "错误: 未知机器人 '$ROBOT'（可选 simple_car / carter）" >&2; exit 1 ;;
  esac
  [ -f "$YAML_VARIANT" ] || { echo "错误: 缺少变体文件 $YAML_VARIANT" >&2; exit 1; }
  [ -d "$MAPS_DIR/$MAP" ] || { echo "错误: 地图目录不存在 $MAPS_DIR/$MAP" >&2; exit 1; }
  [ -f "$NAV_ENV_BASE" ] || { echo "错误: 缺少 $NAV_ENV_BASE" >&2; exit 1; }
  [ -f "$FASTDDS_XML" ] || { echo "错误: 缺少 $FASTDDS_XML" >&2; exit 1; }
}

api_post() { curl -sS --max-time 3 -X POST -H 'Content-Length: 0' "$API$1" 2>/dev/null || true; }

case "$CMD" in
status)
  echo "--- 容器 ---"
  docker_cmd ps -a --filter name="^$CONTAINER$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
  echo "--- 健康 ---"; api_post /v2/get_health; echo
  echo "--- 状态 ---"; api_post /v2/get_status; echo
  exit 0 ;;
stop)
  docker_cmd rm -f "$CONTAINER" >/dev/null 2>&1 && echo "[docker_nav] 容器 $CONTAINER 已删除" || echo "[docker_nav] 容器本就不在运行"
  exit 0 ;;
esac

# ============================ start ============================
resolve_variant

# 容器可写的 .info 运行时目录：设备信息来自本目录，参数由 param_manager 落盘
mkdir -p "$INFO_MOUNT"
cp -f "$DOCK_DIR/device_info.json" "$INFO_MOUNT/device_info.json"

# 生成临时 env-file：继承 nav.env，仅覆盖 SENSOR_VARIANT / SCENE_NAME（不改原文件）
TMP_ENV=$(mktemp /tmp/nav_env.XXXXXX)
grep -vE '^(SENSOR_VARIANT|SCENE_NAME)=' "$NAV_ENV_BASE" > "$TMP_ENV"
printf 'SENSOR_VARIANT=%s\nSCENE_NAME=%s\n' "$SENSOR_VARIANT" "$MAP" >> "$TMP_ENV"
trap 'rm -f "$TMP_ENV"' EXIT

RUN_ARGS=(run -d --name "$CONTAINER" --network host --restart no
  --env-file "$TMP_ENV"
  -v "$DOCK_DIR/roslog":/tmp/roslog:rw
  -v "$FASTDDS_XML":/opt/nav_sim/fastdds_udp_only.xml:ro
  -v "$MAPS_DIR":/maps:ro
  -v "$INFO_MOUNT":/workspace/.info
  -v "$YAML_VARIANT":/workspace/.info/system_parameters.yaml:ro
  "$NAV_IMAGE")

echo "=============================================================="
echo " docker_nav start | 机器人=$ROBOT 雷达=$LIDAR 地图=$MAP"
echo " SENSOR_VARIANT=$SENSOR_VARIANT  外参文件=$(basename "$YAML_VARIANT")"
echo " 镜像 runtime-0.2.10 不变（无需编译）"
echo "=============================================================="

if [ "$DRY_RUN" = 1 ]; then
  echo "[dry-run] docker rm -f $CONTAINER; docker ${RUN_ARGS[*]}"; exit 0
fi

mkdir -p "$DOCK_DIR/roslog"
docker_cmd rm -f "$CONTAINER" >/dev/null 2>&1
if ! docker_cmd "${RUN_ARGS[@]}" >/dev/null; then
  echo "[docker_nav] 启动失败，请手动检查: docker logs $CONTAINER" >&2; exit 1
fi

printf '等待容器就绪'
ok=""
for i in $(seq 1 90); do
  out=$(api_post /v2/get_health)
  printf '.'; [ $((i % 10)) = 0 ] && printf ' '
  if printf '%s' "$out" | grep -q '"healthy":true'; then ok=1; break; fi
  sleep 1
done
echo ""
if [ -z "$ok" ]; then
  echo "[docker_nav] 警告: 90 秒未 healthy（无仿真时钟时节点等待属正常）。"
  echo "             Isaac 链请先本脚本再起仿真；排错: docker logs $CONTAINER"
else
  echo "[docker_nav] healthy ✓  $(api_post /v2/get_health)"
fi

for i in $(seq 1 30); do
  out=$(api_post /v2/get_status)
  if printf '%s' "$out" | grep -q '"status":"idle"'; then echo "[docker_nav] 状态 idle ✓（可以发目标了）"; break; fi
  sleep 1
done
out=$(api_post /v2/get_status)
printf '%s' "$out" | grep -q '"status":"idle"' || echo "[docker_nav] 当前状态: $out"

if [ "$FOLLOW" = 1 ]; then
  echo "[docker_nav] 前台跟随日志中... (Ctrl+C 仅退出跟随，容器保留；stop 才删除)"
  trap - INT TERM HUP
  docker_cmd logs -f "$CONTAINER" & LOG_PID=$!
  wait "$LOG_PID" 2>/dev/null
else
  echo "[docker_nav] 后台运行中。停止: $0 stop | 状态: $0 status"
fi
