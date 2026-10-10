"""Isaac Lab adapter for the idealized G1 two-arm box grasp skill."""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.controllers import DifferentialIKController, DifferentialIKControllerCfg
from isaaclab.utils import configclass
from isaaclab.utils.math import matrix_from_quat, quat_inv, subtract_frame_transforms

from EAI.controllers import ControllerCfg

from EAI_assets.g1_box_grasp_skill import G1BoxGraspConfig, G1BoxGraspPhase, G1BoxGraspState


_ARM_JOINT_SUFFIXES = (
    "shoulder_pitch_joint",
    "shoulder_roll_joint",
    "shoulder_yaw_joint",
    "elbow_roll_joint",
    "wrist_roll_joint",
    "wrist_pitch_joint",
    "wrist_yaw_joint",
)
_HAND_BODY_SUFFIXES = (
    "palm_link",
    "rubber_hand",
    "hand_palm_link",
    "wrist_yaw_link",
)


def _find_first(robot: Any, names: tuple[str, ...], *, body: bool = False) -> int | None:
    finder = robot.find_bodies if body else robot.find_joints
    for name in names:
        try:
            ids, _ = finder(name, preserve_order=True)
        except (AttributeError, ValueError, TypeError):
            continue
        if ids:
            return int(ids[0])
    return None


