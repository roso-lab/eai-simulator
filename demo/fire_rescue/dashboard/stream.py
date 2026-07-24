"""
Isaac Sim 实时画面 WebSocket 流式传输服务器  v2

架构说明：
  - 使用 omni.replicator.core 的 LdrColor 标注器从 Isaac Sim 视口捕获 RGB 帧
  - 使用 asyncio + websockets 将 JPEG 帧以【二进制 WebSocket 帧】推送至客户端
    （相比 base64-JSON 节省约 33% 带宽，降低编解码延迟）
  - WebSocket 服务器在后台线程运行，不阻塞仿真主循环
  - viewer.html / index_v4.html 可在浏览器中接收并实时显示仿真画面

用法：
  1. import stream_server
  2. stream_server.start(port=8766, fps=30)
  3. stream_server.setup_capture(resolution=(1280, 720))
  4. 主循环中调用 stream_server.broadcast_if_ready()
  5. 浏览器连接 ws://localhost:8766

帧格式（二进制）：
  原始 JPEG 字节，直接通过 WebSocket binary frame 发送。
  客户端示例：
    ws.binaryType = 'arraybuffer';
    ws.onmessage = e => {
      const blob = new Blob([e.data], {type: 'image/jpeg'});
      img.src = URL.createObjectURL(blob);
    };
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 全局状态 ──────────────────────────────────────────────────────────────────
_clients: set = set()
_loop: Optional[asyncio.AbstractEventLoop] = None
_server_thread: Optional[threading.Thread] = None
_annotator = None          # omni.replicator LdrColor 标注器
_render_product = None
_capture_camera_path: Optional[str] = None
_capture_config: Optional[dict] = None
_last_capture_retry_time: float = 0.0
_last_capture_error: str = ""
_frame_diagnostics_logged: bool = False
_red_frame_warning_logged: bool = False

_frame_lock = threading.Lock()
_latest_jpeg: Optional[bytes] = None

_target_fps: int = 30
_last_broadcast_time: float = 0.0

_frame_count: int = 0
_client_count: int = 0

# JPEG 编码质量（越低文件越小，延迟越低）
_JPEG_QUALITY: int = 60


# ── WebSocket 服务器 ──────────────────────────────────────────────────────────

async def _client_handler(websocket):
    global _clients, _client_count
    _clients.add(websocket)
    _client_count = len(_clients)
    remote = getattr(websocket, "remote_address", "?")
    logger.info(f"[StreamServer] 客户端连接：{remote}，当前 {_client_count} 个")

    # 新连接立即推最新帧
    with _frame_lock:
        latest = _latest_jpeg
    if latest is not None:
        try:
            await websocket.send(latest)   # 二进制帧
        except Exception:
            pass

    try:
        await websocket.wait_closed()
    finally:
        _clients.discard(websocket)
        _client_count = len(_clients)
        logger.info(f"[StreamServer] 客户端断开：{remote}，剩余 {_client_count} 个")


async def _server_main(host: str, port: int):
    import websockets
    async with websockets.serve(_client_handler, host, port,
                                max_size=None,          # 不限制帧大小
                                compression=None):      # 禁用压缩（JPEG 已压缩）
        print(f"[StreamServer] ✅ WebSocket 视频流已启动：ws://{host}:{port}")
        await asyncio.Future()


def _run_server(host: str, port: int):
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        _loop.run_until_complete(_server_main(host, port))
    except Exception as e:
        print(f"[StreamServer] ❌ 异常退出：{e}")


# ── 公共 API ──────────────────────────────────────────────────────────────────

def start(host: str = "0.0.0.0", port: int = 8766, fps: int = 30,
          jpeg_quality: int = 60) -> None:
    """启动后台 WebSocket 流服务器。"""
    global _server_thread, _target_fps, _JPEG_QUALITY
    _target_fps = max(1, fps)
    _JPEG_QUALITY = max(10, min(95, jpeg_quality))

    if _server_thread is not None and _server_thread.is_alive():
        logger.warning("[StreamServer] 已在运行，跳过。")
        return

    _server_thread = threading.Thread(
        target=_run_server, args=(host, port), daemon=True, name="StreamServer"
    )
    _server_thread.start()

    for _ in range(20):
        if _loop is not None:
            break
        time.sleep(0.1)
    logger.info(f"[StreamServer] 已启动，目标 {_target_fps} fps，JPEG 质量 {_JPEG_QUALITY}")


def setup_capture(
    resolution: tuple[int, int] = (1280, 720),
    camera_path: str = "/OmniverseKit_Persp",
    fallback_camera_paths: tuple[str, ...] = (),
    retry_interval_s: float = 2.0,
) -> bool:
    """创建 omni.replicator.core 渲染产品并绑定 LdrColor 标注器。"""
    global _annotator, _render_product, _capture_camera_path, _capture_config
    global _last_capture_retry_time, _last_capture_error
    global _frame_diagnostics_logged, _red_frame_warning_logged
    _capture_config = {
        "resolution": tuple(resolution),
        "camera_path": str(camera_path),
        "fallback_camera_paths": tuple(str(path) for path in fallback_camera_paths),
        "retry_interval_s": max(0.0, float(retry_interval_s)),
    }
    _annotator = None
    _render_product = None
    _capture_camera_path = None
    _last_capture_error = ""
    _last_capture_retry_time = 0.0
    _frame_diagnostics_logged = False
    _red_frame_warning_logged = False
    return _try_setup_capture_once(log_all_failures=True)


def _try_setup_capture_once(*, log_all_failures: bool = False) -> bool:
    """Try the configured camera list once; keep config for later retries."""
    global _annotator, _render_product, _capture_camera_path, _last_capture_error
    if not _capture_config:
        return False
    resolution = _capture_config["resolution"]
    camera_paths = (
        _capture_config["camera_path"],
        *_capture_config.get("fallback_camera_paths", ()),
    )
    seen: set[str] = set()
    errors: list[str] = []
    try:
        import omni.replicator.core as rep
    except Exception as e:
        _last_capture_error = str(e)
        logger.error(f"[StreamServer] 创建渲染产品失败：{e}")
        return False
    logger.debug(f"[StreamServer] omni.replicator.core={getattr(rep, '__file__', None)}")

    for candidate in camera_paths:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            logger.info(
                f"[StreamServer] 准备创建渲染产品 camera={candidate} resolution={resolution}"
            )
            logger.debug(f"[StreamServer] 相机 prim 诊断: {_describe_camera_prim(candidate)}")
            render_product = rep.create.render_product(
                candidate, resolution=resolution
            )
            annotator = rep.AnnotatorRegistry.get_annotator("LdrColor")
            annotator.attach([render_product])
            _render_product = render_product
            _annotator = annotator
            _capture_camera_path = candidate
            _last_capture_error = ""
            logger.info(
                f"[StreamServer] 渲染产品已创建 {resolution[0]}×{resolution[1]} "
                f"(camera={candidate}, render_product={render_product!r})"
            )
            return True
        except Exception as e:
            errors.append(f"{candidate}: {e}")

    _last_capture_error = "; ".join(errors) if errors else "no camera paths configured"
    if log_all_failures:
        logger.error(f"[StreamServer] 创建渲染产品失败：{_last_capture_error}")
    else:
        logger.debug(f"[StreamServer] 创建渲染产品重试失败：{_last_capture_error}")
    return False


def _retry_capture_if_due() -> bool:
    global _last_capture_retry_time
    if _annotator is not None or not _capture_config:
        return _annotator is not None
    now = time.monotonic()
    interval = float(_capture_config.get("retry_interval_s", 2.0))
    if now - _last_capture_retry_time < interval:
        return False
    _last_capture_retry_time = now
    return _try_setup_capture_once(log_all_failures=False)


def broadcast_if_ready() -> bool:
    """
    若到达下一帧时间，则捕获当前帧并广播（主循环每迭代调用）。
    内部自动限速到 _target_fps。
    """
    global _last_broadcast_time, _frame_count, _latest_jpeg
    global _frame_diagnostics_logged, _red_frame_warning_logged

    if _annotator is None and not _retry_capture_if_due():
        return False

    # 无客户端连接时完全跳过捕获和编码（核心 FPS 优化）
    if not _clients:
        return False

    now = time.monotonic()
    if now - _last_broadcast_time < 1.0 / _target_fps:
        return False
    _last_broadcast_time = now

    try:
        rgba = _annotator.get_data()
        if rgba is None or rgba.size == 0:
            return False

        if not _frame_diagnostics_logged:
            summary = _summarize_rgb_frame(rgba)
            logger.info(f"[StreamServer] 首帧诊断: {summary}")
            _frame_diagnostics_logged = True

            if summary.get("mostly_red") and not _red_frame_warning_logged:
                logger.error(
                    "[StreamServer] 检测到 Replicator 输出几乎全红帧；"
                    "WebSocket 正在推送 IsaacSim 的实际输出，问题更可能在 Kit/Replicator/相机渲染链路。"
                )
                _red_frame_warning_logged = True

        jpeg_bytes = _rgba_to_jpeg(rgba, quality=_JPEG_QUALITY)
        if jpeg_bytes is None:
            return False

        with _frame_lock:
            _latest_jpeg = jpeg_bytes

        if _loop is not None and _clients:
            asyncio.run_coroutine_threadsafe(_broadcast_binary(jpeg_bytes), _loop)
            _frame_count += 1
            return True

    except Exception as e:
        logger.debug(f"[StreamServer] 帧捕获忽略：{e}")

    return False


def broadcast_numpy(frame: np.ndarray) -> bool:
    """直接广播 numpy 数组帧（(H,W,3) RGB 或 (H,W,4) RGBA）。"""
    global _latest_jpeg, _frame_count
    try:
        jpeg_bytes = _rgba_to_jpeg(frame)
        if jpeg_bytes is None:
            return False
        with _frame_lock:
            _latest_jpeg = jpeg_bytes
        if _loop is not None and _clients:
            asyncio.run_coroutine_threadsafe(_broadcast_binary(jpeg_bytes), _loop)
            _frame_count += 1
            return True
    except Exception as e:
        logger.debug(f"[StreamServer] broadcast_numpy 错误：{e}")
    return False


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _summarize_rgb_frame(frame: np.ndarray) -> dict:
    """Return compact diagnostics for a Replicator RGB/RGBA frame."""
    arr = np.asarray(frame)
    summary = {
        "shape": tuple(int(v) for v in arr.shape),
        "dtype": str(arr.dtype),
        "size": int(arr.size),
        "mean_rgb": None,
        "min_rgb": None,
        "max_rgb": None,
        "red_fraction": 0.0,
        "mostly_red": False,
    }
    if arr.ndim != 3 or arr.shape[0] == 0 or arr.shape[1] == 0 or arr.shape[2] < 3:
        return summary
    rgb = arr[:, :, :3].astype(np.float32, copy=False)
    mean_rgb = rgb.mean(axis=(0, 1))
    min_rgb = rgb.min(axis=(0, 1))
    max_rgb = rgb.max(axis=(0, 1))
    red_mask = (rgb[:, :, 0] >= 220.0) & (rgb[:, :, 1] <= 35.0) & (rgb[:, :, 2] <= 35.0)
    red_fraction = float(red_mask.mean())
    summary.update(
        {
            "mean_rgb": tuple(round(float(v), 2) for v in mean_rgb),
            "min_rgb": tuple(round(float(v), 2) for v in min_rgb),
            "max_rgb": tuple(round(float(v), 2) for v in max_rgb),
            "red_fraction": round(red_fraction, 4),
            "mostly_red": red_fraction >= 0.98,
        }
    )
    return summary


def _describe_camera_prim(camera_path: str) -> dict:
    """Return USD camera prim diagnostics without requiring callers to import pxr."""
    out = {"path": str(camera_path), "valid": False}
    try:
        import omni.usd
        from pxr import UsdGeom

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            out["error"] = "no current USD stage"
            return out
        prim = stage.GetPrimAtPath(camera_path)
        out["valid"] = bool(prim and prim.IsValid())
        if not out["valid"]:
            return out
        out["type_name"] = prim.GetTypeName()
        cam = UsdGeom.Camera(prim)
        out["is_camera"] = bool(cam)
        if cam:
            clipping = cam.GetClippingRangeAttr().Get()
            out["clipping_range"] = tuple(float(v) for v in clipping) if clipping is not None else None
        xformable = UsdGeom.Xformable(prim)
        out["xform_ops"] = [op.GetOpName() for op in xformable.GetOrderedXformOps()]
        out["local_to_world"] = str(xformable.ComputeLocalToWorldTransform(0.0))
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _rgba_to_jpeg(rgba: np.ndarray, quality: int = None) -> Optional[bytes]:
    """(H,W,4) RGBA 或 (H,W,3) RGB → JPEG bytes。"""
    q = quality if quality is not None else _JPEG_QUALITY
    try:
        from PIL import Image
        if rgba.ndim != 3:
            return None
        h, w, c = rgba.shape
        if h == 0 or w == 0:
            return None
        rgb = rgba[:, :, :3] if c == 4 else rgba
        img = Image.fromarray(rgb.astype(np.uint8), "RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=False, subsampling=0)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"[StreamServer] JPEG 编码失败：{e}")
        return None


async def _broadcast_binary(jpeg_bytes: bytes) -> None:
    """向所有客户端发送二进制 JPEG 帧（在事件循环内执行）。"""
    if not _clients:
        return
    dead: set = set()
    for client in list(_clients):
        try:
            await asyncio.wait_for(client.send(jpeg_bytes), timeout=0.5)
        except Exception:
            dead.add(client)
    _clients.difference_update(dead)


def get_stats() -> dict:
    return {
        "client_count": _client_count,
        "frame_count": _frame_count,
        "target_fps": _target_fps,
        "jpeg_quality": _JPEG_QUALITY,
        "has_annotator": _annotator is not None,
        "capture_camera_path": _capture_camera_path,
        "last_capture_error": _last_capture_error,
        "server_running": _server_thread is not None and _server_thread.is_alive(),
    }
