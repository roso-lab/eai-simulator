"""On-demand Orsus/RGB render-product lifecycle for benchmark camera tools.

Default policy is idle_destroy: when the outstanding request queue is empty and
the idle timeout elapses, detach/destroy (or pause) the owned render product.
Requests ensure/create, warm up to a valid fresh frame, then capture.

Ownership is explicit. Continuous ROS camera publishers that already own a
shared render product must register as external owners; this module will not
destroy a product while a non-benchmark owner still holds a reference.
Native Isaac/Orsus/RTX paths are not claimed verified by the fake backend used
in static tests.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from typing import Any, Callable, Mapping


def _finite(value: Any, name: str, *, nonnegative: bool = False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"{name} must be finite")
    number = float(value)
    if nonnegative and number < 0:
        raise ValueError(f"{name} must be nonnegative")
    return number


class OnDemandRenderLifecycle:
    """Reference-counted render-product gate with idle destroy telemetry."""

    def __init__(
        self,
        *,
        create_product: Callable[[str], Any],
        destroy_product: Callable[[Any], None],
        idle_timeout_s: float = 0.5,
        warmup_frames: int = 1,
        owner_id: str = "benchmark_ondemand",
    ):
        idle = _finite(idle_timeout_s, "idle_timeout_s", nonnegative=True)
        if type(warmup_frames) is not int or isinstance(warmup_frames, bool) or warmup_frames < 0:
            raise ValueError("warmup_frames must be a nonnegative integer")
        if not callable(create_product) or not callable(destroy_product):
            raise ValueError("lifecycle requires create/destroy callables")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be nonempty")
        self._create_product = create_product
        self._destroy_product = destroy_product
        self.idle_timeout_s = idle
        self.warmup_frames = warmup_frames
        self.owner_id = owner_id
        self._products: dict[str, dict[str, Any]] = {}
        self._external_owners: dict[str, set[str]] = {}
        self._active_since: dict[str, float | None] = {}
        self._queue_empty_since: dict[str, float | None] = {}
        self._telemetry = {
            "render_product_active_s": 0.0,
            "create_count": 0,
            "destroy_count": 0,
            "warmup_frames": 0,
            "first_frame_latency_s": [],
            "failures": [],
            "native_orsus_rtx_verified": False,
            "cleanup_pending": False,
            "cleanup_complete": True,
        }

    def register_external_owner(self, sensor_key: str, owner: str) -> None:
        if not isinstance(sensor_key, str) or not sensor_key.strip():
            raise ValueError("sensor_key must be nonempty")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must be nonempty")
        self._external_owners.setdefault(sensor_key, set()).add(owner)

    def release_external_owner(self, sensor_key: str, owner: str) -> None:
        owners = self._external_owners.get(sensor_key)
        if not owners:
            return
        owners.discard(owner)
        if not owners:
            self._external_owners.pop(sensor_key, None)

    def has_external_owners(self, sensor_key: str) -> bool:
        return bool(self._external_owners.get(sensor_key))

    def active_sensor_keys(self) -> list[str]:
        return [key for key, state in self._products.items() if state.get("product") is not None]

    def _accumulate_active(self, sensor_key: str, now: float) -> None:
        started = self._active_since.get(sensor_key)
        if started is not None:
            self._telemetry["render_product_active_s"] += max(0.0, now - started)
            self._active_since[sensor_key] = now

    def ensure(self, sensor_key: str, *, now_s: float) -> Any:
        now = _finite(now_s, "now_s", nonnegative=True)
        state = self._products.get(sensor_key)
        if state is not None and state.get("product") is not None:
            self._queue_empty_since[sensor_key] = None
            return state["product"]
        try:
            product = self._create_product(sensor_key)
        except Exception as exc:
            self._telemetry["failures"].append({
                "sensor_key": sensor_key, "phase": "create",
                "error": f"{type(exc).__name__}: {exc}", "time_s": now,
            })
            raise
        self._products[sensor_key] = {
            "product": product,
            "owners": {self.owner_id},
            "created_at_s": now,
            "warmup_remaining": self.warmup_frames,
            "first_frame_started_at_s": now,
        }
        self._active_since[sensor_key] = now
        self._queue_empty_since[sensor_key] = None
        self._telemetry["create_count"] += 1
        return product

    def mark_queue_activity(self, sensor_key: str, *, queue_depth: int, now_s: float) -> None:
        now = _finite(now_s, "now_s", nonnegative=True)
        if queue_depth > 0:
            self._queue_empty_since[sensor_key] = None
            return
        if self._queue_empty_since.get(sensor_key) is None:
            self._queue_empty_since[sensor_key] = now

    def warmup_tick(self, sensor_key: str, *, now_s: float) -> bool:
        now = _finite(now_s, "now_s", nonnegative=True)
        state = self._products.get(sensor_key)
        if state is None or state.get("product") is None:
            raise ValueError("warmup requires an active render product")
        remaining = state["warmup_remaining"]
        if remaining > 0:
            state["warmup_remaining"] = remaining - 1
            self._telemetry["warmup_frames"] += 1
            return False
        return True

    def note_first_frame(self, sensor_key: str, *, now_s: float) -> float:
        now = _finite(now_s, "now_s", nonnegative=True)
        state = self._products[sensor_key]
        started = state["first_frame_started_at_s"]
        latency = max(0.0, now - started)
        self._telemetry["first_frame_latency_s"].append(latency)
        return latency

    def release_if_idle(self, sensor_key: str, *, now_s: float) -> bool:
        now = _finite(now_s, "now_s", nonnegative=True)
        state = self._products.get(sensor_key)
        if state is None or state.get("product") is None:
            return False
        if self.has_external_owners(sensor_key):
            return False
        empty_since = self._queue_empty_since.get(sensor_key)
        if empty_since is None:
            return False
        if now - empty_since < self.idle_timeout_s:
            return False
        return self.force_release(sensor_key, now_s=now)

    def force_release(self, sensor_key: str, *, now_s: float) -> bool:
        now = _finite(now_s, "now_s", nonnegative=True)
        state = self._products.get(sensor_key)
        if state is None or state.get("product") is None:
            return False
        if self.has_external_owners(sensor_key):
            self._telemetry["failures"].append({
                "sensor_key": sensor_key, "phase": "destroy_blocked_shared_owner",
                "error": "external_owner_present", "time_s": now,
            })
            return False
        self._accumulate_active(sensor_key, now)
        product = state["product"]
        try:
            self._destroy_product(product)
        except Exception as exc:
            self._telemetry["failures"].append({
                "sensor_key": sensor_key, "phase": "destroy",
                "error": f"{type(exc).__name__}: {exc}", "time_s": now,
            })
            # Keep the product registered so a later tick/close can retry.
            # Never claim destroy_count success after a raised failure.
            raise
        state["product"] = None
        self._active_since[sensor_key] = None
        self._queue_empty_since[sensor_key] = None
        self._telemetry["destroy_count"] += 1
        return True

    def tick_idle_releases(self, now_s: float) -> list[str]:
        """Release every owned product whose idle timeout has elapsed."""
        released = []
        for sensor_key in list(self.active_sensor_keys()):
            if self.release_if_idle(sensor_key, now_s=now_s):
                released.append(sensor_key)
        return released

    def close(self, *, now_s: float = 0.0) -> None:
        """Release owned products; safe to retry while cleanup is pending.

        External/shared owners are never destroyed. Failures leave products
        registered so a later ``close`` can retry. ``cleanup_complete`` becomes
        true only when no owned products remain.
        """
        pending = False
        for sensor_key in list(self.active_sensor_keys()):
            if self.has_external_owners(sensor_key):
                continue
            try:
                self.force_release(sensor_key, now_s=now_s)
            except Exception as exc:
                pending = True
                # force_release already recorded a destroy failure; mark the close
                # attempt without duplicating the same destroy row.
                self._telemetry["failures"].append({
                    "sensor_key": sensor_key, "phase": "close_retry_pending",
                    "error": f"{type(exc).__name__}: {exc}", "time_s": float(now_s),
                })
        owned = [
            key for key in self.active_sensor_keys()
            if not self.has_external_owners(key)
        ]
        self._telemetry["cleanup_pending"] = bool(owned) or pending
        self._telemetry["cleanup_complete"] = not owned

    def telemetry(self) -> dict[str, Any]:
        report = deepcopy(self._telemetry)
        owned = [
            key for key in self.active_sensor_keys()
            if not self.has_external_owners(key)
        ]
        report.setdefault("cleanup_pending", bool(owned))
        report.setdefault("cleanup_complete", not owned)
        report["cleanup_pending"] = bool(owned)
        report["cleanup_complete"] = not owned
        return report


class FakeOnDemandCameraBackend:
    """Pure-Python capture backend for static tests; never imports Isaac."""

    def __init__(
        self,
        *,
        lifecycle: OnDemandRenderLifecycle | None = None,
        idle_timeout_s: float = 0.5,
        warmup_frames: int = 1,
        physics_step_fn: Callable[[float], int] | None = None,
        fail_sensors: set[str] | None = None,
        oversized_bytes: int | None = None,
        raise_on_destroy: bool = False,
        fixed_payload: bytes | None = None,
    ):
        self._raise_on_destroy = raise_on_destroy
        self.lifecycle = lifecycle or OnDemandRenderLifecycle(
            create_product=self._create,
            destroy_product=self._destroy,
            idle_timeout_s=idle_timeout_s,
            warmup_frames=warmup_frames,
        )
        self._products: dict[str, dict[str, Any]] = {}
        self._physics_step_fn = physics_step_fn or (lambda t: int(t * 100))
        self._fail_sensors = set(fail_sensors or ())
        self._oversized_bytes = oversized_bytes
        self._fixed_payload = fixed_payload
        self.captures: list[dict[str, Any]] = []
        self._logical_closed = False

    def set_raise_on_destroy(self, value: bool) -> None:
        """Test helper: toggle destroy failures without replacing the backend."""
        self._raise_on_destroy = bool(value)

    def _create(self, sensor_key: str) -> dict[str, Any]:
        product = {"sensor_key": sensor_key, "alive": True, "frames": 0}
        self._products[sensor_key] = product
        return product

    def _destroy(self, product: Any) -> None:
        if self._raise_on_destroy:
            raise RuntimeError("synthetic destroy failure")
        if not isinstance(product, dict) or not product.get("alive"):
            raise RuntimeError("destroying inactive fake render product")
        product["alive"] = False
        self._products.pop(product["sensor_key"], None)

    def sensor_key(self, robot_id: str, sensor_id: str) -> str:
        return f"{robot_id}/{sensor_id}"

    def capture(self, request: Mapping[str, Any], time_s: float) -> dict[str, Any]:
        if self._logical_closed:
            raise RuntimeError("camera backend is logically closed")
        robot_id = request["robot_id"]
        sensor_id = request["sensor_id"]
        key = self.sensor_key(robot_id, sensor_id)
        if key in self._fail_sensors:
            self.lifecycle.ensure(key, now_s=time_s)
            try:
                raise RuntimeError("synthetic capture failure")
            finally:
                self.lifecycle.mark_queue_activity(key, queue_depth=0, now_s=time_s)
                try:
                    self.lifecycle.force_release(key, now_s=time_s)
                except Exception:
                    pass

        product = self.lifecycle.ensure(key, now_s=time_s)
        self.lifecycle.mark_queue_activity(key, queue_depth=1, now_s=time_s)
        while not self.lifecycle.warmup_tick(key, now_s=time_s):
            product["frames"] += 1
            time_s = float(time_s)
        latency = self.lifecycle.note_first_frame(key, now_s=time_s)
        width = int(request["width"])
        height = int(request["height"])
        if self._oversized_bytes is not None:
            payload = b"X" * int(self._oversized_bytes)
        elif self._fixed_payload is not None:
            payload = bytes(self._fixed_payload)
        else:
            seed = f"{key}:{time_s}:{width}x{height}:{request['encoding']}".encode()
            digest = hashlib.sha256(seed).digest()
            payload = digest + bytes((width % 256, height % 256)) + b"\x00RGB"
        frame = {
            "frame_id": f"{robot_id}/{request.get('member', 'primary')}/optical_frame",
            "capture_sim_time_s": float(time_s),
            "physics_step": int(self._physics_step_fn(float(time_s))),
            "pose": {
                "position_m": [float(time_s), 0.0, 0.5],
                "orientation_xyzw": [0.0, 0.0, 0.0, 1.0],
                "source": "synthetic",
            },
            "calibration": {
                "model": "pinhole",
                "resolution": [width, height],
                "limitations": ["fake backend; not hardware calibration"],
            },
            "payload": payload,
            "status": "fresh",
            "first_frame_latency_s": latency,
        }
        product["frames"] += 1
        self.captures.append({"sensor_key": key, "time_s": time_s})
        # Idle release is deferred to tick(); capture only marks the queue empty.
        self.lifecycle.mark_queue_activity(key, queue_depth=0, now_s=time_s)
        return frame

    def tick(self, time_s: float) -> None:
        # Idle releases remain allowed after logical close so cleanup can proceed.
        self.lifecycle.tick_idle_releases(time_s)

    def lifecycle_report(self) -> Mapping[str, Any]:
        return self.lifecycle.telemetry()

    def close(self, *, now_s: float = 0.0) -> None:
        """Mark logically closed and attempt owned-product cleanup (retryable)."""
        self._logical_closed = True
        self.lifecycle.close(now_s=now_s)


class OrsusOnDemandCameraBackend:
    """Benchmark-owned Orsus RGB capture with idle_destroy lifecycle."""

    def __init__(
        self,
        *,
        create_product: Callable[[str], Any],
        destroy_product: Callable[[Any], None],
        read_rgb: Callable[[Any, Mapping[str, Any], float], Mapping[str, Any]],
        idle_timeout_s: float = 0.5,
        warmup_frames: int = 2,
        continuous_ros_owners: Mapping[str, str] | None = None,
    ):
        self.lifecycle = OnDemandRenderLifecycle(
            create_product=create_product,
            destroy_product=destroy_product,
            idle_timeout_s=idle_timeout_s,
            warmup_frames=warmup_frames,
            owner_id="benchmark_orsus_ondemand",
        )
        self._read_rgb = read_rgb
        self._logical_closed = False
        for sensor_key, owner in dict(continuous_ros_owners or {}).items():
            self.lifecycle.register_external_owner(sensor_key, owner)
        self.lifecycle._telemetry["native_orsus_rtx_verified"] = False

    def capture(self, request: Mapping[str, Any], time_s: float) -> dict[str, Any]:
        if self._logical_closed:
            raise RuntimeError("camera backend is logically closed")
        key = f"{request['robot_id']}/{request['sensor_id']}"
        product = self.lifecycle.ensure(key, now_s=time_s)
        self.lifecycle.mark_queue_activity(key, queue_depth=1, now_s=time_s)
        try:
            while not self.lifecycle.warmup_tick(key, now_s=time_s):
                self._read_rgb(product, request, time_s)
            latency = self.lifecycle.note_first_frame(key, now_s=time_s)
            frame = dict(self._read_rgb(product, request, time_s))
            frame.setdefault("capture_sim_time_s", float(time_s))
            frame.setdefault("status", "fresh")
            frame["first_frame_latency_s"] = latency
            if frame.get("capture_sim_time_s") != float(time_s):
                raise ValueError("Orsus capture clock mismatch")
            return frame
        except Exception:
            try:
                self.lifecycle.force_release(key, now_s=time_s)
            except Exception:
                pass
            raise
        finally:
            self.lifecycle.mark_queue_activity(key, queue_depth=0, now_s=time_s)

    def tick(self, time_s: float) -> None:
        self.lifecycle.tick_idle_releases(time_s)

    def lifecycle_report(self) -> Mapping[str, Any]:
        return self.lifecycle.telemetry()

    def close(self, *, now_s: float = 0.0) -> None:
        self._logical_closed = True
        self.lifecycle.close(now_s=now_s)


__all__ = [
    "FakeOnDemandCameraBackend",
    "OnDemandRenderLifecycle",
    "OrsusOnDemandCameraBackend",
]
