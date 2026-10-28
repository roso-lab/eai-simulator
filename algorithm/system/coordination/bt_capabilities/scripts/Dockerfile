FROM ubuntu:22.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git build-essential cmake catkin python3-venv python3-pytest python3-rospy \
    python3-rosservice python3-roslaunch python3-rosmaster python3-rosparam \
    python3-actionlib python3-tf python3-genpy python3-jsonpickle python3-requests \
    python3-simplejson python3-rospkg ros-message-generation ros-geometry-msgs \
    ros-actionlib-msgs ros-std-msgs python3-geometry-msgs python3-actionlib-msgs \
    python3-std-msgs && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv --system-site-packages /home/airs/.cache/heterocoord/envs/bt-capabilities
# Original message definitions are mounted read-only at runtime.
CMD ["bash", "-c", "mkdir -p /catkin_ws/src && ln -s /upstream/ros_bt_py_msgs /catkin_ws/src/ros_bt_py_msgs && cd /catkin_ws && catkin_make -DPYTHON_EXECUTABLE=/usr/bin/python3 -j2 && bash /workspace/algorithm/system/coordination/bt_capabilities/scripts/start_runtime.sh"]
