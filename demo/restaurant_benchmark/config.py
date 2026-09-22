"""Repository configuration for Restaurant authoring and benchmark scenes."""

SCENE_KEY = "restaurant"
BENCHMARK_SCENE_KEY = "restaurant_benchmark"
USD_RELATIVE_PATH = "scene/restaurant/Restaurant_Only.usda"
SOURCE_SHA256 = "618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b"
UNRESOLVED_MANIFEST_SHA256 = (
    "9c05e33a274844ba5f8fc7dd6a24ab00eabcaf9e5d9ece99d9c6d641a6b1f626"
)
USED_LAYER_MANIFEST_SHA256 = (
    "9a499b56079e4a7658fac047cb0d80bc2b98f54f17ef1aac7c4caa42bf887eaf"
)
SOURCE_METERS_PER_UNIT = 0.01
RUNTIME_SCALE = (0.01, 0.01, 0.01)

# The first robot receives the builder's -3 m X formation offset. These values
# therefore place Carter at (21.875, 20.875), a high-clearance cell in the
# largest component of the 0.40 m inflated candidate map.
BENCHMARK_ROBOT_SPAWN_ORIGIN = (24.875, 20.875)
BENCHMARK_PRIMARY_SPAWN_XY_M = (21.875, 20.875)

# These gates remain closed until scene-derived products and execution evidence
# exist. Keeping them explicit prevents a successful visual load from being
# interpreted as a benchmark-ready episode.
RELEASE_STATUS = "authoring_draft"
EXECUTION_VALIDATED = False
SCOREABLE = False
