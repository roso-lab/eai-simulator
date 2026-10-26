"""Builder/USD default-camera adapter.

This adapter owns no sensor discovery.  A trusted builder supplies the
already registered camera binding (robot, prim, publication and calibration),
and the adapter creates a render product/annotator only when capture is
requested.  The callbacks keep Isaac imports out of CPU tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class BuilderCameraBinding:
    robot_id: str
    sensor_id: str
    camera_prim: str
    publication: Mapping[str, Any]
    calibration: Mapping[str, Any] = None  # type: ignore[assignment]
    builder_registered_default: bool = True

    def __post_init__(self) -> None:
        for name in ("robot_id", "sensor_id", "camera_prim"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if not isinstance(self.publication, Mapping) or not self.publication:
            raise ValueError("publication must be a non-empty mapping")
        if self.calibration is None:
            object.__setattr__(self, "calibration", {})
        if not isinstance(self.calibration, Mapping):
            raise ValueError("calibration must be a mapping")
        if type(self.builder_registered_default) is not bool or not self.builder_registered_default:
            raise ValueError("only builder_registered_default cameras are admissible")


class BuilderCameraBackend:
    """Protocol bridge for a builder-configured camera.

    ``create_render_product`` receives a binding and requested dimensions and
    returns an opaque product that must carry ``width``/``height``/
    ``encoding`` entries describing what it was built for;
    ``destroy_render_product`` disposes it. ``read_annotator`` returns encoded
    bytes plus optional pose/calibration metadata. Both callbacks execute on
    the owning simulator thread.
    """
    def __init__(self, bindings: Mapping[tuple[str, str], BuilderCameraBinding], *,
                 create_render_product: Callable[[BuilderCameraBinding, int, int, str], Any],
                 destroy_render_product: Callable[[Any], None],
                 read_annotator: Callable[[Any, BuilderCameraBinding, Mapping[str, Any], float], Mapping[str, Any]],
                 physics_step: Callable[[float], int] | None = None):
        if not isinstance(bindings, Mapping) or not bindings:
            raise ValueError("builder bindings are required")
        if not all(isinstance(k, tuple) and len(k) == 2 and isinstance(v, BuilderCameraBinding)
                   for k, v in bindings.items()):
            raise ValueError("bindings must map (robot_id, sensor_id) to BuilderCameraBinding")
        for key, binding in bindings.items():
            if key != (binding.robot_id, binding.sensor_id):
                raise ValueError("binding key does not match camera identity")
        for callback in (create_render_product, destroy_render_product, read_annotator):
            if not callable(callback):
                raise ValueError("builder backend callbacks are required")
        self.bindings = dict(bindings)
        self._create = create_render_product
        self._destroy = destroy_render_product
        self._read = read_annotator
        self._physics_step = physics_step or (lambda t: int(t * 100))
        self._products: dict[tuple[str, str], Any] = {}
        self._lifecycle = {"create_count": 0, "destroy_count": 0, "native_orsus_rtx_verified": False,
                           "cleanup_pending": False, "cleanup_complete": True, "failures": []}
        self._closed = False

    def capture(self, request: Mapping[str, Any], time_s: float) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("builder camera backend is closed")
        key = (request["robot_id"], request["sensor_id"])
        binding = self.bindings.get(key)
        if binding is None:
            raise ValueError("camera is absent from builder registration")
        product = self._products.get(key)
        if product is None:
            product = self._create(binding, int(request["width"]), int(request["height"]), request["encoding"])
            self._products[key] = product
            self._lifecycle["create_count"] += 1
        else:
            # A live render product is bound to its creation dimensions and
            # encoding; serving a different request off it would fabricate
            # pixels.  Fail closed instead of silently reusing or recreating.
            if (int(request["width"]), int(request["height"]), request["encoding"]) != (
                    product.get("width"), product.get("height"), product.get("encoding")):
                raise ValueError(
                    "live render product dimensions/encoding do not match the request; "
                    "close the sensor before requesting a different resolution")
        data = dict(self._read(product, binding, request, float(time_s)))
        payload = data.pop("payload", None)
        if not isinstance(payload, (bytes, bytearray)):
            raise ValueError("builder annotator must return bytes payload")
        capture_time = data.pop("capture_sim_time_s", float(time_s))
        if capture_time != float(time_s):
            raise ValueError("builder camera capture clock mismatch")
        return {"frame_id": data.pop("frame_id", binding.camera_prim),
                "capture_sim_time_s": capture_time,
                "physics_step": data.pop("physics_step", self._physics_step(time_s)),
                "pose": data.pop("pose", {"source": "physx", "camera_prim": binding.camera_prim}),
                "calibration": data.pop("calibration", dict(binding.calibration)),
                "payload": bytes(payload), "status": data.pop("status", "fresh"), **data}

    def tick(self, time_s: float) -> None:
        return None

    def close(self, *, now_s: float = 0.0) -> None:
        # A failed destroy leaves the product owned and cleanup_pending.  Keep
        # close retryable until every owned product is actually released.
        if self._closed and not self._products:
            return
        self._closed = True
        for key, product in list(self._products.items()):
            try:
                self._destroy(product)
                self._lifecycle["destroy_count"] += 1
                self._products.pop(key, None)
            except Exception as exc:
                self._lifecycle["failures"].append({"sensor_key": f"{key[0]}/{key[1]}", "phase": "destroy", "error": str(exc), "time_s": now_s})
        self._lifecycle["cleanup_pending"] = bool(self._products)
        self._lifecycle["cleanup_complete"] = not self._products

    def lifecycle_report(self) -> Mapping[str, Any]:
        return dict(self._lifecycle)


__all__ = ["BuilderCameraBackend", "BuilderCameraBinding"]
