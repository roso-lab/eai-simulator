# EAI executors

`PickPlaceExecutor` is the public local manipulation executor. It accepts one world-coordinate pick or place request, drives the host-owned physics callback, and returns only `True` or `False`. Register a host-created `ArmPickPlaceAdapter`, `DronePickPlaceAdapter`, `PepperPickPlaceAdapter`, `G1PickPlaceAdapter`, or another implementation of `PickPlaceAdapter`.

The executor does not choose navigation or advance a simulator outside the injected callback. It retains object ownership between successful pick and place and keeps uncertain physical state leased for host recovery.

`cooperative.py` remains available for independent navigation and other caller-owned shared-tick tasks. The low-level arm, aerial bridge, Pepper controller and G1 planner modules remain reusable by their adapters.
