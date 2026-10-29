"""PickPlace robot adapters. Importing these does not start Isaac Sim."""
from .arm import ArmPickPlaceAdapter
from .drone import DronePickPlaceAdapter
from .pepper import PepperPickPlaceAdapter

__all__ = ["ArmPickPlaceAdapter", "DronePickPlaceAdapter", "PepperPickPlaceAdapter"]


def __getattr__(name):
    if name == "G1PickPlaceAdapter":
        # G1's USD planning dependency is available in the Isaac runtime;
        # other local backends remain importable without pxr installed.
        from .g1 import G1PickPlaceAdapter
        return G1PickPlaceAdapter
    raise AttributeError(name)


__all__ += ["G1PickPlaceAdapter"]