def _resolve_arm_joint_ids(robot: Any, side: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for suffix in _ARM_JOINT_SUFFIXES:
        candidates = (f"{side}_{suffix}",)
        joint_id = _find_first(robot, candidates)
        if joint_id is not None and joint_id not in seen:
            ids.append(joint_id)
            seen.add(joint_id)
    elbow_id = _find_first(
        robot,
        (f"{side}_elbow_pitch_joint", f"{side}_elbow_joint"),
    )
    if elbow_id is not None and elbow_id not in seen:
        # Insert the elbow between shoulder yaw and the wrist chain.
        ids.insert(min(3, len(ids)), elbow_id)
    # Keep up to seven canonical arm joints and do not include finger joints,
    # even when the USD has Inspire hands.  The bundled G1 USD resolves five
    # arm joints (three shoulder + two elbow joints).
    return ids[:7]


def _resolve_ee_body_id(robot: Any, side: str) -> int | None:
    return _find_first(robot, tuple(f"{side}_{suffix}" for suffix in _HAND_BODY_SUFFIXES), body=True)


def _jacobian_row(robot: Any, body_id: int, jacobian: torch.Tensor) -> int:
    n_bodies = int(getattr(robot, "num_bodies", jacobian.shape[1]))
    return body_id if jacobian.shape[1] == n_bodies else max(0, body_id - 1)


class G1BoxGraspRuntime:
    """Per-environment runtime state and low-level Jacobian IK."""

    def __init__(self, config: G1BoxGraspConfig) -> None:
        self.state = G1BoxGraspState(config)
        self._joint_ids: dict[tuple[int, str], list[int]] = {}
        self._ee_ids: dict[tuple[int, str], int | None] = {}
        self._home_targets: dict[tuple[int, str], torch.Tensor] = {}
        self._ik: dict[tuple[str, int, str], DifferentialIKController] = {}
        self.last_error: str | None = None
        self._release_pose_written = False
        self._grasp_wait_elapsed_s = 0.0
        self._grasp_timeout_reported = False
        self.last_ee_positions: dict[str, tuple[float, float, float]] = {}
        self.last_target_distances: dict[str, float] = {}
        self.last_joint_positions: dict[str, list[float]] = {}
        # Composite skills can pause the timed carry phase while locomotion is
        # active and translate all carry/place targets into the robot's new
        # world frame.  Stationary box-grasp behavior keeps both defaults.
        self.freeze_carry = False
        self.world_offset = (0.0, 0.0, 0.0)
        self.hold_box_before_grasp = True
        self.write_release_pose = True
        self.hold_arm_joints = False
        self._held_arm_targets: dict[tuple[int, str], torch.Tensor] = {}

    def start(self) -> None:
        self.state.start()

    def reset(self) -> None:
        self.state.reset()
        self.last_error = None
        self._release_pose_written = False
        self._grasp_wait_elapsed_s = 0.0
        self._grasp_timeout_reported = False
        self.last_ee_positions.clear()
        self.last_target_distances.clear()
        self.last_joint_positions.clear()
        self.freeze_carry = False
        self.world_offset = (0.0, 0.0, 0.0)
        self.hold_arm_joints = False
        self._held_arm_targets.clear()

    def set_composite_motion(self, *, freeze_carry: bool, world_offset: tuple[float, float, float]) -> None:
        self.freeze_carry = bool(freeze_carry)
        self.world_offset = tuple(float(item) for item in world_offset)

    def _world_point(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        return tuple(float(value) + float(offset) for value, offset in zip(point, self.world_offset))

    def _world_targets(
        self, targets: dict[str, tuple[float, float, float]] | None
    ) -> dict[str, tuple[float, float, float]] | None:
        if targets is None:
            return None
        return {side: self._world_point(point) for side, point in targets.items()}

    def _resolve(self, robot: Any, side: str) -> tuple[list[int], int | None]:
        key = (id(robot), side)
        if key not in self._joint_ids:
            self._joint_ids[key] = _resolve_arm_joint_ids(robot, side)
        if key not in self._ee_ids:
            self._ee_ids[key] = _resolve_ee_body_id(robot, side)
        return self._joint_ids[key], self._ee_ids[key]

    def _home(self, robot: Any, side: str, joint_ids: list[int]) -> torch.Tensor:
        key = (id(robot), side)
        if key not in self._home_targets:
            self._home_targets[key] = robot.data.default_joint_pos[:, joint_ids].clone()
        return self._home_targets[key]

    def _apply_home(self, robot: Any, side: str, joint_ids: list[int]) -> None:
        if joint_ids:
            target = self._home(robot, side, joint_ids)
            robot.set_joint_position_target(target, joint_ids=joint_ids)

    def _enforce_joint_safety(self, robot: Any, joint_ids: list[int]) -> None:
        """Clamp a transient physics overshoot before it feeds back into IK.

        Pepper's imported USD contains high-gain implicit arm drives.  A
        position target can therefore overshoot a revolute joint limit for a
        single physics step even though the commanded target was bounded.
        Feeding that wrapped position back into the next Jacobian solve can
        make the solver chase an impossible pose.  Keep the normal actuator
        path intact and only write a corrected full articulation state when an
        observed arm joint is outside its finite soft limit.  For assets that
        publish an unbounded limit, use a conservative +/-3 rad safety envelope
        appropriate for this fixed-base two-arm skill.
        """

        if not joint_ids or not hasattr(robot, "write_joint_state_to_sim"):
            return
        joint_pos = robot.data.joint_pos
        joint_vel = robot.data.joint_vel
        limits = robot.data.soft_joint_pos_limits[:, joint_ids]
        fallback_lower = torch.full_like(limits[..., 0], -3.0)
        fallback_upper = torch.full_like(limits[..., 1], 3.0)
        lower = torch.where(torch.isfinite(limits[..., 0]), limits[..., 0], fallback_lower)
        upper = torch.where(torch.isfinite(limits[..., 1]), limits[..., 1], fallback_upper)
        # The skill never needs a full revolute turn.  This also protects
        # malformed/imported limits that are finite but much wider than the
        # robot's useful arm workspace.
        lower = torch.maximum(lower, fallback_lower)
        upper = torch.minimum(upper, fallback_upper)
        current = joint_pos[:, joint_ids]
        clamped = torch.maximum(torch.minimum(current, upper), lower)
        violated = torch.any(torch.abs(clamped - current) > 1e-5, dim=1)
        if not bool(torch.any(violated).item()):
            return
        corrected_pos = joint_pos.clone()
        corrected_vel = joint_vel.clone()
        corrected_pos[:, joint_ids] = clamped
        corrected_vel[:, joint_ids] = torch.where(
            torch.abs(clamped - current) > 1e-5,
            torch.zeros_like(corrected_vel[:, joint_ids]),
            corrected_vel[:, joint_ids],
        )
        robot.write_joint_state_to_sim(corrected_pos, corrected_vel, None)

    def _apply_target(self, robot: Any, side: str, joint_ids: list[int], body_id: int, target_w: tuple[float, float, float]) -> None:
        if len(joint_ids) < 4:
            raise RuntimeError(f"could not resolve enough {side} G1 arm joints")
        jacobians = robot.root_physx_view.get_jacobians()
        row = _jacobian_row(robot, body_id, jacobians)
        jacobian = jacobians[:, row, :, :]
        root_pos = robot.data.root_pos_w
        root_quat = robot.data.root_quat_w
        ee_pos_w = robot.data.body_pos_w[:, body_id]
        ee_quat_w = robot.data.body_quat_w[:, body_id]
        identity = torch.zeros((robot.num_instances, 4), device=robot.device, dtype=torch.float32)
        identity[:, 0] = 1.0
        target_w_tensor = torch.tensor([target_w], device=robot.device, dtype=torch.float32).expand(robot.num_instances, -1)
        target_b, _ = subtract_frame_transforms(root_pos, root_quat, target_w_tensor, identity)
        ee_pos_b, ee_quat_b = subtract_frame_transforms(root_pos, root_quat, ee_pos_w, ee_quat_w)
        base_rot = matrix_from_quat(quat_inv(root_quat))
        jacobian = jacobian.clone()
        jacobian[:, :3, :] = torch.bmm(base_rot, jacobian[:, :3, :])
        jacobian[:, 3:, :] = torch.bmm(base_rot, jacobian[:, 3:, :])
        offset = 6 if jacobian.shape[-1] == int(getattr(robot, "num_joints", jacobian.shape[-1])) + 6 else 0
        columns = [joint_id + offset for joint_id in joint_ids]
        if any(column >= jacobian.shape[-1] for column in columns):
            raise RuntimeError(f"invalid {side} G1 Jacobian columns")
        jacobian = jacobian[:, :, columns]
        current = robot.data.joint_pos[:, joint_ids]
        # Position-only damped least-squares IK.  The previous use of the
        # full DifferentialIKController fed the current hand orientation back
        # as a pose target; in this symmetric two-arm skill that caused one
        # arm to spin away from the carry point even though the position error
        # was small.  The box skill does not require wrist orientation, so
        # solve only the translational 3xN Jacobian here.
        position_jacobian = jacobian[:, :3, :]
        position_error = target_b - ee_pos_b
        damping = 0.05
        identity_3 = torch.eye(3, device=robot.device, dtype=position_jacobian.dtype).expand(robot.num_instances, -1, -1)
        gram = torch.bmm(position_jacobian, position_jacobian.transpose(1, 2))
        gram = gram + (damping * damping) * identity_3
        dq = torch.bmm(
            position_jacobian.transpose(1, 2),
            torch.linalg.solve(gram, position_error.unsqueeze(-1)),
        ).squeeze(-1)
        desired = current + torch.clamp(dq, -0.06, 0.06)
        limits = robot.data.soft_joint_pos_limits[:, joint_ids]
        desired = torch.maximum(torch.minimum(desired, limits[..., 1]), limits[..., 0])
        robot.set_joint_position_target(desired, joint_ids=joint_ids)

    def _measure_reach(
        self,
        robot: Any,
        side: str,
        body_id: int | None,
        target_w: tuple[float, float, float],
    ) -> float:
        """Measure the current palm-to-target distance in world metres."""

        if body_id is None:
            return float("inf")
        ee = robot.data.body_pos_w[:, body_id, :3]
        target = torch.tensor(target_w, device=robot.device, dtype=ee.dtype).expand_as(ee)
        distance = torch.linalg.vector_norm(ee - target, dim=-1)
        value = float(distance[0].item())
        self.last_ee_positions[side] = tuple(float(item) for item in ee[0].detach().cpu().tolist())
        self.last_target_distances[side] = value
        return value

    def _sync_box(self, env: Any) -> None:
        scene = getattr(env, "scene", None)
        boxes = getattr(scene, "rigid_objects", {}) if scene is not None else {}
        box = boxes.get(self.state.config.cube_prim_name)
        if box is None:
            self.last_error = f"cube rigid object '{self.state.config.cube_prim_name}' was not found"
            return
        if self.state.attached:
            position = self._world_point(self.state.cube_position())
            root_pose = box.data.root_pose_w.clone()
            root_pose[:, :3] = torch.tensor(position, device=box.device, dtype=torch.float32)
            box.write_root_pose_to_sim(root_pose)
            box.write_root_velocity_to_sim(torch.zeros_like(box.data.root_vel_w))
            self._release_pose_written = False
        elif (
            self.hold_box_before_grasp
            and self.state.phase in {G1BoxGraspPhase.IDLE, G1BoxGraspPhase.APPROACH, G1BoxGraspPhase.GRASP}
        ):
            # Keep the dynamic box at its authored pickup pose until the
            # approach/grasp sequence completes.  This is a skill-level
            # staging hold, not a kinematic body: the object remains a
            # dynamic rigid body with mass, gravity and contact properties.
            root_pose = box.data.root_pose_w.clone()
            root_pose[:, :3] = torch.tensor(self.state.config.box_position, device=box.device, dtype=torch.float32)
            box.write_root_pose_to_sim(root_pose)
            box.write_root_velocity_to_sim(torch.zeros_like(box.data.root_vel_w))
        elif (
            self.write_release_pose
            and self.state.phase in {G1BoxGraspPhase.RELEASE, G1BoxGraspPhase.RETURN, G1BoxGraspPhase.DONE}
            and not self._release_pose_written
        ):
            # Teleport to the supported release pose once, then let PhysX
            # own the dynamic body's motion and settle it on the plane.
            root_pose = box.data.root_pose_w.clone()
            root_pose[:, :3] = torch.tensor(
                self._world_point(self.state.config.place_position),
                device=box.device,
                dtype=torch.float32,
            )
            box.write_root_pose_to_sim(root_pose)
            box.write_root_velocity_to_sim(torch.zeros_like(box.data.root_vel_w))
            self._release_pose_written = True

    def step(self, env: Any, robot_name: str, dt_s: float) -> None:
        robot = env.scene.articulations.get(robot_name)
        if robot is None:
            self.last_error = f"G1 articulation '{robot_name}' was not found"
            return
        try:
            current_phase = self.state.phase
            targets = self._world_targets(self.state.hand_targets())
            resolved: dict[str, tuple[list[int], int | None]] = {}
            for side in ("left", "right"):
                joint_ids, body_id = self._resolve(robot, side)
                resolved[side] = (joint_ids, body_id)
                if joint_ids:
                    self.last_joint_positions[side] = [
                        float(item) for item in robot.data.joint_pos[0, joint_ids].detach().cpu().tolist()
                    ]

            # Gate the GRASP -> CARRY transition exactly like the fire-rescue
            # M20 controller gates its FixedJoint on end-effector distance.
            if current_phase is G1BoxGraspPhase.GRASP and targets is not None:
                distances = [
                    self._measure_reach(robot, side, resolved[side][1], targets[side])
                    for side in ("left", "right")
                ]
                ready = all(distance <= self.state.config.grasp_distance_threshold_m for distance in distances)
                self.state.grasp_ready = ready
                if ready:
                    self._grasp_wait_elapsed_s = 0.0
                else:
                    self._grasp_wait_elapsed_s += float(dt_s)
                    if (
                        self._grasp_wait_elapsed_s > self.state.config.grasp_timeout_s
                        and not self._grasp_timeout_reported
                    ):
                        self.last_error = (
                            "G1 box grasp timed out: "
                            f"left={distances[0]:.3f} m, right={distances[1]:.3f} m, "
                            f"threshold={self.state.config.grasp_distance_threshold_m:.3f} m"
                        )
                        self._grasp_timeout_reported = True
            advance_dt = 0.0 if self.freeze_carry and current_phase is G1BoxGraspPhase.CARRY else float(dt_s)
            self.state.advance(advance_dt)
            targets = self._world_targets(self.state.hand_targets())
            for side in ("left", "right"):
                joint_ids, body_id = resolved[side]
                hold_key = (id(robot), side)
                if self.hold_arm_joints and joint_ids:
                    if hold_key not in self._held_arm_targets:
                        self._held_arm_targets[hold_key] = robot.data.joint_pos[:, joint_ids].clone()
                    robot.set_joint_position_target(
                        self._held_arm_targets[hold_key],
                        joint_ids=joint_ids,
                    )
                elif targets is None:
                    self._held_arm_targets.pop(hold_key, None)
                    self._apply_home(robot, side, joint_ids)
                elif body_id is not None:
                    self._held_arm_targets.pop(hold_key, None)
                    self._apply_target(robot, side, joint_ids, body_id, targets[side])
                else:
                    raise RuntimeError(f"could not resolve {side} G1 hand body")
                self._enforce_joint_safety(robot, joint_ids)
            self._sync_box(env)
        except Exception as exc:
            self.last_error = str(exc)


def compute_g1_box_grasp_action(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    del controller_cfg, command, controller_dict
    return torch.zeros((env.num_envs, 1), device=env.device, dtype=torch.float32)


def apply_g1_box_grasp_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    del action
    runtime = controller_dict["runtime"]
    if runtime.state.phase is G1BoxGraspPhase.IDLE and controller_dict.get("auto_start", True):
        runtime.start()
    sim_cfg = getattr(getattr(env, "cfg", None), "sim", None)
    # DirectMARLEnv invokes ``_apply_action`` once per physics substep inside
    # its decimation loop, so advancing by the control-step duration here
    # would make the skill run ``decimation`` times too fast.
    dt = float(getattr(sim_cfg, "dt", 0.005))
    runtime.step(env, robot_name, dt)
    env.g1_box_grasp_state = runtime.state.snapshot()
    if runtime.last_error:
        env.g1_box_grasp_error = runtime.last_error


def start_g1_box_grasp(env: Any, robot_name: str) -> None:
    """Explicitly trigger the skill for a controller-backed G1."""

    runtime = env._controllers_dict[robot_name]["runtime"]
    runtime.start()


def reset_g1_box_grasp(env: Any, robot_name: str) -> None:
    """Reset the skill to idle without changing the authored robot pose."""

    runtime = env._controllers_dict[robot_name]["runtime"]
    runtime.reset()


@configclass
class G1BoxGraspControllerCfg(ControllerCfg):
    """Primary controller that exposes the G1 box skill as a bottom-level action."""

    skill_config: G1BoxGraspConfig = G1BoxGraspConfig()
    auto_start: bool = True

    def load(self, robot_name: str, task_name: str, device: str, env: Any) -> Dict[str, Any]:
        del task_name, device, env
        return {
            "name": robot_name,
            "runtime": G1BoxGraspRuntime(G1BoxGraspConfig.from_environment(self.skill_config)),
            "auto_start": bool(self.auto_start),
        }

    def compute_action(
        self, env: Any, robot_name: str, observations: Optional[torch.Tensor], controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        del robot_name, observations, controller_dict
        return torch.zeros((env.num_envs, 1), device=env.device, dtype=torch.float32)

    def compute_action_from_command(
        self, env: Any, robot_name: str, command: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        return compute_g1_box_grasp_action(self, env, robot_name, command, controller_dict)

    def reset(
        self, env: Any, robot_name: str, controller_dict: Dict[str, Any], env_ids: Any
    ) -> None:
        del env, robot_name, env_ids
        runtime = controller_dict.get("runtime")
        if runtime is not None:
            runtime.reset()

    def apply_action(
        self, env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]
    ) -> None:
        apply_g1_box_grasp_action(env, robot_name, action, controller_dict)


G1_BOX_GRASP_CFG = G1BoxGraspControllerCfg(
    robot_type="G1",
    compute_action_from_command_func=compute_g1_box_grasp_action,
    apply_action_func=apply_g1_box_grasp_action,
)


__all__ = [
    "G1_BOX_GRASP_CFG",
    "G1BoxGraspConfig",
    "G1BoxGraspControllerCfg",
    "G1BoxGraspRuntime",
    "reset_g1_box_grasp",
    "start_g1_box_grasp",
]
