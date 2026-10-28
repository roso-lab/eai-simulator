#!/usr/bin/env bash
set -eo pipefail
source /catkin_ws/devel/setup.bash
set -u
export ROS_MASTER_URI=http://127.0.0.1:11311 ROS_HOSTNAME=localhost
export PYTHONPATH=/upstream/ros_bt_py/src:${PYTHONPATH:-}
export PYTHONDONTWRITEBYTECODE=1
roscore > /tmp/roscore.log 2>&1 &
master_pid=$!
trap 'kill "$master_pid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 100); do
    if rosparam list >/dev/null 2>&1; then break; fi
    sleep 0.1
done
/home/airs/.cache/heterocoord/envs/bt-capabilities/bin/python \
    /workspace/algorithm/system/coordination/bt_capabilities/scripts/native_endpoint.py \
    --source-root /upstream --bind 0.0.0.0 "$@"
