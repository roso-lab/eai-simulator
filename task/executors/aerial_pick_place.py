"""Native aerial I/O and owned USD mutations for a caller-owned EAI session."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import importlib
import math
from uuid import uuid4

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.aerial_transport.provider import (AerialPickPlaceProvider,
    SUPPORTED_ROBOT_TYPES, normalize_aerial_pick_place_request)
from algorithm.skills.aerial_transport.geometry import RigidPose, conjugate, number, vector
from .ground_navigation import _vector, read_simulation_time
from ..resources import ResourceManager


def read_aerial_pose(env, robot_id):
    if int(getattr(env, "num_envs", 1)) != 1:
        raise SkillContractError("aerial executor requires one environment")
    data = env.scene[robot_id].data
    return RigidPose(_vector(data.root_pos_w, 3), _vector(data.root_quat_w, 4))


class LivePhysxPoseReader:
    """Cache tensor views; never fall back to potentially stale USD xforms."""
    def __init__(self, env):
        self.env, self._views = env, {}

    def __call__(self, path):
        if path not in self._views:
            module = importlib.import_module("omni.physics.tensors")
            sim = module.create_simulation_view("torch" if str(self.env.device).startswith("cuda") else "numpy")
            view = sim.create_rigid_body_view(path)
            if view is None or view.count != 1:
                raise SkillContractError(f"one live PhysX body is required: {path}")
            self._views[path] = sim, view
        transform = _vector(self._views[path][1].get_transforms(), 7)
        return RigidPose(transform[:3], (transform[6], *transform[3:6]))


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
            try:
                joint = UsdPhysics.FixedJoint.Define(self.stage, path)
                joint.CreateJointEnabledAttr().Set(False)
                joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*robot_pose.to_local(anchor)))
                joint.CreateLocalPos1Attr().Set(Gf.Vec3f(*b.object_anchor_local))
                for factory, pose in ((joint.CreateLocalRot0Attr, robot_pose), (joint.CreateLocalRot1Attr, object_pose)):
                    q = conjugate(pose.orientation_wxyz)
                    factory().Set(Gf.Quatf(q[0], Gf.Vec3f(*q[1:])))
                joint.CreateCollisionEnabledAttr().Set(False)
                joint.CreateExcludeFromArticulationAttr().Set(True)
                joint.CreateBody0Rel().SetTargets([] if support else [Sdf.Path(b.robot_body)])
                joint.CreateBody1Rel().SetTargets([Sdf.Path(b.object_body)])
                self._flush()
                joint.GetJointEnabledAttr().Set(True)
                self._flush()
            except Exception:
                self.stage.RemovePrim(path)
                raise
        self._owned.add(path)
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
        with Usd.EditContext(self.stage, self._edit_target):
            for prim in Usd.PrimRange(self._body(b.object_body)):
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


class AerialPickPlaceExecutor:
    """Control-cycle adapter. The host owns stepping order and physical verdicts.

    advance_hook(actions) must APPLY and advance synchronously; a queue-only
    hook is not an execution confirmation. Share resource_manager across all
    executors using a scene. A retained terminal attachment requires explicit
    host_release(task_id, quiescent=True) after the host establishes recovery.
    """
    def __init__(self, env, *, topology, world_state_validator, provider=None,
                 bridge=None, reference_resolver=None, pose_reader=None,
                 simulation_time_reader=None, action_factory=None, advance_hook=None,
                 resource_manager=None, timeout_s=None, drone_anchor_local_m=(0., 0., 0.)):
        if not callable(world_state_validator):
            raise SkillContractError("world_state_validator is required")
        if int(getattr(env, "num_envs", 1)) != 1:
            raise SkillContractError("aerial executor requires one environment")
        if timeout_s is not None and number(timeout_s) <= 0:
            raise SkillContractError("timeout_s must be positive")
        self.env, self.topology, self.timeout_s = env, topology, timeout_s
        self.provider = provider or AerialPickPlaceProvider()
        self.resources = resource_manager or ResourceManager()
        self._validate = world_state_validator
        self._pose = pose_reader or (lambda robot: read_aerial_pose(env, robot))
        self._time = simulation_time_reader or (lambda: read_simulation_time(env))
        self._action = action_factory or _default_action
        self._advance = advance_hook or env.step
        if bridge is None:
            stage = importlib.import_module("omni.usd").get_context().get_stage()
            bridge = PhysxAerialTransportBridge(stage, pose_reader=LivePhysxPoseReader(env),
                drone_anchor_local_m=drone_anchor_local_m, flush_hook=flush_physx_transforms)
        self.bridge = bridge
        self._resolve = reference_resolver or self._default_reference
        self._acks, self._leases, self._deadlines, self._samples, self._quarantined = {}, {}, {}, {}, set()

    def _default_reference(self, kind, ref):
        if kind == "object":
            # An absolute body path is the only default object reference;
            # symbolic host references require an explicit resolver.
            if not ref.startswith("/"):
                raise SkillContractError("symbolic object_ref requires reference_resolver")
            return ref
        from pxr import UsdPhysics, Usd
        # Articulations often have rotors as rigid links; select the explicitly
        # declared root body via live articulation link_paths, never by bbox.
        view = getattr(self.env.scene[ref], "root_physx_view", None)
        paths = getattr(view, "link_paths", None)
        if paths and len(paths) == 1 and paths[0]:
            return str(paths[0][0])
        root = self.env.scene[ref].cfg.prim_path.replace("{ENV_REGEX_NS}", "/World/envs/env_0")
        # An unresolved regex path cannot be interpreted as a USD prim path.
        if any(token in root for token in (".*", "[", "]")):
            raise SkillContractError("robot body path requires reference_resolver")
        candidates = [str(p.GetPath()) for p in Usd.PrimRange(self.bridge.stage.GetPrimAtPath(root)) if p.HasAPI(UsdPhysics.RigidBodyAPI)]
        if len(candidates) != 1:
            raise SkillContractError("robot body is ambiguous; inject reference_resolver")
        return candidates[0]

    def _observation(self, request, *, motion, include_ack=True):
        key = request.task_id or request.request_id
        sequence = request.parameters["world_state_seq"]
        self._validate(sequence, motion)
        drone_anchor, object_anchor = self.bridge.measure(key)
        observation = {"valid": True, "fresh": motion, "world_state_seq": sequence,
                       "drone_pose": self._pose(request.robot_id), "simulation_time_s": number(self._time()),
                       "drone_anchor_world_m": drone_anchor, "object_anchor_world_m": object_anchor}
        if motion:
            observation["topology"] = self.topology(request.robot_id) if callable(self.topology) else self.topology
        if include_ack and key in self._acks:
            observation["confirmation"] = self._acks[key]
        return observation

    def dispatch(self, value):
        r = normalize_aerial_pick_place_request(value)
        key = r.task_id or r.request_id
        if key in self._leases:
            raise SkillContractError("aerial executor task already exists")
        configs = getattr(self.env, "_controller_configs", {})
        cfg = configs.get(r.robot_id)
        if cfg is None:
            raise SkillContractError("a goal_position controller is required")
        robot_type = getattr(cfg, "robot_type", "Unknown")
        if robot_type not in SUPPORTED_ROBOT_TYPES:
            raise SkillContractError("aerial pick/place supports only iris and pegasus position robots "
                                     f"(controller robot_type one of {sorted(SUPPORTED_ROBOT_TYPES)}); got {robot_type!r}")
        if cfg.command_name != "goal_position" or getattr(cfg, "control_mode", "position") != "position":
            raise SkillContractError("a goal_position controller is required")
        robot_body, object_body = self._resolve("robot", r.robot_id), self._resolve("object", r.parameters["object_ref"])
        lease = self.resources.acquire("aerial:"+key, ["robot:"+r.robot_id, "object:"+object_body])
        bound = False
        try:
            self.bridge.bind(key, robot_body, object_body, r.parameters["pick_anchor_world_m"])
            bound = True
            observation = self._observation(r, motion=False)
            response = self.provider.dispatch(r, observation=observation)
        except Exception:
            if bound:
                self.bridge.unbind(key)
            self.resources.release(lease)
            raise
        self._leases[key], self._samples[key] = lease, []
        if self.timeout_s is not None:
            self._deadlines[key] = observation["simulation_time_s"]+self.timeout_s
        return self._apply(response)

    def control(self, value):
        # Validate identity before reading/applying anything: a conflicting
        # request must not cancel or operate on another task's resources.
        r = self.provider.validate_identity(value)
        key = r.task_id or r.request_id
        try:
            observation = self._observation(r, motion=r.parameters["intent"] not in {"hold", "stop"})
            if r.parameters["intent"] not in {"hold", "stop"} and observation["simulation_time_s"] >= self._deadlines.get(key, math.inf):
                raise TimeoutError("aerial execution window expired")
            response = self.provider.control(r, observation=observation)
            self._acks.pop(key, None)
        except Exception as exc:
            response = self._fail(key, exc)
        return self._apply(response)

    def _fail(self, key, error):
        try:
            hover = self._pose(self.provider.response(key).plan.robot_id).position_m
        except Exception:
            hover = None
        return self.provider.fail(key, error, attached=self.bridge.attached(key), hover_position=hover,
                                  reason_code="timeout" if isinstance(error, TimeoutError) else "execution_failure")

    def _send(self, response):
        key, robot = response.state["task_id"], response.plan.robot_id
        for command in response.plan.commands:
            if command.command_type == "goal_position":
                goal = vector(command.payload["target_pose"])
                self.env.set_command(robot, "goal_position", self._action(self.env, robot, goal))
            elif command.command_type == "transport_mutation":
                p = command.payload
                ack = self.bridge.apply(key, p["operation"], p["confirmation_id"])
                if (ack.get("applied") is not True or ack.get("operation") != p["operation"]
                        or ack.get("confirmation_id") != p["confirmation_id"]):
                    raise RuntimeError("bridge did not confirm mutation")
                if p["operation"] != "rollback":
                    self._acks[key] = ack
            else:
                raise SkillContractError("unsupported aerial command")
        # Zero external XYZ lets the existing controller consume goal_position
        # (including [0,0,0]) and calculate rotor actions normally.
        before = number(self._time())
        self._advance({robot: self._action(self.env, robot, (0., 0., 0.))})
        if response.state["safe_action"] == "advance" and number(self._time()) <= before:
            raise RuntimeError("advance_hook did not advance simulator time")

    def _apply(self, response):
        key = response.state["task_id"]
        applied, errors = False, {}
        try:
            self._send(response)
            applied = True
        except Exception as exc:
            response = self._fail(key, exc)
            try:
                self._send(response)
                applied = True
            except Exception as recovery:
                errors["recovery_error_type"] = type(recovery).__name__
        terminal = response.state["status"] in {"succeeded", "cancelled", "failed"}
        if terminal:
            retained = self.bridge.attached(key) or not applied
            if not retained:
                try:
                    self.bridge.unbind(key)
                except Exception as exc:
                    errors["cleanup_error_type"] = type(exc).__name__
                    retained = True
            if retained:
                if key not in self._quarantined:
                    self.resources.quarantine(self._leases[key], "aerial attachment or recovery requires host release")
                    self._quarantined.add(key)
            else:
                self.resources.release(self._leases[key])
        else:
            retained = True
        sample = {"command_applied": applied, "phase": response.state["phase"], "commands": [c.to_mapping() for c in response.plan.commands]}
        try:
            sample["simulation_time_s"] = number(self._time())
            sample["position_m"] = self._pose(response.plan.robot_id).position_m
            sample.update(self.bridge.evidence(key))
            drone_anchor, object_anchor = self.bridge.measure(key)
            sample["drone_anchor_world_m"], sample["object_anchor_world_m"] = drone_anchor, object_anchor
            if key in self._acks:
                sample["confirmation"] = deepcopy(self._acks[key])
        except (KeyError, SkillContractError):
            pass
        self._samples[key].append(sample)
        evidence = {**response.private_evidence, **errors, "command_applied": applied,
                    "resources_retained": retained, "execution_sample_count": len(self._samples[key]),
                    "execution_samples": deepcopy(self._samples[key] if terminal else self._samples[key][-1:])}
        return replace(response, private_evidence=evidence)

    def host_release(self, task_id, *, quiescent):
        """Explicit host recovery; no new control on a terminal task."""
        if quiescent is not True or task_id not in self._quarantined:
            raise SkillContractError("host recovery requires a retained lease and quiescent=True")
        self.bridge.apply(task_id, "release", "host-release:"+task_id)
        self.bridge.unbind(task_id)
        self.resources.recover(self._leases[task_id], quiescent=True)
        self.resources.release(self._leases[task_id])
        self._quarantined.remove(task_id)
        return self.provider.record_host_release(task_id)


__all__ = ["AerialPickPlaceExecutor", "PhysxAerialTransportBridge", "LivePhysxPoseReader", "read_aerial_pose"]
