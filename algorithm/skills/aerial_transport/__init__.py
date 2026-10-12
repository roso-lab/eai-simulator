"""Pure aerial transport API; native execution is owned by task.executors."""
from .geometry import RigidPose
from .trajectory import VerticalProfile


def __getattr__(name):
    # Navigation uses the geometry module; defer the state machine to avoid an
    # import cycle without importing any native dependency at package import.
    if name in {"AerialPickPlaceProvider", "AerialPickPlaceResponse", "normalize_aerial_pick_place_request"}:
        from . import provider
        return getattr(provider, name)
    raise AttributeError(name)


__all__ = ["AerialPickPlaceProvider", "AerialPickPlaceResponse", "RigidPose", "VerticalProfile", "normalize_aerial_pick_place_request"]
