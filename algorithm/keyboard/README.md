# EAI Keyboard Cmd Vel Client

keyboard.py is an interactive ROS2 client that publishes geometry_msgs/msg/Twist to the EAI /<robot>/cmd_vel interfaces. It does not start Isaac Sim, build scenes, or own robot controllers. The simulator must already be running with Keyboard or Navigation I/O enabled so that the subscribers exist.

Run it with the Python from the selected system ROS installation, not env_isaaclab:

~~~bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robot carter_1
/usr/bin/python3 algorithm/keyboard/keyboard.py --robots carter_1,go2_1,lite3_1
~~~

Press Q to rotate through configured robots. Use --topic for a custom topic. With no --robot, --robots, or --topic, the client briefly discovers published /<robot>/cmd_vel topics.

## Key bindings

W/S moves forward or backward; A/D strafes holonomic bases; R/F moves aerial robots up or down; C/V rotates left or right; K or Space stops; Q selects the next robot; Esc or Ctrl+C exits. On exit, one zero-velocity command is published to every configured topic.
