# EAI executors

`PickPlaceExecutor` is the public local manipulation executor. It accepts one fixed-action request containing a robot, operation, logical `object_ref`, and timeout; navigation is caller-owned. It drives the host-owned physics callback and returns only `True` or `False`. Built-in adapters live under `task.executors.pick_place_adapters`; their robot action controllers live under `task.controllers`. They execute motion phases without payload attachment or payload pose writes.

The executor does not choose navigation or advance a simulator outside the injected callback. It retains object ownership between successful pick and place and keeps uncertain physical state leased for host recovery.

`cooperative.py` remains available for independent navigation and other caller-owned shared-tick tasks. The low-level arm, Pepper and G1 controllers remain reusable by their adapters; aerial PhysX pose reads live under `source/EAI/EAI/physics/aerial_pose_reader.py`.
