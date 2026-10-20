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

    def identity(self) -> dict[str, Any]:
        if self.context is None:
            raise IsaacBridgeError("Isaac authoring context is not loaded")
        return {
            "revision": self.context["revision"],
            "csrf_token": self.context["csrf_token"],
            "scene_key": self.context["scene_key"],
            "scene_generation": self.context["scene_generation"],
            "context_generation": self.context["context_generation"],
            "domain": self.context["active_domain"],
        }

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

    def set_context(self, *, domain: str | None = None, tool: str | None = None,
                    level_id: str | None = None,
                    _identity: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.context or {}
        extra = {"active_domain": domain or current.get("active_domain")}
        if tool is not None:
            extra["tool"] = tool
        if level_id is not None:
            extra["level_id"] = level_id
        return self.request("/api/editor/context", extra, identity=_identity)

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

    @property
    def pending(self) -> bool:
        return self._future is not None and not self._future.done()

    def submit(self, tag: Any, method: str, *args: Any, **kwargs: Any) -> bool:
        if self._future is not None:
            if not self._future.done():
                return False
            return False
        function = getattr(self.client, method)
        self._tag = tag
        self._future = self._executor.submit(function, *args, **kwargs)
        return True

    def drain(self):
        future = self._future
        if future is None or not future.done():
            return None
        tag, self._tag, self._future = self._tag, None, None
        try:
            return tag, future.result(), None
        except Exception as exc:
            return tag, None, exc

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._future = None
        self._tag = None


def _node_selection(identifier: str) -> dict[str, Any]:
    return {"kind": "node", "collection": None, "id": identifier}


def _edge_selection(identifier: str) -> dict[str, Any]:
    return {"kind": "edge", "collection": None, "id": identifier}


def _region_selection(identifier: str) -> dict[str, Any]:
    return {"kind": "semantic", "collection": "regions", "id": identifier}


_PRESERVE_LEVEL = object()


class IsaacTopologyController:
    """Native editor state machine; viewport math lives in viewport.py."""

    TOOLS = ("add-node", "add-edge", "add-region")

    def __init__(self, dispatcher: IsaacEditDispatcher):
        self.dispatcher = dispatcher
        self.context: dict[str, Any] | None = None
        self.snapshot: dict[str, Any] = {}
        self.selection: dict[str, Any] | None = None
        self.edge_start: str | None = None
        self.edge_end: str | None = None
        self.edge_identity: dict[str, Any] | None = None
        self.region_draft: list[list[float]] = []
        self.region_identity: dict[str, Any] | None = None
        self.gesture: dict[str, Any] | None = None
        self.preview: dict[str, Any] | None = None
        self.directed = False
        self.new_edge_traversal_mode = "walk"
        self.new_node_edit_mode = "3d"
        self.boundary_candidate: dict[str, Any] | None = None
        self.on_boundary_menu: Callable[[dict[str, Any] | None], None] | None = None
        self.retry_request: dict[str, Any] | None = None
        self._active_edit: dict[str, Any] | None = None
        self.result_serial = 0
        self.last_result_tag: Any = None
        self.last_result_succeeded = False
        self.selection_dirty = False
        self.last_error: str | None = None
        self.on_change: Callable[[], None] | None = None
        # Topology coordinates remain metres; viewport.py converts at the
        # camera boundary for stages authored in another linear unit.
        self.meters_per_unit = 1.0

    def update_context(self, context: dict[str, Any]) -> None:
        previous = None if self.context is None else (
            self.context.get("scene_key"), self.context.get("scene_generation"),
            self.context.get("context_generation"), self.context.get("csrf_token"),
            self.context.get("active_domain"), self.context.get("active_level"),
        )
        current = (
            context.get("scene_key"), context.get("scene_generation"),
            context.get("context_generation"), context.get("csrf_token"),
            context.get("active_domain"), context.get("active_level"),
        )
        context_changed = previous is not None and previous != current
        if context_changed:
            self.cancel_gesture(clear_region=True)
            self.selection_dirty = False
        self.context = context
        if context.get("active_domain") != "ground":
            self.new_edge_traversal_mode = "walk"
        self.snapshot = context.get("document_snapshot") or {}
        self.dispatcher.client.update_context(context)
        incoming = context.get("web_selection")
        if (incoming != self.selection and self.gesture is None
                and (context_changed or not self.selection_dirty)):
            self.selection = incoming
            self.selection_dirty = False
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
        return submitted

    def retry_conflict(self) -> bool:
        """Retry a definite 409 only after an explicit user action."""
        request = self.retry_request
        if request is None:
            return False
        kwargs = json.loads(json.dumps(request["kwargs"]))
        kwargs["_identity"] = self.dispatcher.client.identity()
        submitted = self.dispatcher.submit(
            request["tag"], "edit", request["operation"], **kwargs)
        if submitted:
            self._active_edit = {
                "tag": request["tag"], "operation": request["operation"],
                "kwargs": json.loads(json.dumps(kwargs)),
            }
            self.retry_request = None
            self.last_error = None
            self._changed()
        return submitted

    def nodes(self) -> list[dict[str, Any]]:
        return list(self.snapshot.get("nodes") or [])

    def node(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.nodes() if row.get("id") == identifier), None)

    def edge(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.snapshot.get("edges") or []
                     if row.get("id") == identifier), None)

    def regions(self) -> list[dict[str, Any]]:
        return list((self.snapshot.get("semantics") or {}).get("regions") or [])

    def region(self, identifier: str) -> dict[str, Any] | None:
        return next((row for row in self.regions() if row.get("id") == identifier), None)

    def active_z(self) -> float:
        level_id = (self.context or {}).get("active_level")
        for row in (self.context or {}).get("levels") or []:
            if row.get("id") == level_id:
                return float(row.get("support_z_m", 0.0))
        return 0.0

    def set_tool(self, tool: str) -> bool:
        if tool not in self.TOOLS:
            raise IsaacBridgeError("editor tool must be add-node, add-edge or add-region")
        return self.dispatcher.submit(("context", tool), "set_context",
                                      domain=(self.context or {}).get("active_domain"),
                                      tool=tool, level_id=(self.context or {}).get("active_level"),
                                      _identity=self.dispatcher.client.identity())

    def set_domain(self, domain: str) -> bool:
        if domain not in {"ground", "aerial"}:
            raise IsaacBridgeError("domain must be ground or aerial")
        return self.dispatcher.submit(("context", domain), "set_context", domain=domain,
                                      tool=(self.context or {}).get("editor_tool"),
                                      level_id=(self.context or {}).get("active_level"),
                                      _identity=self.dispatcher.client.identity())

    def set_level(self, level_id: str) -> bool:
        return self.dispatcher.submit(("context", level_id), "set_context",
                                      domain=(self.context or {}).get("active_domain"),
                                      tool=(self.context or {}).get("editor_tool"),
                                      level_id=level_id,
                                      _identity=self.dispatcher.client.identity())

    def set_directed(self, value: bool) -> None:
        self.directed = bool(value)
        if self.preview and self.preview.get("kind") == "edge":
            self.preview["directed"] = self.directed
        if self.selection and self.selection.get("kind") == "edge":
            edge = self.edge(self.selection["id"]) or {}
            self._submit_frozen_edit(
                ("edge-direction", self.selection["id"]), "update_edge",
                id=self.selection["id"], directed=self.directed,
                traversal_mode=edge.get("traversal_mode", "walk"))
        self._changed()

    def set_selected_edit_mode(self, mode: str) -> bool:
        if mode not in {"2.5d", "3d"}:
            raise IsaacBridgeError("edit_mode must be 2.5d or 3d")
        if ((self.context or {}).get("active_domain") != "aerial"
                or not self.selection or self.selection.get("kind") != "node"
                or self.node(self.selection["id"]) is None):
            return False
        return self._submit_frozen_edit(
            ("node-edit-mode", self.selection["id"]), "update_node",
            id=self.selection["id"], edit_mode=mode)

    def set_new_node_edit_mode(self, mode: str) -> None:
        if mode not in {"2.5d", "3d"}:
            raise IsaacBridgeError("edit_mode must be 2.5d or 3d")
        self.new_node_edit_mode = mode
        self._changed()

    def set_new_edge_traversal_mode(self, mode: str) -> bool:
        if mode not in {"walk", "stairs", "ramp"}:
            raise IsaacBridgeError("traversal_mode must be walk, stairs or ramp")
        if (self.context or {}).get("active_domain") != "ground" and mode != "walk":
            self.new_edge_traversal_mode = "walk"
            self.last_error = "stairs and ramp edge creation are ground-only"
            self._changed()
            return False
        self.new_edge_traversal_mode = mode
        self.last_error = None
        if self.preview and self.preview.get("kind") == "edge":
            self.preview["traversal_mode"] = mode
        self._changed()
        return True

    def set_selected_node_properties(self, position_xyz: Iterable[float], *,
                                     label: str = "", kind: str = "custom",
                                     level_id: str | None | object = _PRESERVE_LEVEL,
                                     _identity: dict[str, Any] | None = None) -> bool:
        if not self.selection or self.selection.get("kind") != "node":
            return False
        node = self.node(self.selection["id"])
        if node is None:
            return False
        if level_id is _PRESERVE_LEVEL:
            selected_level = node.get("level_id")
        else:
            selected_level = level_id
            known_levels = {row.get("id") for row in
                            (self.context or {}).get("levels") or []}
            if selected_level is not None and selected_level not in known_levels:
                return False
        args = {
            "id": node["id"],
            "position_xyz": list(_finite_xyz(list(position_xyz))),
            "label": str(label).strip(),
            "kind": str(kind).strip() or "custom",
            "level_id": selected_level,
        }
        if (self.context or {}).get("active_domain") == "aerial":
            args["edit_mode"] = node.get("edit_mode", "3d")
        return self._submit_frozen_edit(
            ("node-properties", node["id"]), "update_node",
            identity=_identity, **args)

    def set_selected_region_properties(self, *, label: str = "", kind: str = "room",
                                       height_m: float = 2.5,
                                       _identity: dict[str, Any] | None = None) -> bool:
        if (not self.selection or self.selection.get("kind") != "semantic"
                or self.selection.get("collection") != "regions"):
            return False
        region = self.region(self.selection["id"])
        try:
            height = float(height_m)
        except (TypeError, ValueError):
            return False
        if region is None or not math.isfinite(height) or height <= 0:
            return False
        return self._submit_frozen_edit(
            ("region-properties", region["id"]), "update_region",
            identity=_identity, id=region["id"], label=str(label).strip(),
            kind=str(kind).strip() or "room", height_m=height)

    def set_selected_z(self, z: float) -> bool:
        if not self.selection or self.selection.get("kind") != "node":
            return False
        node = self.node(self.selection["id"])
        if node is None or not math.isfinite(float(z)):
            return False
        position = list(node["position_xyz"])
        position[2] = float(z)
        return self._submit_frozen_edit(
            ("node-z", node["id"]), "move_node",
            id=node["id"], position_xyz=position)

    def select(self, selection: dict[str, Any] | None) -> None:
        self.selection = selection
        self.clear_boundary_candidate()
        self.selection_dirty = True
        self._changed()

    def set_boundary_candidate(self, candidate: dict[str, Any]) -> None:
        self.boundary_candidate = json.loads(json.dumps(candidate))
        if self.on_boundary_menu is not None:
            self.on_boundary_menu(self.boundary_candidate)
        self._changed()

    def clear_boundary_candidate(self) -> None:
        had_candidate = self.boundary_candidate is not None
        self.boundary_candidate = None
        if had_candidate and self.on_boundary_menu is not None:
            self.on_boundary_menu(None)

    def flush_selection(self) -> bool:
        if not self.selection_dirty or self.dispatcher.pending or self.gesture is not None:
            return False
        submitted = self.dispatcher.submit(
            ("selection", self.selection), "select", self.selection,
            _identity=self.dispatcher.client.identity())
        if submitted:
            self.selection_dirty = False
        return submitted

    def add_node(self, point: Iterable[float]) -> bool:
        position = list(_finite_xyz(list(point)))
        domain = (self.context or {}).get("active_domain")
        return self._submit_frozen_edit(
            ("add-node", position), "add_node", position_xyz=position,
            level_id=(self.context or {}).get("active_level"), label="", kind="custom",
            edit_mode=self.new_node_edit_mode if domain == "aerial" else None)

    def activate_node_for_edge(self, identifier: str) -> None:
        if self.edge_start is None:
            self.edge_start = identifier
            self.edge_end = None
            self.edge_identity = self.dispatcher.client.identity()
            self.select(_node_selection(identifier))
        elif identifier == self.edge_start:
            self.last_error = "edge endpoints must differ"
        else:
            start_node, end_node = self.node(self.edge_start), self.node(identifier)
            if start_node is None or end_node is None:
                self.last_error = "edge endpoint no longer exists"
            else:
                self.edge_end = identifier
                mode = (self.new_edge_traversal_mode
                        if (self.context or {}).get("active_domain") == "ground"
                        else "walk")
                self.preview = {
                    "kind": "edge", "from": self.edge_start, "to": identifier,
                    "directed": self.directed,
                    "traversal_mode": mode,
                    "points": [list(start_node["position_xyz"]),
                               list(end_node["position_xyz"])],
                }
                self.last_error = None
        self._changed()

    def finish_edge(self) -> bool:
        if self.edge_start is None or self.edge_end is None:
            return False
        submitted = self._submit_frozen_edit(
            ("add-edge", self.edge_start, self.edge_end), "add_edge",
            identity=self.edge_identity,
            from_id=self.edge_start, to_id=self.edge_end, directed=self.directed,
            traversal_mode=(self.new_edge_traversal_mode
                            if (self.context or {}).get("active_domain") == "ground"
                            else "walk"))
        self._changed()
        return submitted

    def add_region_point(self, point: Iterable[float]) -> None:
        value = list(_finite_xyz(list(point)))
        if not self.region_draft:
            self.region_identity = self.dispatcher.client.identity()
        self.region_draft.append(value)
        self._changed()

    def finish_region(self) -> bool:
        if len(self.region_draft) < 3:
            return False
        identifier = f"region:{uuid.uuid4()}"
        record = {
            "id": identifier, "label": "", "kind": "room",
            "level_id": (self.context or {}).get("active_level"),
            "polygon_xyz": clone_points(self.region_draft),
            "height_m": 2.5, "aliases": [], "properties": {},
            "entrance_node_ids": [],
        }
        submitted = self._submit_frozen_edit(
            ("add-region", identifier), "upsert_semantic",
            identity=self.region_identity, collection="regions", record=record)
        self._changed()
        return submitted

    def delete_selected(self) -> bool:
        selection = self.selection
        if not selection:
            return False
        kind, identifier = selection.get("kind"), selection.get("id")
        if kind == "node":
            args = ("remove_node",)
            kwargs = {"id": identifier}
        elif kind == "edge":
            args = ("remove_edge",)
            kwargs = {"id": identifier}
        elif kind == "semantic" and selection.get("collection") == "regions":
            args = ("remove_semantic",)
            kwargs = {"collection": "regions", "id": identifier}
        else:
            return False
        return self._submit_frozen_edit(
            ("delete", kind, identifier), args[0], **kwargs)

    def undo(self) -> bool:
        return self.dispatcher.submit(
            ("undo",), "undo", _identity=self.dispatcher.client.identity())

    def redo(self) -> bool:
        return self.dispatcher.submit(
            ("redo",), "redo", _identity=self.dispatcher.client.identity())

    def save(self) -> bool:
        return self.dispatcher.submit(
            ("save",), "save", _identity=self.dispatcher.client.identity())

    def cancel_gesture(self, *, clear_region: bool = False) -> None:
        self.gesture = None
        self.preview = None
        self.edge_start = None
        self.edge_end = None
        self.edge_identity = None
        self.retry_request = None
        self._active_edit = None
        self.clear_boundary_candidate()
        if clear_region:
            self.region_draft = []
            self.region_identity = None
        self._changed()

    def begin_drag(self, target: dict[str, Any], point: Iterable[float],
                   *, axis: str | None = None) -> None:
        original = None
        if target.get("kind") == "node":
            node = self.node(target.get("id"))
            if node is not None:
                original = list(_finite_xyz(list(node["position_xyz"])))
        elif target.get("kind") == "region-vertex":
            region = self.region(target.get("id"))
            index = target.get("index")
            if (region is not None and type(index) is int
                    and 0 <= index < len(region.get("polygon_xyz") or [])):
                original = list(_finite_xyz(list(region["polygon_xyz"][index])))
        if original is None:
            return
        self.gesture = {
            "target": dict(target),
            "start": list(_finite_xyz(list(point))),
            "original_position": original,
            "axis": axis,
            "identity": self.dispatcher.client.identity(),
        }
        self.preview = None

    def update_drag(self, point: Iterable[float]) -> None:
        if self.gesture is None:
            return
        target = self.gesture["target"]
        value = list(_finite_xyz(list(point)))
        start = self.gesture["start"]
        original = self.gesture["original_position"]
        delta = [value[index] - start[index] for index in range(3)]
        if target["kind"] == "node":
            axis = self.gesture.get("axis")
            position = list(original)
            if axis in ("x", "y", "z"):
                index = "xyz".index(axis)
                position[index] = original[index] + delta[index]
            else:
                position[0] = original[0] + delta[0]
                position[1] = original[1] + delta[1]
            self.preview = {"kind": "node", "id": target["id"], "position_xyz": position}
        elif target["kind"] == "region-vertex":
            self.preview = {"kind": "region-vertex", "id": target["id"],
                            "index": target["index"],
                            "position_xyz": [original[0] + delta[0],
                                             original[1] + delta[1], original[2]]}
        self._changed()

    def end_drag(self) -> bool:
        preview, gesture, self.gesture = self.preview, self.gesture, None
        if not preview:
            self.preview = None
            self._changed()
            return False
        if preview["kind"] == "node":
            node = self.node(preview["id"]) or {}
            full_xyz = (self.context or {}).get("active_domain") == "aerial" and (
                node.get("edit_mode", "3d") == "3d" and
                preview["position_xyz"][2] != node.get("position_xyz", [0, 0, 0])[2])
            operation = "move_node" if full_xyz else "move_node_xy"
            kwargs = ({"position_xyz": preview["position_xyz"]} if full_xyz
                      else {"position_xy": preview["position_xyz"][:2]})
            submitted = self._submit_frozen_edit(
                ("move-node", preview["id"]), operation,
                identity=gesture["identity"], id=preview["id"], **kwargs)
        else:
            submitted = self._submit_frozen_edit(
                ("move-region-vertex", preview["id"], preview["index"]),
                "move_region_vertex", identity=gesture["identity"],
                id=preview["id"], index=preview["index"],
                position_xy=preview["position_xyz"][:2])
        self._changed()
        return submitted

    def insert_region_vertex(self, identifier: str, index: int,
                             point: Iterable[float]) -> bool:
        return self._submit_frozen_edit(
            ("insert-region-vertex", identifier, index), "insert_region_vertex",
            identity=(self.boundary_candidate or {}).get("identity"),
            id=identifier, index=index,
            position_xyz=list(_finite_xyz(list(point))))

    def add_boundary_vertex(self) -> bool:
        value = self.boundary_candidate
        if not value:
            return False
        submitted = self.insert_region_vertex(
            value["id"], value["index"], value["position_xyz"])
        if submitted:
            self.clear_boundary_candidate()
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
        if error is not None:
            self.last_error = str(error)
            # A stale write leaves the explicitly authored edge/polygon draft
            # visible for inspection. Transport/disconnect failures cancel all
            # interaction state because their frozen identity is no longer safe.
            self.gesture = None
            if getattr(error, "status", None) == 409:
                self.retry_request = request
            else:
                self.retry_request = None
                self.cancel_gesture(clear_region=True)
        else:
            self.last_error = None
            self.retry_request = None
            if isinstance(payload, dict) and payload.get("document"):
                # Write responses carry the full active document. Convert it
                # into the same shape used by the 250 ms compact context feed.
                document = payload["document"]
                compact = {
                    "revision": payload.get("revision"),
                    "version": document.get("version", 2),
                    "nodes": json.loads(json.dumps(document.get("nodes") or [])),
                    "edges": json.loads(json.dumps(document.get("edges") or [])),
                    "semantics": {
                        "regions": json.loads(json.dumps(
                            (document.get("semantics") or {}).get("regions") or []))
                    },
                }
                self.update_context({**(self.context or {}), **payload,
                                     "document_snapshot": compact})
            if tag and tag[0] == "selection":
                self.selection = tag[1]
                self.selection_dirty = False
            elif tag and tag[0] == "add-edge":
                self.edge_start = None
                self.edge_end = None
                self.edge_identity = None
                self.preview = None
            elif tag and tag[0] == "add-region":
                self.region_draft = []
                self.region_identity = None
            elif tag and tag[0] in {"move-node", "move-region-vertex"}:
                self.preview = None
            elif tag and tag[0] == "insert-region-vertex":
                self.clear_boundary_candidate()
        self._changed()


