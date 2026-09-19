"""Draft 2020-12 schema for robot + attachment + payload combinations."""
from __future__ import annotations

from typing import Any


def combination_profile_schema() -> dict[str, Any]:
    claim_quantity = {"type": ["number", "null"]}
    component = {
        "type": "object", "required": ["name", "mass_kg", "com_m", "inertia_kg_m2",
                                          "translation_m", "rotation_matrix", "evidence_status"],
        "properties": {
            "name": {"type": "string", "minLength": 1}, "mass_kg": claim_quantity,
            "com_m": {"type": ["array", "null"], "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
            "inertia_kg_m2": {"type": ["array", "null"]},
            "translation_m": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
            "rotation_matrix": {"type": "array", "minItems": 3, "maxItems": 3},
            "evidence_status": {"type": "string", "minLength": 1},
        }, "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://eai-simulator.local/schema/eai.combination_profile-1.0.json",
        "type": "object", "additionalProperties": False,
        "required": ["schema_id", "schema_version", "profile_id", "robot_keys", "robot_dossier_snapshot_sha256",
                      "payload_policy", "components", "aggregate_rigid_body", "condition", "validation", "content_sha256"],
        "properties": {
            "schema_id": {"const": "eai.combination_profile"}, "schema_version": {"const": "1.0"},
            "profile_id": {"type": "string", "minLength": 1},
            "robot_keys": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
            "robot_dossier_snapshot_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
            "payload_policy": {"type": "object", "required": ["values_kg", "sha256", "basis"],
                                "properties": {"values_kg": {"type": "object", "minProperties": 1},
                                               "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                               "basis": {"const": "declared_policy"}},
                                "additionalProperties": False},
            "components": {"type": "array", "minItems": 1, "items": component},
            "aggregate_rigid_body": {"type": "object", "required": ["status", "total_mass_kg", "com_m",
                                                                            "inertia_kg_m2", "quantity_status"],
                                      "properties": {"status": {"enum": ["derived", "unknown"]},
                                                     "total_mass_kg": claim_quantity, "com_m": {"type": ["array", "null"]},
                                                     "com_m_m": {"type": ["array", "null"]},
                                                     "inertia_kg_m2": {"type": ["array", "null"]},
                                                     "quantity_status": {"type": "object"}},
                                      "additionalProperties": True},
            "condition": {"type": "object"},
            "validation": {"type": "object", "required": ["physical_status", "kinematic_status", "required_next_tier"]},
            "content_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        },
    }


def validate_combination_profile(profile: dict[str, Any]) -> list[str]:
    from jsonschema import Draft202012Validator
    errors = sorted(Draft202012Validator(combination_profile_schema()).iter_errors(profile),
                    key=lambda error: list(error.absolute_path))
    return [f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}" for error in errors]


__all__ = ["combination_profile_schema", "validate_combination_profile"]
