"""Non-blocking Isaac Sim selection bridge for the browser map editor.

The bridge intentionally has no hard Isaac/Kit import at module import time.  It
can therefore be unit-tested with normal Python and imported by an Isaac Script
Editor session.  USD coordinates are converted to metres before they leave the
process; Y-up stages are rejected rather than silently rotated.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Iterable

from .web_overlay import WEB_OVERLAY_ROOT


class IsaacBridgeError(RuntimeError):
    """A context, USD, or transport contract error."""


def _finite_xyz(value: Any, name: str = "position_xyz") -> tuple[float, float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise IsaacBridgeError(f"{name} must contain three finite numbers")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise IsaacBridgeError(f"{name} must contain three finite numbers")
    return result


def _stage_axis_and_scale(stage: Any) -> tuple[str, float]:
    """Return USD stage up-axis and metres-per-unit, rejecting invalid metadata."""
    axis = None
    scale = None
    try:
        from pxr import UsdGeom
        axis = UsdGeom.GetStageUpAxis(stage)
        scale = UsdGeom.GetStageMetersPerUnit(stage)
    except (ImportError, AttributeError, RuntimeError, TypeError):
        pass
    if axis is None and hasattr(stage, "GetMetadata"):
        axis = stage.GetMetadata("upAxis")
    if scale is None and hasattr(stage, "GetMetadata"):
        scale = stage.GetMetadata("metersPerUnit")
    axis = str(axis or "Z").upper()
    if axis not in {"Z", "Y"}:
        raise IsaacBridgeError(f"unsupported USD up axis: {axis!r}")
    if axis != "Z":
        raise IsaacBridgeError("only Z-up Isaac stages are supported")
    try:
        scale = float(scale if scale is not None else 1.0)
    except (TypeError, ValueError):
        raise IsaacBridgeError("invalid USD metersPerUnit") from None
    if not math.isfinite(scale) or scale <= 0:
        raise IsaacBridgeError("invalid USD metersPerUnit")
    return axis, scale


def stage_source_usd(stage: Any) -> str | None:
    """Return the resolved root-layer file path for a USD stage, if available."""
    root = getattr(stage, "GetRootLayer", lambda: None)()
    if root is None:
        return None
    path = getattr(root, "realPath", None) or getattr(root, "identifier", None)
    if not path:
        return None
    return str(Path(path).expanduser().resolve(strict=False))


def assert_source_matches(stage: Any, expected_source_usd: str | None) -> str | None:
    actual = stage_source_usd(stage)
    if expected_source_usd:
        expected = str(Path(expected_source_usd).expanduser().resolve(strict=False))
        if actual != expected:
            raise IsaacBridgeError(
                f"Isaac stage source does not match map context: {actual!r} != {expected!r}"
            )
    return actual


def _bbox_for_prim(stage: Any, path: str, scale: float) -> tuple[list[float], list[float]]:
    prim = stage.GetPrimAtPath(path)
    if prim is None or not getattr(prim, "IsValid", lambda: False)():
        raise IsaacBridgeError(f"selected USD prim is invalid: {path}")
    try:
        from pxr import Usd, UsdGeom
        cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_])
        bound = cache.ComputeWorldBound(prim)
        rng = bound.ComputeAlignedBox()
        lo, hi = rng.GetMin(), rng.GetMax()
        minimum = [float(lo[i]) * scale for i in range(3)]
        maximum = [float(hi[i]) * scale for i in range(3)]
    except (ImportError, AttributeError, RuntimeError, TypeError, IndexError) as exc:
        raise IsaacBridgeError(f"unable to compute world bbox for {path}: {exc}") from exc
    if not all(math.isfinite(v) for v in (*minimum, *maximum)):
        raise IsaacBridgeError(f"selected USD prim has a non-finite bbox: {path}")
    if any(a > b for a, b in zip(minimum, maximum)):
        raise IsaacBridgeError(f"selected USD prim has an invalid bbox: {path}")
    return minimum, maximum


def prim_selection_payload(stage: Any, prim_paths: Iterable[str], *, source_usd: str | None = None,
                           session_id: str | None = None, sequence: int = 1) -> dict[str, Any]:
    """Build the server selection contract from exact USD prim paths."""
    _axis, scale = _stage_axis_and_scale(stage)
    actual_source = assert_source_matches(stage, source_usd)
    paths = [str(path) for path in prim_paths]
    if not paths or any(not path.startswith("/") or path == "/" for path in paths):
        raise IsaacBridgeError("prim_paths must contain absolute USD prim paths")
    boxes = [_bbox_for_prim(stage, path, scale) for path in paths]
    minimum = [min(box[0][axis] for box in boxes) for axis in range(3)]
    maximum = [max(box[1][axis] for box in boxes) for axis in range(3)]
    center = [(minimum[axis] + maximum[axis]) / 2.0 for axis in range(3)]
    return {
        "selection_kind": "prim", "prim_paths": paths,
        "position_xyz": center, "bbox_min_xyz": minimum, "bbox_max_xyz": maximum,
        "source_usd": actual_source or source_usd, "up_axis": "Z",
        "timestamp": time.time(), "session_id": session_id or str(uuid.uuid4()),
        "sequence": int(sequence),
    }


def point_selection_payload(stage: Any, position_xyz: Iterable[float], *, prim_path: str | None = None,
                            source_usd: str | None = None, session_id: str | None = None,
                            sequence: int = 1) -> dict[str, Any]:
    """Build a point-hit selection in metres, preserving the exact world hit."""
    _axis, scale = _stage_axis_and_scale(stage)
    actual_source = assert_source_matches(stage, source_usd)
    point = _finite_xyz(position_xyz)
    paths = [] if prim_path is None else [str(prim_path)]
    if any(not path.startswith("/") or path == "/" for path in paths):
        raise IsaacBridgeError("prim_path must be an absolute USD prim path")
    point_m = [value * scale for value in point]
    result = {
        "selection_kind": "point", "prim_paths": paths,
        "position_xyz": point_m, "source_usd": actual_source or source_usd,
        "up_axis": "Z", "timestamp": time.time(),
        "session_id": session_id or str(uuid.uuid4()), "sequence": int(sequence),
    }
    if prim_path is not None:
        result["bbox_min_xyz"], result["bbox_max_xyz"] = _bbox_for_prim(stage, prim_path, scale)
    return result


@dataclass(frozen=True)
class BridgeStatus:
    state: str
    scene_key: str | None
    source_usd: str | None
    pending: bool
    sequence: int
    session_id: str
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state, "scene_key": self.scene_key,
            "source_usd": self.source_usd, "pending": self.pending,
            "sequence": self.sequence, "session_id": self.session_id,
            "last_error": self.last_error,
        }


class IsaacSelectionBridge:
    """Threaded selection transport; all Kit/USD callbacks stay on the main thread."""

    def __init__(self, server_url: str = "http://127.0.0.1:8765", scene_key: str | None = None,
                 *, timeout: float = 2.0, opener: Callable[..., Any] | None = None):
        self.server_url = server_url.rstrip("/")
        self.request_timeout = float(timeout)
        if not math.isfinite(self.request_timeout) or self.request_timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.request_opener = opener or urllib.request.urlopen
        self.requested_scene_key = scene_key
        self.scene_key: str | None = scene_key
        self.source_usd: str | None = None
        self.csrf_token: str | None = None
        self.session_id = str(uuid.uuid4())
        self._sequence = 0
        self._pending: deque[dict[str, Any]] = deque(maxlen=1)
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._reconnect = False
        self._blocked = False
        self._worker: threading.Thread | None = None
        self._state = "stopped"
        self._last_error: str | None = None
        self._selection = None
        self._selection_context = None
        self._selection_subscription = None
        self._selection_event_type: int | None = None
        self._stage_clear_event_types: set[int] = set()
        self._stage_open_event_type: int | None = None
        self._point_registration = None
        self._point_enabled = False

    def start(self) -> "IsaacSelectionBridge":
        with self._condition:
            if self._worker and self._worker.is_alive():
                return self
            self._stop.clear()
            self._reconnect = True
            self._blocked = False
            self._state = "connecting"
            self._worker = threading.Thread(target=self._run, name="isaac-selection-bridge", daemon=True)
            self._worker.start()
        return self

    connect = start

    def stop(self) -> None:
        self.disable_point_picking()
        self.unbind_selection()
        with self._condition:
            self._stop.set()
            self._condition.notify_all()
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(1.0, self.request_timeout + 0.5))
        with self._condition:
            self._state = "stopped"
            self._worker = None
            self._pending.clear()

    def reconnect(self) -> None:
        with self._condition:
            self.csrf_token = None
            self._pending.clear()
            self._last_error = None
            self._reconnect = True
            self._blocked = False
            self._state = "connecting"
            self._condition.notify_all()
        if not self._worker or not self._worker.is_alive():
            self.start()

    def status(self) -> dict[str, Any]:
        with self._condition:
            return BridgeStatus(self._state, self.scene_key, self.source_usd,
                                bool(self._pending), self._sequence, self.session_id,
                                self._last_error).to_dict()

    def _url(self, path: str) -> str:
        return self.server_url + path

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(self._url(path), data=data, method=method,
                                         headers={"Accept": "application/json",
                                                  "Content-Type": "application/json"})
        try:
            with self.request_opener(request, timeout=self.request_timeout) as response:
                raw = response.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise IsaacBridgeError(f"Isaac bridge transport failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IsaacBridgeError("Isaac bridge returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise IsaacBridgeError("Isaac bridge returned a non-object JSON payload")
        return payload

    def _load_context(self) -> None:
        context = self._request("GET", "/api/isaac/context")
        scene = context.get("scene_key")
        if not isinstance(scene, str) or not scene:
            raise IsaacBridgeError("Isaac context has no scene_key")
        if self.requested_scene_key is not None and scene != self.requested_scene_key:
            raise IsaacBridgeError(f"Isaac scene_key mismatch: {scene!r} != {self.requested_scene_key!r}")
        token = context.get("csrf_token")
        if not isinstance(token, str) or not token:
            raise IsaacBridgeError("Isaac context has no csrf_token")
        with self._condition:
            self.scene_key, self.source_usd, self.csrf_token = scene, context.get("source_usd"), token

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._condition:
                    while self._blocked and not self._stop.is_set() and not self._reconnect:
                        self._condition.wait(timeout=0.25)
                    if self._stop.is_set():
                        break
                    reconnect = self._reconnect or self.csrf_token is None
                    self._reconnect = False
                if reconnect:
                    self._load_context()
                    with self._condition:
                        self._state = "connected"
                        self._last_error = None
                with self._condition:
                    while not self._pending and not self._stop.is_set() and not self._reconnect:
                        self._condition.wait(timeout=0.25)
                    if self._stop.is_set():
                        break
                    if self._reconnect:
                        continue
                    payload = self._pending.pop()
                    token = self.csrf_token
                payload = dict(payload, csrf_token=token, scene_key=self.scene_key)
                response = self._request("POST", "/api/isaac/selection", payload)
                if "error" in response:
                    raise IsaacBridgeError(str(response["error"]))
            except IsaacBridgeError as exc:
                with self._condition:
                    self._state, self._last_error = "error", str(exc)
                    # A failed context or publish is never silently remapped or
                    # retried against a potentially different scene.  The user
                    # must explicitly press reconnect.
                    self._blocked = True
                    self._condition.notify_all()

    def _enqueue(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            if not self._worker or not self._worker.is_alive():
                raise IsaacBridgeError("Isaac selection bridge is not connected")
            self._sequence += 1
            payload = dict(payload, session_id=self.session_id, sequence=self._sequence)
            self._pending.clear()
            self._pending.append(payload)
            self._condition.notify_all()
        return payload

    def send_clear(self) -> dict[str, Any]:
        return self._enqueue({"selection_kind": "clear", "prim_paths": [],
                              "position_xyz": [0.0, 0.0, 0.0], "up_axis": "Z",
                              "source_usd": self.source_usd, "timestamp": time.time()})

    def send_prim_selection(self, stage: Any, prim_paths: Iterable[str]) -> dict[str, Any]:
        # Web overlay prims are browser-map visualization, never scene assets.
        prim_paths = [path for path in prim_paths
                      if not str(path).startswith(WEB_OVERLAY_ROOT)]
        return self._enqueue(prim_selection_payload(stage, prim_paths, source_usd=self.source_usd,
                                                     session_id=self.session_id, sequence=self._sequence + 1))

    def send_point_selection(self, stage: Any, position_xyz: Iterable[float],
                             prim_path: str | None = None) -> dict[str, Any]:
        return self._enqueue(point_selection_payload(stage, position_xyz, prim_path=prim_path,
                                                      source_usd=self.source_usd,
                                                      session_id=self.session_id,
                                                      sequence=self._sequence + 1))

    def bind_selection(self, stage: Any = None, selection: Any = None, context: Any = None) -> None:
        try:
            import omni.usd
            context = context or omni.usd.get_context()
            selection = selection or context.get_selection()
            stage = stage or context.get_stage()
            event_type = int(omni.usd.StageEventType.SELECTION_CHANGED)
            stream = context.get_stage_event_stream()
            subscribe = getattr(stream, "create_subscription_to_pop", None)
            if not callable(subscribe):
                raise AttributeError("stage event stream has no subscription API")
        except (ImportError, AttributeError, RuntimeError, TypeError) as exc:
            raise IsaacBridgeError(f"Isaac selection event API is unavailable: {exc}") from exc
        self.unbind_selection()
        self._selection, self._selection_stage = selection, stage
        self._selection_context = context
        self._selection_event_type = event_type
        self._stage_clear_event_types = {int(getattr(omni.usd.StageEventType, name))
                                         for name in ("CLOSING", "CLOSED")
                                         if hasattr(omni.usd.StageEventType, name)}
        opened = getattr(omni.usd.StageEventType, "OPENED", None)
        self._stage_open_event_type = int(opened) if opened is not None else None
        self._selection_subscription = subscribe(
            self._on_stage_event, name="eai.semantic_map.selection_bridge"
        )
        self.send_current_selection()

    def unbind_selection(self) -> None:
        self._selection = None
        self._selection_stage = None
        self._selection_context = None
        # carb.events subscriptions unsubscribe when the handle is released.
        self._selection_subscription = None
        self._selection_event_type = None
        self._stage_clear_event_types = set()
        self._stage_open_event_type = None
        self._selection_stage = None

    def _on_stage_event(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        if kind not in self._stage_clear_event_types and kind not in (self._selection_event_type, self._stage_open_event_type):
            return
        try:
            if kind in self._stage_clear_event_types:
                self.send_clear()
            elif kind == self._selection_event_type and self._point_enabled:
                # Kit also selects the hit prim. Its bounding-box snapshot must
                # never replace the exact surface hit, regardless of event order.
                return
            else:
                self.send_current_selection()
        except IsaacBridgeError as exc:
            with self._condition:
                self._last_error = str(exc)

    def send_current_selection(self) -> dict[str, Any]:
        if self._selection is None:
            raise IsaacBridgeError("Isaac selection is not bound")
        stage = self._bound_stage()
        if stage is None:
            return self.send_clear()
        selection = self._selection_context.get_selection() if self._selection_context else self._selection
        getter = getattr(selection, "get_selected_prim_paths", None)
        # Kit selects clicked overlay prims too; they must never reach the web
        # editor as assets.  An overlay-only selection degrades to a clear.
        paths = [path for path in (getter() if callable(getter) else ())
                 if not str(path).startswith(WEB_OVERLAY_ROOT)]
        if not paths:
            return self.send_clear()
        return self.send_prim_selection(stage, paths)

    def _bound_stage(self) -> Any:
        stage = self._selection_context.get_stage() if self._selection_context else self._current_stage()
        if stage is not None:
            try:
                assert_source_matches(stage, self.source_usd)
            except IsaacBridgeError:
                self.send_clear()
                raise
        return stage

    def enable_point_picking(self, enabled: bool = True) -> bool:
        enabled = bool(enabled)
        if enabled == self._point_enabled:
            return enabled
        if enabled:
            try:
                from omni.kit.viewport.registry import RegisterScene
                from .viewport import _TopologyViewportScene
                self._point_registration = RegisterScene(
                    lambda desc: _TopologyViewportScene(desc, self._on_point_hit, None),
                    "eai.topology_authoring.isaac_bridge",
                )
            except (ImportError, AttributeError, RuntimeError, TypeError) as exc:
                raise IsaacBridgeError(f"Isaac viewport point picking is unavailable: {exc}") from exc
        else:
            registration, self._point_registration = self._point_registration, None
            if registration is not None:
                registration.destroy()
        self._point_enabled = enabled
        return enabled

    def disable_point_picking(self) -> bool:
        if not self._point_enabled:
            return False
        return self.enable_point_picking(False)

    def _on_point_hit(self, prim_path: Any, world_position: Any, *_args: Any) -> None:
        """Consume Kit raycast callbacks: (prim_path, world_position)."""
        if not self._point_enabled:
            return
        # Overlay prims visualize the browser map; treat them as a miss.
        if str(prim_path or "").startswith(WEB_OVERLAY_ROOT):
            return
        # A miss is not a point at the world origin. Kit may supply (0,0,0)
        # alongside an empty path for a miss.
        if not prim_path or world_position is None:
            return
        try:
            point = tuple(float(world_position[i]) for i in range(3))
            _finite_xyz(point)
            stage = self._bound_stage()
            if stage is None:
                self.send_clear()
                return
            self.send_point_selection(stage, point, str(prim_path))
        except (IsaacBridgeError, IndexError, TypeError, ValueError) as exc:
            with self._condition:
                self._last_error = str(exc)

    @staticmethod
    def _current_stage() -> Any:
        import omni.usd
        return omni.usd.get_context().get_stage()


class IsaacSelectionWindow:
    """Small optional Kit window; methods are useful even when UI is unavailable."""

    def __init__(self, bridge: IsaacSelectionBridge, title: str = "Isaac Semantic Map Bridge"):
        self.bridge, self.title, self._window = bridge, title, None

    def connect(self):
        self.bridge.connect()

    def reconnect(self):
        self.bridge.reconnect()

    def status(self):
        return self.bridge.status()

    def send_current_selection(self):
        return self.bridge.send_current_selection()

    def enable_point_picking(self, enabled: bool = True):
        return self.bridge.enable_point_picking(enabled)

    def show(self):
        try:
            import omni.ui as ui
        except ImportError as exc:
            raise IsaacBridgeError("omni.ui is unavailable") from exc
        self._window = ui.Window(self.title, width=380, height=230)
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                ui.Label("Isaac Surface Point -> Semantic Map Preview")
                mode_label = ui.Label("Mode: Surface Point" if self.bridge._point_enabled else "Mode: Asset Selection")
                def set_mode(enabled):
                    try:
                        self.bridge.enable_point_picking(enabled)
                        mode_label.text = "Mode: Surface Point" if enabled else "Mode: Asset Selection"
                    except IsaacBridgeError as exc:
                        mode_label.text = str(exc)
                ui.Button("Connect / Reconnect", clicked_fn=lambda: self.bridge.reconnect())
                ui.Button("Surface Point Mode", clicked_fn=lambda: set_mode(True))
                ui.Button("Asset Selection Mode", clicked_fn=lambda: set_mode(False))
                ui.Button("Send Asset Selection", clicked_fn=lambda: (set_mode(False), self.bridge.send_current_selection()))
                ui.Label("Surface mode: left-click a visible surface.")
                ui.Label("Then use the web editor to add a node or semantic.")
        return self._window

    def destroy(self):
        self.bridge.stop()
        if self._window is not None:
            self._window.destroy()
            self._window = None


__all__ = [
    "IsaacBridgeError", "IsaacSelectionBridge", "IsaacSelectionWindow",
    "BridgeStatus", "stage_source_usd", "assert_source_matches",
    "prim_selection_payload", "point_selection_payload",
]
