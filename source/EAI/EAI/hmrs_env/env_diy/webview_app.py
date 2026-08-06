from __future__ import annotations

import argparse
import ctypes
import json
import sys
import threading
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT
from .storage import save_task_with_payload


HTML_PATH = Path(__file__).with_name("env_diy_app.html")
_IGNORED_QT_MESSAGE_PREFIXES = (
    "GBM is not supported with the current configuration.",
    "Release of profile requested but WebEnginePage still not deleted.",
)


def _qt_message_handler(_message_type: Any, _context: Any, message: str) -> None:
    if message.startswith(_IGNORED_QT_MESSAGE_PREFIXES):
        return
    sys.stderr.write(f"{message}\n")


def _initialize_linux_qt() -> None:
    if not sys.platform.startswith("linux"):
        return

    try:
        fontconfig = ctypes.CDLL("libfontconfig.so.1")
        fontconfig.FcInit.restype = ctypes.c_int
        fontconfig.FcInit()
    except (AttributeError, OSError):
        pass

    try:
        from PyQt6.QtCore import qInstallMessageHandler
    except ImportError:
        return
    qInstallMessageHandler(_qt_message_handler)


class EnvDiyWebViewBridge:
    """Minimal bridge between the HTML tutorial and the Python launcher."""

    def __init__(self, output_path: Path | None = None, *, repo_root: Path = REPO_ROOT) -> None:
        self.output_path = Path(output_path) if output_path is not None else None
        self.repo_root = repo_root
        self.window = None

    def attach_window(self, window: Any) -> None:
        self.window = window

    def submit_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise TypeError("Env DIY payload must be a JSON object.")
        task_name = str(payload.get("task_name", "")).strip()
        if not task_name:
            raise ValueError("Env DIY payload must include a non-empty task_name.")
        path, saved_task = save_task_with_payload(task_name, payload, repo_root=self.repo_root)
        result = {
            "should_run": bool(payload.get("should_run", True)),
            "selection": saved_task,
            "saved_task": saved_task,
        }
        if self.output_path is not None:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        if self.window is not None:
            close_timer = threading.Timer(0.2, self.window.destroy)
            close_timer.daemon = True
            close_timer.start()
        return {"ok": True, "saved_path": str(path)}


def _load_webview():
    try:
        import webview  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on local environment
        package = "pywebview[qt]" if sys.platform.startswith("linux") else "pywebview"
        raise ModuleNotFoundError(
            "pywebview is required for the Env DIY web window. "
            f"Install it for this Python with `{sys.executable} -m pip install '{package}'`."
        ) from exc
    return webview


def launch(
    *,
    output_path: Path | None = None,
    html_path: Path = HTML_PATH,
    repo_root: Path = REPO_ROOT,
    webview_module=None,
) -> int:
    html_path = Path(html_path)
    if not html_path.is_file():
        raise FileNotFoundError(html_path)
    webview = webview_module or _load_webview()
    _initialize_linux_qt()
    bridge = EnvDiyWebViewBridge(output_path=output_path, repo_root=repo_root)
    window = webview.create_window(
        "EAI Env DIY",
        html_path.resolve().as_uri(),
        width=1360,
        height=860,
        min_size=(1080, 700),
        js_api=bridge,
    )
    bridge.attach_window(window)
    start_options = {"gui": "qt"} if sys.platform.startswith("linux") else {}
    webview.start(**start_options)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EAI Env DIY webview window")
    parser.add_argument("--keyboard-preflight-output", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return launch(output_path=args.keyboard_preflight_output)


if __name__ == "__main__":
    raise SystemExit(main())
