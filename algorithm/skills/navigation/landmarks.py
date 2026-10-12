"""Pure boundary from declared landmarks to an auditable authored XYZ route.

The caller (including a language model) chooses from landmark_inventory. This
module does not infer coordinates, floor numbers, or connectivity from prose.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import SkillContractError
from .ground_navigation import GroundPose, select_node_route
from .topology import topology_fields, validate_ground_topology


def _data(document: Any) -> Mapping[str, Any]:
    errors = validate_ground_topology(document)
    if errors:
        raise SkillContractError("invalid topology: " + "; ".join(errors))
    return topology_fields(document)


def landmark_inventory(document: Any) -> list[dict[str, Any]]:
    """Public allowed identifiers; no private simulator state is exposed."""
    data = _data(document)
    return [{"node_id": row["id"], "refs": list(row.get("target_refs", [])),
             "label": row.get("label", ""), "aliases": list(row.get("aliases", [])),
             "level_id": row.get("level_id"), "position_xyz": list(row["position_xyz"])}
            for row in sorted(data["nodes"], key=lambda row: row["id"])
            if row.get("target_refs") or row.get("label") or row.get("aliases")]


def resolve_landmark(document: Any, query: str, *, level_id: str | None = None) -> Mapping[str, Any]:
    data = _data(document)
    if not isinstance(query, str) or not query.strip():
        raise SkillContractError("landmark query must be a nonempty identifier or label")
    rows = [row for row in data["nodes"] if level_id is None or row.get("level_id") == level_id]
    # An exact stable reference wins over free-text aliases, but never over a
    # second stable match. Authors cannot make an ambiguous ID executable.
    matches = [row for row in rows if query == row["id"] or query in row.get("target_refs", [])]
    if not matches:
        key = query.strip().casefold()
        matches = [row for row in rows if key in {
            name.strip().casefold() for name in (row.get("label", ""), *row.get("aliases", [])) if name}]
    if len(matches) != 1:
        raise SkillContractError(f"{'ambiguous' if matches else 'unknown'} landmark: {query!r}")
    return matches[0]


def resolve_landmark_route(
    document: Any, *, origin_ref: str, destination_ref: str,
    controller_capabilities: Sequence[str] = ("walk",),
    origin_level_id: str | None = None, destination_level_id: str | None = None,
) -> dict[str, Any]:
    """Resolve a model goal and use the same router as the execution provider.

    The synthetic pose here represents the declared origin for planning only.
    The provider independently checks the actual measured start before motion.
    """
    data = _data(document)
    origin = resolve_landmark(document, origin_ref, level_id=origin_level_id)
    destination = resolve_landmark(document, destination_ref, level_id=destination_level_id)
    def stable_ref(row, query):
        refs = row.get("target_refs", [])
        if query in refs or (data.get("version",1)==2 and query==row["id"]):
            return query
        if not refs and data.get("version", 1) == 1:
            raise SkillContractError("v1 landmark requires a bound target reference")
        return sorted(refs)[0] if refs else row["id"]
    start, target = stable_ref(origin, origin_ref), stable_ref(destination, destination_ref)
    result = select_node_route(document,
        GroundPose(tuple(origin["position_xyz"]), 0.0, support_z_m=origin["position_xyz"][2]),
        origin_ref=start, target_refs=(target,), controller_capabilities=controller_capabilities)
    result.update(schema="eai.navigation.landmark_route", version=1,
                  origin_ref=start, destination_ref=target,
                  coordinate_frame="scene_world", meters_per_unit=1, up_axis="Z")
    if result["admitted"]:
        result["skill_parameters"] = {"origin_ref": start, "route_graph_digest": result["route_graph_digest"]}
        result["target_ref"] = target
    return result
