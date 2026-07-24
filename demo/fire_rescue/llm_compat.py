from __future__ import annotations

from typing import Any


_PATCH_MARKER = "_fire_rescue_original_is_typeddict"


def _original_func(func: Any) -> Any:
    return getattr(func, _PATCH_MARKER, func)


def _looks_like_typeddict(tp: Any) -> bool:
    if not isinstance(tp, type):
        return False
    if tp is dict:
        return False
    if not issubclass(tp, dict):
        return False
    return (
        hasattr(tp, "__required_keys__")
        and hasattr(tp, "__optional_keys__")
        and hasattr(tp, "__annotations__")
    )


def ensure_openai_typeddict_compat() -> bool:
    """Keep OpenAI SDK request-body cleanup working after Isaac reloads typing helpers."""

    try:
        import typing
        import typing_extensions
        from openai._utils import _compat as openai_compat
        from openai._utils import _transform as openai_transform
    except Exception:
        return False

    original_typing_ext = _original_func(typing_extensions.is_typeddict)
    original_openai = _original_func(openai_compat.is_typeddict)

    def is_typeddict(tp: Any) -> bool:
        for checker in (
            original_typing_ext,
            getattr(typing, "is_typeddict", None),
            original_openai,
        ):
            if checker is None:
                continue
            try:
                if checker(tp):
                    return True
            except Exception:
                pass
        return _looks_like_typeddict(tp)

    setattr(is_typeddict, _PATCH_MARKER, original_typing_ext)
    typing_extensions.is_typeddict = is_typeddict
    openai_compat.is_typeddict = is_typeddict
    openai_transform.is_typeddict = is_typeddict
    return True
