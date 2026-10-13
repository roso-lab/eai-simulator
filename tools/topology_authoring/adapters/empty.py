"""Generic scene adapter for scenes whose topology is drawn by the user."""
from __future__ import annotations
from pathlib import Path
from typing import Sequence
from ..adapter import SceneSource

class EmptyTopologyAdapter:
    def __init__(self, scene_key: str = "empty"):
        self.scene_key = str(scene_key)

    def load_source(self, asset_root): return SceneSource(self.scene_key, Path(asset_root), {})
    def load_stage(self, source): return None
    def semantic_targets(self, source): return []
    def project_to_support_surface(self, position, source): return tuple(float(x) for x in position)
    def validate_polyline(self, polyline, profile, source): return []
    def validate_aerial_polyline(self, polyline, clearance_m, source):
        # Empty authoring scenes have no obstacle model. Authored links are a
        # user assertion of passage, never a collision certificate.
        return []
    def source_digests(self, source): return {}
