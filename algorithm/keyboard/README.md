# EAI Keyboard Cmd Vel Client

`keyboard.py` is the interactive ROS2 keyboard client for EAI simulator
`/<robot>/cmd_vel` interfaces. It does not start Isaac Sim, build scenes, or
own robot controllers; the simulator must already be running with Keyboard or
Navigation I/O enabled so that `geometry_msgs/msg/Twist` subscribers exist.

Run it with the Python from the selected system ROS installation, not the
`env_isaaclab` Conda Python:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robot carter_1
```

For multiple robots, pass a comma-separated list and press `Q` to switch the
active topic:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robots carter_1,go2_1,lite3_1
```

If no `--robot`, `--robots`, or `--topic` is supplied, the client waits briefly
and discovers published `/<robot>/cmd_vel` topics. Use `--topic` only when a
custom topic is required.

Key bindings:

| Key | Command |
| --- | --- |
| `W` / `S` | forward / backward |
| `A` / `D` | lateral left / right for holonomic bases |
| `R` / `F` | ascend / descend for aerial robots |
| `C` / `V` | yaw left / right |
| `K` or Space | stop |
| `Q` | switch to the next configured robot |
| Esc or Ctrl+C | stop and exit |

On exit, the client publishes one zero-velocity command to every configured
topic before shutting down the ROS node.
