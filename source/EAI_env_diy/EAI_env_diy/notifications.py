"""Optional Kit notifications used by the Env DIY authoring UI."""

from __future__ import annotations


def post_preview_error(message: str) -> bool:
    """Post a visible warning when Kit's notification extension is available."""
    try:
        from omni.kit.notification_manager import NotificationStatus, post_notification
    except (ImportError, ModuleNotFoundError):
        return False
    try:
        notification = post_notification(str(message), status=NotificationStatus.WARNING)
    except TypeError:
        # Older Kit builds accept the message but not the status keyword.
        try:
            notification = post_notification(str(message))
        except Exception:
            return False
    except Exception:
        return False
    return notification is not None
