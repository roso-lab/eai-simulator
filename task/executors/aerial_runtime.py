"""Aerial lifecycle bindings for one host-owned scene and shared physics tick.

The caller supplies the assignment/request mapping and scene source reader.
Handles never step. Completion receipts describe execution, not task success.
"""
from copy import deepcopy
from dataclasses import replace
import math

from algorithm.skills.contracts import SkillCommand, SkillContractError, SkillPlan, SkillRequest
from algorithm.skills.aerial_transport.geometry import number
from algorithm.skills.simulator_schema import AERIAL_NAVIGATION_SKILL_ID, AERIAL_PICK_PLACE_SKILL_ID
from algorithm.skills.navigation.aerial_navigation import AerialNavigationProvider, AerialPose, digest, normalize_request
from algorithm.skills.navigation.aerial_topology import FrozenAerialTopology
from algorithm.common_coordination.contract import FailureCode
from task.contracts import DependencyOutcome
from task.resources import ResourceManager
from .aerial_pick_place import AerialPickPlaceExecutor, read_aerial_pose, _default_action
from .ground_navigation import _vector, read_simulation_time
from .cooperative import CooperativeBinding
from .skills import ExecutionReceipt


class AerialSkillRuntime:
    """Session-scoped bridge; share this manager with every scene executor.

    source_reader() returns the current scene key and source digest inventory.
    It must read authoritative scene inputs, not repeat the frozen inventory.
    Custom observation/velocity readers are for explicit host backends/tests.
    """

    def __init__(self, env, *, topology, source_reader, resources,
                 sequence_reader=None, pose_reader=None, time_reader=None,
                 velocity_reader=None, action_factory=None, transport_factory=None,
                 transport_options=None, settle_timeout_s=5.):
        if not isinstance(topology, FrozenAerialTopology) or not callable(source_reader):
            raise SkillContractError("frozen aerial product and live source_reader are required")
        if not isinstance(resources, ResourceManager):
            raise SkillContractError("a scene-shared ResourceManager is required")
        if int(getattr(env, "num_envs", 1)) != 1:
            raise SkillContractError("aerial runtime requires one environment")
        settle_timeout_s = number(settle_timeout_s, "settle timeout")
        if settle_timeout_s <= 0:
            raise SkillContractError("settle timeout must be positive")
        self.env, self.topology, self.resources = env, topology, resources
        self.source_reader = source_reader
        self.pose = pose_reader or (lambda robot: read_aerial_pose(env, robot))
        raw_time = time_reader or (lambda: read_simulation_time(env))
        self.time = lambda: number(raw_time(), "simulation time")
        self.sequence = sequence_reader or (lambda: env.sim.current_time_step_index)
        self.velocity = velocity_reader or self._velocity
        self.action = action_factory or _default_action
        self.transport_factory = transport_factory or AerialPickPlaceExecutor
        self.transport_options = dict(transport_options or {})
        if set(self.transport_options) & {"resource_manager", "topology", "world_state_validator"}:
            raise SkillContractError("transport options cannot override scene ownership")
        self.settle_timeout_s = settle_timeout_s
        self.handles = {}

    def _require_scene(self, scene):
        if scene is self.env or getattr(scene, "base_env", None) is self.env:
            return
        raise SkillContractError(
            "aerial lifecycle scene must be the runtime environment or its session wrapper"
        )

    def _velocity(self, robot):
        data = self.env.scene[robot].data
        return (*_vector(data.root_lin_vel_w, 3), *_vector(data.root_ang_vel_w, 3))

    def check_scene(self):
        if any(row["state"] == "quarantined" for row in self.resources.snapshot()["leases"]):
            raise SkillContractError("scene has quarantined runtime ownership")
        self.topology.validate_source(*self.source_reader())

    def validate_sequence(self, sequence, motion):
        self.check_scene()
        current = self.sequence()
        if type(current) is not int or current < 0 or type(sequence) is not int or sequence != current:
            raise SkillContractError("request must use current measured world_state_seq")

    def _transport(self):
        return self.transport_factory(self.env, topology=self.topology.snapshot(),
            world_state_validator=self.validate_sequence, resource_manager=self.resources,
            **self.transport_options)

    def normalize(self, value):
        r = value if isinstance(value, SkillRequest) else SkillRequest.from_mapping(value)
        if r.skill_id not in {AERIAL_NAVIGATION_SKILL_ID, AERIAL_PICK_PLACE_SKILL_ID}:
            raise SkillContractError("unsupported aerial lifecycle skill")
        r = normalize_request(r, r.skill_id)
        expected = r.parameters.get("route_graph_digest")
        if expected is not None and expected != self.topology.route_graph_digest:
            raise SkillContractError("request route differs from frozen topology")
        return replace(r, parameters={**r.parameters, "route_graph_digest": self.topology.route_graph_digest})

    def validate(self, value):
        r = self.normalize(value)
        self.validate_sequence(r.parameters["world_state_seq"], False)
        cfg = getattr(self.env, "_controller_configs", {}).get(r.robot_id)
        if cfg is None or getattr(cfg, "command_name", None) != "goal_position" or getattr(cfg, "control_mode", "position") != "position":
            raise SkillContractError("aerial lifecycle requires a position controller")
        if r.parameters.get("target_yaw") is not None:
            raise SkillContractError("aerial XYZ lifecycle does not support target_yaw execution")
        return r

    def plan(self, value):
        """Compile a measured initial hold; start owns the continuing lifecycle."""
        r = self.validate(value)
        position = self.pose(r.robot_id).position_m
        metadata = {"schema_version": r.skill_version, "provider_id": "eai.aerial_lifecycle",
            "provider_version": "1.0", "request_digest": digest(r.to_mapping()),
            "observation_digest": digest({"position_m": position, "time_s": self.time(), "world_state_seq": self.sequence()}),
            "task_id": r.task_id, "branch_context": deepcopy(r.branch_context),
            "topology_product_digest": self.topology.product_digest,
            "route_graph_digest": self.topology.route_graph_digest,
            "command_route": "AerialSkillRuntime -> shared tick -> EAI position controller",
            "expected_external_outcome": "measured arrival or payload placement; external evaluator required",
            "physical_outcome": "unknown", "lifecycle_request": r.to_mapping()}
        return SkillPlan(r.request_id, r.skill_id, r.robot_id,
                         (SkillCommand("goal_position", {"target_pose": position, "frame": "world"}),), metadata=metadata)

    def binding(self, value):
        r = self.normalize(value)
        transport = self._transport() if r.skill_id == AERIAL_PICK_PLACE_SKILL_ID else None
        resources = ()
        if transport is not None:
            _, object_body = transport.validate_request(r)
            resources = ("object:" + object_body,)
        runtime = self

        class Compiler:
            skill_id = r.skill_id

            def plan(self, request, *, observation=None):
                return runtime.plan(request)

        def validate(*, scene, request):
            runtime._require_scene(scene)
            runtime.validate(request)
            if transport is not None:
                transport.validate_request(request)

        def start(plan, *, scene, lease):
            runtime._require_scene(scene)
            runtime.validate(r)
            expected = runtime.plan(r)
            if plan.to_mapping() != expected.to_mapping():
                raise SkillContractError("aerial plan or measured binding changed before start")
            key = r.task_id or r.request_id
            if key in runtime.handles:
                raise SkillContractError("aerial lifecycle task already started")
            handle = (_TransportHandle(runtime, r, lease, transport) if transport is not None
                      else _NavigationHandle(runtime, r, lease))
            runtime.handles[key] = handle
            return handle

        return CooperativeBinding(r, Compiler(), start, validate, resources, start_with_lease=True)


