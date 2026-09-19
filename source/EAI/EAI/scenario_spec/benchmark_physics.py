"""Benchmark attachment physics policy, independent of interactive DIY tuning."""
from dataclasses import fields, replace

BENCHMARK_PHYSICS_CONTRACT = {
    "id": "asset_authored_unscaled_v1",
    "arm_mass_scale": 1.0,
    "arm_inertia_scale": 1.0,
    "arm_geometry_scale": 1.0,
    "hardware_equivalence": "requires_asset_and_actuator_calibration",
}


def benchmark_mount_profile(profile):
    """Copy a mount profile without modifying the shared simulator defaults."""
    names = {field.name for field in fields(profile)}
    if "mass_scale" not in names:
        raise ValueError("benchmark mount profile must expose mass_scale")
    changes = {"mass_scale": 1.0}
    for field_name in ("visual_scale", "inertia_scale", "geometry_scale"):
        if field_name in names:
            changes[field_name] = 1.0
    return replace(profile, **changes)


def has_current_mass_audit(report):
    """Legacy execution evidence cannot establish the unscaled benchmark contract."""
    audit = report.get("benchmark_mass_audit") if isinstance(report, dict) else None
    return (isinstance(audit, dict) and audit.get("validated") is True
            and audit.get("contract") == BENCHMARK_PHYSICS_CONTRACT
            and audit.get("violations") == [] and isinstance(audit.get("arms"), dict))
