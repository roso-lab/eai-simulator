# External I/O skill providers

This namespace is the upload target for ROS/Nav2 and operator-command
transport providers. The concrete providers are intentionally absent from this
branch and will be supplied by the dedicated Skill developer. They produce
transport commands; EAI's cmd_vel bridge and controller remain the execution
owner.
