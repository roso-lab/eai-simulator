"""Aggregate runtime mass properties; inputs are simulation observations."""
from __future__ import annotations

import torch


def aggregate_runtime_physics(masses, local_coms, inertias_at_com, positions, rotations):
    count = masses.numel()
    if count == 0 or masses.shape != (count,):
        raise ValueError("Expected nonempty one-dimensional masses")
    if any(value.shape != (count, 3) for value in (local_coms, positions)):
        raise ValueError("COM and position coverage must match masses")
    if any(value.shape != (count, 3, 3) for value in (inertias_at_com, rotations)):
        raise ValueError("Inertia and rotation coverage must match masses")
    values = (masses, local_coms, inertias_at_com, positions, rotations)
    if not all(torch.isfinite(value).all() for value in values) or not (masses > 0).all():
        raise ValueError("Runtime mass properties must be finite with positive mass")
    identity = torch.eye(3, dtype=masses.dtype, device=masses.device)
    if (not torch.allclose(rotations.transpose(-1, -2) @ rotations,
                           identity.expand(count, 3, 3), atol=1e-5, rtol=0)
            or not torch.allclose(torch.linalg.det(rotations), torch.ones_like(masses), atol=1e-5, rtol=0)):
        raise ValueError("Rotations must be proper orthonormal matrices")
    if not torch.allclose(inertias_at_com, inertias_at_com.transpose(-1, -2), atol=1e-6, rtol=1e-5):
        raise ValueError("Inertia tensors must be symmetric")
    world_coms = positions + (rotations @ local_coms.unsqueeze(-1)).squeeze(-1)
    total_mass = masses.sum()
    center = (masses[:, None] * world_coms).sum(dim=0) / total_mass
    deltas = world_coms - center
    shifted = masses[:, None, None] * (
        deltas.square().sum(dim=-1)[:, None, None] * identity
        - deltas.unsqueeze(-1) @ deltas.unsqueeze(-2))
    tensors = rotations @ inertias_at_com @ rotations.transpose(-1, -2) + shifted
    return {"mass_kg": float(total_mass), "center_of_mass_m": center.tolist(),
            "inertia_kg_m2": tensors.sum(dim=0).tolist(),
            "inertia_reference_point": "system_center_of_mass", "frame": "simulation_world",
            "status": "simulation_derived", "body_count": count}
