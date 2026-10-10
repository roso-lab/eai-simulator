"""Idealized Unitree G1 two-arm box grasp controller.

The state machine is importable without Isaac Lab.  The Isaac adapter is
loaded lazily so offline planners and unit tests do not need simulator imports.
"""

from .skill import G1BoxGraspConfig, G1BoxGraspPhase, G1BoxGraspState


def __getattr__(name):
    if name in {"G1_BOX_GRASP_CFG", "G1BoxGraspControllerCfg", "G1BoxGraspRuntime"}:
        from .controller import G1_BOX_GRASP_CFG, G1BoxGraspControllerCfg, G1BoxGraspRuntime

        return {
            "G1_BOX_GRASP_CFG": G1_BOX_GRASP_CFG,
            "G1BoxGraspControllerCfg": G1BoxGraspControllerCfg,
            "G1BoxGraspRuntime": G1BoxGraspRuntime,
        }[name]
    raise AttributeError(name)

__all__ = [
    "G1BoxGraspConfig",
    "G1BoxGraspPhase",
    "G1BoxGraspState",
    "G1_BOX_GRASP_CFG",
    "G1BoxGraspControllerCfg",
    "G1BoxGraspRuntime",
]
