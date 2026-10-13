"""Immutable, scene-bound input for authored aerial navigation and transport.

File loading (load_product) and live source verification stay with the host.
This object validates the executable subset and owns an isolated JSON copy so
caller edits cannot change a route. Decorative polylines/profiles are validated
by the authoring product loader; execution does not interpret them.
"""
from dataclasses import dataclass, field
import json
from typing import Mapping

from ..contracts import SkillContractError
from .aerial_navigation import AerialPose, digest, select_aerial_node_route
from .topology import validate_aerial_topology


@dataclass(frozen=True, init=False)
class FrozenAerialTopology:
    scene_key: str
    product_digest: str
    route_graph_digest: str
    _json: str = field(repr=False)

    def __init__(self, document, *, scene_key, source_digests):
        if not isinstance(document, Mapping):
            raise SkillContractError("aerial topology product must be an object")
        if (document.get("schema") != "eai.navigation.topology"
                or type(document.get("version")) is not int or document["version"] != 2
                or document.get("domain") != "aerial"):
            raise SkillContractError("frozen aerial input requires an explicit v2 aerial product")
        if (document.get("coordinate_frame") != "scene_world"
                or type(document.get("meters_per_unit")) not in (int, float)
                or document["meters_per_unit"] != 1 or document.get("up_axis") != "Z"):
            raise SkillContractError("frozen aerial product requires explicit world metre Z-up coordinates")
        errors = validate_aerial_topology(document)
        if errors:
            raise SkillContractError("; ".join(errors))
        if len(document["nodes"]) < 2 or not document["edges"]:
            raise SkillContractError("frozen aerial product must contain connected nodes and an edge")
        if document.get("scene_key") != scene_key or not isinstance(scene_key, str) or not scene_key:
            raise SkillContractError("aerial topology belongs to another scene")
        if not isinstance(source_digests, Mapping) or document.get("source") != dict(source_digests):
            raise SkillContractError("aerial topology source inventory differs from scene")
        for name, value in source_digests.items():
            if (not isinstance(name, str) or not name or not isinstance(value, str)
                    or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)):
                raise SkillContractError("invalid aerial topology source digest")
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        detached = json.loads(payload)
        graph = select_aerial_node_route(detached, AerialPose((0, 0, 0)))
        for name, value in (("scene_key", scene_key), ("product_digest", digest(detached)),
                            ("route_graph_digest", graph["route_graph_digest"]), ("_json", payload)):
            object.__setattr__(self, name, value)

    def snapshot(self):
        return json.loads(self._json)

    def validate_source(self, scene_key, source_digests):
        if scene_key != self.scene_key or self.snapshot()["source"] != dict(source_digests):
            raise SkillContractError("frozen aerial topology source changed")