class _Handle:
    def __init__(self, runtime, request, lease):
        self.runtime, self.request, self.lease = runtime, request, lease
        self.response = None
        self.stopping = False
        self.prepared = None
        self.done = False

    def current_request(self, intent):
        return replace(self.request, parameters={**self.request.parameters,
            "world_state_seq": self.runtime.sequence(), "intent": intent})

    def receipt(self):
        state = self.response.state
        completed = state["status"] == "succeeded" and not self.stopping
        self.runtime.resources.validate_lease(
            self.lease,
            required_resources=("robot:" + self.request.robot_id,),
        )
        receipt = ExecutionReceipt(self.request.request_id, self.request.skill_id, self.request.robot_id,
            DependencyOutcome.COMPLETED if completed else DependencyOutcome.RETRYABLE_FAILURE, True,
            FailureCode.NONE if completed else FailureCode.EXECUTION_FAILURE,
            "aerial control cycle complete; physical outcome requires external evaluator" if completed else state["reason_code"])
        key = self.request.task_id or self.request.request_id
        if self.runtime.handles.get(key) is self:
            del self.runtime.handles[key]
        self.done = True
        return receipt

    def quarantine(self, reason):
        rows = self.runtime.resources.snapshot()["leases"]
        if any(row["lease_id"] == self.lease.lease_id and row["state"] == "active" for row in rows):
            self.runtime.resources.quarantine(self.lease, reason)


