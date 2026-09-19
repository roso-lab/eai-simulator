"""Preserve mounted root poses before simulator warm-up changes their state."""
from __future__ import annotations


def capture_root_poses(stage, root_paths):
    from pxr import UsdGeom

    poses = {}
    for path in root_paths:
        prim = stage.GetPrimAtPath(path)
        if not prim.IsValid():
            raise ValueError(f"Missing articulation root: {path}")
        transform = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0)
        rotation = transform.ExtractRotationQuat().GetNormalized()
        poses[str(path)] = (*transform.ExtractTranslation(), rotation.GetReal(), *rotation.GetImaginary())
    return poses


def set_spawn_root_defaults(articulation, poses, env_origins=None):
    """Use authored world poses and zero velocity, never a warm-up snapshot."""
    paths = articulation.root_physx_view.prim_paths
    missing = [path for path in paths if path not in poses]
    if missing:
        raise ValueError(f"Missing spawn poses for articulation roots: {missing}")
    defaults = articulation.data.default_root_state
    defaults[:, :7] = defaults.new_tensor([poses[path] for path in paths])
    if env_origins is not None:
        defaults[:, :3] -= env_origins
    defaults[:, 7:] = 0.0
