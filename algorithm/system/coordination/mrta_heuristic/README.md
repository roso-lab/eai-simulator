# MRTA heuristic planner → EAI

This adapter follows the public ICRA 2024 / IEEE T-RO 2025 heuristic MRTA
repository at `c69509e8d2370f55824519dda0a13252e1817415`. It mirrors the
official Agent/Task allocation and `planRepair` delay-repair boundary in pure
Python so Isaac Lab does not need MATLAB or Gurobi. It is reported as an
interface-equivalent adapter, not a zero-modification MATLAB reproduction.

The planner emits assignments only. EAI owns navigation, Z1 actions, physical
milestones, execution feedback, replay and success verification. Coalition or
fractional Fire Rescue tasks are explicitly `N/A`.
