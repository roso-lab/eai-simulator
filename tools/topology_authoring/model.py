"""Pure mutable model for topology drafts."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any, Iterable, Mapping, Sequence

# Clearance metadata remains useful to a scene validator, but it is not a
# controller capability and is never used by the navigation router.  Ground
# and aerial products deliberately expose the same authoring profile.
PROFILES = {"default": 1.0}


@dataclass
class PendingNode:
    position_xyz: list[float]
    support_ref: str | None = None
    heading_rad: float | None = None


@dataclass
class PendingEdge:
    from_node_id: str
    to_node_id: str | None = None
    preview_polyline_xyz: list[list[float]] = field(default_factory=list)
    directed: bool = False


def _point(value: Sequence[float]) -> list[float]:
    if len(value) != 3 or any(isinstance(x, bool) or not isinstance(x, (int, float))
                               or not math.isfinite(float(x)) for x in value):
        raise ValueError("position must contain three finite coordinates")
    return [float(x) for x in value]


def _strings(value: Iterable[str], label: str) -> list[str]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a string array")
    try:
        items = list(value)
    except TypeError as exc:
        raise ValueError(f"{label} must be a string array") from exc
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{label} must contain nonempty strings")
    return sorted(set(items))


def _heading(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("heading must be finite radians or null")
    return math.atan2(math.sin(value), math.cos(value))


def _level_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("level_id must be null or a nonempty string")
    return value


def _node_metadata(level_id: str | None, label: str,
                   aliases: Iterable[str]) -> tuple[str | None, str, list[str]]:
    if not isinstance(label, str):
        raise ValueError("node label must be a string")
    return _level_id(level_id), label, _strings(aliases, "node aliases")


def _normalize_levels(levels: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(levels, (list, tuple)):
        raise ValueError("levels must be an array")
    normalized: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, row in enumerate(levels):
        if not isinstance(row, Mapping):
            raise ValueError(f"invalid level object: {index}")
        identifier = _level_id(row.get("id"))
        if identifier is None or identifier in used:
            raise ValueError(f"duplicate/invalid level id: {identifier!r}")
        label = row.get("label")
        height = row.get("support_z_m")
        if not isinstance(label, str):
            raise ValueError(f"invalid level label: {identifier}")
        if (isinstance(height, bool) or not isinstance(height, (int, float))
                or not math.isfinite(float(height))):
            raise ValueError(f"invalid level support height: {identifier}")
        used.add(identifier)
        normalized.append({
            "id": identifier,
            "label": label,
            "support_z_m": float(height),
        })
    return normalized


@dataclass
class TopologyDocument:
    scene_key: str
    source: dict[str, str] = field(default_factory=dict)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    coordinate_frame: str = "scene_world"
    meters_per_unit: float = 1.0
    up_axis: str = "Z"
    version: int = 2
    levels: list[dict[str, Any]] = field(default_factory=list)
    domain: str = "ground"
    _undo: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _redo: list[dict[str, Any]] = field(default_factory=list, repr=False)
    pending_node: PendingNode | None = field(default=None, repr=False, compare=False)
    pending_edge: PendingEdge | None = field(default=None, repr=False, compare=False)
    selected_node_id: str | None = field(default=None, repr=False, compare=False)
    selected_edge_id: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.domain not in {"ground", "aerial"}:
            raise ValueError("domain must be ground or aerial")
        if self.version not in {1, 2}:
            raise ValueError("version must be 1 or 2")

    @property
    def profiles(self):
        return PROFILES

    def _snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "levels": deepcopy(self.levels),
            "nodes": deepcopy(self.nodes),
            "edges": deepcopy(self.edges),
        }

    def _restore_snapshot(self, state: Mapping[str, Any]) -> None:
        self.version = int(state.get("version", 1))
        self.levels = deepcopy(state.get("levels", []))
        self.nodes = deepcopy(state["nodes"])
        self.edges = deepcopy(state["edges"])

    def _record(self) -> None:
        self._undo.append(self._snapshot())
        self._redo.clear()

    def replace_content(self, other: "TopologyDocument") -> None:
        """Commit a validated load as a new document/history boundary."""
        for name in ("scene_key", "source", "nodes", "edges", "coordinate_frame",
                     "meters_per_unit", "up_axis", "version", "levels", "domain"):
            setattr(self, name, deepcopy(getattr(other, name)))
        self._undo.clear()
        self._redo.clear()
        self.cancel_preview()

    @staticmethod
    def _next_id(rows: list[dict[str, Any]], prefix: str) -> str:
        used = {row["id"] for row in rows}
        suffixes = [int(value[len(prefix):]) for value in used
                    if value.startswith(prefix) and value[len(prefix):].isascii()
                    and value[len(prefix):].isdigit()]
        ordinal = max(suffixes, default=0) + 1
        return f"{prefix}{ordinal:04d}"

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        state = self._undo.pop()
        self._restore_snapshot(state)
        self.cancel_preview()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        state = self._redo.pop()
        self._restore_snapshot(state)
        self.cancel_preview()
        return True

    def set_levels(self, levels: Iterable[Mapping[str, Any]]) -> None:
        """Atomically opt this document into the explicit multilevel contract."""
        normalized = _normalize_levels(levels)
        self._record()
        self.version = 2
        self.levels = normalized
        self.cancel_preview()

    def add_node(self, position: Sequence[float], *, kind="custom", node_id=None,
                 target_refs: Iterable[str] = (), source_ref=None, locked=False,
                 level_id=None, label="", aliases: Iterable[str] = (), heading_rad=None) -> str:
        node_id = self._next_id(self.nodes, "node:") if node_id is None else node_id
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("node id must be a nonempty string")
        if any(row["id"] == node_id for row in self.nodes):
            raise ValueError(f"duplicate node id: {node_id}")
        point = _point(position)
        refs = sorted(set(map(str, target_refs)))
        node_level, node_label, node_aliases = _node_metadata(level_id, label, aliases)
        heading = _heading(heading_rad)
        if self.version == 1 and (node_level is not None or node_label or node_aliases or heading is not None):
            raise ValueError("multilevel node metadata requires topology version 2")
        row = {"id": str(node_id), "kind": str(kind), "position_xyz": point,
               "target_refs": refs, "source_ref": source_ref, "locked": bool(locked)}
        if self.version == 2:
            row.update({"level_id": node_level, "label": node_label, "aliases": node_aliases})
            if heading is not None:
                row["heading_rad"] = heading
        self._record()
        self.nodes.append(row)
        return str(node_id)

    def begin_node_preview(self, position: Sequence[float], *, support_ref: str | None = None,
                           heading_rad=None) -> None:
        point, heading = _point(position), _heading(heading_rad)
        if self.version == 1 and heading is not None:
            raise ValueError("node heading requires topology version 2")
        self.pending_edge = None
        self.selected_node_id = None
        self.selected_edge_id = None
        self.pending_node = PendingNode(point, support_ref, heading)

    def confirm_node_preview(self, *, kind: str = "custom", target_refs: Iterable[str] = (),
                             level_id=None, label="", aliases: Iterable[str] = (), heading_rad=None) -> str:
        if self.pending_node is None:
            raise ValueError("no node preview is active")
        pending = self.pending_node
        node_id = self.add_node(pending.position_xyz, kind=kind, target_refs=target_refs,
                                source_ref=pending.support_ref, level_id=level_id,
                                label=label, aliases=aliases,
                                heading_rad=pending.heading_rad if heading_rad is None else heading_rad)
        self.pending_node = None
        self.selected_node_id = node_id
        self.selected_edge_id = None
        return node_id

    def cancel_node_preview(self) -> None:
        self.pending_node = None

    def begin_edge_preview(self, node_id: str) -> None:
        self.node(node_id)
        self.pending_node = None
        self.selected_node_id = node_id
        self.selected_edge_id = None
        self.pending_edge = PendingEdge(node_id)

    def update_edge_preview(self, position: Sequence[float], *, snapped_node_id: str | None = None) -> None:
        if self.pending_edge is None:
            raise ValueError("no edge preview is active")
        points = [_point(position)]
        if snapped_node_id is not None:
            target = self.node(snapped_node_id)
            if snapped_node_id == self.pending_edge.from_node_id:
                raise ValueError("edge cannot return to its start node")
            self.pending_edge.to_node_id = snapped_node_id
            points = [list(self.node(self.pending_edge.from_node_id)["position_xyz"]),
                      list(target["position_xyz"])]
        else:
            self.pending_edge.to_node_id = None
        self.pending_edge.preview_polyline_xyz = points

    def confirm_edge_preview(self, *, directed: bool | None = None,
                             profiles: Mapping[str, Mapping[str, float]] | None = None) -> str:
        if self.pending_edge is None or self.pending_edge.to_node_id is None:
            raise ValueError("select a destination node before confirming the edge")
        pending = self.pending_edge
        edge_id = self.add_edge(pending.from_node_id, pending.to_node_id,
                                pending.preview_polyline_xyz, directed=pending.directed if directed is None else directed,
                                profiles=profiles)
        self.pending_edge = None
        self.selected_node_id = pending.to_node_id
        self.selected_edge_id = edge_id
        return edge_id

    def cancel_edge_preview(self) -> None:
        self.pending_edge = None

    def cancel_preview(self) -> None:
        self.cancel_node_preview(); self.cancel_edge_preview()
        self.selected_node_id = None
        self.selected_edge_id = None

    def select_node(self, node_id: str) -> dict[str, Any]:
        node = self.node(node_id)
        self.pending_node = None
        self.pending_edge = None
        self.selected_node_id = node_id
        self.selected_edge_id = None
        return node

    def select_edge(self, edge_id: str) -> dict[str, Any]:
        edge = self.edge(edge_id)
        self.pending_node = None
        self.pending_edge = None
        self.selected_node_id = None
        self.selected_edge_id = edge_id
        return edge

    def bind_target(self, node_id: str, target_ref: str) -> None:
        node = self.node(node_id)
        ref = str(target_ref)
        self._record()
        refs = set(node.setdefault("target_refs", [])); refs.add(ref)
        node["target_refs"] = sorted(refs)
        node["kind"] = "target_entrance"

    def set_node_metadata(self, node_id: str, *, level_id, label,
                          aliases: Iterable[str]) -> None:
        if self.version != 2:
            raise ValueError("node metadata requires topology version 2")
        node = self.node(node_id)
        if node.get("locked"):
            raise ValueError("locked node cannot be edited")
        node_level, node_label, node_aliases = _node_metadata(level_id, label, aliases)
        self._record()
        node.update({"level_id": node_level, "label": node_label, "aliases": node_aliases})

    def set_node_heading(self, node_id: str, heading_rad: float | None) -> None:
        if self.version != 2:
            raise ValueError("node heading requires topology version 2")
        node = self.node(node_id)
        if node.get("locked"):
            raise ValueError("locked node cannot be edited")
        heading = _heading(heading_rad)
        self._record()
        if heading is None:
            node.pop("heading_rad", None)
        else:
            node["heading_rad"] = heading

    def add_edge(self, from_id: str, to_id: str, polyline: Sequence[Sequence[float]], *,
                 edge_id=None, directed=False, kind="custom_connection", source_ref=None,
                 profiles: Mapping[str, Mapping[str, float]] | None = None,
                 locked_geometry=False) -> str:
        points = [_point(p) for p in polyline]
        edge_id = self._next_id(self.edges, "edge:") if edge_id is None else edge_id
        if not isinstance(edge_id, str) or not edge_id.strip():
            raise ValueError("edge id must be a nonempty string")
        if from_id == to_id or len(points) < 2:
            raise ValueError("edge requires two distinct nodes and two points")
        if any(row["id"] == edge_id for row in self.edges):
            raise ValueError(f"duplicate edge id: {edge_id}")
        if from_id not in {n["id"] for n in self.nodes} or to_id not in {n["id"] for n in self.nodes}:
            raise ValueError("edge endpoint does not exist")
        prof = deepcopy(dict(profiles or {k: {"clearance_m": v} for k, v in self.profiles.items()}))
        row = {"id": str(edge_id), "kind": str(kind), "from": str(from_id),
               "to": str(to_id), "directed": bool(directed),
               "polyline_xyz": points, "profiles": prof,
               "source_ref": source_ref, "locked_geometry": bool(locked_geometry)}
        self._record()
        self.edges.append(row)
        return str(edge_id)

    def node(self, node_id: str) -> dict[str, Any]:
        for row in self.nodes:
            if row["id"] == node_id:
                return row
        raise KeyError(node_id)

    def remove_node(self, node_id: str) -> None:
        node = self.node(node_id)
        if node.get("locked"):
            raise ValueError("locked node cannot be removed")
        self._record()
        self.nodes = [n for n in self.nodes if n["id"] != node_id]
        self.edges = [e for e in self.edges if e["from"] != node_id and e["to"] != node_id]
        self.cancel_preview()

    def remove_edge(self, edge_id: str) -> None:
        edge = next((e for e in self.edges if e["id"] == edge_id), None)
        if edge is None: raise KeyError(edge_id)
        if edge.get("locked_geometry"): raise ValueError("locked edge cannot be removed")
        self._record(); self.edges = [e for e in self.edges if e["id"] != edge_id]
        if self.selected_edge_id == edge_id:
            self.selected_edge_id = None

    def edge(self, edge_id: str) -> dict[str, Any]:
        for edge in self.edges:
            if edge["id"] == edge_id:
                return edge
        raise KeyError(edge_id)

    def set_edge_polyline(self, edge_id: str, polyline: Sequence[Sequence[float]]) -> None:
        edge = self.edge(edge_id)
        if edge.get("locked_geometry"):
            raise ValueError("locked edge geometry cannot be edited")
        points = [_point(point) for point in polyline]
        if len(points) < 2:
            raise ValueError("edge requires at least two points")
        self._record(); edge["polyline_xyz"] = points

    def insert_edge_point(self, edge_id: str, index: int, position: Sequence[float]) -> None:
        edge = self.edge(edge_id)
        if edge.get("locked_geometry"):
            raise ValueError("locked edge geometry cannot be edited")
        point = _point(position)
        if not isinstance(index, int) or index <= 0 or index >= len(edge["polyline_xyz"]):
            raise ValueError("edge point index must be between the endpoints")
        self._record(); edge["polyline_xyz"].insert(index, point)

    def remove_edge_point(self, edge_id: str, index: int) -> None:
        edge = self.edge(edge_id)
        if edge.get("locked_geometry"):
            raise ValueError("locked edge geometry cannot be edited")
        if not isinstance(index, int) or index <= 0 or index >= len(edge["polyline_xyz"]) - 1:
            raise ValueError("edge endpoints cannot be removed")
        self._record(); edge["polyline_xyz"].pop(index)

    def set_edge_direction(self, edge_id: str, directed: bool) -> None:
        edge = self.edge(edge_id)
        self._record(); edge["directed"] = bool(directed)

    def set_edge_profile(self, edge_id: str, profile: str, clearance_m: float) -> None:
        if profile not in self.profiles:
            raise ValueError(f"unknown topology profile: {profile}")
        value = float(clearance_m)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("clearance_m must be positive and finite")
        edge = self.edge(edge_id)
        self._record(); edge.setdefault("profiles", {})[profile] = {"clearance_m": value}

    def move_node(self, node_id: str, position: Sequence[float]) -> None:
        node = self.node(node_id)
        if node.get("locked"): raise ValueError("locked node cannot move")
        old = list(node["position_xyz"])
        new = _point(position)
        self._record(); node["position_xyz"] = new
        for edge in self.edges:
            points = edge.get("polyline_xyz", [])
            if edge.get("from") == node_id and points and points[0] == old:
                points[0] = list(new)
            if edge.get("to") == node_id and points and points[-1] == old:
                points[-1] = list(new)

    def selected(self) -> tuple[str | None, dict[str, Any] | None]:
        if self.selected_node_id is not None:
            return "node", self.node(self.selected_node_id)
        if self.selected_edge_id is not None:
            return "edge", self.edge(self.selected_edge_id)
        return None, None

    def to_dict(self) -> dict[str, Any]:
        result = {"schema": "eai.navigation.topology", "version": self.version,
                  "scene_key": self.scene_key, "coordinate_frame": self.coordinate_frame,
                  "meters_per_unit": self.meters_per_unit, "up_axis": self.up_axis,
                  "domain": self.domain}
        if self.version == 2:
            result["levels"] = deepcopy(self.levels)
        result.update({"source": dict(self.source), "nodes": deepcopy(self.nodes),
                       "edges": deepcopy(self.edges)})
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TopologyDocument":
        if (not isinstance(value, Mapping) or value.get("schema") != "eai.navigation.topology"
                or type(value.get("version")) is not int or value["version"] not in {1, 2}):
            raise ValueError("unsupported topology product")
        if not isinstance(value.get("scene_key"), str) or not isinstance(value.get("source", {}), Mapping):
            raise ValueError("invalid topology scene/source")
        if not isinstance(value.get("nodes", []), list) or not isinstance(value.get("edges", []), list):
            raise ValueError("topology nodes and edges must be arrays")
        document = cls(
            value["scene_key"], deepcopy(dict(value.get("source", {}))),
            deepcopy(value.get("nodes", [])), deepcopy(value.get("edges", [])),
            value.get("coordinate_frame", "scene_world"),
            value.get("meters_per_unit", 1.0), value.get("up_axis", "Z"),
            version=value["version"],
            domain=value.get("domain", "ground"),
        )
        if value["version"] == 2:
            document.levels = _normalize_levels(value.get("levels", []))
        document._undo.clear(); document._redo.clear()
        return document
