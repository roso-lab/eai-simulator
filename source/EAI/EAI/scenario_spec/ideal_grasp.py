"""Deterministic, non-physical grasp feasibility for scenario tooling.

This module deliberately models an *ideal* grasp.  It checks the symbolic
contracts needed before a scripted attachment can be created, but it does not
simulate contact, inverse kinematics, collision, or dynamics.  It has no
runtime simulator or tensor-framework dependency.

``evaluate_grasp`` accepts the scenario-shaped dictionaries used by
``scenario_spec``.  A robot has an ``id`` (or ``ref``), ``type``, and
``attachments`` containing ``{"type": "ur5"}`` or ``{"type": "z1"}``.
An object has ``id`` and either ``mass_kg`` or ``physics.mass_kg``, plus
``size_m``.  The policy may use these equivalent top-level names:

* ``capacity_kg`` / ``payload_capacity_kg`` / ``payload_capacity``
* ``grasp_envelope_m`` / ``grasp_envelope``

Each policy value may be a scalar, a three-value sequence, or a mapping keyed
by robot id, robot type, or manipulator type.  A mapping value may itself use
``size_m``, ``dimensions``, ``max_size_m``, or ``envelope_m``.  Capacities are
in kilograms and envelopes are metres.  Values are required to be finite and
strictly positive; omitted declarations fail closed.

When ``team`` is supplied, it is interpreted as additional team members and
is combined with ``robot`` (duplicates are removed by stable robot id).  Team
capacity is the sum of each member's declared capacity.  Every participant
must have a supported manipulator, since this API models a grasping team;
non-grasping escorts belong in a navigation feasibility check instead.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from numbers import Real
from typing import Any


MODEL = "ideal_grasp"
_MANIPULATORS = frozenset({"ur5", "z1"})
_MISSING = object()


def _value(source: Any, *names: str, default: Any = _MISSING) -> Any:
    """Read a field from a mapping or a small attribute-based test object."""
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
    else:
        for name in names:
            try:
                return getattr(source, name)
            except AttributeError:
                continue
    return default


def _base(success: bool) -> dict[str, Any]:
    return {
        "success": success,
        "feasible": success,
        "model": MODEL,
        "physical_grasp": False,
        "contact_dynamics": False,
    }


def _failure(reason: str, **details: Any) -> dict[str, Any]:
    result = _base(False)
    result["reason"] = reason
    if details:
        result["details"] = details
    return result


def _robot_id(robot: Any) -> str | None:
    identifier = _value(robot, "id", "robot_id", "ref", "name")
    if identifier is not _MISSING and isinstance(identifier, str) and identifier.strip():
        return identifier.strip()
    robot_type = _value(robot, "type", "robot_type")
    if isinstance(robot_type, str) and robot_type.strip():
        return robot_type.strip()
    return None


def _robot_type(robot: Any) -> str | None:
    value = _value(robot, "type", "robot_type")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _attachments(robot: Any) -> list[str]:
    raw = _value(robot, "attachments", default=())
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return []
    result = []
    for item in raw:
        kind = _value(item, "type", "kind", default=item) if not isinstance(item, str) else item
        if isinstance(kind, str):
            result.append(kind.strip().lower())
    return result


def _manipulators(robot: Any) -> list[str]:
    return [kind for kind in _attachments(robot) if kind in _MANIPULATORS]


def _finite_positive(value: Any) -> bool:
    return (isinstance(value, Real) and not isinstance(value, bool)
            and math.isfinite(float(value)) and float(value) > 0.0)


def _dimensions(value: Any, *, field: str) -> tuple[float, float, float] | None:
    """Normalize a scalar or 3-vector into dimensions, or return ``None``."""
    if _finite_positive(value):
        number = float(value)
        return (number, number, number)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 3:
        if all(_finite_positive(item) for item in value):
            return tuple(float(item) for item in value)  # type: ignore[return-value]
    return None


def _policy_section(policy: Any, names: tuple[str, ...]) -> Any:
    section = _value(policy, *names, default=_MISSING)
    return section


def _mapping_entry(section: Any, robot: Any, manipulator: str | None) -> Any:
    """Resolve a policy section by the most specific stable key."""
    if not isinstance(section, Mapping):
        return section
    keys = (_robot_id(robot), _robot_type(robot), manipulator, "default")
    for key in keys:
        if key is not None and key in section:
            return section[key]
    return _MISSING


def _capacity(policy: Any, robot: Any) -> float | None:
    section = _policy_section(policy, ("capacity_kg", "payload_capacity_kg", "payload_capacity", "capacities"))
    if section is _MISSING:
        return None
    selected = _mapping_entry(section, robot, None)
    if not _finite_positive(selected):
        return None
    return float(selected)


def _envelope(policy: Any, robot: Any, manipulator: str) -> tuple[float, float, float] | None:
    section = _policy_section(policy, ("grasp_envelope_m", "grasp_envelope", "envelope_m", "envelope"))
    if section is _MISSING:
        return None
    selected = _mapping_entry(section, robot, manipulator)
    if selected is _MISSING:
        return None
    # A selected mapping is commonly written as {"size_m": [...]}.
    if isinstance(selected, Mapping):
        selected = _value(selected, "size_m", "dimensions", "max_size_m", "envelope_m", default=_MISSING)
    if selected is _MISSING:
        return None
    return _dimensions(selected, field="grasp_envelope_m")


def _object_id(object_spec: Any) -> str:
    identifier = _value(object_spec, "id", "object_id", "ref", "name")
    if isinstance(identifier, str) and identifier.strip():
        return identifier.strip()
    return "object"


def _object_mass(object_spec: Any) -> Any:
    direct = _value(object_spec, "mass_kg", "mass", default=_MISSING)
    if direct is not _MISSING:
        return direct
    physics = _value(object_spec, "physics", default=_MISSING)
    if physics is not _MISSING:
        return _value(physics, "mass_kg", "mass", default=_MISSING)
    return _MISSING


def _object_size(object_spec: Any) -> Any:
    return _value(object_spec, "size_m", "size", "dimensions", default=_MISSING)


def _team_members(robot: Any, team: Any) -> tuple[list[Any], str | None]:
    members = [robot]
    if team is None:
        return members, None
    if isinstance(team, (str, bytes)) or not isinstance(team, Sequence):
        return [], "team must be a sequence of robot specifications"
    members.extend(team)
    result: list[Any] = []
    seen: set[str] = set()
    for member in members:
        identifier = _robot_id(member)
        if identifier is None:
            return [], "every robot must provide a stable id or type"
        if identifier not in seen:
            seen.add(identifier)
            result.append(member)
    return result, None


def evaluate_grasp(
    robot: Any,
    object_spec: Any,
    policy: Any,
    *,
    team: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Evaluate an ideal grasp and return a stable report-shaped dictionary.

    The function is pure: it does not mutate inputs, access simulator state, or
    infer capacity from robot profiles.  Every successful report contains a
    deterministic fixed-attachment and ownership record.  Every unsuccessful
    report contains a human-readable ``reason`` and the same explicit model
    markers, allowing callers to safely compose either result.
    """
    common = _base(False)
    if robot is None or object_spec is None or policy is None:
        common["reason"] = "robot, object_spec, and policy are required"
        return common

    members, team_error = _team_members(robot, team)
    if team_error:
        return _failure(team_error)
    if not members:
        return _failure("at least one robot is required")

    object_id = _object_id(object_spec)
    mass_raw = _object_mass(object_spec)
    if not _finite_positive(mass_raw):
        return _failure("object mass_kg must be a finite positive declared value", object_id=object_id)
    mass = float(mass_raw)

    size_raw = _object_size(object_spec)
    size = _dimensions(size_raw, field="size_m")
    if size is None:
        return _failure("object size_m must contain three finite positive dimensions", object_id=object_id)

    member_info: list[dict[str, Any]] = []
    for member in members:
        identifier = _robot_id(member)
        assert identifier is not None  # _team_members validated this invariant.
        manipulator_types = _manipulators(member)
        if not manipulator_types:
            return _failure(
                f"robot '{identifier}' has no manipulation capability (requires ur5 or z1)",
                robot_id=identifier,
            )
        capacity = _capacity(policy, member)
        if capacity is None:
            return _failure(
                f"no finite positive declared capacity for robot '{identifier}'",
                robot_id=identifier,
            )
        # A member may expose multiple arms.  The first catalog order is stable;
        # the envelope only needs to fit one supported arm for that member.
        envelopes = [(_envelope(policy, member, kind), kind) for kind in sorted(set(manipulator_types))]
        usable = [(envelope, kind) for envelope, kind in envelopes if envelope is not None]
        if not usable:
            return _failure(
                f"no declared grasp envelope for robot '{identifier}'",
                robot_id=identifier,
            )
        fitting = [(envelope, kind) for envelope, kind in usable
                   if all(a <= b + 1e-12 for a, b in zip(sorted(size), sorted(envelope)))]
        if not fitting:
            largest = max(envelope for envelope, _ in usable)
            return _failure(
                f"object dimensions {list(size)} exceed grasp envelope for robot '{identifier}'",
                robot_id=identifier,
                object_size_m=list(size),
                grasp_envelope_m=list(largest),
            )
        envelope, manipulator = sorted(fitting, key=lambda item: item[1])[0]
        member_info.append({
            "id": identifier,
            "capacity_kg": capacity,
            "manipulator": manipulator,
            "grasp_envelope_m": list(envelope),
        })

    total_capacity = sum(item["capacity_kg"] for item in member_info)
    if mass > total_capacity + 1e-12:
        return _failure(
            f"declared joint capacity {total_capacity:g} kg is below object mass {mass:g} kg",
            object_id=object_id,
            object_mass_kg=mass,
            joint_capacity_kg=total_capacity,
            members=[item["id"] for item in member_info],
        )

    identifiers = [item["id"] for item in member_info]
    physical_screen = None
    if isinstance(policy, Mapping) and "physical_limits" in policy:
        from .physical_limits import transport_load_screen

        limits = policy["physical_limits"]
        if not isinstance(limits, Mapping) or not isinstance(limits.get("carriers"), list):
            return _failure("physical_limits requires explicit carriers")
        if {item.get("id") for item in limits["carriers"] if isinstance(item, Mapping)} != set(identifiers):
            return _failure("physical_limits carriers must exactly match the grasp team")
        try:
            physical_screen = transport_load_screen(
                mass, limits["carriers"],
                vertical_acceleration_m_s2=limits.get("vertical_acceleration_m_s2", 0.0),
                horizontal_acceleration_m_s2=limits.get("horizontal_acceleration_m_s2", 0.0),
                safety_factor=limits.get("safety_factor", 1.5))
        except (TypeError, ValueError, AttributeError) as exc:
            return _failure(f"invalid physical_limits: {exc}")
        if physical_screen["status"] != "conditional_pass":
            return _failure("physical load path is rejected or unknown", physical_screen=physical_screen)
    owner_id = identifiers[0] if len(identifiers) == 1 else "team:" + "+".join(sorted(identifiers))
    attachment_id = f"{MODEL}:{object_id}:{owner_id}"
    attachment = {
        "id": attachment_id,
        "object_id": object_id,
        "owner_id": owner_id,
        "members": identifiers,
        "manipulators": [item["manipulator"] for item in member_info],
        "mode": "ideal_fixed_attachment",
    }
    ownership = {
        "owner_id": owner_id,
        "kind": "robot" if len(identifiers) == 1 else "joint_team",
        "members": identifiers,
    }
    result = _base(True)
    result.update({
        "attachment": attachment,
        "ownership": ownership,
        "object_id": object_id,
        "object_mass_kg": mass,
        "joint_capacity_kg": total_capacity,
        "members": member_info,
    })
    if physical_screen is not None:
        result["physical_screen"] = physical_screen
    return result


__all__ = ["MODEL", "evaluate_grasp"]
