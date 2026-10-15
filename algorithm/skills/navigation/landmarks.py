"""Pure boundary from declared landmarks to an auditable authored XYZ route.

The caller (including a language model) chooses from landmark_inventory. This
module does not infer coordinates, floor numbers, or connectivity from prose.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..contracts import SkillContractError
from .node_route import NavigationPose, NavigationProfile, ground_navigation_profile, select_node_route
from .topology import topology_fields, validate_topology


def _data(document: Any) -> Mapping[str, Any]:
    errors = validate_topology(document)
    if errors:
        raise SkillContractError("invalid topology: " + "; ".join(errors))
    return topology_fields(document)


def landmark_inventory(document: Any) -> list[dict[str, Any]]:
    """Public allowed identifiers; no private simulator state is exposed."""
    data = _data(document)
    return [{"node_id": row["id"], "refs": list(row.get("target_refs", [])),
             "label": row.get("label", ""), "aliases": list(row.get("aliases", [])),
             "level_id": row.get("level_id"), "position_xyz": list(row["position_xyz"]),
             **({"heading_rad": row.get("heading_rad")} if data.get("version") == 2 else {})}
            for row in sorted(data["nodes"], key=lambda row: row["id"])
            if row.get("target_refs") or row.get("label") or row.get("aliases")]


def resolve_landmark(document: Any, query: str, *, level_id: str | None = None) -> Mapping[str, Any]:
    data = _data(document)
    if not isinstance(query, str) or not query.strip():
        raise SkillContractError("landmark query must be a nonempty identifier or label")
    rows = [row for row in data["nodes"] if level_id is None or row.get("level_id") == level_id]
    # An exact stable reference wins over free-text aliases, but never over a
    # second stable match. Authors cannot make an ambiguous ID executable.
    matches = [row for row in rows if query == row["id"]]
    if not matches:
        matches = [row for row in rows if query in row.get("target_refs", [])]
    if not matches:
        key = query.strip().casefold()
        matches = [row for row in rows if key in {
            name.strip().casefold() for name in (row.get("label", ""), *row.get("aliases", [])) if name}]
    if len(matches) != 1:
        raise SkillContractError(f"{'ambiguous' if matches else 'unknown'} landmark: {query!r}")
    return matches[0]


def resolve_landmark_route(
    document: Any, *, origin_ref: str, destination_ref: str,
    origin_level_id: str | None = None, destination_level_id: str | None = None,
    profile: NavigationProfile | None = None,
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
        if query in refs or query == row["id"]:
            return query
        return sorted(refs)[0] if refs else row["id"]
    start, target = stable_ref(origin, origin_ref), stable_ref(destination, destination_ref)
    profile = profile or ground_navigation_profile()
    result = select_node_route(
        document, NavigationPose(tuple(origin["position_xyz"])), profile=profile,
        origin_ref=start, target_ref=target,
    )
    result["domain"] = data.get("domain", "ground")
    result["authored_heading_rad"] = destination.get("heading_rad")
    result.update(schema="eai.navigation.landmark_route", version=1,
                  origin_ref=start, destination_ref=target,
                  coordinate_frame="scene_world", meters_per_unit=1, up_axis="Z")
    if result["admitted"]:
        rows = {row["id"]: row for row in data["nodes"]}
        result["route_level_ids"] = [rows[node_id].get("level_id") for node_id in result["topology_node_ids"]]
        result["route_headings_rad"] = [rows[node_id].get("heading_rad") for node_id in result["topology_node_ids"]]
        result["already_at_target"] = False
        result["skill_parameters"] = {"origin_ref": start, "route_graph_digest": result["route_graph_digest"]}
        if result.get("target_heading_rad") is not None and profile.supports_final_heading:
            result["skill_parameters"]["target_yaw"] = result["target_heading_rad"]
            # The declared origin supplies no measured robot orientation.
            # Only the execution provider may certify heading arrival.
            result["already_at_target"] = False
        result["target_ref"] = target
    return result
