"""Pure, confirmation-driven aerial transport state machine."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import math
from typing import Mapping

from ..contracts import SkillCommand, SkillContractError, SkillKind, SkillPlan, SkillRequest
from ..simulator_schema import AERIAL_PICK_PLACE_SKILL_ID
from ..navigation.aerial_navigation import (AerialPose, AerialNodeRouteTracker, digest,
    invalid_request_plan, normalize_request, select_aerial_node_route, validate_observation)
from .geometry import RigidPose, number, vector
from .trajectory import VerticalProfile

Z_CONNECTION_TOLERANCE_M = .01
XY_ALIGNMENT_TOLERANCE_M = .01
# Declared capability: controller robot_type identifiers of the only robots
# this skill supports (catalog keys iris and pegasus). The executor enforces
# this set at dispatch, before binding any physical state; cf2x and every
# other robot type are rejected there.
SUPPORTED_ROBOT_TYPES = frozenset({"PegasusIris", "PegasusX4"})
DEFAULTS = {"carry_mode": "fixed_joint", "frame": "world", "vertical_speed_m_s": .1,
            "vertical_acceleration_m_s2": .5, "phase_timeout_s": 60., "intent": "start"}
MUTATIONS = {"supporting": "create_support", "disabling_collision": "disable_collision",
             "attaching": "attach", "removing_support": "remove_support", "releasing": "release"}
NEXT = {"supporting": "disabling_collision", "disabling_collision": "descending_to_pick",
        "attaching": "removing_support", "removing_support": "ascending_from_pick",
        "releasing": "ascending_from_place"}


def normalize_aerial_pick_place_request(value) -> SkillRequest:
    request = normalize_request(value, AERIAL_PICK_PLACE_SKILL_ID)
    return replace(request, parameters={**DEFAULTS, **request.parameters})


@dataclass
class _Task:
    request: SkillRequest
    phase: str = "pending"
    status: str = "pending"
    reason: str = "awaiting_start"
    attached: bool = False
    terminal: bool = False
    held: bool = False
    last_time: float = 0.
    phase_elapsed: float = 0.
    total_elapsed: float = 0.
    phase_serial: int = 0
    tracker: AerialNodeRouteTracker | None = None
    hover_z: float | None = None
    goal: tuple[float, float, float] | None = None
    profile: VerticalProfile | None = None
    profile_elapsed: float = 0.
    vertical_xy: tuple | None = None
    vertical_target: float | None = None
    route_graph_digest: str | None = None
    commands: tuple = ()
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AerialPickPlaceResponse:
    state: Mapping
    plan: SkillPlan
    private_evidence: Mapping

    def to_mapping(self):
        return {"transport_state": deepcopy(dict(self.state)), "skill_plan": self.plan.to_mapping()}


class AerialPickPlaceProvider:
    skill_id = AERIAL_PICK_PLACE_SKILL_ID
    kind = SkillKind.TRANSPORT
    provider_id = "aerial_pick_place.provider"
    provider_version = "1.0"

    def __init__(self, *, connection_tolerance_m=Z_CONNECTION_TOLERANCE_M):
        self.connection_tolerance_m = number(connection_tolerance_m, "connection tolerance")
        if not 0 < self.connection_tolerance_m <= Z_CONNECTION_TOLERANCE_M:
            raise SkillContractError("connection tolerance must be positive and at most 1 cm")
        self._tasks = {}

    @property
    def active_task_ids(self):
        return tuple(sorted(key for key, task in self._tasks.items() if not task.terminal))

    def dispatch(self, value, *, observation=None):
        r = normalize_aerial_pick_place_request(value)
        validate_observation(r, observation, motion=False)
        pose, _, _, now = self._measure(observation)
        key = r.task_id or r.request_id
        if key in self._tasks:
            raise SkillContractError("aerial transport task already exists")
        task = _Task(r, last_time=now, goal=pose.position_m)
        task.evidence["initial_request_digest"] = digest(r.to_mapping())
        self._tasks[key] = task
        self._compile(task)
        return self.response(key)

    @staticmethod
    def _measure(observation):
        pose = observation.get("drone_pose")
        if not isinstance(pose, RigidPose):
            raise SkillContractError("measured drone RigidPose is required")
        drone_anchor = vector(observation.get("drone_anchor_world_m"))
        object_anchor = vector(observation.get("object_anchor_world_m"))
        now = number(observation.get("simulation_time_s"), "simulation time")
        return pose, drone_anchor, object_anchor, now

    @staticmethod
    def _identity(r):
        mapping = r.to_mapping()
        for key in ("request_id", "simulator_snapshot"):
            mapping.pop(key, None)
        for key in ("intent", "world_state_seq"):
            mapping["parameters"].pop(key, None)
        return mapping

    def validate_identity(self, value):
        r = normalize_aerial_pick_place_request(value)
        key = r.task_id or r.request_id
        if key not in self._tasks or self._tasks[key].terminal:
            raise SkillContractError("aerial transport task is not active")
        if self._identity(r) != self._identity(self._tasks[key].request):
            raise SkillContractError("aerial transport task identity and parameters are immutable")
        return r

    def control(self, value, *, observation=None):
        r = self.validate_identity(value)
        key = r.task_id or r.request_id
        task = self._tasks[key]
        intent = r.parameters["intent"]
        validate_observation(r, observation, motion=intent not in {"hold", "stop"})
        pose, drone_anchor, object_anchor, now = self._measure(observation)
        if now < task.last_time:
            raise SkillContractError("simulation time went backwards")
        ack = observation.get("confirmation")
        if ack is not None:
            expected = self._confirmation_id(task)
            if not isinstance(ack, Mapping) or task.phase not in MUTATIONS or ack.get("confirmation_id") != expected or ack.get("operation") != MUTATIONS[task.phase]:
                raise SkillContractError("unexpected mutation confirmation")

        # Charge the interval to the previous phase before a confirmation can
        # reset its clock. A held interval is paused, including the resume tick.
        dt = 0. if task.held else now-task.last_time
        task.last_time, task.request = now, r
        task.phase_elapsed += dt
        task.total_elapsed += dt
        duration = min((value["duration_s"] for value in (r.timing, r.budget) if value and "duration_s" in value), default=math.inf)
        expired = task.phase_elapsed > r.parameters["phase_timeout_s"] or task.total_elapsed > duration
        task.evidence["observation_digest"] = digest({"root": pose.position_m, "orientation_wxyz": pose.orientation_wxyz,
            "drone_anchor": drone_anchor, "object_anchor": object_anchor, "time_s": now,
            "world_state_seq": r.parameters["world_state_seq"]})

        # Apply confirmations before stop/timeout/hold: a preceding attach may
        # already have succeeded and must never be rolled back as free.
        if ack is not None:
            if ack.get("applied") is not True:
                return self.fail(key, RuntimeError("mutation rejected"))
            if task.phase == "attaching":
                task.attached = True
            elif task.phase == "releasing":
                task.attached = False
            task.evidence.setdefault("confirmed_operations", []).append(MUTATIONS[task.phase])
            self._phase(task, NEXT[task.phase])
            dt = 0.
        if intent == "stop":
            task.goal = pose.position_m
            return self._terminate(key, "cancelled", "stopped")
        if expired:
            task.goal = pose.position_m
            return self.fail(key, TimeoutError(), reason_code="timeout")
        if intent == "hold":
            task.goal, task.held = pose.position_m, True
            task.profile = None
            task.reason = "holding"
            self._compile(task, mutations=False)
            return self.response(key)
        task.held = False
        if task.phase == "pending":
            if intent == "continue":
                task.goal = pose.position_m
                self._compile(task)
                return self.response(key)
            self._phase(task, "navigating_to_pick")
        if intent == "replan" and task.phase not in {"navigating_to_pick", "navigating_to_place"}:
            raise SkillContractError("replan is only supported during navigation")
        try:
            self._advance(task, observation, pose, drone_anchor, object_anchor, dt, replan=intent == "replan")
        except (SkillContractError, KeyError, TypeError) as exc:
            task.goal = pose.position_m
            return self.fail(key, exc)
        self._compile(task)
        return self.response(key)

    @staticmethod
    def _phase(task, phase):
        task.phase, task.phase_elapsed = phase, 0.
        task.phase_serial += 1
        task.tracker, task.profile, task.vertical_target, task.vertical_xy = None, None, None, None
        task.profile_elapsed = 0.
        task.reason = phase

    def _advance(self, task, observation, pose, drone_anchor, object_anchor, dt, *, replan):
        phase, p = task.phase, task.request.parameters
        root = pose.position_m
        if phase in {"navigating_to_pick", "navigating_to_place"}:
            ref = p["source_ref"] if phase == "navigating_to_pick" else p["destination_ref"]
            if task.tracker is None or replan:
                route = select_aerial_node_route(observation.get("topology"), AerialPose(root), target_refs=[ref])
                expected = task.route_graph_digest or p.get("route_graph_digest")
                if expected is not None and expected != route["route_graph_digest"]:
                    raise SkillContractError("aerial route graph identity changed")
                task.route_graph_digest = route["route_graph_digest"]
                if not route["admitted"]:
                    raise SkillContractError("route_unreachable")
                task.evidence["route_selection"] = route
                task.tracker = AerialNodeRouteTracker(route["route_points_m"])
            target = task.tracker.target(AerialPose(root))
            if target is not None:
                task.goal = target
                return
            anchor = object_anchor if phase == "navigating_to_pick" else vector(p["place_anchor_world_m"])
            offset = tuple((drone_anchor if phase == "navigating_to_pick" else object_anchor)[i]-root[i] for i in range(3))
            hover_z = task.tracker.route_points_m[-1][2]
            task.goal = anchor[0]-offset[0], anchor[1]-offset[1], hover_z
            if (math.dist(root, task.goal) > .01
                    or math.dist(root[:2], task.goal[:2]) > self.connection_tolerance_m):
                return
            if drone_anchor[2] <= object_anchor[2] and phase == "navigating_to_pick":
                raise SkillContractError("pick hover must be above the object anchor")
            if phase == "navigating_to_place" and object_anchor[2] < anchor[2]:
                raise SkillContractError("place hover must be above the destination anchor")
            task.hover_z = root[2]
            task.evidence["pick_hover_z" if phase == "navigating_to_pick" else "place_hover_z"] = root[2]
            self._phase(task, "supporting" if phase == "navigating_to_pick" else "descending_to_place")
            task.goal = root
            return
        if phase in MUTATIONS:
            task.goal = root
            return
        if phase not in {"descending_to_pick", "ascending_from_pick", "descending_to_place", "ascending_from_place"}:
            return
        if task.vertical_target is None:
            if phase == "descending_to_pick":
                # Aim at coincident anchors; the 1 cm rule is an admission gate.
                task.vertical_target = root[2]+object_anchor[2]-drone_anchor[2]
            elif phase == "descending_to_place":
                task.vertical_target = root[2]+p["place_anchor_world_m"][2]-object_anchor[2]
            else:
                task.vertical_target = task.hover_z
            task.vertical_xy = root[:2]
            if phase in {"descending_to_pick", "descending_to_place"}:
                target_anchor = object_anchor if phase == "descending_to_pick" else vector(p["place_anchor_world_m"])
                moving_anchor = drone_anchor if phase == "descending_to_pick" else object_anchor
                # Preserve the actual anchor destination, not the small
                # residual hover-position error admitted by the router.
                task.vertical_xy = tuple(root[i] + target_anchor[i] - moving_anchor[i] for i in range(2))
            if "descending" in phase and task.vertical_target > root[2]+.01:
                raise SkillContractError("descent target is above current height")
        if task.profile is None:
            task.profile = VerticalProfile(root[2], task.vertical_target, p["vertical_speed_m_s"], p["vertical_acceleration_m_s2"])
            task.profile_elapsed = 0.
        else:
            task.profile_elapsed += dt
        z, velocity = task.profile.sample(task.profile_elapsed)
        task.goal = (*task.vertical_xy, z)
        task.evidence["vertical_sample"] = {"elapsed_s": task.profile_elapsed, "z_m": z, "velocity_m_s": velocity}
        done = task.profile_elapsed >= task.profile.duration_s
        if phase == "descending_to_pick":
            reached = math.dist(drone_anchor[:2], object_anchor[:2]) <= self.connection_tolerance_m and abs(drone_anchor[2]-object_anchor[2]) <= self.connection_tolerance_m
            if done and reached:
                self._phase(task, "attaching")
        elif phase == "descending_to_place":
            reached = math.dist(object_anchor[:2], p["place_anchor_world_m"][:2]) <= self.connection_tolerance_m and abs(object_anchor[2]-p["place_anchor_world_m"][2]) <= self.connection_tolerance_m
            if done and reached:
                self._phase(task, "releasing")
        elif done and math.dist(root, task.goal) <= .01:
            if phase == "ascending_from_pick":
                self._phase(task, "navigating_to_place")
            else:
                task.phase, task.status, task.terminal, task.reason = "completed", "succeeded", True, "arrived"

    @staticmethod
    def _confirmation_id(task):
        return f"{task.request.task_id or task.request.request_id}:{task.phase_serial}:{task.phase}"

    def _compile(self, task, *, mutations=True):
        commands = [SkillCommand("goal_position", {"target_pose": task.goal, "frame": "world"})] if task.goal is not None else []
        if mutations and task.phase in MUTATIONS and not task.terminal:
            commands.append(SkillCommand("transport_mutation", {"operation": MUTATIONS[task.phase], "confirmation_id": self._confirmation_id(task)}))
        elif task.terminal and not task.attached and task.status != "succeeded":
            commands.append(SkillCommand("transport_mutation", {"operation": "rollback", "confirmation_id": self._confirmation_id(task)}))
        task.commands = tuple(commands)

    def _terminate(self, key, status, reason):
        task = self._tasks[key]
        task.status, task.terminal, task.reason = status, True, reason
        task.phase = "cancelled" if status == "cancelled" else "failed"
        self._compile(task)
        return self.response(key)

    def fail(self, task_id, error, *, reason_code="execution_failure", attached=None, hover_position=None):
        task = self._tasks[task_id]
        if attached is not None:
            task.attached = attached
        if hover_position is not None:
            task.goal = vector(hover_position)
        task.evidence["error_type"] = type(error).__name__
        task.evidence["error_message"] = str(error)
        return self._terminate(task_id, "failed", reason_code)

    def response(self, task_id):
        task = self._tasks[task_id]
        r = task.request
        metadata = {"schema_version": r.skill_version, "provider_id": self.provider_id,
                    "provider_version": self.provider_version, "request_digest": digest(r.to_mapping()),
                    "observation_digest": task.evidence.get("observation_digest"),
                    "route_graph_digest": task.route_graph_digest,
                    "command_route": "AerialPickPlaceExecutor -> EAI position controller / PhysxAerialTransportBridge",
                    "physical_outcome": "unknown", "task_id": task_id, "branch_context": deepcopy(r.branch_context)}
        plan = SkillPlan(r.request_id, self.skill_id, r.robot_id, task.commands, metadata=metadata)
        state = {"task_id": task_id, "phase": task.phase, "status": task.status, "reason_code": task.reason,
                 "attached": task.attached, "safe_action": "hover_retain_attachment" if task.attached and task.terminal else "hold" if task.held or task.terminal or task.phase == "pending" else "advance",
                 "world_state_seq": r.parameters["world_state_seq"], "physical_outcome": "unknown"}
        return AerialPickPlaceResponse(state, plan, deepcopy(task.evidence))

    def record_host_release(self, task_id):
        task = self._tasks[task_id]
        if not task.terminal:
            raise SkillContractError("host release requires a terminal task")
        task.attached = False
        task.evidence["host_released"] = True
        # Recovery already ran in the host. Keep only the hover command.
        task.commands = tuple(c for c in task.commands if c.command_type == "goal_position")
        return self.response(task_id)

    def plan(self, request, *, observation=None):
        try:
            r = normalize_aerial_pick_place_request(request)
            key = r.task_id or r.request_id
            if key not in self._tasks:
                self.dispatch(r, observation=observation)
            return self.control(r, observation=observation).plan
        except (SkillContractError, KeyError, TypeError) as exc:
            return invalid_request_plan(request, self.skill_id, self.provider_id, self.provider_version, exc)
