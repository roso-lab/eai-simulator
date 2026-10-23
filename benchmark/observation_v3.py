"""Field-level public/evaluator observation projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ObservationRightsV3:
    allowed_fields: tuple[str, ...]
    denied_fields: tuple[str, ...] = ()


class ObservationProjectorV3:
    def __init__(self, rights: Mapping[str, ObservationRightsV3]):
        self._rights = dict(rights)

    def project(self, *, audience: str, observation: Mapping[str, Any]) -> dict[str, Any]:
        rule = self._rights.get(audience)
        if rule is None:
            raise KeyError(f"unknown observation audience: {audience}")
        denied = set(rule.denied_fields)
        allowed = set(rule.allowed_fields)
        return {key: value for key, value in observation.items() if key in allowed and key not in denied}

    def assert_no_leak(self, *, audience: str, observation: Mapping[str, Any], hidden_fields: set[str]) -> None:
        projected = self.project(audience=audience, observation=observation)
        leaked = set(projected) & hidden_fields
        if leaked:
            raise ValueError(f"observation rights leak: {sorted(leaked)}")


__all__ = ["ObservationRightsV3", "ObservationProjectorV3"]
