"""Native aerial pose I/O for a caller-owned EAI session."""
import importlib
import math
import weakref

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.aerial_transport.geometry import RigidPose, vector


def _vector(value, size):
    """Read one finite vector without depending on a navigation executor."""
    for method_name in ("detach", "cpu", "tolist"):
        method = getattr(value, method_name, None)
        if callable(method):
            value = method()
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise SkillContractError(f"aerial state must contain {size} values")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise SkillContractError("aerial state must be finite")
    return result


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
    """Synchronize live solver transforms for host diagnostics."""
    physx = importlib.import_module("omni.physx")
    flush = getattr(physx.get_physx_simulation_interface(), "flush_changes", None)
    if callable(flush):
        flush()
    update = getattr(physx.get_physx_interface(), "update_transformations", None)
    if callable(update):
        update(False, True, False, False)


__all__ = ["LivePhysxPoseReader", "flush_physx_transforms", "read_aerial_pose"]
