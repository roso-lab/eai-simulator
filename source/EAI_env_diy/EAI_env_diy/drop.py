"""Viewport drag/drop delegate that preserves the picked world position."""

from __future__ import annotations

from omni.kit.viewport.window.dragdrop.delegate import DragDropDelegate


def register_scene_drop_protocol(protocol: str) -> bool:
    """Keep Kit's built-in file drop handlers away from Env DIY payloads."""
    try:
        from omni.kit.viewport.window.dragdrop.scene_drop_delegate import SceneDropDelegate
    except (ImportError, ModuleNotFoundError):
        return False
    add_protocol = getattr(SceneDropDelegate, "add_ignored_protocol", None)
    if not callable(add_protocol):
        return False
    add_protocol(str(protocol))
    return True


def unregister_scene_drop_protocol(protocol: str) -> bool:
    """Undo :func:`register_scene_drop_protocol` during extension shutdown."""
    try:
        from omni.kit.viewport.window.dragdrop.scene_drop_delegate import SceneDropDelegate
    except (ImportError, ModuleNotFoundError):
        return False
    remove_protocol = getattr(SceneDropDelegate, "remove_ignored_protocol", None)
    if not callable(remove_protocol):
        return False
    remove_protocol(str(protocol))
    return True


class EnvDiyViewportDropDelegate(DragDropDelegate):
    def __init__(self, prefix: str, on_drop) -> None:
        super().__init__()
        self._prefix = prefix
        self._on_drop = on_drop

    @property
    def add_outline(self) -> bool:
        return True

    def accepted(self, drop_data: dict) -> bool:
        return str(drop_data.get("mime_data", "")).startswith(self._prefix)

    def dropped(self, drop_data: dict) -> None:
        if not self.accepted(drop_data):
            return
        prim_path = drop_data.get("prim_path")
        # ``prim_path`` is normally an ``Sdf.Path``.  Some Isaac Sim builds
        # pass the dragged MIME payload string back as this field when the
        # drop originated from an omni.ui button.  Never coerce that URI into
        # an SdfPath: it is not a valid prim path and produces warnings such as
        # ``Ill-formed SdfPath <eai-env-diy://robot/lite3>``.
        if prim_path and hasattr(prim_path, "pathString"):
            target = str(prim_path.pathString)
        else:
            candidate = str(prim_path or "")
            target = candidate if candidate.startswith("/") else ""
        self._on_drop(
            str(drop_data["mime_data"]),
            target,
            tuple(float(item) for item in drop_data.get("world_space_pos", (0.0, 0.0, 0.0))),
            str(drop_data.get("usd_context_name", "")),
        )
