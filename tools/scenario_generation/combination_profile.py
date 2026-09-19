"""Pure rigid-body aggregation and deterministic combination profiles.

This module deliberately contains no Isaac Sim or Isaac Lab imports.  It only
validates declared quantities and applies the rigid-body parallel-axis theorem.
Missing quantities remain unknown; in particular, an absent inertia tensor is
never treated as a zero tensor.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import numbers
from typing import Any, Mapping


_IDENTITY: tuple[tuple[float, float, float], ...] = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


@dataclass(frozen=True)
class RigidComponent:
    """A rigid body's declared quantities in a common parent frame.

    ``rotation_matrix`` is appended to the dataclass fields so existing
    positional construction remains valid.  It maps component-local vectors
    into the parent frame and defaults to the identity rotation.
    """

    name: str
    mass_kg: float | None
    com_m: tuple[float, float, float] | None = None
    inertia_kg_m2: tuple[tuple[float, float, float], ...] | None = None
    translation_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    evidence_status: str = "unknown"
    rotation_matrix: tuple[tuple[float, float, float], ...] = _IDENTITY


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{label} must be a finite numeric value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite numeric value")
    return result


def _vector(value: Any, label: str) -> tuple[float, float, float]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a finite xyz tuple")
    try:
        items = tuple(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite xyz tuple") from exc
    if len(items) != 3:
        raise ValueError(f"{label} must be a finite xyz tuple")
    return tuple(_number(item, label) for item in items)  # type: ignore[return-value]


def _matrix(value: Any, label: str) -> tuple[tuple[float, float, float], ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a 3x3 matrix")
    try:
        rows = tuple(tuple(row) for row in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a 3x3 matrix") from exc
    if len(rows) != 3 or any(len(row) != 3 for row in rows):
        raise ValueError(f"{label} must be a 3x3 matrix")
    return tuple(
        tuple(_number(item, label) for item in row)  # type: ignore[misc]
        for row in rows
    )


def _determinant(matrix: tuple[tuple[float, float, float], ...]) -> float:
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def _validate_rotation(value: Any, name: str) -> tuple[tuple[float, float, float], ...]:
    matrix = _matrix(value, f"{name}: rotation_matrix")
    scale = max(1.0, max(abs(item) for row in matrix for item in row))
    tolerance = 1e-9 * scale
    for i in range(3):
        for j in range(3):
            dot = sum(matrix[k][i] * matrix[k][j] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > tolerance:
                raise ValueError(f"{name}: rotation_matrix must be orthonormal")
    if abs(_determinant(matrix) - 1.0) > tolerance:
        raise ValueError(f"{name}: rotation_matrix must be a proper SO(3) rotation")
    return matrix


def _principal_minors_nonnegative(matrix: tuple[tuple[float, float, float], ...], relative_tolerance: float = 1e-10) -> bool:
    """Return whether all principal minors are nonnegative within scale-aware error.

    A k-by-k determinant has units of ``matrix_unit ** k``.  Comparing all
    minors to one absolute tolerance makes small, physically invalid tensors
    appear PSD (especially when the determinant is close to zero), so each
    order gets its own tolerance relative to the matrix scale.
    """
    scale = max(abs(item) for row in matrix for item in row)
    if scale == 0.0:
        return True
    tolerances = {
        1: relative_tolerance * scale,
        2: relative_tolerance * scale**2,
        3: relative_tolerance * scale**3,
    }
    for i in range(3):
        if matrix[i][i] < -tolerances[1]:
            return False
    for i, j in ((0, 1), (0, 2), (1, 2)):
        minor = matrix[i][i] * matrix[j][j] - matrix[i][j] * matrix[j][i]
        if minor < -tolerances[2]:
            return False
    return _determinant(matrix) >= -tolerances[3]


def _validate_inertia(value: Any, name: str) -> tuple[tuple[float, float, float], ...]:
    matrix = _matrix(value, f"{name}: inertia_kg_m2")
    scale = max(abs(item) for row in matrix for item in row)
    tolerance = 1e-10 * scale if scale else 0.0
    for i in range(3):
        for j in range(i):
            if abs(matrix[i][j] - matrix[j][i]) > tolerance:
                raise ValueError(f"{name}: inertia must be symmetric")
    if not _principal_minors_nonnegative(matrix):
        raise ValueError(f"{name}: inertia must be positive semidefinite")

    # For a physical inertia tensor, the triangle inequalities on principal
    # moments are equivalent to positive semidefiniteness of this second-moment
    # tensor: 1/2 tr(I) * identity - I.
    trace_half = 0.5 * sum(matrix[i][i] for i in range(3))
    second_moment = tuple(
        tuple((trace_half if i == j else 0.0) - matrix[i][j] for j in range(3))
        for i in range(3)
    )
    if not _principal_minors_nonnegative(second_moment):
        raise ValueError(f"{name}: inertia violates principal-moment triangle inequalities")
    return matrix


def _validate_component(component: RigidComponent) -> None:
    if not isinstance(component.name, str) or not component.name.strip():
        raise ValueError("component names must be non-empty strings")
    if component.mass_kg is not None:
        mass = _number(component.mass_kg, f"{component.name}: mass_kg")
        if mass <= 0:
            raise ValueError(f"{component.name}: mass_kg must be positive and finite")
    if component.com_m is not None:
        _vector(component.com_m, f"{component.name}: com_m")
    _vector(component.translation_m, f"{component.name}: translation_m")
    _validate_rotation(component.rotation_matrix, component.name)
    if component.inertia_kg_m2 is not None:
        _validate_inertia(component.inertia_kg_m2, component.name)


def _mat_add(a: tuple[tuple[float, float, float], ...], b: tuple[tuple[float, float, float], ...]):
    return tuple(tuple(a[i][j] + b[i][j] for j in range(3)) for i in range(3))


def _mat_mul(a: tuple[tuple[float, float, float], ...], b: tuple[tuple[float, float, float], ...]):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _mat_transpose(a: tuple[tuple[float, float, float], ...]):
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def _rotate_vector(rotation: tuple[tuple[float, float, float], ...], vector):
    return tuple(sum(rotation[i][j] * vector[j] for j in range(3)) for i in range(3))


def _parallel_axis(mass: float, d: tuple[float, float, float]):
    norm2 = sum(item * item for item in d)
    return tuple(
        tuple(mass * ((norm2 if i == j else 0.0) - d[i] * d[j]) for j in range(3))
        for i in range(3)
    )


def combine_components(components: list[RigidComponent]) -> dict[str, Any]:
    """Aggregate complete rigid-body quantities while preserving unknowns."""
    if not components:
        raise ValueError("at least one component is required")
    if not isinstance(components, (list, tuple)):
        raise ValueError("components must be a sequence of RigidComponent values")
    names: set[str] = set()
    for component in components:
        if not isinstance(component, RigidComponent):
            raise ValueError("components must contain RigidComponent values")
        _validate_component(component)
        if component.name in names:
            raise ValueError(f"duplicate component name {component.name!r}")
        names.add(component.name)

    mass_complete = all(component.mass_kg is not None for component in components)
    com_complete = mass_complete and all(component.com_m is not None for component in components)
    inertia_complete = com_complete and all(component.inertia_kg_m2 is not None for component in components)
    total_mass = sum(_number(c.mass_kg, f"{c.name}: mass_kg") for c in components) if mass_complete else None

    com = None
    aggregate = None
    if com_complete:
        positions = [
            tuple(
                _vector(c.translation_m, f"{c.name}: translation_m")[i]
                + _rotate_vector(_validate_rotation(c.rotation_matrix, c.name), _vector(c.com_m, f"{c.name}: com_m"))[i]
                for i in range(3)
            )
            for c in components
        ]
        assert total_mass is not None
        com = tuple(
            sum(_number(c.mass_kg, f"{c.name}: mass_kg") * position[i] for c, position in zip(components, positions))
            / total_mass
            for i in range(3)
        )
        if inertia_complete:
            aggregate = ((0.0, 0.0, 0.0),) * 3
            for component, position in zip(components, positions):
                rotation = _validate_rotation(component.rotation_matrix, component.name)
                inertia = _validate_inertia(component.inertia_kg_m2, component.name)
                rotated = _mat_mul(_mat_mul(rotation, inertia), _mat_transpose(rotation))
                distance = tuple(position[i] - com[i] for i in range(3))
                aggregate = _mat_add(
                    aggregate,
                    _mat_add(rotated, _parallel_axis(_number(component.mass_kg, f"{component.name}: mass_kg"), distance)),
                )

    unknown_components = [
        component.name
        for component in components
        if component.mass_kg is None or component.com_m is None or component.inertia_kg_m2 is None
    ]
    return {
        "status": "derived" if mass_complete and com_complete and inertia_complete else "unknown",
        "total_mass_kg": total_mass,
        "com_m": com,
        "inertia_kg_m2": aggregate,
        "quantity_status": {
            "mass": "known" if mass_complete else "unknown",
            "com": "known" if com_complete else "unknown",
            "inertia": "known" if inertia_complete else "unknown",
        },
        "unknown_components": unknown_components,
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def payload_policy_hash(policy: Mapping[str, float]) -> str:
    """Hash a finite, positive, canonical per-type payload policy."""
    if not isinstance(policy, Mapping) or not policy:
        raise ValueError("payload policy cannot be empty")
    normalized: dict[str, float] = {}
    for robot_type, capacity in policy.items():
        if not isinstance(robot_type, str) or not robot_type.strip():
            raise ValueError("payload policy keys must be non-empty strings")
        value = _number(capacity, f"payload capacity for {robot_type!r}")
        if value <= 0:
            raise ValueError(f"invalid payload capacity for {robot_type!r}")
        normalized[robot_type] = value
    return hashlib.sha256(_canonical_json(normalized)).hexdigest()


def _catalog_keys() -> tuple[str, ...]:
    try:
        from EAI.hmrs_env.env_diy.catalog import robot_keys
    except ImportError as exc:
        raise ValueError("robot catalog is unavailable") from exc
    return tuple(robot_keys())


def _serialized_component(component: RigidComponent) -> dict[str, Any]:
    def list_or_none(value):
        if value is None:
            return None
        return [list(row) for row in value] if isinstance(value[0], tuple) else list(value)

    return {
        "name": component.name,
        "mass_kg": component.mass_kg,
        "com_m": list_or_none(component.com_m),
        "inertia_kg_m2": list_or_none(component.inertia_kg_m2),
        "translation_m": list_or_none(component.translation_m),
        "rotation_matrix": list_or_none(component.rotation_matrix),
        "evidence_status": component.evidence_status,
    }


def _all_finite(value: Any, path: str = "$") -> list[str]:
    errors: list[str] = []
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return errors
    if isinstance(value, numbers.Real):
        if not math.isfinite(float(value)):
            errors.append(f"{path}: must contain only finite numeric values")
        return errors
    if isinstance(value, Mapping):
        for key, item in value.items():
            errors.extend(_all_finite(item, f"{path}.{key}"))
        return errors
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            errors.extend(_all_finite(item, f"{path}[{index}]"))
    return errors


def _values_match(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Compare JSON-shaped values while allowing only floating round-off."""
    if isinstance(expected, numbers.Real) and not isinstance(expected, bool):
        if not isinstance(actual, numbers.Real) or isinstance(actual, bool):
            return [f"{path}: expected numeric value {expected!r}"]
        if not math.isclose(float(expected), float(actual), rel_tol=1e-12, abs_tol=0.0):
            return [f"{path}: expected {expected!r}, got {actual!r}"]
        return []
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return [f"{path}: expected an object"]
        errors: list[str] = []
        if set(expected) != set(actual):
            errors.append(f"{path}: keys do not match")
        for key in expected.keys() & actual.keys():
            errors.extend(_values_match(expected[key], actual[key], f"{path}.{key}"))
        return errors
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(expected) != len(actual):
            return [f"{path}: shape does not match"]
        errors: list[str] = []
        for index, (left, right) in enumerate(zip(expected, actual)):
            errors.extend(_values_match(left, right, f"{path}[{index}]"))
        return errors
    if expected != actual:
        return [f"{path}: expected {expected!r}, got {actual!r}"]
    return []


