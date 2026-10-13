"""Adapter protocol used by the generic topology authoring tool.

Adapters contain scene facts; the editor owns mutable topology state.  The
module has no Isaac imports so adapters can be tested in a normal Python
process.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

Point = tuple[float, float, float]


@dataclass(frozen=True)
class SemanticTarget:
    ref: str
    label: str
    kind: str = "target_entrance"
    position: Point | None = None


@dataclass(frozen=True)
class SceneSource:
    scene_key: str
    asset_root: Path
    payload: Mapping[str, Any] = field(default_factory=dict)


class TopologySceneAdapter(Protocol):
    scene_key: str

    def load_source(self, asset_root: Path) -> SceneSource: ...
    def load_stage(self, source: SceneSource) -> None: ...
    def semantic_targets(self, source: SceneSource) -> list[SemanticTarget]: ...
    def project_to_support_surface(self, position: Sequence[float], source: SceneSource) -> Point: ...
    def validate_polyline(self, polyline: Sequence[Sequence[float]], profile: str,
                          source: SceneSource) -> list[str]: ...
    def source_digests(self, source: SceneSource) -> dict[str, str]: ...

    def validate_aerial_polyline(self, polyline: Sequence[Sequence[float]], clearance_m: float,
                                source: SceneSource) -> list[str]:
        """Explicit air authoring admission; document geometry limits per adapter."""
        ...
