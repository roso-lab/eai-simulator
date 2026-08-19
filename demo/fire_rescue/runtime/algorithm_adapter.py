# Copyright (c) 2022-2025. EAI-Factory EMOS.
"""Fire Rescue compatibility facade for the shared EAI navigation component."""

from algorithm.multi_robot_navigation.eai_plugin import (
    EaiMultiRobotNavigationPlugin,
)


class EmosFactoryNavBridge(EaiMultiRobotNavigationPlugin):
    """Backward-compatible Fire Rescue name for the generic EAI plugin."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("planner_backend", "global")
        kwargs.setdefault("exclude_aerial", False)
        super().__init__(*args, **kwargs)


__all__ = ["EmosFactoryNavBridge"]
