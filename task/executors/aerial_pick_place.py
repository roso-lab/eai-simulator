"""Native aerial I/O and owned USD mutations for a caller-owned EAI session."""
from copy import deepcopy
from dataclasses import dataclass, field, replace
import importlib
import math
import weakref
from typing import Any, Mapping
from uuid import uuid4

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.aerial_transport.geometry import RigidPose, conjugate, number, vector
from .ground_navigation import _vector


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



__all__ = ["PhysxAerialTransportBridge", "LivePhysxPoseReader", "flush_physx_transforms", "read_aerial_pose"]
