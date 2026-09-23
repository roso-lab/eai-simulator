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

# Restaurant adopts the deployment-realism v2 authoring standard from its
# first goal episode. These anchors are deliberately spread across the
# source-bound candidate map instead of using the dynamic builder's compact
# formation. Ground anchors are admitted on the 0.40 m inflated map; aerial
# anchors use the same conservative horizontal cells and a 3 m flight height.
# The public goal task exposes the complete map so team selection can reason
# about travel distance at t=0.
BENCHMARK_DEPLOYMENT_PROFILE = "bound_native_v2"
BENCHMARK_ROBOT_SPAWN_XYZ_M = {
    "carter_1": (21.875, 20.875, 0.20),
    "carter_2": (10.675, 18.375, 0.20),
    "m20_1": (2.125, -0.675, 0.53),
    "m20_2": (33.525, -0.675, 0.53),
    "go2_1": (0.875, 14.525, 0.62),
    "lite3_1": (34.675, 13.475, 0.35),
    "iris_1": (8.325, 7.675, 3.0),
    "iris_2": (26.975, 6.975, 3.0),
}
BENCHMARK_MINIMUM_SPAWN_SPACING_M = 3.0
# Every anchor now holds route-level native evidence: the Carters from the
# original route/contact probes, go2/lite3/iris from the 2026-09-22
# route_admission runs, and both M20s through the registered navigation
# controller (the rough policy's zero-command bias stays rejected; the nav
# variant's closed position loop passes admission and route gates).
BENCHMARK_NATIVE_VALIDATED_ROBOT_IDS = (
    "carter_1",
    "carter_2",
    "m20_1",
    "m20_2",
    "go2_1",
    "lite3_1",
    "iris_1",
    "iris_2",
)
BENCHMARK_ADMISSION_STATUS = "authoring_pending_native_candidate_evidence"

# These gates remain closed until scene-derived products and execution evidence
# exist. Keeping them explicit prevents a successful visual load from being
# interpreted as a benchmark-ready episode.
RELEASE_STATUS = "authoring_draft"
EXECUTION_VALIDATED = False
SCOREABLE = False