def verify_combination_profile(profile: Mapping[str, Any]) -> list[str]:
    """Verify semantic integrity of a schema-shaped persisted profile.

    This verifier is intentionally separate from JSON Schema so it can check
    catalog membership, canonical hashes, finite values, and values derived
    from component physics.  Callers should run the formal schema validator
    first when they need structural diagnostics.
    """
    if not isinstance(profile, Mapping):
        return ["$: profile must be an object"]
    errors = _all_finite(profile)
    try:
        robot_keys = profile["robot_keys"]
        policy_record = profile["payload_policy"]
        components_data = profile["components"]
        aggregate_record = profile["aggregate_rigid_body"]
        content_hash = profile["content_sha256"]
    except KeyError as exc:
        return errors + [f"$: missing required field {exc.args[0]!r}"]

    if not isinstance(robot_keys, (list, tuple)) or any(
        not isinstance(key, str) or not key.strip() for key in robot_keys
    ):
        errors.append("robot_keys: must contain non-empty strings")
    elif len(set(robot_keys)) != len(robot_keys):
        errors.append("robot_keys: values must be unique")
    else:
        try:
            catalog = set(_catalog_keys())
            unknown = sorted(set(robot_keys) - catalog)
            if unknown:
                errors.append("robot_keys: unknown catalog keys: " + ", ".join(unknown))
        except (ImportError, ValueError) as exc:
            errors.append(f"robot_keys: unable to load robot catalog: {exc}")

    if not isinstance(policy_record, Mapping):
        errors.append("payload_policy: must be an object")
    else:
        values = policy_record.get("values_kg")
        if not isinstance(values, Mapping):
            errors.append("payload_policy.values_kg: must be an object")
        elif isinstance(robot_keys, (list, tuple)) and all(isinstance(key, str) for key in robot_keys):
            if set(values) != set(robot_keys):
                errors.append("payload_policy.values_kg: keys must exactly match robot_keys")
            try:
                expected_policy_hash = payload_policy_hash(values)
                if policy_record.get("sha256") != expected_policy_hash:
                    errors.append("payload_policy.sha256: does not match canonical values_kg")
            except ValueError as exc:
                errors.append(f"payload_policy.values_kg: {exc}")

    if isinstance(content_hash, str):
        payload = dict(profile)
        payload.pop("content_sha256", None)
        try:
            expected_content_hash = hashlib.sha256(_canonical_json(payload)).hexdigest()
            if content_hash != expected_content_hash:
                errors.append("content_sha256: does not match canonical profile payload")
        except (TypeError, ValueError) as exc:
            errors.append(f"content_sha256: profile is not canonically serializable: {exc}")
    else:
        errors.append("content_sha256: must be a SHA-256 string")

    components: list[RigidComponent] = []
    if not isinstance(components_data, (list, tuple)) or not components_data:
        errors.append("components: must be a non-empty array")
    else:
        try:
            for item in components_data:
                if not isinstance(item, Mapping):
                    raise ValueError("each component must be an object")
                components.append(RigidComponent(
                    name=item["name"], mass_kg=item["mass_kg"], com_m=item["com_m"],
                    inertia_kg_m2=item["inertia_kg_m2"], translation_m=item["translation_m"],
                    evidence_status=item["evidence_status"], rotation_matrix=item["rotation_matrix"],
                ))
            recomputed = combine_components(components)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"components: invalid component quantities: {exc}")
        else:
            errors.extend(_values_match(recomputed, aggregate_record, "aggregate_rigid_body"))
    return errors


