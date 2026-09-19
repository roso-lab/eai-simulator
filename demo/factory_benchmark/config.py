"""Scene resources for the factory benchmark application."""

SCENE_KEY = 'factory'
USD_RELATIVE_PATH = 'scene/factory/factory.usd'
MAP_RELATIVE_PATH = 'scene/factory/factory_map.yaml'
# This is a derived, conservative 3-D product.  It is part of the released
# scene closure when the provider revision includes it; a missing product is
# reported as unknown by the aerial baseline rather than replaced by the
# 2-D map.
AERIAL_VOXEL_RELATIVE_PATH = 'scene/factory/drone_voxels_0.25m.npz'
AERIAL_VOXEL_METADATA_RELATIVE_PATH = 'scene/factory/drone_voxels_0.25m.meta.json'
AERIAL_PROXY_XY_BLOCK_SIZE = 1

from .prim_bindings import audit_factory_prim_bindings

PRIM_BINDING_CHECK = audit_factory_prim_bindings