def clone_points(points: Iterable[Iterable[float]]) -> list[list[float]]:
    return [[float(value) for value in point] for point in points]


class IsaacTopologyWindow:
    """Compact native controls for all topology editor operations."""

    def __init__(self, controller: IsaacTopologyController,
                 title: str = "Isaac Topology Editor"):
        self.controller = controller
        self.title = title
        self._window = None
        self._status = None
        self._node_label_model = self._node_kind_model = None
        self._node_level_model = None
        self._node_xyz_models = []
        self._region_label_model = self._region_kind_model = None
        self._region_height_model = None
        self._property_token = None
        self._property_identity = None
        self._property_revision = None
        self._property_dirty = False
        self._property_setting = False
        self._seen_result_serial = controller.result_serial
        self._model_subscriptions = []
        self._input = self._keyboard = self._key_subscription = None
        self._boundary_menu = self._boundary_menu_item = None

    def refresh(self) -> None:
        if self._status is None:
            return
        context = self.controller.context or {}
        selection = self.controller.selection
        selected = selection.get("id") if selection else "none"
        selected_node = (self.controller.node(selection.get("id"))
                         if selection and selection.get("kind") == "node" else None)
        mode = (f" | node {selected_node.get('edit_mode', '3d')}"
                if selected_node is not None
                and context.get("active_domain") == "aerial" else "")
        error = f" | {self.controller.last_error}" if self.controller.last_error else ""
        self._status.text = (
            f"{context.get('active_domain', '?')} | {context.get('editor_tool', '?')} | "
            f"{context.get('active_level', '?')} | selected {selected} | "
            f"new edge {self.controller.new_edge_traversal_mode} | "
            f"new aerial {self.controller.new_node_edit_mode}{mode}{error}"
        )
        if self.controller.result_serial != self._seen_result_serial:
            self._seen_result_serial = self.controller.result_serial
            tag = self.controller.last_result_tag
            if (self.controller.last_result_succeeded and tag
                    and tag[0] in {"node-properties", "region-properties"}):
                self._property_dirty = False
                self._property_revision = None
        token = (context.get("scene_key"), context.get("scene_generation"),
                 context.get("context_generation"),
                 None if selection is None else (
                     selection.get("kind"), selection.get("collection"), selection.get("id")))
        revision = self.controller.snapshot.get("revision")
        if (token == self._property_token
                and (self._property_dirty or revision == self._property_revision)):
            return
        if token != self._property_token:
            self._property_dirty = False
        self._property_token = token
        self._property_revision = revision
        self._property_identity = self.controller.dispatcher.client.identity()
        self._property_setting = True
        try:
            if selection and selection.get("kind") == "node":
                node = self.controller.node(selection.get("id"))
                if node is not None:
                    self._set_model(self._node_label_model, node.get("label", ""))
                    self._set_model(self._node_kind_model, node.get("kind", "custom"))
                    self._set_model(self._node_level_model, node.get("level_id") or "")
                    for model, value in zip(self._node_xyz_models,
                                            node.get("position_xyz", ())):
                        self._set_model(model, float(value))
            elif (selection and selection.get("kind") == "semantic"
                  and selection.get("collection") == "regions"):
                region = self.controller.region(selection.get("id"))
                if region is not None:
                    self._set_model(self._region_label_model, region.get("label", ""))
                    self._set_model(self._region_kind_model, region.get("kind", "room"))
                    self._set_model(self._region_height_model,
                                    float(region.get("height_m", 2.5)))
        finally:
            self._property_setting = False

    def _mark_property_dirty(self, *_args):
        if self._property_setting:
            return
        if not self._property_dirty:
            self._property_identity = self.controller.dispatcher.client.identity()
        self._property_dirty = True

    @staticmethod
    def _set_model(model, value):
        if model is not None:
            model.set_value(value)

    @staticmethod
    def _string(model, fallback=""):
        return model.get_value_as_string() if model is not None else fallback

    def _apply_node_properties(self):
        kwargs = {}
        if self._node_level_model is not None:
            value = self._string(self._node_level_model).strip()
            kwargs["level_id"] = value or None
        self.controller.set_selected_node_properties(
            [model.get_value_as_float() for model in self._node_xyz_models],
            label=self._string(self._node_label_model),
            kind=self._string(self._node_kind_model, "custom"),
            _identity=self._property_identity, **kwargs)

    def _apply_region_properties(self):
        self.controller.set_selected_region_properties(
            label=self._string(self._region_label_model),
            kind=self._string(self._region_kind_model, "room"),
            height_m=self._region_height_model.get_value_as_float(),
            _identity=self._property_identity,
        )

    def show(self):
        try:
            import omni.ui as ui
        except ImportError as exc:
            raise IsaacBridgeError("omni.ui is unavailable") from exc
        self._window = ui.Window(self.title, width=520, height=760)
        with self._window.frame:
            with ui.VStack(spacing=6, height=0):
                ui.Label("Graph")
                with ui.HStack(height=32):
                    ui.Button("Ground", clicked_fn=lambda: self.controller.set_domain("ground"))
                    ui.Button("Aerial", clicked_fn=lambda: self.controller.set_domain("aerial"))
                ui.Label("Tools (selection is implicit)")
                with ui.HStack(height=32):
                    ui.Button("Point", clicked_fn=lambda: self.controller.set_tool("add-node"))
                    ui.Button("Edge", clicked_fn=lambda: self.controller.set_tool("add-edge"))
                    ui.Button("Region", clicked_fn=lambda: self.controller.set_tool("add-region"))
                ui.Label("Level")
                with ui.HStack(height=32):
                    for level in (self.controller.context or {}).get("levels") or []:
                        ui.Button(
                            str(level.get("label") or level.get("id")),
                            clicked_fn=lambda identifier=level.get("id"):
                                self.controller.set_level(identifier),
                        )
                with ui.HStack(height=32):
                    ui.Button("Confirm edge", clicked_fn=self.controller.finish_edge)
                    ui.Button("Finish region", clicked_fn=self.controller.finish_region)
                    ui.Button("Add boundary point", clicked_fn=self.controller.add_boundary_vertex)
                    ui.Button("Cancel / Esc", clicked_fn=lambda: self.controller.cancel_gesture(clear_region=True))
                with ui.HStack(height=32):
                    ui.Button("Undirected", clicked_fn=lambda: self.controller.set_directed(False))
                    ui.Button("Directed", clicked_fn=lambda: self.controller.set_directed(True))
                    ui.Button("Delete", clicked_fn=self.controller.delete_selected)
                with ui.HStack(height=32):
                    ui.Label("New ground edge")
                    ui.Button("Walk", clicked_fn=lambda:
                              self.controller.set_new_edge_traversal_mode("walk"))
                    ui.Button("Stairs", clicked_fn=lambda:
                              self.controller.set_new_edge_traversal_mode("stairs"))
                    ui.Button("Ramp", clicked_fn=lambda:
                              self.controller.set_new_edge_traversal_mode("ramp"))
                with ui.HStack(height=32):
                    ui.Label("New aerial node")
                    ui.Button("2.5D", clicked_fn=lambda: self.controller.set_new_node_edit_mode("2.5d"))
                    ui.Button("3D", clicked_fn=lambda: self.controller.set_new_node_edit_mode("3d"))
                with ui.HStack(height=32):
                    ui.Button("Node 2.5D", clicked_fn=lambda: self.controller.set_selected_edit_mode("2.5d"))
                    ui.Button("Node 3D", clicked_fn=lambda: self.controller.set_selected_edit_mode("3d"))
                ui.Label("Selected node properties")
                with ui.HStack(height=32):
                    ui.Label("Name", width=48)
                    node_label = ui.StringField()
                    self._node_label_model = node_label.model
                    ui.Label("Kind", width=42)
                    node_kind = ui.StringField()
                    self._node_kind_model = node_kind.model
                with ui.HStack(height=32):
                    self._node_xyz_models = []
                    for axis in "XYZ":
                        ui.Label(axis, width=18)
                        field = ui.FloatField()
                        self._node_xyz_models.append(field.model)
                with ui.HStack(height=32):
                    ui.Label("Level (blank = Unbound)", width=150)
                    node_level = ui.StringField()
                    self._node_level_model = node_level.model
                    ui.Button("Apply node", clicked_fn=self._apply_node_properties)
                ui.Label("Selected region properties")
                with ui.HStack(height=32):
                    ui.Label("Name", width=48)
                    region_label = ui.StringField()
                    self._region_label_model = region_label.model
                    ui.Label("Kind", width=42)
                    region_kind = ui.StringField()
                    self._region_kind_model = region_kind.model
                with ui.HStack(height=32):
                    ui.Label("Height", width=48)
                    region_height = ui.FloatField()
                    self._region_height_model = region_height.model
                    ui.Button("Apply region", clicked_fn=self._apply_region_properties)
                with ui.HStack(height=32):
                    ui.Button("Undo", clicked_fn=self.controller.undo)
                    ui.Button("Redo", clicked_fn=self.controller.redo)
                    ui.Button("Retry conflict", clicked_fn=self.controller.retry_conflict)
                    ui.Button("Save", clicked_fn=self.controller.save)
                ui.Label("Aerial 3D nodes use the XYZ gizmo; 2.5D drags preserve Z.")
                self._status = ui.Label("")
        models = [self._node_label_model, self._node_kind_model, self._node_level_model,
                  *self._node_xyz_models, self._region_label_model,
                  self._region_kind_model, self._region_height_model]
        self._model_subscriptions = [
            (model, model.add_value_changed_fn(self._mark_property_dirty))
            for model in models if model is not None
        ]
        self._bind_escape()
        self.controller.on_boundary_menu = self._show_boundary_menu
        self.controller.on_change = self.refresh
        self.refresh()
        return self._window

    def _show_boundary_menu(self, candidate):
        if candidate is None:
            if self._boundary_menu is not None:
                self._boundary_menu.visible = False
            return
        import omni.ui as ui
        if self._boundary_menu is None:
            self._boundary_menu = ui.Menu("Region boundary")
            with self._boundary_menu:
                self._boundary_menu_item = ui.MenuItem(
                    "Add point here", triggered_fn=self.controller.add_boundary_vertex)
        self._boundary_menu.show()

    def _bind_escape(self):
        try:
            import carb.input
            import omni.appwindow
            self._input = carb.input.acquire_input_interface()
            self._keyboard = omni.appwindow.get_default_app_window().get_keyboard()

            def on_key(event):
                if (event.type == carb.input.KeyboardEventType.KEY_PRESS
                        and event.input == carb.input.KeyboardInput.ESCAPE):
                    self.controller.cancel_gesture(clear_region=True)
                return True

            self._key_subscription = self._input.subscribe_to_keyboard_events(
                self._keyboard, on_key)
        except (ImportError, AttributeError, RuntimeError):
            self._input = self._keyboard = self._key_subscription = None

    def destroy(self):
        self.controller.on_change = None
        if self.controller.on_boundary_menu == self._show_boundary_menu:
            self.controller.on_boundary_menu = None
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
        if self._boundary_menu is not None:
            self._boundary_menu.destroy()
        self._boundary_menu = self._boundary_menu_item = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
        self._status = None
        self._node_label_model = self._node_kind_model = None
        self._node_level_model = None
        self._node_xyz_models = []
        self._region_label_model = self._region_kind_model = None
        self._region_height_model = None
        self._property_token = None
        self._property_identity = None
        self._property_revision = None
        self._property_dirty = False
        self._property_setting = False


__all__ = [
    "IsaacBridgeError", "IsaacSelectionBridge", "IsaacSelectionWindow",
    "IsaacAuthoringClient", "IsaacEditDispatcher", "IsaacTopologyController",
    "IsaacTopologyWindow",
    "BridgeStatus", "stage_source_usd", "assert_source_matches",
    "prim_selection_payload", "point_selection_payload",
]
