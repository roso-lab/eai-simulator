# EAI 键盘速度客户端

keyboard.py 是向 EAI Simulator 的 /<robot>/cmd_vel 发布 geometry_msgs/msg/Twist 的交互式 ROS2 客户端。它不启动 Isaac Sim、不创建场景，也不拥有机器人控制器；模拟器必须已经运行，并为机器人启用 Keyboard 或 Navigation I/O，使 subscriber 存在。

请使用所选系统 ROS 的 Python，不要使用 env_isaaclab Conda Python：

~~~bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robot carter_1
/usr/bin/python3 algorithm/keyboard/keyboard.py --robots carter_1,go2_1,lite3_1
~~~

多个机器人时按 Q 切换当前 topic。可以使用 --topic 指定自定义 topic；如果没有 --robot、--robots 或 --topic，客户端会短暂等待并发现已发布的 /<robot>/cmd_vel topic。

## 按键

W/S 前进或后退；A/D 让全向底盘左移或右移；R/F 让飞行机器人上升或下降；C/V 左转或右转；K 或 Space 停止；Q 选择下一个机器人；Esc 或 Ctrl+C 退出。退出时，客户端会向每个配置 topic 发布一次零速度。
