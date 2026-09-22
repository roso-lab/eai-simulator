"""Repository configuration for the restaurant authoring scene."""

SCENE_KEY = "restaurant"
USD_RELATIVE_PATH = "scene/restaurant/Restaurant_Only.usda"
SOURCE_METERS_PER_UNIT = 0.01
RUNTIME_SCALE = (0.01, 0.01, 0.01)

# These gates remain closed until scene-derived products and execution evidence
# exist. Keeping them explicit prevents a successful visual load from being
# interpreted as a benchmark-ready episode.
RELEASE_STATUS = "authoring_draft"
EXECUTION_VALIDATED = False
SCOREABLE = False
