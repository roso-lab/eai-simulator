"""Native aerial I/O and owned USD mutations for a caller-owned EAI session."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import importlib
import math
import weakref
from typing import Any, Mapping
from uuid import uuid4

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.aerial_transport.provider import (AerialPickPlaceProvider,
    SUPPORTED_ROBOT_TYPES, normalize_aerial_pick_place_request)
from algorithm.skills.aerial_transport.geometry import RigidPose, conjugate, number, vector
from .ground_navigation import _vector, read_simulation_time
from ..resources import (InvalidResourceLeaseError, ResourceLease,
    ResourceManager, ResourceQuarantinedError)


LINEAR_QUIESCENCE_TOLERANCE_M_S = .01
ANGULAR_QUIESCENCE_TOLERANCE_RAD_S = .05


def read_aerial_pose(env, robot_id):
    if int(getattr(env, "num_envs", 1)) != 1:
        raise SkillContractError("aerial executor requires one environment")
    data = env.scene[robot_id].data
    return RigidPose(_vector(data.root_pos_w, 3), _vector(data.root_quat_w, 4))


class LivePhysxPoseReader:
    """Cache tensor views; never fall back to potentially stale USD xforms."""
    def __init__(self, env):
        self.env, self._views = env, {}
        self._closed = False
        timeline = importlib.import_module("omni.timeline")
        ref = weakref.ref(self)
        def stopped(event):
            reader = ref()
            if reader is not None:
                reader.close()
        self._stop_subscription = timeline.get_timeline_interface().get_timeline_event_stream().create_subscription_to_pop_by_type(
            int(timeline.TimelineEventType.STOP), stopped, order=0)

    def close(self):
        """Drop native buffers before Kit unloads the PhysX plugin."""
        self._closed = True
        views, self._views = self._views, {}
        failure = None
        for sim, view in views.values():
            view._backend = None
            try:
                sim.invalidate()
            except BaseException as exc:
                if failure is None:
                    failure = exc
            finally:
                sim._backend = None
        if failure is not None:
            raise failure

    def __call__(self, path):
        if self._closed:
            raise SkillContractError("live PhysX reader belongs to a stopped session")
        if path not in self._views:
            module = importlib.import_module("omni.physics.tensors")
            sim = module.create_simulation_view("torch" if str(self.env.device).startswith("cuda") else "numpy")
            view = sim.create_rigid_body_view(path)
            if view is None or view.count != 1:
                raise SkillContractError(f"one live PhysX body is required: {path}")
            self._views[path] = sim, view
        transform = _vector(self._views[path][1].get_transforms(), 7)
        return RigidPose(transform[:3], (transform[6], *transform[3:6]))

    def velocity(self, path):
        # PhysX tensor velocities are linear XYZ followed by angular XYZ.
        self(path)
        return _vector(self._views[path][1].get_velocities(), 6)


def flush_physx_transforms():
    """Make the live solver transforms visible to USD joint parsing."""
    physx = importlib.import_module("omni.physx")
    flush = getattr(physx.get_physx_simulation_interface(), "flush_changes", None)
    if callable(flush):
        flush()
    update = getattr(physx.get_physx_interface(), "update_transformations", None)
    if callable(update):
        update(False, True, False, False)


@dataclass
class _Binding:
    robot_body: str
    object_body: str
    object_anchor_local: tuple
    support: str | None = None
    attachment: str | None = None
    collision_snapshot: list = field(default_factory=list)
    collision_disabled: bool = False


class PhysxAerialTransportBridge:
    """FixedJoints and CollisionAPI only; every other physical property is kept.

    A pose_reader must return live body poses. Tests can inject a reader and an
    in-memory USD stage; production uses LivePhysxPoseReader.
    """
    def __init__(self, stage, *, pose_reader, drone_anchor_local_m=(0., 0., 0.), flush_hook=None):
        from pxr import Sdf
        self.stage, self._pose = stage, pose_reader
        self.drone_anchor_local_m = vector(drone_anchor_local_m)
        self._flush = flush_hook or (lambda: None)
        self._edit_target = stage.GetEditTarget()
        self._saved = Sdf.Layer.CreateAnonymous("aerial_collision_backup")
        self._bindings, self._owned, self._applied = {}, set(), {}
        self._scope = "/World/AerialTransport_"+uuid4().hex
        self._serial = 0

    def _body(self, path):
        from pxr import UsdPhysics
        prim = self.stage.GetPrimAtPath(path)
        if not prim or not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            raise SkillContractError(f"reference must resolve to a USD rigid body: {path}")
        if UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get() is False:
            raise SkillContractError("rigid body is disabled")
        return prim

    def bind(self, task_id, robot_body, object_body, pick_anchor_world_m):
        self._body(robot_body)
        self._body(object_body)
        if robot_body == object_body:
            raise SkillContractError("robot and object must be distinct rigid bodies")
        if task_id in self._bindings or any(b.object_body == object_body for b in self._bindings.values()):
            raise SkillContractError("transport body already bound to this bridge")
        pose = self._pose(object_body)
        self._bindings[task_id] = _Binding(robot_body, object_body, pose.to_local(pick_anchor_world_m))

    def measure(self, task_id):
        b = self._bindings[task_id]
        return self._pose(b.robot_body).to_world(self.drone_anchor_local_m), self._pose(b.object_body).to_world(b.object_anchor_local)

    def attached(self, task_id):
        b = self._bindings[task_id]
        return b.attachment is not None

    def evidence(self, task_id):
        b = self._bindings[task_id]
        return {"joint_path": b.attachment, "support_joint_path": b.support,
                "object_anchor_local_m": b.object_anchor_local, "collision_disabled": b.collision_disabled}

    def quiescent(self, task_id):
        """Measure both leased bodies; absence of live velocity is an error."""
        velocity = getattr(self._pose, "velocity", None)
        if not callable(velocity):
            raise SkillContractError("live rigid-body velocity reader is required for quiescence")
        b = self._bindings[task_id]
        samples = (velocity(b.robot_body), velocity(b.object_body))
        return all(
            max(abs(value) for value in sample[:3]) <= LINEAR_QUIESCENCE_TOLERANCE_M_S
            and max(abs(value) for value in sample[3:]) <= ANGULAR_QUIESCENCE_TOLERANCE_RAD_S
            for sample in samples
        )

    def _joint(self, b, *, support):
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics
        self._flush()
        object_pose = self._pose(b.object_body)
        robot_pose = RigidPose((0., 0., 0.)) if support else self._pose(b.robot_body)
        anchor = object_pose.to_world(b.object_anchor_local)
        self._serial += 1
        path = f"{self._scope}/joint_{self._serial}"
        with Usd.EditContext(self.stage, self._edit_target):
            UsdGeom.Scope.Define(self.stage, self._scope)
            if self.stage.GetPrimAtPath(path):
                raise SkillContractError("refusing to overwrite an existing joint")
            # Record ownership before any native authoring callback can fail.
            # Failed cleanup must leave a discoverable path for host recovery.
            attribute = "support" if support else "attachment"
            self._owned.add(path)
            setattr(b, attribute, path)
            try:
                joint = UsdPhysics.FixedJoint.Define(self.stage, path)
                joint.CreateJointEnabledAttr().Set(False)
                joint.CreateCollisionEnabledAttr().Set(False)
                joint.CreateExcludeFromArticulationAttr().Set(True)
                # Kit's joint authoring listener rewrites local frames when
                # body relationships change, using USD (stale with Fabric).
                # Bind bodies first, then overwrite those defaults with the
                # measured PhysX frames while the joint is still disabled.
                joint.CreateBody0Rel().SetTargets([] if support else [Sdf.Path(b.robot_body)])
                joint.CreateBody1Rel().SetTargets([Sdf.Path(b.object_body)])
                self._flush()
                joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*robot_pose.to_local(anchor)))
                joint.CreateLocalPos1Attr().Set(Gf.Vec3f(*b.object_anchor_local))
                for factory, pose in ((joint.CreateLocalRot0Attr, robot_pose), (joint.CreateLocalRot1Attr, object_pose)):
                    q = conjugate(pose.orientation_wxyz)
                    factory().Set(Gf.Quatf(q[0], Gf.Vec3f(*q[1:])))
                self._flush()
                joint.GetJointEnabledAttr().Set(True)
                self._flush()
            except BaseException as exc:
                try:
                    if self.stage.GetPrimAtPath(path):
                        self._remove(path)
                    else:
                        self._owned.discard(path)
                    setattr(b, attribute, None)
                except BaseException as cleanup_error:
                    exc.add_note(f"owned joint cleanup pending at {path}: {cleanup_error}")
                raise
        return path

    def _remove(self, path):
        from pxr import Usd
        if path not in self._owned:
            raise SkillContractError("refusing to delete a joint not owned by this bridge")
        with Usd.EditContext(self.stage, self._edit_target):
            if not self.stage.RemovePrim(path) or self.stage.GetPrimAtPath(path):
                raise RuntimeError("joint removal failed")
        self._owned.remove(path)

    def _disable_collision(self, b):
        from pxr import Sdf, Usd, UsdPhysics
        if b.collision_disabled:
            return
        if b.support is None:
            raise SkillContractError("a world support is required before disabling collisions")
        prims = list(Usd.PrimRange(self._body(b.object_body)))
        # Descendant rigid bodies are independent ownership boundaries.  Reject
        # the hierarchy before authoring any collision opinion because this
        # binding leases only b.object_body.
        if any(prim.HasAPI(UsdPhysics.RigidBodyAPI) for prim in prims[1:]):
            raise SkillContractError("payload contains a nested rigid body")
        with Usd.EditContext(self.stage, self._edit_target):
            for prim in prims:
                if not prim.HasAPI(UsdPhysics.CollisionAPI):
                    continue
                attr = UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr()
                spec_path = self._edit_target.MapToSpecPath(attr.GetPath())
                layer = self._edit_target.GetLayer()
                had_spec = bool(layer.GetAttributeAtPath(spec_path))
                if had_spec:
                    Sdf.CreatePrimInLayer(self._saved, spec_path.GetPrimPath())
                    Sdf.CopySpec(layer, spec_path, self._saved, spec_path)
                b.collision_snapshot.append((str(prim.GetPath()), str(spec_path), had_spec))
                # A default cannot override time samples in the same layer.
                # Keep their full spec in the backup, then author a constant.
                for time in layer.ListTimeSamplesForPath(spec_path):
                    layer.EraseTimeSample(spec_path, time)
                if not attr.Set(False):
                    raise RuntimeError("collision disable write failed")
        b.collision_disabled = True

    def _restore_collision(self, b):
        from pxr import Sdf, Usd
        # Restore the entire original authored spec (including time samples),
        # or remove only our override so inherited/reference opinions return.
        with Usd.EditContext(self.stage, self._edit_target):
            for prim_path, spec_path, had_spec in b.collision_snapshot:
                prim = self.stage.GetPrimAtPath(prim_path)
                if not prim:
                    raise RuntimeError("collision prim disappeared during transport")
                prim.RemoveProperty("physics:collisionEnabled")
                if had_spec:
                    Sdf.CopySpec(self._saved, Sdf.Path(spec_path), self._edit_target.GetLayer(), Sdf.Path(spec_path))
        b.collision_snapshot.clear()
        b.collision_disabled = False

    def apply(self, task_id, operation, confirmation_id):
        cached = self._applied.get((task_id, confirmation_id))
        if cached is not None:
            if cached["operation"] != operation:
                raise SkillContractError("confirmation id was reused for another operation")
            return deepcopy(cached)
        b = self._bindings[task_id]
        if operation == "create_support":
            if b.support is None:
                b.support = self._joint(b, support=True)
        elif operation == "disable_collision":
            try:
                self._disable_collision(b)
            except Exception:
                self._restore_collision(b)
                raise
        elif operation == "attach":
            drone, obj = self.measure(task_id)
            if math.dist(drone[:2], obj[:2]) > .01 or abs(drone[2]-obj[2]) > .01:
                raise SkillContractError("live attachment anchors exceed the 1 cm gate")
            if not b.collision_disabled or b.support is None:
                raise SkillContractError("attachment requires support and disabled collision")
            if b.attachment is None:
                b.attachment = self._joint(b, support=False)
        elif operation == "remove_support":
            if b.attachment is None:
                raise SkillContractError("attachment must precede support removal")
            if b.support:
                self._remove(b.support)
                b.support = None
        elif operation in {"release", "rollback"}:
            if operation == "rollback" and b.attachment:
                raise SkillContractError("rollback must not drop an attached object")
            if b.attachment:
                self._remove(b.attachment)
                b.attachment = None
            # No simulator step or host callback can occur between detach and
            # restoring collision. A failed restore keeps the resource lease.
            self._restore_collision(b)
            if b.support:
                self._remove(b.support)
                b.support = None
        else:
            raise SkillContractError("unknown transport mutation")
        ack = {"operation": operation, "confirmation_id": confirmation_id, "applied": True}
        self._applied[task_id, confirmation_id] = ack
        return deepcopy(ack)

    def unbind(self, task_id):
        b = self._bindings[task_id]
        if b.attachment or b.support or b.collision_disabled or b.collision_snapshot:
            raise SkillContractError("transport still owns physical state")
        del self._bindings[task_id]
        for key in [key for key in self._applied if key[0] == task_id]:
            del self._applied[key]


def _default_action(env, robot, goal):
    torch = importlib.import_module("torch")
    return torch.tensor([list(goal)], dtype=torch.float32, device=env.device)


@dataclass(frozen=True)
class PreparedAerialStep:
    """One executor contribution to exactly one caller-owned physics step."""

    task_id: str
    robot_id: str
    action: Any
    response: Any
    before_time_s: float
    token: str
    commands: tuple[Mapping, ...] = field(default=(), repr=False, compare=False)
    pending_ack: Mapping | None = field(default=None, repr=False, compare=False)
    preparation_errors: tuple[tuple[str, str], ...] = field(default=(), repr=False, compare=False)
    settling: bool = False


class AerialPickPlaceExecutor:
    """Prepare/confirm adapter for a caller-owned EAI simulation step.

    Preparation methods author controller goals and at most one owned USD
    mutation, but never advance physics. The host merges prepared actions,
    performs exactly one shared step, then calls after_step. The synchronous
    dispatch/control wrappers use the same protocol with advance_hook.

    Every executor in one scene must share a ResourceManager. Terminal release
    requires both a real advancing step and measured robot/object quiescence.
    """

    def __init__(self, env, *, topology, world_state_validator, provider=None,
                 bridge=None, reference_resolver=None, pose_reader=None,
                 simulation_time_reader=None, action_factory=None, advance_hook=None,
                 resource_manager=None, timeout_s=None, drone_anchor_local_m=(0., 0., 0.),
                 quiescence_reader=None, terminal_settle_timeout_s=5., connection_tolerance_m=.01):
        if not callable(world_state_validator):
            raise SkillContractError("world_state_validator is required")
        if int(getattr(env, "num_envs", 1)) != 1:
            raise SkillContractError("aerial executor requires one environment")
        if timeout_s is not None and number(timeout_s) <= 0:
            raise SkillContractError("timeout_s must be positive")
        if number(terminal_settle_timeout_s, "terminal settle timeout") <= 0:
            raise SkillContractError("terminal_settle_timeout_s must be positive")
        self.env, self.topology, self.timeout_s = env, topology, timeout_s
        self.terminal_settle_timeout_s = number(
            terminal_settle_timeout_s, "terminal settle timeout")
        if provider is not None and connection_tolerance_m != .01:
            raise SkillContractError("configure connection tolerance on the injected provider")
        self.provider = provider or AerialPickPlaceProvider(connection_tolerance_m=connection_tolerance_m)
        self.resources = resource_manager or ResourceManager()
        self._validate = world_state_validator
        self._pose = pose_reader or (lambda robot: read_aerial_pose(env, robot))
        self._time = simulation_time_reader or (lambda: read_simulation_time(env))
        self._action = action_factory or _default_action
        self._advance = advance_hook or env.step
        if bridge is None:
            stage = importlib.import_module("omni.usd").get_context().get_stage()
            bridge = PhysxAerialTransportBridge(
                stage, pose_reader=LivePhysxPoseReader(env),
                drone_anchor_local_m=drone_anchor_local_m,
                flush_hook=flush_physx_transforms)
        self.bridge = bridge
        self._quiescence = quiescence_reader
        self._resolve = reference_resolver or self._default_reference
        self._acks, self._leases, self._lease_owned = {}, {}, {}
        self._deadlines, self._samples, self._quarantined = {}, {}, set()
        self._settling, self._settle_elapsed = {}, {}
        self._prepared = None

    @property
    def settling_task_ids(self):
        return tuple(sorted(self._settling))

    def _default_reference(self, kind, ref):
        if kind == "object":
            if not ref.startswith("/"):
                raise SkillContractError("symbolic object_ref requires reference_resolver")
            return ref
        from pxr import UsdPhysics, Usd
        view = getattr(self.env.scene[ref], "root_physx_view", None)
        paths = getattr(view, "link_paths", None)
        if paths and len(paths) == 1 and paths[0]:
            return str(paths[0][0])
        root = self.env.scene[ref].cfg.prim_path.replace(
            "{ENV_REGEX_NS}", "/World/envs/env_0")
        if any(token in root for token in (".*", "[", "]")):
            raise SkillContractError("robot body path requires reference_resolver")
        candidates = [
            str(p.GetPath())
            for p in Usd.PrimRange(self.bridge.stage.GetPrimAtPath(root))
            if p.HasAPI(UsdPhysics.RigidBodyAPI)
        ]
        if len(candidates) != 1:
            raise SkillContractError(
                "robot body is ambiguous; inject reference_resolver")
        return candidates[0]

    def _observation(self, request, *, motion, include_ack=True):
        key = request.task_id or request.request_id
        sequence = request.parameters["world_state_seq"]
        self._validate(sequence, motion)
        drone_anchor, object_anchor = self.bridge.measure(key)
        observation = {
            "valid": True,
            "fresh": motion,
            "world_state_seq": sequence,
            "drone_pose": self._pose(request.robot_id),
            "simulation_time_s": number(self._time()),
            "drone_anchor_world_m": drone_anchor,
            "object_anchor_world_m": object_anchor,
        }
        if motion:
            observation["topology"] = (
                self.topology(request.robot_id)
                if callable(self.topology) else self.topology)
        if include_ack and key in self._acks:
            observation["confirmation"] = self._acks[key]
        return observation

    def _require_scene_available(self):
        if any(item["state"] == "quarantined"
               for item in self.resources.snapshot()["leases"]):
            raise ResourceQuarantinedError(
                "scene contains a quarantined resource lease")

    def _begin_prepare(self):
        if self._prepared is not None:
            raise SkillContractError(
                "an aerial step is already prepared and must be confirmed or aborted")
        self._require_scene_available()

    def _validate_external_lease(self, lease, required):
        if not isinstance(lease, ResourceLease):
            raise InvalidResourceLeaseError(
                "external lease was not issued by this manager")
        record = self.resources._records.get(lease.lease_id)
        if record is None or record.lease is not lease:
            raise InvalidResourceLeaseError(
                "external lease was not issued by this manager")
        if record.state != "active":
            raise ResourceQuarantinedError("external lease is not active")
        missing = sorted(set(required)-set(lease.resources))
        if missing:
            raise InvalidResourceLeaseError(
                f"external lease does not cover required resources: {missing}")

    def _controller_resources(self, request):
        configs = getattr(self.env, "_controller_configs", {})
        cfg = configs.get(request.robot_id)
        if cfg is None:
            raise SkillContractError("a goal_position controller is required")
        robot_type = getattr(cfg, "robot_type", "Unknown")
        if robot_type not in SUPPORTED_ROBOT_TYPES:
            raise SkillContractError(
                "aerial pick/place supports only iris and pegasus position robots "
                f"(controller robot_type one of {sorted(SUPPORTED_ROBOT_TYPES)}); "
                f"got {robot_type!r}")
        if (cfg.command_name != "goal_position"
                or getattr(cfg, "control_mode", "position") != "position"):
            raise SkillContractError("a goal_position controller is required")
        robot_body = self._resolve("robot", request.robot_id)
        object_body = self._resolve("object", request.parameters["object_ref"])
        required = ("robot:"+request.robot_id, "object:"+object_body)
        return robot_body, object_body, required

    def validate_request(self, value):
        """Resolve the two rigid bodies without binding or changing the scene."""
        request = normalize_aerial_pick_place_request(value)
        robot_body, object_body, _ = self._controller_resources(request)
        return robot_body, object_body

    def prepare_dispatch(self, value, *, lease=None):
        self._begin_prepare()
        request = normalize_aerial_pick_place_request(value)
        key = request.task_id or request.request_id
        if key in self._leases:
            raise SkillContractError("aerial executor task already exists")
        robot_body, object_body, required = self._controller_resources(request)
        owned = lease is None
        if owned:
            lease = self.resources.acquire("aerial:"+key, required)
        else:
            self._validate_external_lease(lease, required)
        self._leases[key], self._lease_owned[key], self._samples[key] = (
            lease, owned, [])
        bound = False
        try:
            self.bridge.bind(
                key, robot_body, object_body,
                request.parameters["pick_anchor_world_m"])
            bound = True
            observation = self._observation(request, motion=False)
            response = self.provider.dispatch(request, observation=observation)
        except BaseException:
            clean = True
            if bound:
                try:
                    self.bridge.unbind(key)
                except BaseException:
                    clean = False
            if clean:
                if owned:
                    self.resources.release(lease)
                self._leases.pop(key, None)
                self._lease_owned.pop(key, None)
                self._samples.pop(key, None)
            else:
                self._quarantine_task(
                    key, "dispatch cleanup was interrupted or failed")
            raise
        if self.timeout_s is not None:
            self._deadlines[key] = (
                observation["simulation_time_s"]+self.timeout_s)
        try:
            return self._prepare_or_fail(response)
        except BaseException:
            self._quarantine_task(
                key, "aerial dispatch preparation was interrupted or failed")
            raise

    def dispatch(self, value):
        return self._execute_prepared(self.prepare_dispatch(value))

    def prepare_control(self, value):
        self._begin_prepare()
        request = self.provider.validate_identity(value)
        key = request.task_id or request.request_id
        try:
            observation = self._observation(
                request,
                motion=request.parameters["intent"] not in {"hold", "stop"})
            if (request.parameters["intent"] not in {"hold", "stop"}
                    and observation["simulation_time_s"]
                    >= self._deadlines.get(key, math.inf)):
                raise TimeoutError("aerial execution window expired")
            response = self.provider.control(request, observation=observation)
            self._acks.pop(key, None)
        except Exception as exc:
            response = self._fail(key, exc)
        except BaseException:
            self._quarantine_task(
                key, "aerial control preparation was interrupted")
            raise
        try:
            return self._prepare_or_fail(response)
        except BaseException:
            self._quarantine_task(
                key, "aerial control preparation was interrupted or failed")
            raise

    def control(self, value):
        return self._execute_prepared(self.prepare_control(value))

    def prepare_settle(self, task_id):
        """Prepare another terminal hover tick without replaying a mutation."""
        self._begin_prepare()
        if task_id not in self._settling or task_id in self._quarantined:
            raise SkillContractError(
                "terminal task is not awaiting bounded settling")
        return self._prepare_response(
            self._settling[task_id], allow_mutation=False, settling=True)

    def _fail(self, key, error):
        try:
            hover = self._pose(
                self.provider.response(key).plan.robot_id).position_m
        except Exception:
            hover = None
        return self.provider.fail(
            key, error, attached=self.bridge.attached(key),
            hover_position=hover,
            reason_code=(
                "timeout" if isinstance(error, TimeoutError)
                else "execution_failure"))

    def _prepare_or_fail(self, response):
        try:
            return self._prepare_response(response)
        except Exception as exc:
            failed = self._fail(response.state["task_id"], exc)
            return self._prepare_response(
                failed,
                preparation_errors=(
                    ("execution_error_type", type(exc).__name__),))

    def _prepare_response(self, response, *, allow_mutation=True,
                          preparation_errors=(), settling=False):
        key, robot = response.state["task_id"], response.plan.robot_id
        before = number(self._time())
        pending_ack = None
        applied_commands = []
        for command in response.plan.commands:
            if command.command_type == "goal_position":
                goal = vector(command.payload["target_pose"])
                self.env.set_command(
                    robot, "goal_position",
                    self._action(self.env, robot, goal))
                applied_commands.append(command.to_mapping())
            elif command.command_type == "transport_mutation":
                if not allow_mutation:
                    continue
                if pending_ack is not None:
                    raise SkillContractError(
                        "only one transport mutation is allowed per cycle")
                payload = command.payload
                ack = self.bridge.apply(
                    key, payload["operation"], payload["confirmation_id"])
                if (ack.get("applied") is not True
                        or ack.get("operation") != payload["operation"]
                        or ack.get("confirmation_id")
                        != payload["confirmation_id"]):
                    raise RuntimeError("bridge did not confirm mutation")
                if payload["operation"] != "rollback":
                    pending_ack = deepcopy(ack)
                applied_commands.append(command.to_mapping())
            else:
                raise SkillContractError("unsupported aerial command")
        action = self._action(self.env, robot, (0., 0., 0.))
        prepared = PreparedAerialStep(
            task_id=key,
            robot_id=robot,
            action=action,
            response=response,
            before_time_s=before,
            token=uuid4().hex,
            commands=tuple(applied_commands),
            pending_ack=pending_ack,
            preparation_errors=tuple(preparation_errors),
            settling=settling)
        self._prepared = prepared
        return prepared

    def _execute_prepared(self, prepared):
        try:
            self._advance({prepared.robot_id: prepared.action})
        except BaseException as exc:
            response = self.abort_prepared(prepared, exc)
            if not isinstance(exc, Exception):
                raise
            return response
        return self.after_step(prepared)

    def abort_prepared(self, prepared, error):
        """Record an unconfirmed/failed host step and quarantine ownership."""
        if not isinstance(error, BaseException):
            raise TypeError("error must be a BaseException")
        return self.after_step(prepared, error=error)

    def after_step(self, prepared, *, error=None):
        if self._prepared is not prepared:
            raise SkillContractError(
                "prepared aerial step is stale or belongs to another cycle")
        self._prepared = None
        key = prepared.task_id
        errors = dict(prepared.preparation_errors)
        if error is not None:
            response = self._fail(key, error)
            errors["step_error_type"] = type(error).__name__
            self._quarantine_task(
                key, "aerial shared step failed or was interrupted")
            return self._record(
                response, applied=False, retained=True,
                quiescent=False, settling=False, errors=errors,
                commands=prepared.commands)
        try:
            now = number(self._time())
        except BaseException as exc:
            response = self._fail(key, exc)
            errors["time_error_type"] = type(exc).__name__
            self._quarantine_task(
                key, "aerial shared step time confirmation failed")
            if not isinstance(exc, Exception):
                raise
            return self._record(
                response, applied=False, retained=True,
                quiescent=False, settling=False, errors=errors,
                commands=prepared.commands)
        if now <= prepared.before_time_s:
            failure = RuntimeError(
                "shared step did not advance simulator time")
            response = self._fail(key, failure)
            errors["step_error_type"] = type(failure).__name__
            self._quarantine_task(
                key, "aerial shared step did not advance simulator time")
            return self._record(
                response, applied=False, retained=True,
                quiescent=False, settling=False, errors=errors,
                commands=prepared.commands)

        if prepared.pending_ack is not None:
            self._acks[key] = deepcopy(prepared.pending_ack)
        response = prepared.response
        terminal = response.state["status"] in {
            "succeeded", "cancelled", "failed"}
        retained, quiescent, settling, ready_to_release = True, None, False, False
        settle_elapsed = 0.
        if terminal:
            if self.bridge.attached(key):
                self._quarantine_task(
                    key, "terminal aerial task retains an attachment")
                quiescent = False
            else:
                try:
                    quiescent = self._read_quiescent(
                        key, prepared.robot_id)
                except BaseException as exc:
                    errors["quiescence_error_type"] = type(exc).__name__
                    if not isinstance(exc, Exception):
                        self._quarantine_task(
                            key, "terminal quiescence measurement was interrupted")
                        raise
                    quiescent = False
                self._settle_elapsed[key] = (
                    self._settle_elapsed.get(key, 0.)
                    + now-prepared.before_time_s)
                settle_elapsed = self._settle_elapsed[key]
                if quiescent:
                    retained = self._cleanup_quiescent(key, errors)
                    ready_to_release = (
                        not self._lease_owned[key]
                        and self._lease_state(key) == "active")
                    self._settling.pop(key, None)
                    self._settle_elapsed.pop(key, None)
                elif (self._settle_elapsed[key]
                      >= self.terminal_settle_timeout_s):
                    self._quarantine_task(
                        key, "terminal aerial task did not settle in time")
                else:
                    self._settling[key] = response
                    settling = True
        return self._record(
            response, applied=True, retained=retained,
            quiescent=quiescent, settling=settling, errors=errors,
            ready_to_release=ready_to_release,
            commands=prepared.commands, settle_elapsed_s=settle_elapsed)

    def _read_quiescent(self, key, robot):
        if self._quiescence is None:
            reader = getattr(self.bridge, "quiescent", None)
            if not callable(reader):
                raise SkillContractError(
                    "quiescence_reader is required for a custom bridge")
            value = reader(key)
        else:
            value = self._quiescence(key, robot)
        if type(value) is not bool:
            raise SkillContractError(
                "quiescence_reader must return an explicit boolean")
        return value

    def _lease_state(self, key):
        lease = self._leases[key]
        for item in self.resources.snapshot()["leases"]:
            if item["lease_id"] == lease.lease_id:
                return item["state"]
        return "released"

    def _quarantine_task(self, key, reason):
        if key not in self._leases:
            return
        state = self._lease_state(key)
        if state == "active":
            self.resources.quarantine(self._leases[key], reason)
        elif state != "quarantined":
            raise InvalidResourceLeaseError(
                "cannot quarantine a released aerial lease")
        self._quarantined.add(key)
        self._settling.pop(key, None)

    def _cleanup_quiescent(self, key, errors):
        try:
            self.bridge.unbind(key)
        except BaseException as exc:
            errors["cleanup_error_type"] = type(exc).__name__
            self._quarantine_task(
                key, "aerial physical cleanup failed")
            if not isinstance(exc, Exception):
                raise
            return True
        if self._lease_owned[key]:
            self.resources.release(self._leases[key])
            return False
        return True

    def _record(self, response, *, applied, retained, quiescent,
                settling, errors, ready_to_release=False, commands=None,
                settle_elapsed_s=None):
        key = response.state["task_id"]
        sample = {
            "command_applied": applied,
            "phase": response.state["phase"],
            "commands": deepcopy(list(commands)) if commands is not None else [
                command.to_mapping() for command in response.plan.commands],
        }
        try:
            sample["simulation_time_s"] = number(self._time())
            sample["position_m"] = self._pose(
                response.plan.robot_id).position_m
            sample.update(self.bridge.evidence(key))
            drone_anchor, object_anchor = self.bridge.measure(key)
            sample["drone_anchor_world_m"] = drone_anchor
            sample["object_anchor_world_m"] = object_anchor
            if key in self._acks:
                sample["confirmation"] = deepcopy(self._acks[key])
        except (KeyError, SkillContractError):
            pass
        self._samples[key].append(sample)
        evidence = {
            **response.private_evidence,
            **errors,
            "command_applied": applied,
            "resources_retained": retained,
            "external_lease": not self._lease_owned[key],
            "quiescent": quiescent,
            "settling": settling,
            "ready_to_release": ready_to_release,
            "settle_elapsed_s": (self._settle_elapsed.get(key, 0.)
                                 if settle_elapsed_s is None
                                 else settle_elapsed_s),
            "execution_sample_count": len(self._samples[key]),
            "execution_samples": deepcopy(
                self._samples[key]
                if response.state["status"] in {
                    "succeeded", "cancelled", "failed"}
                else self._samples[key][-1:]),
        }
        return replace(response, private_evidence=evidence)

    def host_release(self, task_id, *, quiescent=None):
        """Recover quarantined state after fresh measured quiescence."""
        if quiescent is not None and type(quiescent) is not bool:
            raise SkillContractError(
                "quiescent compatibility assertion must be boolean")
        if quiescent is False or task_id not in self._quarantined:
            raise SkillContractError(
                "host recovery requires a retained quarantined lease")
        robot = self.provider.response(task_id).plan.robot_id
        if self._read_quiescent(task_id, robot) is not True:
            raise SkillContractError(
                "host recovery requires measured quiescence")
        try:
            self.bridge.apply(
                task_id, "release", "host-release:"+task_id)
            self.bridge.unbind(task_id)
        except BaseException:
            raise
        self.resources.recover(
            self._leases[task_id], quiescent=True)
        if self._lease_owned[task_id]:
            self.resources.release(self._leases[task_id])
        self._quarantined.remove(task_id)
        self._settling.pop(task_id, None)
        self._settle_elapsed.pop(task_id, None)
        return self.provider.record_host_release(task_id)


__all__ = [
    "AerialPickPlaceExecutor",
    "PreparedAerialStep",
    "PhysxAerialTransportBridge",
    "LivePhysxPoseReader",
    "read_aerial_pose",
]
