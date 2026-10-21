"""Project-local protocol-scope declarations for scenario generation.

The historical ``demo.*`` benchmark package was removed from HeteroCoord.
These two small metadata helpers are the only scope surface required by the
pool-splitting protocol; keeping them local avoids reviving the old manifest
or executor packages.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping


FACTORY_SCENE_KEY = "factory"
WAREHOUSE_SCENE_KEY = "warehouse_nvd"
MODERN_HOUSE_SCENE_KEY = "modern_house_nvd"

PROTOCOL_SCOPES: dict[str, dict[str, Any]] = {
    "factory": {
        "name": "factory",
        "scene_keys": (FACTORY_SCENE_KEY,),
        "description": "within-Factory family/layout holdout",
    },
    "warehouse": {
        "name": "warehouse",
        "scene_keys": (WAREHOUSE_SCENE_KEY,),
        "description": "Warehouse benchmark metadata scope",
    },
    "modern_house": {
        "name": "modern_house",
        "scene_keys": (MODERN_HOUSE_SCENE_KEY,),
        "description": "Modern House benchmark metadata scope",
    },
}


def protocol_scope_metadata(scope: str) -> dict[str, Any]:
    try:
        value = PROTOCOL_SCOPES[scope]
    except KeyError as exc:
        raise ValueError(f"unknown benchmark protocol scope: {scope!r}") from exc
    result = deepcopy(value)
    result["scene_keys"] = list(result["scene_keys"])
    return result


def validate_protocol_scope(
    records: Iterable[Mapping[str, Any]],
    *,
    scope: str = "factory",
    pool_scene_key: str | None = None,
) -> None:
    """Reject explicit scene keys outside the selected protocol scope.

    Missing per-record scene keys remain compatible with legacy Factory pool
    records.  No provider/fleet availability is consulted here: this function
    validates directory/scene identity only, as required by D1 material.
    """

    allowed = set(protocol_scope_metadata(scope)["scene_keys"])
    observed: set[str] = set()
    if pool_scene_key not in (None, ""):
        observed.add(str(pool_scene_key))
    for record in records:
        scene_key = record.get("scene_key")
        if scene_key not in (None, ""):
            observed.add(str(scene_key))
    invalid = sorted(observed - allowed)
    if invalid:
        raise ValueError(
            f"benchmark protocol scope {scope!r} cannot include scene_key(s): "
            + ", ".join(invalid)
        )


__all__ = [
    "FACTORY_SCENE_KEY",
    "WAREHOUSE_SCENE_KEY",
    "MODERN_HOUSE_SCENE_KEY",
    "PROTOCOL_SCOPES",
    "protocol_scope_metadata",
    "validate_protocol_scope",
]
