"""Conditional engineering screens, never physical validation certificates.

Quantities must describe the same hardware/configuration and operating point.
Unknown limits fail closed. Equal load sharing, friction and aerodynamic data
must be supplied explicitly rather than inferred from robot names.
"""
from __future__ import annotations

import math


def _quantity(data, name, *, positive=False):
    value = data.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _result(checks, missing, **details):
    failed = [name for name, margin in checks.items() if margin < 0]
    return {"status": "rejected" if failed else "unknown" if missing else "conditional_pass",
            "physical_validated": False, "basis": "declared_model_screen",
            "margins": checks, "violations": failed, "missing": sorted(set(missing)), **details}


def transport_load_screen(object_mass_kg, carriers, *, vertical_acceleration_m_s2=0.0,
                          horizontal_acceleration_m_s2=0.0, safety_factor=1.5):
    """Check each load path: base, arm including tool, and frictional retention.

arm_payload_at_cog_kg is the capacity at the actual tool+object COM, not the
headline arm rating. attachment_mass_kg includes the whole arm/tool/sensor
assembly carried by the base. normal_force_N is the sum across contacting
jaws; it is not a motor joint torque. Positive vertical acceleration is up.
"""
    mass = _quantity({"mass": object_mass_kg}, "mass", positive=True)
    vertical = _quantity({"vertical": vertical_acceleration_m_s2}, "vertical")
    horizontal = _quantity({"horizontal": horizontal_acceleration_m_s2}, "horizontal")
    factor = _quantity({"factor": safety_factor}, "factor", positive=True)
    if mass is None or vertical is None or horizontal is None or factor is None or factor < 1:
        raise ValueError("mass, accelerations and safety_factor >= 1 are required")
    if not carriers:
        raise ValueError("at least one explicit carrier is required")
    checks, missing, ids = {}, [], set()
    total_share = 0.0
    for carrier in carriers:
        identifier = carrier.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError("carrier ids must be unique and nonempty")
        ids.add(identifier)
        quantities = {name: _quantity(carrier, name) for name in (
            "share_kg", "base_payload_kg", "attachment_mass_kg", "arm_payload_at_cog_kg",
            "tool_mass_kg", "normal_force_N", "friction_coefficient")}
        missing.extend(f"{identifier}.{name}" for name, value in quantities.items() if value is None)
        share = quantities["share_kg"]
        if share is None:
            continue
        total_share += share
        if quantities["base_payload_kg"] is not None and quantities["attachment_mass_kg"] is not None:
            checks[f"{identifier}.base_spare_kg"] = (
                quantities["base_payload_kg"] - quantities["attachment_mass_kg"] - share)
        if quantities["arm_payload_at_cog_kg"] is not None and quantities["tool_mass_kg"] is not None:
            checks[f"{identifier}.arm_spare_kg"] = (
                quantities["arm_payload_at_cog_kg"] - quantities["tool_mass_kg"] - share)
        if quantities["normal_force_N"] is not None and quantities["friction_coefficient"] is not None:
            demand = share * math.hypot(9.81 + vertical, horizontal) * factor
            checks[f"{identifier}.retention_spare_N"] = (
                quantities["normal_force_N"] * quantities["friction_coefficient"] - demand)
    if not any(name.endswith(".share_kg") for name in missing):
        checks["load_share_balance_kg"] = 1e-6 - abs(total_share - mass)
    return _result(checks, missing, object_mass_kg=mass, safety_factor=factor,
                   limitations=["Requires verified CoG-specific ratings and load distribution",
                                "No grasp reachability, torque trajectory, base stability or contact simulation"])


def clearance_screen(*, measured_clearance_m=None, required_margin_m=None,
                     geometry_error_m=None, tracking_error_m=None, sampling_error_m=None):
    """Budget errors against measured swept clearance; a planning radius is insufficient."""
    data = locals()
    values = {name: _quantity(data, name) for name in data}
    missing = [name for name, value in values.items() if value is None]
    checks = {} if missing else {"clearance_reserve_m": values["measured_clearance_m"] - sum(
        value for name, value in values.items() if name != "measured_clearance_m")}
    return _result(checks, missing, limitations=["Requires swept robot+arm+payload geometry and bounded errors",
                                                "No collision safety certification"])


def wind_screen(*, mass_kg=None, thrust_limit_N=None, tilt_limit_rad=None, drag_coefficient=None,
                projected_area_m2=None, air_density_kg_m3=None, wind_speed_m_s=None):
    """Static drag/thrust balance only; no gust response or controller certification."""
    data = locals()
    values = {name: _quantity(data, name, positive=name not in {"wind_speed_m_s", "tilt_limit_rad"})
              for name in data}
    missing = [name for name, value in values.items() if value is None]
    tilt = values["tilt_limit_rad"]
    if tilt is not None and tilt >= math.pi / 2:
        raise ValueError("tilt_limit_rad must be below pi/2")
    if missing:
        return _result({}, missing)
    weight = values["mass_kg"] * 9.81
    drag = (0.5 * values["air_density_kg_m3"] * values["drag_coefficient"]
            * values["projected_area_m2"] * values["wind_speed_m_s"] ** 2)
    return _result({"thrust_reserve_N": values["thrust_limit_N"] - math.hypot(weight, drag),
                    "tilt_reserve_rad": tilt - math.atan2(drag, weight)}, [], drag_force_N=drag,
                   limitations=["Declared steady aerodynamic model; no measured wind tolerance",
                                "Rotor aerodynamics, battery sag and gust/controller dynamics are untested"])
