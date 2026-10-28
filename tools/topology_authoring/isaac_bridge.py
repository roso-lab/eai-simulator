"""Non-blocking Isaac Sim selection bridge for the browser map editor.

The bridge intentionally has no hard Isaac/Kit import at module import time.  It
can therefore be unit-tested with normal Python and imported by an Isaac Script
Editor session.  USD coordinates are converted to metres before they leave the
process; Y-up stages are rejected rather than silently rotated.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
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
        self._context_identity: dict[str, Any] = {}
        self.session_id = str(uuid.uuid4())
        self._sequence = 0
        self._pending: deque[dict[str, Any]] = deque(maxlen=1)
        self._inflight_selection: dict[str, Any] | None = None
        self._last_acknowledged_selection: dict[str, Any] | None = None
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
        # Native Prim selection is only meaningful during explicit semantic
        # point binding.  The integrated topology editor disables it so Kit's
        # selection events cannot race or overwrite topology selection.
        self._asset_selection_enabled = True

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
            self._inflight_selection = None
            self._last_acknowledged_selection = None

    def reconnect(self) -> None:
        with self._condition:
            self.csrf_token = None
            self._pending.clear()
            self._inflight_selection = None
            self._last_acknowledged_selection = None
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
                                bool(self._pending or self._inflight_selection),
                                self._sequence, self.session_id,
                                self._last_error).to_dict()

    def acknowledged_selection_cursor(self) -> dict[str, Any] | None:
        """Return the idle, server-confirmed single-Prim selection cursor."""
        with self._condition:
            if (self._state != "connected" or self._pending
                    or self._inflight_selection is not None):
                return None
            selected = self._last_acknowledged_selection
            if not isinstance(selected, dict) or selected.get("scene_key") != self.scene_key:
                return None
            if (selected.get("session_id") != self.session_id
                    or (self.source_usd is not None
                        and selected.get("source_usd") != self.source_usd)):
                return None
            for key in ("scene_generation", "context_generation"):
                expected = self._context_identity.get(key)
                if expected is not None and selected.get(key) != expected:
                    return None
            if (selected.get("selection_kind") != "prim"
                    or len(selected.get("prim_paths") or []) != 1):
                return None
            return json.loads(json.dumps(selected))

    def update_context(self, context: dict[str, Any]) -> None:
        """Refresh the identity captured with the next main-thread selection."""
        if not isinstance(context, dict):
            return
        scene = context.get("scene_key")
        if self.requested_scene_key is not None and scene != self.requested_scene_key:
            return
        with self._condition:
            if isinstance(scene, str) and scene:
                self.scene_key = scene
            source = context.get("source_usd")
            if isinstance(source, str) and source:
                self.source_usd = source
            self._context_identity = {
                key: context.get(key) for key in (
                    "scene_generation", "context_generation", "active_domain",
                    "active_layer")}

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
        except urllib.error.HTTPError as exc:
            try:
                error_raw = exc.read()
                error_payload = json.loads(error_raw.decode("utf-8"))
                message = (error_payload.get("error") or error_payload.get("message")
                           if isinstance(error_payload, dict) else None)
            except (OSError, ValueError, UnicodeDecodeError):
                message = None
            error = IsaacBridgeError(message or f"Isaac bridge HTTP {exc.code}")
            error.status = int(exc.code)
            raise error from exc
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
            self._context_identity = {
                key: context.get(key) for key in (
                    "scene_generation", "context_generation", "active_domain",
                    "active_layer")}

    def _run(self) -> None:
        while not self._stop.is_set():
            posting_selection = False
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
                    payload = dict(payload, csrf_token=token)
                    self._inflight_selection = payload
                posting_selection = True
                response = self._request("POST", "/api/isaac/selection", payload)
                if "error" in response:
                    raise IsaacBridgeError(str(response["error"]))
                acknowledged = response.get("selection")
                if not isinstance(acknowledged, dict):
                    acknowledged = payload
                with self._condition:
                    self._last_acknowledged_selection = json.loads(
                        json.dumps(acknowledged))
                    self._inflight_selection = None
                    self._condition.notify_all()
            except IsaacBridgeError as exc:
                if posting_selection and getattr(exc, "status", None) == 409:
                    # A context switch can race with the old selection already
                    # dequeued by this worker. The server rejects that one-shot
                    # cursor; discard it and keep the bridge available for the
                    # next selection carrying the updated native identity.
                    with self._condition:
                        self._state = "connected"
                        self._last_error = None
                        self._blocked = False
                        self._inflight_selection = None
                        self._condition.notify_all()
                    continue
                with self._condition:
                    self._state, self._last_error = "error", str(exc)
                    self._inflight_selection = None
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
            identity = {key: value for key, value in self._context_identity.items()
                        if value is not None}
            payload = dict(payload, session_id=self.session_id, sequence=self._sequence,
                           scene_key=self.scene_key, **identity)
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
            elif kind == self._selection_event_type and (
                    self._point_enabled or not self._asset_selection_enabled):
                # Kit also selects the hit prim. Its bounding-box snapshot must
                # never replace the exact surface hit, regardless of event order.
                return
            else:
                self.send_current_selection()
        except IsaacBridgeError as exc:
            with self._condition:
                self._last_error = str(exc)

    def set_asset_selection_enabled(self, enabled: bool) -> bool:
        """Allow native USD Prim selection only for binding workflows."""
        enabled = bool(enabled)
        if enabled == self._asset_selection_enabled:
            return enabled
        self._asset_selection_enabled = enabled
        if not enabled:
            # Invalidate any previously bindable cursor immediately. This is
            # local state as well as a server clear, so a stale Prim cannot be
            # bound after returning to topology editing.
            with self._condition:
                self._last_acknowledged_selection = None
            try:
                self.send_clear()
            except IsaacBridgeError:
                pass
        return enabled

    def send_current_selection(self) -> dict[str, Any]:
        if self._selection is None:
            raise IsaacBridgeError("Isaac selection is not bound")
        if not self._asset_selection_enabled:
            return self.send_clear()
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
        try:
            return self.send_prim_selection(stage, paths)
        except IsaacBridgeError:
            # A deleted/invalid prim or stale overlay hit must invalidate the
            # authoritative cursor rather than leave the previous object bindable.
            return self.send_clear()

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


class IsaacAuthoringClient:
    """Revision/context guarded HTTP client used by the native Isaac editor."""

    def __init__(self, server_url: str = "http://127.0.0.1:8765", *,
                 timeout: float = 2.0, opener: Callable[..., Any] | None = None):
        self.server_url = server_url.rstrip("/")
        self.timeout = float(timeout)
        self.opener = opener or urllib.request.urlopen
        self.context: dict[str, Any] | None = None

    def update_context(self, context: dict[str, Any]) -> None:
        required = ("revision", "csrf_token", "scene_key", "scene_generation",
                    "context_generation", "active_domain")
        if not isinstance(context, dict) or any(key not in context for key in required):
            raise IsaacBridgeError("Isaac authoring context is incomplete")
        self.context = {key: context[key] for key in required}
        self.context["active_layer"] = context.get(
            "active_layer", context["active_domain"])

    def identity(self) -> dict[str, Any]:
        if self.context is None:
            raise IsaacBridgeError("Isaac authoring context is not loaded")
        identity = {
            "revision": self.context["revision"],
            "csrf_token": self.context["csrf_token"],
            "scene_key": self.context["scene_key"],
            "scene_generation": self.context["scene_generation"],
            "context_generation": self.context["context_generation"],
            "domain": self.context["active_domain"],
        }
        if self.context.get("active_layer") is not None:
            identity["active_layer"] = self.context["active_layer"]
        return identity

    def request(self, path: str, extra: dict[str, Any] | None = None, *,
                identity: dict[str, Any] | None = None) -> dict[str, Any]:
        body = {**(identity or self.identity()), **(extra or {})}
        request = urllib.request.Request(
            self.server_url + path,
            data=json.dumps(body, allow_nan=False).encode("utf-8"),
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                message = payload.get("error") if isinstance(payload, dict) else None
            except (ValueError, UnicodeDecodeError):
                message = None
            error = IsaacBridgeError(message or f"Isaac editor HTTP {exc.code}")
            error.status = exc.code
            raise error from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise IsaacBridgeError(f"Isaac editor transport failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IsaacBridgeError("Isaac editor returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise IsaacBridgeError("Isaac editor returned a non-object JSON payload")
        if "error" in payload:
            raise IsaacBridgeError(str(payload["error"]))
        return payload

    def edit(self, operation: str, _identity: dict[str, Any] | None = None,
             **args: Any) -> dict[str, Any]:
        return self.request("/api/edit", {"operation": operation, **args},
                            identity=_identity)

    def set_context(self, *, _identity: dict[str, Any] | None = None,
                    **fields: Any) -> dict[str, Any]:
        """Update editor context using the contract's public field names.

        The legacy domain/tool/level_id aliases remain accepted by the server;
        native callers use active_layer/editor_tool/active_level directly.
        """
        return self.request("/api/editor/context", fields, identity=_identity)

    def select(self, selection: dict[str, Any] | None, *,
               _identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("/api/web/selection", {"selection": selection},
                            identity=_identity)

    def undo(self, *, _identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("/api/undo", identity=_identity)

    def redo(self, *, _identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("/api/redo", identity=_identity)

    def save(self, *, _identity: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("/api/save", identity=_identity)


class IsaacEditDispatcher:
    """Single-flight editor writes; callbacks are drained by the Kit main thread."""

    def __init__(self, client: IsaacAuthoringClient):
        self.client = client
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="isaac-topology-edit")
        self._future: Future | None = None
        self._tag: Any = None

    def _start(self, tag, method, args, kwargs):
        self._tag = tag
        self._future = self._executor.submit(getattr(self.client, method), *args, **kwargs)

    @property
    def pending(self) -> bool:
        # A completed worker result still owns the single-flight slot until
        # the Kit main thread drains and applies it to controller state.
        return self._future is not None

    def submit(self, tag: Any, method: str, *args: Any, **kwargs: Any) -> bool:
        if self._future is not None:
            return False
        self._start(tag, method, args, kwargs)
        return True

    def drain(self):
        future = self._future
        if future is None or not future.done():
            return None
        tag, self._tag, self._future = self._tag, None, None
        try:
            result = (tag, future.result(), None)
        except Exception as exc:
            result = (tag, None, exc)
        return result

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._future = None
        self._tag = None


def _node_selection(identifier: str) -> dict[str, Any]:
    return {"kind": "node", "collection": None, "id": identifier}


def _edge_selection(identifier: str) -> dict[str, Any]:
    return {"kind": "edge", "collection": None, "id": identifier}


_PRESERVE_LEVEL = object()


class IsaacTopologyController:
    """Unified native editor for navigation graphs and semantic points."""

    TOOLS = ("add-node", "add-edge", "add-semantic-point", "build-ground-road", "delete-ground-edge",
             "build-aerial-road", "delete-aerial-edge")
    LAYERS = ("ground", "aerial", "semantic_points")
    MODES = ("2.5d", "3d")

    def __init__(self, dispatcher: IsaacEditDispatcher):
        self.dispatcher = dispatcher
        self.context: dict[str, Any] | None = None
        self.snapshot: dict[str, Any] = {}
        self.document_snapshots: dict[str, dict[str, Any]] = {}
        self.semantic_points: list[dict[str, Any]] = []
        self.selection: dict[str, Any] | None = None
        self.selection_items: list[dict[str, Any]] = []
        # Screen-space box selection is transient; the viewport converts it
        # to world points for the session overlay while the pointer is held.
        self.box_preview: list[list[float]] | None = None
        self.edge_start: str | None = None
        self.edge_end: str | None = None
        self.edge_identity: dict[str, Any] | None = None
        self.gesture: dict[str, Any] | None = None
        self.road_draft: dict[str, Any] | None = None
        self.road_free_snap = False
        self._road_last_point: list[float] | None = None
        self._road_end_z: float | None = None
        self.preview: dict[str, Any] | None = None
        self.directed = False
        self.new_edge_traversal_mode = "walk"
        self.new_point_settings = {
            "ground": {"z": 0.0},
            "aerial": {"z": 1.5, "edit_mode": "3d"},
            "semantic_points": {"z": 1.5, "edit_mode": "3d"},
        }
        self.retry_request: dict[str, Any] | None = None
        self.pending_edit: dict[str, Any] | None = None
        self.pending_context: dict[str, Any] | None = None
        self._active_edit: dict[str, Any] | None = None
        self.result_serial = 0
        self.last_result_tag: Any = None
        self.last_result_succeeded = False
        self.selection_dirty = False
        self.navigation_binding_mode = False
        self.navigation_binding_target_id: str | None = None
        self.navigation_binding_draft: list[dict[str, str]] | None = None
        self.last_error: str | None = None
        self.edit_status = "idle"
        self.on_change: Callable[[], None] | None = None
        self.interaction_generation = 0
        self.meters_per_unit = 1.0

    @property
    def active_layer(self) -> str:
        context = self.context or {}
        return context.get("active_layer", context.get("active_domain", "ground"))

    @property
    def active_domain(self) -> str:
        return (self.context or {}).get("active_domain", "ground")

    def update_context(self, context: dict[str, Any]) -> None:
        previous = None if self.context is None else (
            self.context.get("scene_key"), self.context.get("scene_generation"),
            self.context.get("context_generation"), self.context.get("csrf_token"),
            self.active_layer, self.context.get("active_level"),
            self.context.get("reference_level"),
        )
        next_layer = context.get("active_layer", context.get("active_domain", "ground"))
        current = (
            context.get("scene_key"), context.get("scene_generation"),
            context.get("context_generation"), context.get("csrf_token"),
            next_layer, context.get("active_level"), context.get("reference_level"),
        )
        if previous is not None and previous != current:
            same_scene = previous[:2] == current[:2]
            preserve_edge = (
                self.edge_start is not None
                and same_scene
                and previous[4:] == current[4:]
                and previous[4] in {"ground", "aerial"}
                and context.get("editor_tool") == "add-edge"
            )
            edge_state = (self.edge_start, self.edge_end, self.edge_identity,
                          json.loads(json.dumps(self.preview))
                          if self.preview and self.preview.get("kind") == "edge-draft"
                          else None)
            preserve_local_selection = bool(
                self.selection_dirty and same_scene and previous[4] == current[4])
            self.cancel_gesture(preserve_retry=same_scene)
            if preserve_edge:
                self.edge_start, self.edge_end, self.edge_identity, preview = edge_state
                if self.node(self.edge_start) is None or (
                        self.edge_end is not None and self.node(self.edge_end) is None):
                    self.edge_start = self.edge_end = self.edge_identity = None
                else:
                    self.preview = preview
            same_binding_context = same_scene and previous[4:6] == current[4:6]
            if not same_binding_context:
                self.navigation_binding_mode = False
                self.navigation_binding_target_id = None
                self.navigation_binding_draft = None
            self.pending_edit = None
            self.pending_context = None
            self.selection_dirty = preserve_local_selection
        self.context = context
        self.document_snapshots = dict(
            context.get("document_snapshots") or context.get("documents") or {})
        if not self.document_snapshots:
            active = context.get("active_domain", "ground")
            self.document_snapshots[active] = (
                context.get("document_snapshot") or context.get("document") or {})
        nav_layer = next_layer if next_layer in {"ground", "aerial"} else self.active_domain
        self.snapshot = (self.document_snapshots.get(nav_layer)
                         or context.get("document_snapshot")
                         or context.get("document") or {})
        self.semantic_points = json.loads(json.dumps(context.get("semantic_points") or []))
        context_binding_mode = context.get("binding_target_mode") == "semantic_navigation"
        context_binding_id = context.get("binding_target_id")
        if context_binding_mode and context_binding_id:
            self.navigation_binding_mode = True
            self.navigation_binding_target_id = context_binding_id
            self.navigation_binding_draft = json.loads(json.dumps(
                context.get("binding_target_draft") or []))
        else:
            self.navigation_binding_mode = False
        supplied_settings = context.get("new_point_settings") or {}
        # Older contexts do not publish ground settings. Fall back to the
        # floor height on initial connection and floor/scene switches.
        if previous is None or previous[:2] != current[:2] or previous[5] != current[5]:
            self.new_point_settings["ground"] = {"z": next(
                (float(row["support_z_m"]) for row in context.get("levels") or []
                 if row.get("id") == context.get("active_level")), 0.0)}
        ground_z = (supplied_settings.get("ground") or {}).get("z")
        if type(ground_z) in (int, float) and math.isfinite(ground_z):
            self.new_point_settings["ground"] = {"z": float(ground_z)}
        for layer in ("aerial", "semantic_points"):
            values = supplied_settings.get(layer) or {}
            try:
                z = float(values.get("z", self.new_point_settings[layer]["z"]))
            except (TypeError, ValueError):
                z = self.new_point_settings[layer]["z"]
            mode = values.get("edit_mode", self.new_point_settings[layer]["edit_mode"])
            if math.isfinite(z) and mode in self.MODES:
                self.new_point_settings[layer] = {"z": z, "edit_mode": mode}
        if self.active_layer != "ground":
            self.new_edge_traversal_mode = "walk"
        self.dispatcher.client.update_context(context)
        incoming = context.get("web_selection")
        # A native click/drag marks the local selection dirty before the
        # browser/authority has acknowledged it.  An edit response can carry
        # the previous web selection (often null); adopting that stale value
        # would clear the node the user just dragged and prevent the pending
        # selection acknowledgement from being flushed on the next tick.
        if (incoming != self.selection and self.gesture is None
                and not self.selection_dirty):
            self.selection = incoming
            self.selection_dirty = False
        if (self.navigation_binding_target_id
                and (not self.selection or self.selection.get("kind") != "semantic_point"
                     or self.selection.get("id") != self.navigation_binding_target_id)):
            self.navigation_binding_mode = False
            self.navigation_binding_target_id = None
            self.navigation_binding_draft = None
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()

    def _submit_frozen_edit(self, tag: Any, operation: str, *,
                            identity: dict[str, Any] | None = None,
                            **kwargs: Any) -> bool:
        call_kwargs = json.loads(json.dumps(kwargs))
        call_kwargs["_identity"] = dict(identity or self.dispatcher.client.identity())
        submitted = self.dispatcher.submit(tag, "edit", operation, **call_kwargs)
        if submitted:
            self._active_edit = {
                "tag": tag, "operation": operation,
                "kwargs": json.loads(json.dumps(call_kwargs)),
            }
            self.retry_request = None
            self.pending_edit = None
            self.last_error = None
            self.edit_status = "submitting"
        elif self.dispatcher.pending:
            # Selection/context writes are single-flight. Keep the complete
            # frozen edit instead of dropping it when the user drags the
            # selected marker immediately after clicking it.
            self.pending_edit = {
                "tag": tag, "operation": operation,
                "kwargs": json.loads(json.dumps(call_kwargs)),
            }
            self.edit_status = "submitting"
            self._changed()
            return True
        return submitted

    def flush_pending_edit(self) -> bool:
        request = self.pending_edit
        if request is None or self.dispatcher.pending:
            return False
        submitted = self.dispatcher.submit(
            request["tag"], "edit", request["operation"], **request["kwargs"])
        if not submitted:
            return False
        self._active_edit = {
            "tag": request["tag"], "operation": request["operation"],
            "kwargs": json.loads(json.dumps(request["kwargs"])),
        }
        self.pending_edit = None
        self.retry_request = None
        self.last_error = None
        self.edit_status = "submitting"
        self._changed()
        return True

    def _submit_context(self, tag: Any, **fields: Any) -> bool:
        request = {"tag": ("context", tag), "fields": json.loads(json.dumps(fields)),
                   "identity": self.dispatcher.client.identity()}
        submitted = self.dispatcher.submit(
            request["tag"], "set_context", **request["fields"],
            _identity=request["identity"])
        if not submitted:
            self.pending_context = request
            self._changed()
            return True
        self.pending_context = None
        return submitted

    def flush_pending_context(self) -> bool:
        request = self.pending_context
        if request is None or self.dispatcher.pending:
            return False
        submitted = self.dispatcher.submit(
            request["tag"], "set_context", **request["fields"],
            _identity=self.dispatcher.client.identity())
        if submitted:
            self.pending_context = None
            self._changed()
        return submitted

    def nodes(self) -> list[dict[str, Any]]:
        return list(self.snapshot.get("nodes") or [])

    def edges(self) -> list[dict[str, Any]]:
        return list(self.snapshot.get("edges") or [])

    def node(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.nodes() if row.get("id") == identifier), None)

    def edge(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.edges() if row.get("id") == identifier), None)

    def semantic_point(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.semantic_points
                     if row.get("id") == identifier), None)

    def point_for_selection(self, selection: dict[str, Any] | None = None):
        selection = selection or self.selection
        if not selection:
            return None
        if selection.get("kind") == "node":
            return self.node(selection.get("id"))
        return None

    def active_z(self) -> float:
        if self.active_layer in self.new_point_settings:
            return float(self.new_point_settings[self.active_layer]["z"])
        level_id = (self.context or {}).get("active_level")
        for row in (self.context or {}).get("levels") or []:
            if row.get("id") == level_id:
                return float(row.get("support_z_m", 0.0))
        return 0.0

    def set_active_layer(self, layer: str) -> bool:
        if layer not in self.LAYERS:
            raise IsaacBridgeError("active_layer must be ground, aerial or semantic_points")
        self.cancel_gesture()
        return self._submit_context(layer, active_layer=layer)

    def set_domain(self, domain: str) -> bool:
        if domain not in {"ground", "aerial"}:
            raise IsaacBridgeError("domain must be ground or aerial")
        return self.set_active_layer(domain)

    def set_tool(self, tool: str) -> bool:
        if tool not in self.TOOLS:
            raise IsaacBridgeError(
                "unknown editor tool")
        target_layer = ("ground" if tool in {"build-ground-road", "delete-ground-edge"}
                        else "aerial" if tool in {"build-aerial-road", "delete-aerial-edge"}
                        else None)
        if target_layer and self.active_layer != target_layer:
            if not self.set_active_layer(target_layer):
                return False
        self.cancel_gesture()
        if target_layer:
            return self._submit_context(tool, editor_tool=tool, active_layer=target_layer,
                                        active_domain=target_layer)
        return self._submit_context(tool, editor_tool=tool)

    def set_level(self, level_id: str) -> bool:
        if self.active_layer != "ground":
            return False
        return self._submit_context(level_id, active_level=level_id)

    def set_reference_level(self, level_id: str | None) -> bool:
        return self._submit_context(("reference-level", level_id),
                                    reference_level=level_id)

    def set_layer_visible(self, layer: str, visible: bool) -> bool:
        if layer not in self.LAYERS:
            raise IsaacBridgeError("unknown visible layer")
        values = dict((self.context or {}).get("visible_layers") or {})
        values.update({name: bool(values.get(name, True)) for name in self.LAYERS})
        values[layer] = bool(visible)
        return self._submit_context(("visible-layer", layer), visible_layers=values)

    def set_type_visible(self, kind: str, visible: bool) -> bool:
        if kind not in {"nodes", "edges", "semantic_points"}:
            raise IsaacBridgeError("unknown visible type")
        values = dict((self.context or {}).get("visible_types") or {})
        for name in ("nodes", "edges", "semantic_points"):
            values[name] = bool(values.get(name, True))
        values[kind] = bool(visible)
        return self._submit_context(("visible-type", kind), visible_types=values)

    def set_new_point_z(self, layer: str, z: float) -> bool:
        if layer not in {"ground", "aerial", "semantic_points"}:
            raise IsaacBridgeError("unknown point layer")
        value = float(z)
        if not math.isfinite(value):
            raise IsaacBridgeError("new point Z must be finite")
        settings = json.loads(json.dumps(self.new_point_settings))
        settings[layer]["z"] = value
        submitted = self._submit_context(("new-z", layer), new_point_settings={layer: settings[layer]})
        if submitted:
            self.new_point_settings = settings
            self._changed()
        return submitted

    def set_new_point_edit_mode(self, layer: str, mode: str) -> bool:
        if layer not in {"aerial", "semantic_points"} or mode not in self.MODES:
            raise IsaacBridgeError("new point mode must be 2.5d or 3d")
        settings = json.loads(json.dumps(self.new_point_settings))
        settings[layer]["edit_mode"] = mode
        submitted = self._submit_context(("new-mode", layer), new_point_settings={layer: settings[layer]})
        if submitted:
            self.new_point_settings = settings
            self._changed()
        return submitted

    # Compatibility name used by callers predating the independent settings.
    def set_new_node_edit_mode(self, mode: str) -> bool:
        return self.set_new_point_edit_mode("aerial", mode)

    def set_directed(self, value: bool) -> None:
        self.directed = bool(value)
        if self.preview and self.preview.get("kind") == "edge-draft":
            self.preview["directed"] = self.directed
        if self.selection and self.selection.get("kind") == "edge":
            edge = self.edge(self.selection["id"]) or {}
            self._submit_frozen_edit(
                ("edge-direction", self.selection["id"]), "update_edge",
                id=self.selection["id"], directed=self.directed,
                traversal_mode=edge.get("traversal_mode", "walk"))
        self._changed()

    def set_new_edge_traversal_mode(self, mode: str) -> bool:
        if mode not in {"walk", "stairs", "ramp"}:
            raise IsaacBridgeError("traversal_mode must be walk, stairs or ramp")
        if self.active_layer != "ground" and mode != "walk":
            self.new_edge_traversal_mode = "walk"
            self.last_error = "stairs and ramp edge creation are ground-only"
            self._changed()
            return False
        self.new_edge_traversal_mode = mode
        self.last_error = None
        if self.preview and self.preview.get("kind") == "edge-draft":
            self.preview["traversal_mode"] = mode
        self._changed()
        return True

    def set_selected_edit_mode(self, mode: str) -> bool:
        if mode not in self.MODES:
            raise IsaacBridgeError("edit_mode must be 2.5d or 3d")
        point = self.point_for_selection()
        if point is None:
            return False
        kind = self.selection.get("kind")
        if kind == "node" and ((self.context or {}).get("editor_tool") == "add-edge"
                                or self.active_layer != "aerial"):
            return False
        operation = "update_node" if kind == "node" else "update_semantic_point"
        return self._submit_frozen_edit(
            ("edit-mode", kind, point["id"]), operation,
            id=point["id"], edit_mode=mode)

    def set_selected_node_properties(self, position_xyz: Iterable[float], *,
                                     label: str = "", kind: str = "custom",
                                     level_id: str | None | object = _PRESERVE_LEVEL,
                                     _identity: dict[str, Any] | None = None) -> bool:
        if not self.selection or self.selection.get("kind") != "node":
            return False
        if (self.context or {}).get("editor_tool") == "add-edge":
            return False
        node = self.node(self.selection["id"])
        if node is None:
            return False
        args = {"id": node["id"], "position_xyz": list(_finite_xyz(position_xyz)),
                "label": str(label).strip(), "kind": str(kind).strip() or "custom"}
        if self.active_layer == "aerial":
            args["edit_mode"] = node.get("edit_mode", "3d")
        else:
            selected_level = (node.get("level_id") if level_id is _PRESERVE_LEVEL
                              else level_id)
            known = {row.get("id") for row in (self.context or {}).get("levels") or []}
            if selected_level is not None and selected_level not in known:
                return False
            args["level_id"] = selected_level
        return self._submit_frozen_edit(("node-properties", node["id"]),
                                        "update_node", identity=_identity, **args)

    def set_selected_semantic_point_properties(
            self, *, label: str = "",
            kind: str = "custom", description: str = "",
            aliases: Iterable[str] = (), properties: dict[str, Any] | None = None,
            _identity: dict[str, Any] | None = None) -> bool:
        if not self.selection or self.selection.get("kind") != "semantic_point":
            return False
        point = self.semantic_point(self.selection["id"])
        if point is None or not isinstance(properties or {}, dict):
            return False
        return self._submit_frozen_edit(
            ("semantic-point-properties", point["id"]), "update_semantic_point",
            identity=_identity, id=point["id"],
            label=str(label).strip(), kind=str(kind).strip() or "custom",
            description=str(description),
            aliases=[str(value).strip() for value in aliases if str(value).strip()],
            properties=json.loads(json.dumps(properties or {})))

    def begin_navigation_binding(self) -> bool:
        """Enter a transient cross-domain node-pick mode for the selected point."""
        if not self.selection or self.selection.get("kind") != "semantic_point":
            return False
        point = self.semantic_point(self.selection["id"])
        if point is None:
            return False
        self.navigation_binding_mode = True
        if self.navigation_binding_target_id != point["id"] or self.navigation_binding_draft is None:
            self.navigation_binding_draft = json.loads(json.dumps(point.get("navigation_bindings", [])))
        self.navigation_binding_target_id = point["id"]
        self.cancel_gesture(preserve_retry=True)
        self._submit_context(("navigation-binding-mode", point["id"]),
                             binding_target_mode="semantic_navigation",
                             binding_target_id=point["id"],
                             binding_target_draft=self.navigation_binding_draft)
        self._changed()
        return True

    def finish_navigation_binding(self) -> bool:
        """Leave map picking while keeping the pending bindings for review/apply."""
        if not self.navigation_binding_mode or self.navigation_binding_target_id is None:
            return False
        self.navigation_binding_mode = False
        self._submit_context(("navigation-binding-mode", self.navigation_binding_target_id),
                             binding_target_mode=None, binding_target_id=None,
                             binding_target_draft=None)
        self._changed()
        return True

    def cancel_navigation_binding(self) -> None:
        self.navigation_binding_mode = False
        self.navigation_binding_target_id = None
        self.navigation_binding_draft = None
        self._submit_context(("navigation-binding-mode",), binding_target_mode=None,
                             binding_target_id=None, binding_target_draft=None)
        self._changed()

    def toggle_navigation_binding(self, domain: str, node_id: str) -> bool:
        if not self.navigation_binding_mode or domain not in {"ground", "aerial"}:
            return False
        if not any(row.get("id") == node_id for row in (self.document_snapshots.get(domain, {}).get("nodes") or [])):
            return False
        draft = list(self.navigation_binding_draft or [])
        key = (domain, node_id)
        if any((row.get("domain"), row.get("node_id")) == key for row in draft):
            draft = [row for row in draft if (row.get("domain"), row.get("node_id")) != key]
        else:
            draft.append({"domain": domain, "node_id": node_id})
        self.navigation_binding_draft = sorted(draft, key=lambda row: (row["domain"], row["node_id"]))
        point_id = self.selection.get("id") if self.selection else None
        if point_id:
            self.navigation_binding_target_id = point_id
            self._submit_context(("navigation-binding-draft", point_id),
                                 binding_target_mode="semantic_navigation",
                                 binding_target_id=point_id,
                                 binding_target_draft=self.navigation_binding_draft)
        self._changed()
        return True

    def commit_navigation_bindings(self) -> bool:
        if (not self.selection or self.selection.get("kind") != "semantic_point"
                or self.navigation_binding_draft is None
                or self.navigation_binding_target_id != self.selection.get("id")):
            return False
        point_id = self.selection.get("id")
        submitted = self._submit_frozen_edit(
            ("semantic-navigation-bindings", point_id),
            "set_semantic_point_navigation_bindings", id=point_id,
            bindings=json.loads(json.dumps(self.navigation_binding_draft or [])))
        return submitted

    def set_selected_z(self, z: float) -> bool:
        point = self.point_for_selection()
        value = float(z)
        if point is None or not math.isfinite(value):
            return False
        if (self.selection.get("kind") == "node"
                and (self.context or {}).get("editor_tool") == "add-edge"):
            return False
        position = list(point["position_xyz"])
        position[2] = value
        return self._submit_frozen_edit(
            ("point-z", point["id"]),
            "move_node",
            id=point["id"], position_xyz=position)

    def select(self, selection: dict[str, Any] | None) -> None:
        # Navigation binding owns map picks while the semantic point remains
        # selected. A navigation node/edge hit must never replace that
        # selection or cause the editor to leave the semantic layer.
        if (self.navigation_binding_mode and selection
                and selection.get("kind") in {"node", "edge"}):
            return
        if (self.navigation_binding_target_id
                and (not selection or selection.get("kind") != "semantic_point"
                     or selection.get("id") != self.navigation_binding_target_id)):
            self.navigation_binding_mode = False
            self.navigation_binding_target_id = None
            self.navigation_binding_draft = None
            self._submit_context(("navigation-binding-mode",), binding_target_mode=None,
                                 binding_target_id=None, binding_target_draft=None)
        self.selection = selection
        self.selection_items = ([dict(selection)] if selection else [])
        self.selection_dirty = True
        self._changed()

    def flush_selection(self) -> bool:
        if (not self.selection_dirty or self.dispatcher.pending
                or self.gesture is not None or self.pending_edit is not None):
            return False
        submitted = self.dispatcher.submit(
            ("selection", self.selection), "select", self.selection,
            _identity=self.dispatcher.client.identity())
        if submitted:
            self.selection_dirty = False
        return submitted

    def add_node(self, point: Iterable[float]) -> bool:
        if self.active_layer not in {"ground", "aerial"}:
            return False
        position = list(_finite_xyz(point))
        args = {"position_xyz": position, "label": "", "kind": "custom"}
        if self.active_layer == "aerial":
            args["edit_mode"] = self.new_point_settings["aerial"]["edit_mode"]
        else:
            args["level_id"] = (self.context or {}).get("active_level")
        return self._submit_frozen_edit(("add-node", position), "add_node", **args)

    def preview_road(self, start: Iterable[float], end: Iterable[float], *,
                     connect_intersections: bool = True) -> dict[str, Any]:
        from .road_builder import plan_navigation_road
        if self.active_layer not in {"ground", "aerial"}:
            raise IsaacBridgeError("road construction requires a navigation layer")
        domain = self.active_layer
        snapshot = self.document_snapshots.get(domain, self.snapshot)
        return {key: value for key, value in plan_navigation_road(
            snapshot, list(_finite_xyz(start)), list(_finite_xyz(end)),
            level_id=(self.context or {}).get("active_level") if domain == "ground" else None,
            snap_radius_m=0.0,
            disable_snap=self.road_free_snap,
            connect_intersections=connect_intersections).items() if key != "document"}

    def preview_ground_road(self, start: Iterable[float], end: Iterable[float], *,
                            connect_intersections: bool = True) -> dict[str, Any]:
        if self.active_layer != "ground":
            raise IsaacBridgeError("road construction is only available on ground")
        return self.preview_road(start, end, connect_intersections=connect_intersections)

    def build_road(self, start: Iterable[float], end: Iterable[float], *,
                   connect_intersections: bool = True) -> bool:
        if self.active_layer not in {"ground", "aerial"}:
            return False
        domain = self.active_layer
        operation = "build_ground_road" if domain == "ground" else "build_aerial_road"
        args = dict(start_xyz=list(_finite_xyz(start)), end_xyz=list(_finite_xyz(end)),
            snap_radius_m=0.0,
            disable_snap=self.road_free_snap,
            connect_intersections=connect_intersections)
        if domain == "ground":
            args["level_id"] = (self.context or {}).get("active_level")
        return self._submit_frozen_edit(("build-road", domain, tuple(start), tuple(end)), operation, **args)

    def build_ground_road(self, start: Iterable[float], end: Iterable[float], *,
                          connect_intersections: bool = True) -> bool:
        if self.active_layer != "ground":
            return False
        return self.build_road(start, end, connect_intersections=connect_intersections)

    def add_semantic_point(self, point: Iterable[float]) -> bool:
        return False  # Annotations are created from navigation nodes only.

    def save_annotation(self, identifier=None, *, identity=None, **fields):
        node = self.selection if self.selection and self.selection.get("kind") == "node" else None
        if node and (self.context or {}).get("editor_tool") == "add-node":
            fields["annotation_node_id"] = node["id"]
            if identifier is None:
                fields["node_id"] = node["id"]
        elif not identifier or self.active_layer != "semantic_points":
            return False
        if identifier:
            fields["id"] = identifier
        return self._submit_frozen_edit(
            ("semantic-point-properties", identifier), "update_semantic_point" if identifier else "create_node_annotation",
            identity=identity, **fields)

    def bind_annotation(self, identifier, *, remove=False):
        if not self.selection or self.selection.get("kind") != "node":
            return False
        row = self.semantic_point(identifier)
        if row is None:
            return False
        node_id, domain = self.selection["id"], self.active_domain
        bindings = [dict(b) for b in row.get("navigation_bindings", [])
                    if (b["domain"], b["node_id"]) != (domain, node_id)]
        if not remove:
            bindings.append({"domain": domain, "node_id": node_id})
        return self._submit_frozen_edit(
            ("semantic-navigation-bindings", identifier), "set_semantic_point_navigation_bindings",
            id=identifier, bindings=bindings, annotation_node_id=node_id)

    def activate_node_for_edge(self, identifier: str) -> None:
        if self.active_layer not in {"ground", "aerial"}:
            return
        if self.edge_start is None:
            self.edge_start, self.edge_end = identifier, None
            self.edge_identity = self.dispatcher.client.identity()
            self.select(_node_selection(identifier))
        elif identifier == self.edge_start:
            self.last_error = "edge endpoints must differ"
        else:
            start, end = self.node(self.edge_start), self.node(identifier)
            if start is None or end is None:
                self.last_error = "edge endpoint no longer exists"
            else:
                self.edge_end = identifier
                mode = self.new_edge_traversal_mode if self.active_layer == "ground" else "walk"
                self.preview = {
                    "kind": "edge-draft", "from": self.edge_start, "to": identifier,
                    "directed": self.directed, "traversal_mode": mode,
                    "points": [list(start["position_xyz"]), list(end["position_xyz"])],
                }
                self.last_error = None
        self._changed()

    def finish_edge(self) -> bool:
        if self.edge_start is None or self.edge_end is None:
            return False
        return self._submit_frozen_edit(
            ("add-edge", self.edge_start, self.edge_end), "add_edge",
            identity=self.edge_identity, from_id=self.edge_start,
            to_id=self.edge_end, directed=self.directed,
            traversal_mode=(self.new_edge_traversal_mode
                            if self.active_layer == "ground" else "walk"))

    def delete_selected(self) -> bool:
        items = list(self.selection_items)
        if not items and self.selection:
            items = [{"kind": self.selection.get("kind"), "id": self.selection.get("id")}]
        if not items:
            return False
        if (self.context or {}).get("editor_tool") == "add-edge":
            return False
        if len(items) == 1:
            kind, identifier = items[0]["kind"], items[0]["id"]
            operation = {"node": "remove_node", "edge": "remove_edge",
                         "semantic_point": "remove_semantic_point"}.get(kind)
            if operation:
                return self._submit_frozen_edit(("delete", kind, identifier), operation, id=identifier)
        return self._submit_frozen_edit(("delete-selection", tuple(
            (item.get("kind"), item.get("id")) for item in items)),
            "remove_selection", items=items)

    def set_selection_items(self, items: Iterable[dict[str, Any]]) -> None:
        self.selection_items = [
            {"kind": row["kind"], "id": row["id"]}
            for row in items if isinstance(row, dict)
            and row.get("kind") in {"node", "edge", "semantic_point"}
            and isinstance(row.get("id"), str)]
        if self.selection_items:
            self.selection = dict(self.selection_items[0])
        else:
            self.selection = None
        self._changed()

    def set_box_preview(self, points: Iterable[Iterable[float]] | None) -> None:
        self.box_preview = (None if points is None else
                            [list(_finite_xyz(point)) for point in points])
        self._changed()

    def undo(self) -> bool:
        return self.dispatcher.submit(("undo",), "undo",
                                      _identity=self.dispatcher.client.identity())

    def redo(self) -> bool:
        return self.dispatcher.submit(("redo",), "redo",
                                      _identity=self.dispatcher.client.identity())

    def save(self) -> bool:
        return self.dispatcher.submit(("save",), "save",
                                      _identity=self.dispatcher.client.identity())

    def cancel_gesture(self, *, preserve_retry: bool = False, **_compat: Any) -> None:
        self.interaction_generation += 1
        self.gesture = None
        self.road_draft = None
        self._road_end_z = None
        self._road_last_point = None
        self.preview = None
        self.selection_items = []
        self.box_preview = None
        self.edge_start = self.edge_end = None
        self.edge_identity = None
        if not preserve_retry:
            self.retry_request = None
        self._active_edit = None
        self.pending_edit = None
        self.edit_status = "idle"
        self._changed()

    def begin_drag(self, target: dict[str, Any], point: Iterable[float], *,
                   axis: str | None = None) -> None:
        kind, identifier = target.get("kind"), target.get("id")
        if kind == "semantic-point":
            return
        original = None
        if kind in {"node", "semantic-point"}:
            record = (self.node(identifier) if kind == "node"
                      else self.semantic_point(identifier))
            if record is not None:
                original = list(_finite_xyz(record["position_xyz"]))
        elif kind == "edge":
            edge = self.edge(identifier)
            if edge is not None:
                original = [list(_finite_xyz(value))
                            for value in edge.get("polyline_xyz") or []]
        if original is None:
            return
        self.gesture = {
            "target": json.loads(json.dumps(target)), "axis": axis,
            "identity": self.dispatcher.client.identity(),
            "grab_point": list(_finite_xyz(point)), "original": original,
        }
        self.preview = None
        self._changed()

    def update_drag(self, point: Iterable[float]) -> None:
        if self.gesture is None:
            return
        value = list(_finite_xyz(point))
        gesture, target = self.gesture, self.gesture["target"]
        delta = [value[i] - gesture["grab_point"][i] for i in range(3)]
        if target["kind"] == "edge":
            points = [[p[0] + delta[0], p[1] + delta[1], p[2]]
                      for p in gesture["original"]]
            edge = self.edge(target["id"]) or {}
            associated = []
            for node_id in dict.fromkeys((edge.get("from"), edge.get("to"))):
                node = self.node(node_id)
                if node is not None:
                    source = node["position_xyz"]
                    associated.append({"id": node_id,
                                       "position_xyz": [source[0] + delta[0],
                                                        source[1] + delta[1], source[2]]})
            self.preview = {"kind": "edge", "id": target["id"],
                            "delta_xy": delta[:2], "points": points,
                            "associated_nodes": associated}
        else:
            original = gesture["original"]
            position = [original[i] + delta[i] for i in range(3)]
            record = (self.node(target["id"]) if target["kind"] == "node"
                      else self.semantic_point(target["id"])) or {}
            axis = gesture.get("axis")
            if axis:
                for index, name in enumerate("xyz"):
                    if name != axis:
                        position[index] = original[index]
            elif (target["kind"] == "node" and self.active_layer == "ground"
                  or record.get("edit_mode", "3d") == "2.5d"):
                position[2] = original[2]
            self.preview = {"kind": target["kind"], "id": target["id"],
                            "position_xyz": position}
        self._changed()

    def end_drag(self) -> bool:
        if self.gesture is None:
            return False
        if self.gesture.get("target", {}).get("kind") == "road":
            gesture, preview = self.gesture, self.preview
            self.gesture = None
            if preview and preview.get("kind") == "road-draft":
                self.road_draft = {"start_xyz": gesture["original"], "end_xyz": preview["position_xyz"],
                                   "identity": gesture["identity"], "disable_snap": self.road_free_snap,
                                   "domain": self.active_layer}
                self.preview = None
                self._changed()
                return True
            self.preview = None
            self._changed()
            return False
        if self.preview is None:
            # A press/release that never crosses Kit's drag threshold has no
            # authored transform. End the interaction without submitting an
            # edit, while preserving the point/edge selection made on press.
            self.gesture = None
            self.preview = None
            self._changed()
            return False
        gesture, preview = self.gesture, self.preview
        kind, identifier = preview["kind"], preview["id"]
        if kind == "edge":
            return self._submit_frozen_edit(
                ("translate-edge", identifier), "translate_edge_xy",
                identity=gesture["identity"], id=identifier,
                delta_xy=preview["delta_xy"])
        if kind != "node":
            return False
        record = self.node(identifier) or {}
        mode = record.get("edit_mode", "3d")
        operation = ("move_node_xy"
                     if (mode == "2.5d" or self.active_layer == "ground")
                     and gesture.get("axis") is None
                     else "move_node")
        kwargs = ({"position_xy": preview["position_xyz"][:2]}
                  if operation.endswith("_xy") else
                  {"position_xyz": preview["position_xyz"]})
        return self._submit_frozen_edit(
            ("move-node", identifier),
            operation, identity=gesture["identity"], id=identifier, **kwargs)

    def update_road_drag(self, point: Iterable[float], *, disable_axis_snap=None) -> None:
        if not self.gesture or self.gesture.get("target", {}).get("kind") != "road":
            return
        from .road_builder import plan_navigation_road
        start = self.gesture["original"]
        end = list(_finite_xyz(point))
        if self.active_layer == "aerial" and self._road_end_z is not None:
            end[2] = self._road_end_z
        if disable_axis_snap is None:
            disable_axis_snap = self.road_free_snap
        if not disable_axis_snap:
            angle = math.atan2(abs(end[1]-start[1]), abs(end[0]-start[0]))
            if angle <= math.pi/18:
                end[1] = start[1]
            elif math.pi/2-angle <= math.pi/18:
                end[0] = start[0]
        try:
            domain = self.active_layer
            snapshot = self.document_snapshots.get(domain, self.snapshot)
            self._road_last_point = end
            plan = plan_navigation_road(snapshot, start, end,
                level_id=(self.context or {}).get("active_level") if domain == "ground" else None,
                snap_radius_m=0.0,
                disable_snap=self.road_free_snap)
            self.preview = {"kind": "road-draft", "position_xyz": plan["end"], "points": [plan["start"], *plan["crossings"], plan["end"]]}
        except (ValueError, KeyError):
            self.preview = {"kind": "road-draft", "position_xyz": end, "points": [start, end], "invalid": True}
        self._changed()

    def adjust_road_end_height(self, delta: float) -> bool:
        if (self.active_layer != "aerial" or self.gesture is None
                or self.gesture.get("target", {}).get("kind") != "road"
                or self._road_last_point is None):
            return False
        value = self._road_end_z if self._road_end_z is not None else self.gesture["original"][2]
        self._road_end_z = float(value) + float(delta)
        self.update_road_drag(self._road_last_point, disable_axis_snap=self.road_free_snap)
        return True

    def adjust_aerial_road_start_height(self, delta: float) -> bool:
        if (self.active_layer != "aerial"
                or (self.context or {}).get("editor_tool") != "build-aerial-road"
                or self.gesture is not None):
            return False
        current = self.new_point_settings["aerial"]["z"]
        return self.set_new_point_z("aerial", float(current) + float(delta))

    def confirm_road(self, *, connect_intersections=True) -> bool:
        draft = self.road_draft
        if not draft:
            return False
        domain = draft.get("domain", self.active_layer)
        operation = "build_ground_road" if domain == "ground" else "build_aerial_road"
        args = dict(identity=draft["identity"], start_xyz=draft["start_xyz"], end_xyz=draft["end_xyz"],
            snap_radius_m=0.0,
            disable_snap=draft.get("disable_snap", False),
            connect_intersections=connect_intersections)
        if domain == "ground":
            args["level_id"] = (self.context or {}).get("active_level")
        submitted = self._submit_frozen_edit(("build-road", domain), operation, **args)
        if submitted:
            self.road_draft = None
        return submitted

    def confirm_ground_road(self, *, connect_intersections=True) -> bool:
        if self.road_draft and self.road_draft.get("domain", self.active_layer) != "ground":
            return False
        return self.confirm_road(connect_intersections=connect_intersections)

    def retry_conflict(self) -> bool:
        request = self.retry_request
        if request is None:
            return False
        kwargs = json.loads(json.dumps(request["kwargs"]))
        kwargs["_identity"] = self.dispatcher.client.identity()
        submitted = self.dispatcher.submit(
            request["tag"], "edit", request["operation"], **kwargs)
        if submitted:
            self._active_edit = {**request, "kwargs": json.loads(json.dumps(kwargs))}
            self.retry_request = None
            self.last_error = None
            self.edit_status = "submitting"
            self._changed()
        return submitted

    def handle_result(self, result) -> None:
        if result is None:
            return
        tag, payload, error = result
        self.result_serial += 1
        self.last_result_tag = tag
        self.last_result_succeeded = error is None
        request = (self._active_edit if self._active_edit is not None
                   and self._active_edit.get("tag") == tag else None)
        self._active_edit = None
        edit_tag = bool(tag and tag[0] in {
            "add-node", "add-edge", "add-semantic-point", "move-node",
            "move-semantic-point", "translate-edge", "node-properties",
            "semantic-point-properties", "node-edit-mode", "point-z",
            "bind-semantic-point", "unbind-semantic-point", "semantic-navigation-bindings",
            "unbind-semantic-point", "delete", "delete-selection"})
        if error is not None:
            self.last_error = str(error)
            if edit_tag:
                # A failed edit must never leave the session-layer preview in
                # place.  The next overlay frame therefore reads the unchanged
                # authoritative snapshot and puts the marker back where the
                # server last accepted it.
                self.interaction_generation += 1
                self.gesture = None
                self.preview = None
                self.pending_edit = None
                self.edge_start = self.edge_end = None
                self.edge_identity = None
                if getattr(error, "status", None) == 409:
                    self.retry_request = request
                    self.edit_status = "retry"
                else:
                    self.retry_request = None
                    self.edit_status = "failed"
            else:
                # Selection/context synchronization can be in flight when a
                # drag ends. Keep that frozen drag request alive across a
                # selection error; dropping it would leave only the local
                # session-layer preview and silently lose the user edit.
                if self.pending_edit is None:
                    self.retry_request = None
                    self.edit_status = "failed"
                else:
                    self.edit_status = "submitting"
        else:
            self.last_error = None
            self.retry_request = None
            successful_edit = edit_tag
            if tag and tag[0] == "add-edge":
                self.edge_start = self.edge_end = None
                self.edge_identity = None
                self.preview = None
            elif tag and tag[0] == "delete-selection":
                self.selection = None
                self.selection_items = []
                self.preview = None
            elif tag and tag[0] in {"move-node", "move-semantic-point", "translate-edge"}:
                self.gesture = None
                self.preview = None
            elif tag and tag[0] == "semantic-navigation-bindings":
                self.navigation_binding_mode = False
                self.navigation_binding_target_id = None
                self.navigation_binding_draft = None
            if isinstance(payload, dict):
                fresh = payload.get("context") or payload.get("state")
                if isinstance(fresh, dict) and "context_generation" in fresh:
                    self.update_context(fresh)
            if successful_edit:
                # update_context() may clear the interaction state when the
                # response carries a newer revision. Restore the user-visible
                # terminal state after that authoritative refresh.
                self.edit_status = "synced"
        self._changed()


class IsaacTopologyWindow:
    """Native controls for independent graph and semantic-point layers."""

    def __init__(self, controller: IsaacTopologyController,
                 title: str = "Isaac Topology Editor"):
        self.controller, self.title = controller, title
        self._window = self._scroll_frame = self._status = None
        self._node_label_model = self._node_kind_model = None
        self._node_level_model = None
        self._node_xyz_models = []
        self._semantic_label_model = self._semantic_kind_model = None
        self._semantic_description_model = self._semantic_aliases_model = None
        self._semantic_properties_model = None
        self._semantic_xyz_models = []
        self._annotation_id = None
        self._annotation_list = self._annotation_search = None
        self._annotation_list_token = None
        self._delete_annotation_dialog = None
        self._new_z_models = {}
        self._property_token = self._property_identity = self._property_revision = None
        self._property_dirty = self._property_setting = False
        self._seen_result_serial = controller.result_serial
        self._model_subscriptions = []
        self._input = self._keyboard = self._key_subscription = None
        self._level_controls = self._node_property_controls = self._node_level_controls = None

    @staticmethod
    def _set_model(model, value):
        if model is not None:
            model.set_value(value)

    @staticmethod
    def _string(model, fallback=""):
        return model.get_value_as_string() if model is not None else fallback

    def refresh(self):
        if self._status is None:
            return
        context, selection = self.controller.context or {}, self.controller.selection
        selected = selection.get("id") if selection else "none"
        point = self.controller.point_for_selection(selection)
        mode = f" | selected mode {point.get('edit_mode', '3d')}" if point else ""
        binding = ""
        if selection and selection.get("kind") == "semantic_point":
            point = self.controller.semantic_point(selection.get("id")) or {}
            count = len(self.controller.navigation_binding_draft
                         if self.controller.navigation_binding_target_id == selection.get("id")
                         and self.controller.navigation_binding_draft is not None
                         else point.get("navigation_bindings", []))
            binding = f" | navigation bindings {count}"
            if self.controller.navigation_binding_mode:
                binding += " (connecting)"
            elif self.controller.navigation_binding_draft is not None:
                binding += " (draft; apply to save)"
        error = f" | {self.controller.last_error}" if self.controller.last_error else ""
        if self.controller.retry_request is not None:
            edit_state = " | 存在待重试草稿"
        elif self.controller.edit_status == "failed":
            edit_state = " | 提交失败，已回退"
        elif (self.controller.preview is not None
              and self.controller.edit_status != "submitting"):
            edit_state = " | 预览中"
        elif (self.controller.edit_status == "submitting"
              or self.controller.pending_edit is not None
              or self.controller._active_edit is not None):
            edit_state = " | 提交中"
        elif self.controller.edit_status == "synced":
            edit_state = " | 已同步到网页"
        else:
            edit_state = ""
        road_height = ""
        if self.controller.active_layer == "aerial" and context.get("editor_tool") == "build-aerial-road":
            if self.controller.gesture is not None and self.controller._road_end_z is not None:
                road_height = f" | road end Z {self.controller._road_end_z:.2f} m"
            else:
                road_height = f" | road start Z {self.controller.new_point_settings['aerial']['z']:.2f} m"
        self._status.text = (
            f"layer {self.controller.active_layer} | tool {context.get('editor_tool', '?')} | "
            f"floor {context.get('active_level', '?')} | reference "
            f"{context.get('reference_level') or 'none'} | selected "
            f"{selected}{mode}{binding}{road_height}{edit_state}{error}")
        if self._level_controls is not None:
            self._level_controls.visible = self.controller.active_layer == "ground"
        if self._node_property_controls is not None:
            self._node_property_controls.enabled = bool(
                selection and selection.get("kind") == "node"
                and context.get("editor_tool") != "add-edge")
        if self._node_level_controls is not None:
            self._node_level_controls.visible = self.controller.active_layer == "ground"
        if self.controller.result_serial != self._seen_result_serial:
            self._seen_result_serial = self.controller.result_serial
            if self.controller.last_result_succeeded:
                self._property_dirty = False
                self._property_revision = None
        token = (context.get("scene_key"), context.get("scene_generation"),
                 context.get("context_generation"),
                 None if selection is None else
                 (selection.get("kind"), selection.get("id")))
        revision = context.get("revision")
        self._refresh_annotations()
        if token == self._property_token and (self._property_dirty or revision == self._property_revision):
            return
        if token != self._property_token:
            self._property_dirty = False
            self._annotation_id = None
            self._load_annotation(None)
        self._property_token, self._property_revision = token, revision
        self._property_identity = self.controller.dispatcher.client.identity()
        self._property_setting = True
        try:
            if selection and selection.get("kind") == "node":
                node = self.controller.node(selection.get("id"))
                if node:
                    self._set_model(self._node_label_model, node.get("label", ""))
                    self._set_model(self._node_kind_model, node.get("kind", "custom"))
                    self._set_model(self._node_level_model, node.get("level_id") or "")
                    for model, value in zip(self._node_xyz_models, node.get("position_xyz", ())):
                        self._set_model(model, float(value))
            elif selection and selection.get("kind") == "semantic_point":
                point = self.controller.semantic_point(selection.get("id"))
                if point:
                    self._set_model(self._semantic_label_model, point.get("label", ""))
                    self._set_model(self._semantic_kind_model, point.get("kind", "custom"))
                    self._set_model(self._semantic_description_model,
                                    point.get("description", ""))
                    self._set_model(self._semantic_aliases_model,
                                    ", ".join(point.get("aliases") or []))
                    self._set_model(self._semantic_properties_model,
                                    json.dumps(point.get("properties") or {}, sort_keys=True))
                    for model, value in zip(self._semantic_xyz_models,
                                            point.get("position_xyz", ())):
                        self._set_model(model, float(value))
            for layer, model in self._new_z_models.items():
                self._set_model(model, self.controller.new_point_settings[layer]["z"])
        finally:
            self._property_setting = False

    def _mark_property_dirty(self, *_args):
        if self._property_setting:
            return
        if not self._property_dirty:
            self._property_identity = self.controller.dispatcher.client.identity()
        self._property_dirty = True

    def _apply_node_properties(self):
        level = self._string(self._node_level_model).strip()
        self.controller.set_selected_node_properties(
            [model.get_value_as_float() for model in self._node_xyz_models],
            label=self._string(self._node_label_model),
            kind=self._string(self._node_kind_model, "custom"),
            level_id=level or None, _identity=self._property_identity)

    def _apply_semantic_properties(self):
        raw = self._string(self._semantic_properties_model, "{}").strip() or "{}"
        try:
            properties = json.loads(raw)
            if not isinstance(properties, dict):
                raise ValueError("properties must be an object")
        except (TypeError, ValueError) as exc:
            self.controller.last_error = f"Invalid properties JSON: {exc}"
            self.controller._changed()
            return
        aliases = [value.strip() for value in
                   self._string(self._semantic_aliases_model).split(",") if value.strip()]
        identifier = (self.controller.selection or {}).get("id") if self.controller.active_layer == "semantic_points" else self._annotation_id
        self.controller.save_annotation(identifier,
            identity=self._property_identity,
            label=self._string(self._semantic_label_model),
            kind=self._string(self._semantic_kind_model, "custom"),
            description=self._string(self._semantic_description_model),
            aliases=aliases, properties=properties)

    def _apply_new_z(self, layer):
        self.controller.set_new_point_z(layer, self._new_z_models[layer].get_value_as_float())

    def _load_annotation(self, identifier):
        self._annotation_id = identifier
        row = self.controller.semantic_point(identifier) or {}
        self._property_setting = True
        try:
            for model, value in (
                (self._semantic_label_model, row.get("label", "")),
                (self._semantic_kind_model, row.get("kind", "custom")),
                (self._semantic_description_model, row.get("description", "")),
                (self._semantic_aliases_model, ", ".join(row.get("aliases", []))),
                (self._semantic_properties_model, json.dumps(row.get("properties", {})))):
                self._set_model(model, value)
        finally:
            self._property_setting = False
        self._property_identity = self.controller.dispatcher.client.identity()

    def _refresh_annotations(self):
        if self._annotation_list is None:
            return
        import omni.ui as ui
        selection = self.controller.selection or {}
        node_id = selection.get("id") if selection.get("kind") == "node" else None
        query = self._string(self._annotation_search).lower()
        token = (json.dumps(self.controller.semantic_points, sort_keys=True),
                 node_id, self.controller.active_layer, query)
        if token == self._annotation_list_token:
            return
        self._annotation_list_token = token
        with self._annotation_list:
            with ui.ScrollingFrame():
                with ui.VStack(height=0):
                    for row in self.controller.semantic_points:
                        if query not in " ".join([row["id"], row.get("label", ""), row.get("kind", ""), *row.get("aliases", [])]).lower():
                            continue
                        bound = any(b["domain"] == self.controller.active_domain and b["node_id"] == node_id
                                    for b in row.get("navigation_bindings", []))
                        with ui.HStack(height=28):
                            ui.Label(("Bound: " if bound else "Unbound: " if not row.get("navigation_bindings") else "") + (row.get("label") or row["id"]))
                            def edit_row(identifier=row["id"]):
                                if self.controller.active_layer == "semantic_points":
                                    self.controller.select({"kind": "semantic_point", "id": identifier})
                                self._load_annotation(identifier)
                            ui.Button("Edit", width=45, clicked_fn=edit_row)
                            if node_id:
                                ui.Button("Unbind" if bound else "Bind", width=65,
                                          clicked_fn=lambda identifier=row["id"], remove=bound:
                                          self.controller.bind_annotation(identifier, remove=remove))
                            if self.controller.active_layer == "semantic_points":
                                ui.Button("Delete", width=55, clicked_fn=lambda identifier=row["id"]:
                                          self._confirm_annotation_delete(identifier))

    def _confirm_annotation_delete(self, identifier):
        import omni.ui as ui
        row = self.controller.semantic_point(identifier)
        if row is None:
            return
        identity = self.controller.dispatcher.client.identity()
        self._delete_annotation_dialog = ui.Window("Delete shared annotation", width=400, height=120)
        def remove():
            self.controller._submit_frozen_edit(("delete", "semantic_point", identifier),
                "remove_semantic_point", identity=identity, id=identifier)
            self._delete_annotation_dialog.visible = False
        with self._delete_annotation_dialog.frame:
            with ui.VStack():
                ui.Label(f"Delete annotation and all {len(row.get('navigation_bindings', []))} bindings?",
                         word_wrap=True)
                ui.Button("Delete entire annotation", clicked_fn=remove)
                ui.Button("Cancel", clicked_fn=lambda: setattr(self._delete_annotation_dialog, "visible", False))

    def show(self):
        try:
            import omni.ui as ui
        except ImportError as exc:
            raise IsaacBridgeError("omni.ui is unavailable") from exc
        self._window = ui.Window(self.title, width=560, height=820)
        with self._window.frame:
            self._scroll_frame = ui.ScrollingFrame(
                horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED)
            with self._scroll_frame:
                with ui.VStack(spacing=5, height=0, width=ui.Fraction(1)):
                    ui.Label("Active layer")
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Button("Ground", width=0, clicked_fn=lambda:
                                  self.controller.set_active_layer("ground"))
                        ui.Button("Aerial", width=0, clicked_fn=lambda:
                                  self.controller.set_active_layer("aerial"))
                        ui.Button("Semantic points", width=0, clicked_fn=lambda:
                                  self.controller.set_active_layer("semantic_points"))
                    ui.Label("Visible layers / hit types")
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        for layer, label in (("ground", "Ground"), ("aerial", "Aerial"),
                                             ("semantic_points", "Semantic")):
                            ui.Button(label, width=0, clicked_fn=lambda name=layer:
                                      self.controller.set_layer_visible(
                                          name, not bool(((self.controller.context or {}).get(
                                              "visible_layers") or {}).get(name, True))))
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        for kind, label in (("nodes", "Nodes"), ("edges", "Edges"),
                                            ("semantic_points", "Semantic points")):
                            ui.Button(label, width=0, clicked_fn=lambda name=kind:
                                      self.controller.set_type_visible(
                                          name, not bool(((self.controller.context or {}).get(
                                              "visible_types") or {}).get(name, True))))
                    ui.Label("Tools")
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Button("Point", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("add-node"))
                        ui.Button("Edge", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("add-edge"))
                        ui.Button("Build road", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("build-ground-road"))
                        ui.Button("Delete edge", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("delete-ground-edge"))
                        ui.Button("Build aerial road", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("build-aerial-road"))
                        ui.Button("Delete aerial edge", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("delete-aerial-edge"))
                        ui.Button("Semantic management", width=0, clicked_fn=lambda:
                                  self.controller.set_tool("add-semantic-point"))
                    self._level_controls = ui.VStack(spacing=3, height=0,
                                                     width=ui.Fraction(1))
                    with self._level_controls:
                        ui.Label("Working floor")
                        with ui.HStack(height=30, width=ui.Fraction(1)):
                            for level in (self.controller.context or {}).get("levels") or []:
                                identifier = level.get("id")
                                label = str(level.get("label") or identifier)
                                ui.Button(label, width=0, clicked_fn=lambda value=identifier:
                                          self.controller.set_level(value))
                    ui.Label("Reference basemap")
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        for level in (self.controller.context or {}).get("levels") or []:
                            identifier = level.get("id")
                            label = str(level.get("label") or identifier)
                            ui.Button(f"Ref {label}", width=0,
                                      clicked_fn=lambda value=identifier:
                                      self.controller.set_reference_level(value))
                        ui.Button("Ref none", width=0, clicked_fn=lambda:
                                  self.controller.set_reference_level(None))
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Button("Confirm edge", width=0,
                                  clicked_fn=self.controller.finish_edge)
                        ui.Button("Connect intersections", width=0,
                                  clicked_fn=lambda: self.controller.confirm_road(connect_intersections=True))
                        ui.Button("No intersections", width=0,
                                  clicked_fn=lambda: self.controller.confirm_road(connect_intersections=False))
                        ui.Button("Cancel road", width=0,
                                  clicked_fn=self.controller.cancel_gesture)
                        ui.Button("Cancel / Esc", width=0,
                                  clicked_fn=self.controller.cancel_gesture)
                        ui.Button("Delete", width=0,
                                  clicked_fn=self.controller.delete_selected)
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Button("Undirected", width=0,
                                  clicked_fn=lambda: self.controller.set_directed(False))
                        ui.Button("Directed", width=0,
                                  clicked_fn=lambda: self.controller.set_directed(True))
                        ui.Button("Walk", width=0, clicked_fn=lambda:
                                  self.controller.set_new_edge_traversal_mode("walk"))
                        ui.Button("Stairs", width=0, clicked_fn=lambda:
                                  self.controller.set_new_edge_traversal_mode("stairs"))
                        ui.Button("Ramp", width=0, clicked_fn=lambda:
                                  self.controller.set_new_edge_traversal_mode("ramp"))
                    ui.Label("Ground Z is visual only; navigation uses XY and edge properties.")
                    for layer, label in (("ground", "New ground Z"),
                                         ("aerial", "New aerial")):
                        with ui.HStack(height=30, width=ui.Fraction(1)):
                            ui.Label(label, width=110)
                            field = ui.FloatField(width=100)
                            self._new_z_models[layer] = field.model
                            ui.Button("Apply Z", width=0, clicked_fn=lambda value=layer:
                                      self._apply_new_z(value))
                            if layer != "ground":
                                ui.Button("2.5D", width=0, clicked_fn=lambda value=layer:
                                          self.controller.set_new_point_edit_mode(value, "2.5d"))
                                ui.Button("3D", width=0, clicked_fn=lambda value=layer:
                                          self.controller.set_new_point_edit_mode(value, "3d"))
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Label("Selected mode", width=110)
                        ui.Button("2.5D", width=0, clicked_fn=lambda:
                                  self.controller.set_selected_edit_mode("2.5d"))
                        ui.Button("3D", width=0, clicked_fn=lambda:
                                  self.controller.set_selected_edit_mode("3d"))
                    self._node_property_controls = ui.VStack(
                        spacing=3, height=0, width=ui.Fraction(1))
                    with self._node_property_controls:
                        ui.Label("Selected navigation point")
                        with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                            ui.Label("Name", width=45)
                            node_label = ui.StringField(width=210)
                            self._node_label_model = node_label.model
                            ui.Label("Kind", width=40)
                            node_kind = ui.StringField(width=210)
                            self._node_kind_model = node_kind.model
                        with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                            self._node_xyz_models = []
                            for axis in "XYZ":
                                ui.Label(axis, width=15)
                                field = ui.FloatField(width=145)
                                self._node_xyz_models.append(field.model)
                        self._node_level_controls = ui.HStack(
                            height=30, width=ui.Fraction(1), spacing=4)
                        with self._node_level_controls:
                            ui.Label("Level", width=45)
                            node_level = ui.StringField(width=ui.Fraction(1))
                            self._node_level_model = node_level.model
                        with ui.HStack(height=30, width=ui.Fraction(1)):
                            ui.Button("Apply navigation point", width=0,
                                      clicked_fn=self._apply_node_properties)
                    ui.Label("Semantic annotations: changes affect all bound nodes")
                    self._annotation_search = ui.StringField().model
                    self._annotation_search.add_value_changed_fn(lambda *_: self._refresh_annotations())
                    self._annotation_list = ui.Frame(height=180)
                    ui.Button("New annotation on selected node", clicked_fn=lambda: self._load_annotation(None))
                    with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                        ui.Label("Name", width=45)
                        semantic_label = ui.StringField(width=210)
                        self._semantic_label_model = semantic_label.model
                        ui.Label("Kind", width=40)
                        semantic_kind = ui.StringField(width=210)
                        self._semantic_kind_model = semantic_kind.model
                    with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                        ui.Label("Description", width=75)
                        value = ui.StringField(width=ui.Fraction(1))
                        self._semantic_description_model = value.model
                    with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                        ui.Label("Aliases", width=75)
                        value = ui.StringField(width=ui.Fraction(1))
                        self._semantic_aliases_model = value.model
                    with ui.HStack(height=30, width=ui.Fraction(1), spacing=4):
                        ui.Label("Properties JSON", width=100)
                        value = ui.StringField(width=ui.Fraction(1))
                        self._semantic_properties_model = value.model
                        ui.Button("Apply semantic", width=120,
                                  clicked_fn=self._apply_semantic_properties)
                    with ui.HStack(height=30, width=ui.Fraction(1)):
                        ui.Button("Undo", width=0, clicked_fn=self.controller.undo)
                        ui.Button("Redo", width=0, clicked_fn=self.controller.redo)
                        ui.Button("Retry conflict", width=0,
                                  clicked_fn=self.controller.retry_conflict)
                        ui.Button("Save", width=0, clicked_fn=self.controller.save)
                    ui.Label(
                        "Reference layers are visible guides; only the active layer is editable.",
                        width=ui.Fraction(1), height=0, word_wrap=True)
                    self._status = ui.Label(
                        "", width=ui.Fraction(1), height=0, word_wrap=True)
        models = [self._node_label_model, self._node_kind_model,
                  self._node_level_model, *self._node_xyz_models,
                  self._semantic_label_model, self._semantic_kind_model,
                  self._semantic_description_model, self._semantic_aliases_model,
                  self._semantic_properties_model, *self._semantic_xyz_models,
                  *self._new_z_models.values()]
        self._model_subscriptions = [
            (model, model.add_value_changed_fn(self._mark_property_dirty))
            for model in models if model is not None]
        self._bind_escape()
        self.controller.on_change = self.refresh
        self.refresh()
        return self._window

    def _bind_escape(self):
        try:
            import carb.input
            import omni.appwindow
            self._input = carb.input.acquire_input_interface()
            self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()
            def on_key(event):
                page_up = getattr(carb.input.KeyboardInput, "PAGE_UP", None)
                page_down = getattr(carb.input.KeyboardInput, "PAGE_DOWN", None)
                delete_key = getattr(carb.input.KeyboardInput, "DELETE", None)
                if event.type == carb.input.KeyboardEventType.KEY_PRESS:
                    if event.input == delete_key:
                        self.controller.delete_selected()
                        return True
                    if event.input == page_up:
                        if not self.controller.adjust_road_end_height(.1):
                            self.controller.adjust_aerial_road_start_height(.1)
                    elif event.input == page_down:
                        if not self.controller.adjust_road_end_height(-.1):
                            self.controller.adjust_aerial_road_start_height(-.1)
                if event.input == carb.input.KeyboardInput.C:
                    if event.type == carb.input.KeyboardEventType.KEY_PRESS:
                        self.controller.road_free_snap = True
                    elif event.type == carb.input.KeyboardEventType.KEY_RELEASE:
                        self.controller.road_free_snap = False
                    if (self.controller.gesture and self.controller._road_last_point is not None):
                        self.controller.update_road_drag(
                            self.controller._road_last_point,
                            disable_axis_snap=self.controller.road_free_snap)
                if event.type == carb.input.KeyboardEventType.KEY_PRESS:
                    if event.input == carb.input.KeyboardInput.ESCAPE:
                        if self.controller.navigation_binding_mode:
                            self.controller.finish_navigation_binding()
                        else:
                            self.controller.cancel_gesture()
                    elif (event.input == carb.input.KeyboardInput.ENTER
                          and self.controller.edge_end is not None):
                        self.controller.finish_edge()
                return True
            self._key_subscription = self._input.subscribe_to_keyboard_events(
                self._keyboard, on_key)
        except (ImportError, AttributeError, RuntimeError):
            self._input = self._keyboard = self._key_subscription = None

    def destroy(self):
        self.controller.on_change = None
        for model, subscription in self._model_subscriptions:
            try:
                model.remove_value_changed_fn(subscription)
            except (AttributeError, RuntimeError):
                pass
        self._model_subscriptions = []
        if self._input is not None and self._key_subscription is not None:
            try:
                self._input.unsubscribe_to_keyboard_events(
                    self._keyboard, self._key_subscription)
            except (AttributeError, RuntimeError):
                pass
        self._input = self._keyboard = self._key_subscription = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._scroll_frame = self._status = None
        self._level_controls = self._node_property_controls = self._node_level_controls = None


__all__ = [
    "IsaacBridgeError", "IsaacSelectionBridge", "IsaacSelectionWindow",
    "IsaacAuthoringClient", "IsaacEditDispatcher", "IsaacTopologyController",
    "IsaacTopologyWindow",
    "BridgeStatus", "stage_source_usd", "assert_source_matches",
    "prim_selection_payload", "point_selection_payload",
]
