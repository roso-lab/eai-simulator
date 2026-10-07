# BT capabilities → EAI adapter

The upstream `ros_bt_py` v1.1.0 capability release provides capability
interfaces, remote capability slots, utility evaluation and auction managers.
This ROS-independent bridge keeps those semantics in typed Python records so
an EAI session can own identity, locks, skill execution and milestone evidence.
It is an adapted condition until the ROS1 mission tree and shared object store
are connected to the EAI interface catalog.

For the Fire Rescue benchmark use
`algorithm.system.coordination.bt_capabilities.fire_rescue_policy:assign`. It is a stateless
module callback, so each assignment/replanning request is a fresh capability
auction over the current active robots. Physical execution, failure evidence,
and replay remain owned by the EAI Fire Rescue runner.