def combination_profile(*, profile_id: str, robot_keys: list[str], policy: dict[str, float],
                        components: list[RigidComponent], condition: dict[str, Any] | None = None,
                        robot_dossier_sha256: str | None = None) -> dict[str, Any]:
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise ValueError("profile_id is required")
    if not isinstance(robot_keys, (list, tuple)) or not robot_keys:
        raise ValueError("robot_keys are required")
    if any(not isinstance(key, str) or not key.strip() for key in robot_keys):
        raise ValueError("robot_keys must contain non-empty strings")
    if len(set(robot_keys)) != len(robot_keys):
        raise ValueError("robot_keys must be unique")
    catalog_keys = _catalog_keys()
    unknown = sorted(set(robot_keys) - set(catalog_keys))
    if unknown:
        raise ValueError(f"unknown robot_keys: {', '.join(unknown)}")
    if not isinstance(policy, Mapping):
        raise ValueError("policy must be a mapping")
    policy_keys = set(policy)
    robot_key_set = set(robot_keys)
    if policy_keys != robot_key_set:
        missing = sorted(robot_key_set - policy_keys)
        extra = sorted(policy_keys - robot_key_set)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unexpected " + ", ".join(str(key) for key in extra))
        raise ValueError("payload policy must be complete (" + "; ".join(detail) + ")")
    normalized_policy = {key: _number(policy[key], f"payload capacity for {key!r}") for key in sorted(policy)}
    if any(value <= 0 for value in normalized_policy.values()):
        raise ValueError("payload capacities must be positive")
    if robot_dossier_sha256 is not None:
        if not isinstance(robot_dossier_sha256, str) or len(robot_dossier_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in robot_dossier_sha256
        ):
            raise ValueError("robot_dossier_sha256 must be a 64-character lowercase hexadecimal SHA-256")
    if condition is not None and not isinstance(condition, dict):
        raise ValueError("condition must be a mapping")

    aggregate = combine_components(components)
    profile: dict[str, Any] = {
        "schema_id": "eai.combination_profile",
        "schema_version": "1.0",
        "profile_id": profile_id,
        "robot_keys": list(robot_keys),
        "robot_dossier_snapshot_sha256": robot_dossier_sha256,
        "payload_policy": {
            "values_kg": normalized_policy,
            "sha256": payload_policy_hash(normalized_policy),
            "basis": "declared_policy",
        },
        "components": [_serialized_component(component) for component in components],
        "aggregate_rigid_body": json.loads(json.dumps(aggregate)),
        "condition": condition if condition is not None else {},
        "validation": {
            "physical_status": "unknown",
            "kinematic_status": "not_run",
            "required_next_tier": "E5",
        },
    }
    try:
        profile["content_sha256"] = hashlib.sha256(_canonical_json(profile)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise ValueError("combination profile inputs must be JSON serializable") from exc
    from tools.scenario_generation.combination_profile_schema import validate_combination_profile
    schema_errors = validate_combination_profile(profile)
    if schema_errors:
        raise ValueError("generated combination profile failed schema: " + "; ".join(schema_errors))
    semantic_errors = verify_combination_profile(profile)
    if semantic_errors:
        raise ValueError("generated combination profile failed semantic verification: " + "; ".join(semantic_errors))
    return profile


__all__ = ["RigidComponent", "combine_components", "combination_profile", "payload_policy_hash", "verify_combination_profile"]
