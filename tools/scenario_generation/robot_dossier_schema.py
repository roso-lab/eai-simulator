"""JSON Schema and validation for versioned EAI robot dossiers."""
from __future__ import annotations

from typing import Any

EVIDENCE_TIERS = tuple(f"E{i}" for i in range(8))
CLAIM_STATUSES = ("measured", "derived", "declared", "inferred", "sim_tuning_only", "unknown")


def claim_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["value", "status", "confidence", "evidence_refs"],
        "properties": {
            "value": {},
            "unit": {"type": ["string", "null"]},
            "status": {"enum": list(CLAIM_STATUSES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_refs": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
        "allOf": [{
            "if": {"properties": {"status": {"const": "unknown"}}},
            "then": {"properties": {
                "value": {"type": "null"}, "confidence": {"const": 0},
                "evidence_refs": {"maxItems": 0},
            }},
        }],
    }


def dossier_schema(robot_keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    """Return the strict top-level schema for the current catalog snapshot."""
    if robot_keys is None:
        from EAI.hmrs_env.env_diy.catalog import robot_keys as catalog_robot_keys
        robot_keys = catalog_robot_keys()
    evidence = {
        "type": "object",
        "required": ["evidence_id", "tier", "kind", "result"],
        "properties": {
            "evidence_id": {"type": "string", "minLength": 1},
            "tier": {"enum": list(EVIDENCE_TIERS)},
            "kind": {"type": "string", "minLength": 1},
            "result": {"enum": ["pass", "fail", "unknown"]},
            "source_refs": {"type": "array", "items": {"type": "string"}},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "artifact": {"type": "object"},
        },
        "additionalProperties": True,
    }
    gate_status = {"type": "string", "enum": ["pass", "fail", "unknown", "not_applicable"]}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://eai-simulator.local/schema/eai.robot_dossier-1.0.json",
        "type": "object",
        "required": ["schema_id", "schema_version", "dossier_id", "robot_key", "identity",
                      "morphology", "controller", "capabilities", "sensing", "manipulation",
                      "payload", "environment_suitability", "evidence", "sources",
                      "validation_status", "uncertainty"],
        "properties": {
            "schema_id": {"const": "eai.robot_dossier"},
            "schema_version": {"const": "1.0"},
            "dossier_id": {"type": "string", "minLength": 1},
            "robot_key": {"enum": list(robot_keys)},
            "identity": {"type": "object"},
            "morphology": {"type": "object"},
            "physics": {
                "type": "object",
                "required": ["mass_kg", "center_of_mass_m", "inertia_kg_m2"],
                "properties": {
                    "mass_kg": {"$ref": "#/$defs/claim"},
                    "center_of_mass_m": {"$ref": "#/$defs/claim"},
                    "inertia_kg_m2": {"$ref": "#/$defs/claim"},
                },
                "additionalProperties": True,
            },
            "controller": {"type": "object"},
            "capabilities": {"type": "object", "additionalProperties": {"$ref": "#/$defs/claim"}},
            "supported_attachments": {"type": "array", "items": {"type": "string"}},
            "selected_attachments": {"type": "array", "items": {"type": "string"}},
            "sensing": {"type": "object"},
            "manipulation": {"type": "object"},
            "sensor_mounts": {"type": "array", "items": {"type": "string"}},
            "payload": {"type": "object"},
            "environment_suitability": {"type": "object"},
            "evidence": {"type": "array", "items": evidence, "minItems": 1},
            "sources": {"type": "array", "items": {"type": "object"}},
            "validation_status": {
                "type": "object",
                "required": ["overall", "per_gate"],
                "properties": {
                    "overall": {"enum": ["registered", "asset_available", "structurally_validated",
                                         "placement_validated", "spawn_validated", "navigation_execution_validated",
                                         "kinematic_execution_validated", "physical_validated", "blocked"]},
                    "runtime_validated": {"type": "boolean"},
                    "per_gate": {"type": "object", "additionalProperties": gate_status},
                },
                "additionalProperties": True,
            },
            "uncertainty": {"type": "array", "items": {"type": "string"}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
        "$defs": {"claim": claim_schema()},
    }


def validate_dossier_schema(dossier: dict[str, Any]) -> list[str]:
    """Return Draft 2020-12 schema errors as readable paths."""
    from jsonschema import Draft202012Validator
    errors = sorted(Draft202012Validator(dossier_schema()).iter_errors(dossier),
                    key=lambda error: list(error.absolute_path))
    return [f"{'.'.join(map(str, error.absolute_path)) or '$'}: {error.message}" for error in errors]


__all__ = ["CLAIM_STATUSES", "EVIDENCE_TIERS", "claim_schema", "dossier_schema", "validate_dossier_schema"]
