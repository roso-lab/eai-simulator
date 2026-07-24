"""Isaac Sim extension entry point for Env DIY 3D authoring."""

from __future__ import annotations

import gc
import os
import asyncio
from pathlib import Path

import omni.ext
import omni.kit.app

from .model import AuthoringModel
from .assets import AssetDownloadManager
from .drop import register_scene_drop_protocol, unregister_scene_drop_protocol
from .preview_stage import PreviewStage
from .protocol import (
    AuthoringResult,
    clear_in_process_callback,
    in_process_restore_context,
    take_in_process_callback,
    write_cancelled_if_missing,
)
from .ui import EnvDiyWindow
from .ui import DRAG_PREFIX


class EnvDiyExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        self._kit_event_loop = asyncio.get_event_loop()
        default_repo_root = Path(__file__).resolve().parents[3]
        self._repo_root = Path(os.environ.get("EAI_ENV_DIY_REPO_ROOT", default_repo_root))
        self._result_path = Path(
            os.environ.get(
                "EAI_ENV_DIY_RESULT_PATH",
                self._repo_root / "tmp" / "env_diy_extension_result.json",
            )
        )
        self._standalone = os.environ.get("EAI_ENV_DIY_STANDALONE") == "1"
        self._in_process_callback = take_in_process_callback()
        initial_selection, restore_error = in_process_restore_context()
        self._finished = False
        self._finishing = False
        self._scene_drop_protocol_registered = register_scene_drop_protocol(DRAG_PREFIX)
        self._model = (
            AuthoringModel.from_selection_dict(initial_selection)
            if initial_selection is not None
            else AuthoringModel("plane")
        )
        self._preview = PreviewStage()
        self._preview.initialize(self._model.scene_key or "plane")
        if self._model.robots:
            self._preview.rebuild_robots(self._model)
        self._asset_manager = AssetDownloadManager(dispatch_to_kit=self._dispatch_to_kit)
        self._window = EnvDiyWindow(
            self._model,
            self._preview,
            self._repo_root,
            self._finish,
            self._asset_manager,
        )
        if restore_error:
            self._window._set_status(f"Formal environment failed; authoring restored: {restore_error}")

    def _dispatch_to_kit(self, callback) -> None:
        app = omni.kit.app.get_app()
        post = getattr(app, "post_to_main_thread", None)
        if callable(post):
            post(callback)
            return
        event_loop = getattr(self, "_kit_event_loop", None)
        if event_loop is not None:
            if not event_loop.is_closed():
                try:
                    event_loop.call_soon_threadsafe(callback)
                except RuntimeError:
                    pass
            return
        # This is only a compatibility fallback for Kit test doubles.
        callback()

    def _finish(self, result: AuthoringResult) -> None:
        if self._finished or getattr(self, "_finishing", False):
            return
        self._finishing = True
        try:
            in_process_callback = getattr(self, "_in_process_callback", None)
            if in_process_callback is not None:
                in_process_callback(result)
                self._window.hide()
                self._finished = True
                return
            result.write(self._result_path)
            try:
                self._preview.remove_preview()
            except Exception as exc:
                print(f"[EnvDiyExtension] Warning: Failed to remove preview during finish: {exc}")
            if self._standalone:
                omni.kit.app.get_app().post_quit(0)
            else:
                self._window.hide()
        except Exception:
            self._finished = False
            raise
        else:
            self._finished = True
        finally:
            self._finishing = False

    def on_shutdown(self) -> None:
        if (
            not getattr(self, "_finished", False)
            and getattr(self, "_in_process_callback", None) is None
            and hasattr(self, "_result_path")
        ):
            write_cancelled_if_missing(self._result_path)
        window = getattr(self, "_window", None)
        preview = getattr(self, "_preview", None)
        asset_manager = getattr(self, "_asset_manager", None)
        self._window = None
        self._preview = None
        self._model = None
        self._asset_manager = None
        try:
            if window is not None:
                window.destroy()
        finally:
            try:
                if preview is not None:
                    preview.remove_preview()
            finally:
                try:
                    if asset_manager is not None:
                        asset_manager.close()
                finally:
                    if getattr(self, "_scene_drop_protocol_registered", False):
                        unregister_scene_drop_protocol(DRAG_PREFIX)
                        self._scene_drop_protocol_registered = False
        del window, preview, asset_manager
        clear_in_process_callback()
        gc.collect()
