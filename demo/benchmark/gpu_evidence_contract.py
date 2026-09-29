"""Formal geometry evidence is a synchronized CUDA readback, or it is unknown.

This matches the City seam in commit 96eb0fd: scene-query support, Fabric,
and a physics step that has already advanced. A suppressed, stale, drifting,
or unbound readback fails closed. CPU remains available for debugging and for
exploration when no GPU is free; it is not formal evidence.
"""

from __future__ import annotations

from typing import Any


def admit_synchronized_gpu_readback(*, device: str, scene_queries_enabled: bool,
                                    fabric_enabled: bool, physics_step: int,
                                    bound_physics_step: int,
                                    readback_suppressed: bool | None) -> tuple[bool, dict[str, Any]]:
    """Admit one geometry query. Admission is not a clearance measurement."""
    context: dict[str, Any] = {
        "device": str(device),
        "scene_queries_enabled": scene_queries_enabled is True,
        "fabric_enabled": fabric_enabled is True,
        "physics_step": physics_step,
        "bound_physics_step": bound_physics_step,
        "readback_suppressed": readback_suppressed,
        "formal_evidence": False,
        "clearance_status": "unknown",
    }
    if not str(device).startswith("cuda"):
        context["reason"] = "cpu_readback_is_not_formal_evidence"
        context["evidence_role"] = "exploratory"
        return False, context
    context["evidence_role"] = "formal_gpu"
    if scene_queries_enabled is not True:
        context["reason"] = "scene_query_support_disabled"
        return False, context
    if fabric_enabled is not True:
        context["reason"] = "fabric_disabled"
        return False, context
    if type(physics_step) is not int or physics_step <= 0:
        context["reason"] = "physics_step_not_advanced"
        return False, context
    if type(bound_physics_step) is not int or bound_physics_step != physics_step:
        context["reason"] = "readback_not_bound_to_current_step"
        return False, context
    if readback_suppressed is None:
        context["reason"] = "readback_suppression_unknown"
        return False, context
    if readback_suppressed is not False:
        context["reason"] = "readback_suppressed"
        return False, context
    context["reason"] = None
    context["formal_evidence"] = True
    context["synchronized"] = True
    context["synchronization_source"] = "current_physics_step_fabric_scene_query"
    return True, context


def admit_live_session(session: Any) -> tuple[bool, dict[str, Any]]:
    """Read the open session once and fail closed unless that step is current."""
    import omni.physx

    device = str(getattr(session, "device", ""))
    sim_cfg = getattr(getattr(session, "env_cfg", None), "sim", None)
    scene_queries = getattr(sim_cfg, "enable_scene_query_support", None) is True
    fabric = getattr(sim_cfg, "use_fabric", None) is True
    try:
        physics_step = int(session.base_env.sim.current_time_step_index)
    except (AttributeError, TypeError, ValueError):
        physics_step = -1
    try:
        suppressed = omni.physx.get_physx_interface().is_readback_suppressed()
    except (AttributeError, RuntimeError, TypeError):
        suppressed = None
    if suppressed is not None and type(suppressed) is not bool:
        suppressed = None
    try:
        bound_step = int(session.base_env.sim.current_time_step_index)
    except (AttributeError, TypeError, ValueError):
        bound_step = -1
    if bound_step != physics_step:
        admitted, context = admit_synchronized_gpu_readback(
            device=device, scene_queries_enabled=scene_queries, fabric_enabled=fabric,
            physics_step=physics_step, bound_physics_step=physics_step,
            readback_suppressed=suppressed if type(suppressed) is bool else None)
        context["reason"] = "physics_step_drifted"
        context["formal_evidence"] = False
        context["bound_physics_step"] = bound_step
        return False, context
    return admit_synchronized_gpu_readback(
        device=device, scene_queries_enabled=scene_queries, fabric_enabled=fabric,
        physics_step=physics_step, bound_physics_step=bound_step,
        readback_suppressed=suppressed if type(suppressed) is bool else None)