class _TransportHandle(_Handle):
    def __init__(self, runtime, request, lease, executor):
        super().__init__(runtime, request, lease)
        self.executor = executor
        self.prepared = executor.prepare_dispatch(request, lease=lease)

    def command(self):
        self.runtime.check_scene()
        if self.prepared is None:
            key = self.request.task_id or self.request.request_id
            if key in self.executor.settling_task_ids:
                self.prepared = self.executor.prepare_settle(key)
            else:
                intent = "stop" if self.stopping else "start" if self.response.state["phase"] == "pending" else "continue"
                self.prepared = self.executor.prepare_control(self.current_request(intent))
        return self.prepared.action

    def after_step(self):
        prepared, self.prepared = self.prepared, None
        self.response = self.executor.after_step(prepared)
        if self.response.private_evidence.get("ready_to_release") is True:
            return self.receipt()
        return None

    def cancel(self, reason):
        self.stopping = True
        if self.prepared is not None:
            prepared, self.prepared = self.prepared, None
            self.response = self.executor.abort_prepared(prepared, RuntimeError(reason))


class _NavigationHandle(_Handle):
    def __init__(self, runtime, request, lease):
        super().__init__(runtime, request, lease)
        runtime.resources.validate_lease(
            lease,
            required_resources=("robot:" + request.robot_id,),
        )
        self.provider = AerialNavigationProvider()
        self.before = None
        self.settle_started = None
        self.last_time = None
        self.response = self.provider.dispatch(request, observation=self.observation())

    def _time(self):
        now = self.runtime.time()
        if self.last_time is not None and now < self.last_time:
            raise SkillContractError("navigation simulation time moved backwards")
        self.last_time = now
        return now

    def observation(self):
        r = self.current_request("continue")
        self.runtime.validate_sequence(r.parameters["world_state_seq"], True)
        return {"valid": True, "fresh": True, "world_state_seq": r.parameters["world_state_seq"],
            "pose": AerialPose(self.runtime.pose(r.robot_id).position_m),
            "simulation_time_s": self._time(), "topology": self.runtime.topology.snapshot()}

    def command(self):
        self.runtime.check_scene()
        if self.before is not None or self.done:
            raise SkillContractError("navigation tick not confirmed or already complete")
        try:
            if self.response.state["status"] == "pending":
                intent = "stop" if self.stopping else "start" if self.response.state["reason_code"] == "awaiting_start" else "continue"
                self.response = self.provider.control(self.current_request(intent), observation=self.observation())
            target = self.response.plan.commands[0].payload["target_pose"]
            self.runtime.env.set_command(self.request.robot_id, "goal_position",
                self.runtime.action(self.runtime.env, self.request.robot_id, target))
            self.before = self._time()
            return self.runtime.action(self.runtime.env, self.request.robot_id, (0., 0., 0.))
        except BaseException:
            self.quarantine("navigation command preparation failed")
            raise

    def after_step(self):
        try:
            now = self._time()
            if self.before is None or now <= self.before:
                raise SkillContractError("navigation requires an advancing shared step")
            self.before = None
            if self.response.state["status"] == "pending":
                return None
            velocities = _vector(self.runtime.velocity(self.request.robot_id), 6)
            if math.hypot(*velocities[:3]) <= .03 and math.hypot(*velocities[3:]) <= .05:
                return self.receipt()
            if self.settle_started is None:
                self.settle_started = now
            if now - self.settle_started >= self.runtime.settle_timeout_s:
                raise SkillContractError("navigation terminal hold did not settle")
            return None
        except BaseException:
            self.quarantine("navigation step or measured stop was not confirmed")
            raise

    def cancel(self, reason):
        self.stopping = True
        if self.before is not None:
            self.quarantine("navigation shared step interrupted: " + reason)
