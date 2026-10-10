"""Fail-closed physical evidence evaluator for Pepper box transport."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from algorithm.skills.contracts import SkillPlan, SkillRequest

from .executor import (
    PepperTransportExecutionError,
    _validated_payload,
    pepper_controller_state,
)
from .provider import (
    CARRY_MODE,
    DEFAULT_HORIZON_TICKS,
    SKILL_ID,
    validate_pepper_transport_request,
)


EXPECTED_BOX_MASS_KG = 8.0


def _plain(value: Any) -> Any:
    current = value
    for method_name in ("detach", "cpu"):
        method = getattr(current, method_name, None)
        if callable(method):
            current = method()
    tolist = getattr(current, "tolist", None)
    return tolist() if callable(tolist) else current


def _single_vector(value: Any, length: int) -> tuple[float, ...] | None:
    raw = _plain(value)
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        raw = list(raw)
        if len(raw) == 1 and isinstance(raw[0], Sequence):
            raw = list(raw[0])
        if len(raw) < length:
            return None
        try:
            result = tuple(float(item) for item in raw[:length])
        except (TypeError, ValueError):
            return None
        return result if all(math.isfinite(item) for item in result) else None
    return None


def _indexed_single_vector(
    value: Any,
    indices: Sequence[int],
) -> tuple[float, ...] | None:
    raw = _plain(value)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    raw = list(raw)
    if len(raw) == 1 and isinstance(raw[0], Sequence):
        raw = list(raw[0])
    try:
        result = tuple(float(raw[index]) for index in indices)
    except (IndexError, TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result) else None


def _snapshot(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    snapshot = getattr(value, "snapshot", None)
    if callable(snapshot):
        result = snapshot()
        return dict(result) if isinstance(result, Mapping) else None
    return None


def _get_named(container: Any, name: str) -> Any:
    if isinstance(container, Mapping):
        return container.get(name)
    return getattr(container, name, None)


def _binding_payload(
    request: SkillRequest | None,
    plan: SkillPlan | None,
) -> tuple[str, str, dict[str, Any]]:
    if request is None and plan is None:
        raise ValueError("a SkillRequest or SkillPlan is required for evidence binding")
    request_payload: dict[str, Any] | None = None
    if request is not None:
        validate_pepper_transport_request(request)
        request_payload = dict(request.parameters)
        robot_id = request.robot_id
        request_id = request.request_id
    else:
        robot_id = plan.robot_id  # type: ignore[union-attr]
        request_id = plan.request_id  # type: ignore[union-attr]
    plan_payload: dict[str, Any] | None = None
    if plan is not None:
        plan_payload = _validated_payload(plan)
        if request is not None:
            comparable = dict(request_payload or {})
            comparable.setdefault("horizon_ticks", DEFAULT_HORIZON_TICKS)
            if (
                plan.request_id != request.request_id
                or plan.skill_id != request.skill_id
                or plan.robot_id != request.robot_id
                or comparable != plan_payload
            ):
                raise ValueError("request and plan bindings do not match")
    return request_id, robot_id, plan_payload or request_payload or {}


def _result(
    verdict: str,
    request_id: str | None,
    robot_id: str | None,
    reasons: list[str],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evidence": evidence,
        "physical_success": True if verdict == "success" else False if verdict == "failure" else None,
        "reason_codes": sorted(set(reasons)),
        "request_id": request_id,
        "robot_id": robot_id,
        "skill_id": SKILL_ID,
        "verdict": verdict,
    }


def _public_transport_state(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    displacement = _single_vector(state.get("displacement"), 3)
    destination = _single_vector(state.get("destination_offset"), 3)
    completed = state.get("completed_cycles")
    return {
        "completed_cycles": completed if type(completed) is int else None,
        "destination_offset": list(destination) if destination is not None else None,
        "displacement": list(displacement) if displacement is not None else None,
        "phase": state.get("phase") if isinstance(state.get("phase"), str) else None,
    }


def _public_grasp_state(state: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    completed = state.get("completed_cycles")
    attached = state.get("attached")
    return {
        "attached": attached if type(attached) is bool else None,
        "completed_cycles": completed if type(completed) is int else None,
        "phase": state.get("phase") if isinstance(state.get("phase"), str) else None,
    }


class PepperTransportEvidenceEvaluator:
    """Evaluate terminal scene/controller evidence without trusting plan status."""

    def __init__(
        self,
        *,
        max_terminal_linear_speed_m_s: float = 0.05,
        max_terminal_angular_speed_rad_s: float = 0.10,
        max_position_error_m: float = 0.03,
        min_movement_m: float = 0.20,
        mass_tolerance_kg: float = 0.05,
    ) -> None:
        limits = (
            max_terminal_linear_speed_m_s,
            max_terminal_angular_speed_rad_s,
            max_position_error_m,
            min_movement_m,
            mass_tolerance_kg,
        )
        if any(not math.isfinite(float(value)) or float(value) < 0.0 for value in limits):
            raise ValueError("evaluator limits must be finite and non-negative")
        self.max_terminal_linear_speed_m_s = float(max_terminal_linear_speed_m_s)
        self.max_terminal_angular_speed_rad_s = float(max_terminal_angular_speed_rad_s)
        self.max_position_error_m = float(max_position_error_m)
        self.min_movement_m = float(min_movement_m)
        self.mass_tolerance_kg = float(mass_tolerance_kg)

    def evaluate(
        self,
        env: Any,
        *,
        request: SkillRequest | None = None,
        plan: SkillPlan | None = None,
    ) -> dict[str, Any]:
        request_id: str | None = (
            request.request_id if request is not None else plan.request_id if plan is not None else None
        )
        robot_id: str | None = (
            request.robot_id if request is not None else plan.robot_id if plan is not None else None
        )
        try:
            request_id, robot_id, parameters = _binding_payload(request, plan)
        except (ValueError, PepperTransportExecutionError) as exc:
            return _result(
                "failure",
                request_id,
                robot_id,
                ["invalid_request_or_plan_binding"],
                {"binding_error": str(exc)},
            )

        try:
            controller = pepper_controller_state(env, robot_id)
        except PepperTransportExecutionError as exc:
            return _result(
                "failure",
                request_id,
                robot_id,
                ["pepper_controller_identity_mismatch"],
                {"controller_error": str(exc)},
            )

        missing: list[str] = []
        invalid: list[str] = []
        blockers: list[str] = []
        evidence: dict[str, Any] = {
            "binding": {
                "carry_mode": parameters.get("carry_mode"),
                "destination_ref": parameters.get("destination_ref"),
                "object_ref": parameters.get("object_ref"),
                "source_ref": parameters.get("source_ref"),
            }
        }

        runtime = controller.get("runtime")
        transport = controller.get("transport")
        runtime_state = getattr(runtime, "state", None)
        grasp_cfg = getattr(runtime_state, "config", None)
        transport_cfg = getattr(transport, "config", None)
        expected_refs = {
            "object_ref": getattr(grasp_cfg, "cube_prim_name", None),
            "source_ref": getattr(transport_cfg, "source_table_name", None),
            "destination_ref": getattr(transport_cfg, "destination_table_name", None),
        }
        if any(value is None for value in expected_refs.values()):
            missing.append("controller_resource_identity_unavailable")
        elif any(parameters.get(name) != value for name, value in expected_refs.items()):
            invalid.append("request_resources_do_not_match_controller")
        if parameters.get("carry_mode") != CARRY_MODE:
            invalid.append("carry_mode_mismatch")
        evidence["controller_resources"] = expected_refs

        env_transport = _snapshot(getattr(env, "box_transport_state", None))
        live_transport = _snapshot(transport)
        transport_state = env_transport or live_transport
        if transport_state is None:
            missing.append("transport_state_unavailable")
        elif env_transport is not None and live_transport is not None:
            for name in ("phase", "completed_cycles", "displacement", "destination_offset"):
                if env_transport.get(name) != live_transport.get(name):
                    invalid.append("transport_state_snapshot_mismatch")
                    break
        evidence["transport_state"] = _public_transport_state(transport_state)

        env_grasp = _snapshot(getattr(env, "pepper_box_grasp_state", None))
        live_grasp = _snapshot(runtime_state)
        grasp_state = env_grasp or live_grasp
        if grasp_state is None:
            missing.append("grasp_state_unavailable")
        elif env_grasp is not None and live_grasp is not None:
            for name in ("phase", "attached", "completed_cycles"):
                if env_grasp.get(name) != live_grasp.get(name):
                    invalid.append("grasp_state_snapshot_mismatch")
                    break
        evidence["grasp_state"] = _public_grasp_state(grasp_state)

        terminal_done = bool(
            transport_state
            and transport_state.get("phase") == "done"
            and type(transport_state.get("completed_cycles")) is int
            and transport_state["completed_cycles"] >= 1
        )
        grasp_done = bool(
            grasp_state
            and grasp_state.get("phase") == "done"
            and grasp_state.get("attached") is False
            and type(grasp_state.get("completed_cycles")) is int
            and grasp_state["completed_cycles"] >= 1
        )
        if transport_state is not None and not terminal_done:
            blockers.append("transport_not_done")
        if grasp_state is not None and not grasp_done:
            blockers.append("grasp_not_done_or_not_released")

        compute_calls = controller.get("locomotion_compute_calls")
        apply_calls = controller.get("locomotion_apply_calls")
        if type(compute_calls) is not int or type(apply_calls) is not int:
            missing.append("controller_call_evidence_unavailable")
        elif compute_calls <= 0 or apply_calls <= 0 or apply_calls < compute_calls:
            blockers.append("controller_calls_not_applied")
        command = _single_vector(controller.get("internal_velocity_command"), 3)
        if command is None:
            missing.append("controller_command_evidence_unavailable")
        evidence["controller_evidence"] = {
            "internal_velocity_command": list(command) if command is not None else None,
            "locomotion_apply_calls": apply_calls if type(apply_calls) is int else None,
            "locomotion_compute_calls": compute_calls if type(compute_calls) is int else None,
        }

        displacement = _single_vector(
            transport_state.get("displacement") if transport_state else None, 3
        )
        initial_pose = _single_vector(getattr(transport, "initial_pose", None), 3)
        last_pose = _single_vector(getattr(transport, "last_pose", None), 3)
        if displacement is None or initial_pose is None or last_pose is None:
            missing.append("measured_transport_movement_unavailable")
            movement_m = None
        else:
            measured = (
                last_pose[0] - initial_pose[0],
                last_pose[1] - initial_pose[1],
                0.0,
            )
            if any(abs(a - b) > 1.0e-6 for a, b in zip(displacement, measured)):
                invalid.append("transport_displacement_not_bound_to_measured_poses")
            movement_m = math.hypot(displacement[0], displacement[1])
            if movement_m < self.min_movement_m:
                blockers.append("insufficient_measured_transport_movement")
        evidence["movement"] = {
            "displacement": list(displacement) if displacement is not None else None,
            "initial_pose": list(initial_pose) if initial_pose is not None else None,
            "last_pose": list(last_pose) if last_pose is not None else None,
            "planar_distance_m": movement_m,
        }

        scene = getattr(env, "scene", None)
        articulations = getattr(scene, "articulations", None)
        pepper = _get_named(articulations, robot_id)
        find_joints = getattr(pepper, "find_joints", None)
        base_velocity: tuple[float, ...] | None = None
        if callable(find_joints):
            try:
                base_ids, _base_names = find_joints(
                    ["XDisp", "YDisp", "ZRot"], preserve_order=True
                )
                base_ids = tuple(int(item) for item in base_ids)
            except (AttributeError, TypeError, ValueError):
                base_ids = ()
            if len(base_ids) == 3 and len(set(base_ids)) == 3:
                base_velocity = _indexed_single_vector(
                    getattr(getattr(pepper, "data", None), "joint_vel", None),
                    base_ids,
                )
        if base_velocity is None:
            missing.append("pepper_base_velocity_unavailable")
            pepper_linear_speed = None
            pepper_angular_speed = None
        else:
            pepper_linear_speed = math.hypot(base_velocity[0], base_velocity[1])
            pepper_angular_speed = abs(base_velocity[2])
            if pepper_linear_speed > self.max_terminal_linear_speed_m_s:
                blockers.append("pepper_terminal_linear_speed_too_high")
            if pepper_angular_speed > self.max_terminal_angular_speed_rad_s:
                blockers.append("pepper_terminal_angular_speed_too_high")
        evidence["pepper_base"] = {
            "joint_velocity": list(base_velocity) if base_velocity is not None else None,
            "linear_speed_m_s": pepper_linear_speed,
            "yaw_speed_rad_s": pepper_angular_speed,
        }

        rigid_objects = getattr(scene, "rigid_objects", None)
        object_ref = parameters.get("object_ref")
        box = _get_named(rigid_objects, object_ref) if isinstance(object_ref, str) else None
        root_state = _single_vector(
            getattr(getattr(box, "data", None), "root_state_w", None), 13
        )
        if box is None:
            missing.append("scene_box_unavailable")
        elif root_state is None:
            invalid.append("box_root_state_not_finite_13d")

        mass_kg: float | None = None
        root_view = getattr(box, "root_physx_view", None)
        get_masses = getattr(root_view, "get_masses", None)
        if callable(get_masses):
            mass_vector = _single_vector(get_masses(), 1)
            mass_kg = mass_vector[0] if mass_vector is not None else None
        if mass_kg is None:
            missing.append("box_mass_measurement_unavailable")
        elif abs(mass_kg - EXPECTED_BOX_MASS_KG) > self.mass_tolerance_kg:
            blockers.append("box_mass_mismatch")

        table_size = _single_vector(getattr(transport_cfg, "table_size", None), 3)
        box_size = _single_vector(getattr(grasp_cfg, "box_size", None), 3)
        destination_position = _single_vector(
            getattr(transport_cfg, "destination_table_position", None), 3
        )
        table_top_z = getattr(transport_cfg, "table_top_z", None)
        try:
            table_top_z = float(table_top_z)
        except (TypeError, ValueError):
            table_top_z = None
        if table_top_z is not None and not math.isfinite(table_top_z):
            table_top_z = None

        scene_cfg = getattr(getattr(env, "cfg", None), "scene", None)
        destination_ref = parameters.get("destination_ref")
        source_ref = parameters.get("source_ref")
        destination_asset = _get_named(scene_cfg, destination_ref) if isinstance(destination_ref, str) else None
        source_asset = _get_named(scene_cfg, source_ref) if isinstance(source_ref, str) else None
        if destination_asset is None or source_asset is None:
            missing.append("source_or_destination_table_not_in_scene")
        else:
            configured_size = _single_vector(
                getattr(getattr(destination_asset, "spawn", None), "size", None), 3
            )
            configured_position = _single_vector(
                getattr(getattr(destination_asset, "init_state", None), "pos", None), 3
            )
            if configured_size != table_size or configured_position != destination_position:
                invalid.append("destination_table_scene_config_mismatch")

        support_error: list[float] | None = None
        linear_speed: float | None = None
        angular_speed: float | None = None
        if (
            root_state is not None
            and table_size is not None
            and box_size is not None
            and destination_position is not None
            and table_top_z is not None
        ):
            expected_z = table_top_z + box_size[2] * 0.5
            support_error = [
                root_state[0] - destination_position[0],
                root_state[1] - destination_position[1],
                root_state[2] - expected_z,
            ]
            x_limit = max(0.0, (table_size[0] - box_size[0]) * 0.5)
            y_limit = max(0.0, (table_size[1] - box_size[1]) * 0.5)
            if (
                abs(support_error[0]) > x_limit + self.max_position_error_m
                or abs(support_error[1]) > y_limit + self.max_position_error_m
                or abs(support_error[2]) > self.max_position_error_m
            ):
                blockers.append("box_not_released_on_destination_table")
            source_position = _single_vector(getattr(grasp_cfg, "box_position", None), 3)
            if source_position is None:
                missing.append("source_box_position_unavailable")
            elif math.dist(root_state[:3], source_position) < self.min_movement_m:
                blockers.append("box_has_no_scene_measured_transport")
            linear_speed = math.sqrt(sum(value * value for value in root_state[7:10]))
            angular_speed = math.sqrt(sum(value * value for value in root_state[10:13]))
            if linear_speed > self.max_terminal_linear_speed_m_s:
                blockers.append("terminal_linear_speed_too_high")
            if angular_speed > self.max_terminal_angular_speed_rad_s:
                blockers.append("terminal_angular_speed_too_high")
        else:
            missing.append("destination_support_geometry_unavailable")
        evidence["scene_box"] = {
            "angular_speed_rad_s": angular_speed,
            "destination_support_error_m": support_error,
            "linear_speed_m_s": linear_speed,
            "mass_kg": mass_kg,
            "root_state_w": list(root_state) if root_state is not None else None,
        }

        if invalid:
            return _result("failure", request_id, robot_id, invalid, evidence)
        if missing:
            return _result("unknown", request_id, robot_id, missing + blockers, evidence)
        if blockers:
            verdict = "failure" if terminal_done else "unknown"
            return _result(verdict, request_id, robot_id, blockers, evidence)
        return _result("success", request_id, robot_id, [], evidence)


PepperTransportEvaluator = PepperTransportEvidenceEvaluator


__all__ = [
    "EXPECTED_BOX_MASS_KG",
    "PepperTransportEvaluator",
    "PepperTransportEvidenceEvaluator",
]
