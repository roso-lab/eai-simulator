"""Public on-demand camera observation skill.

The implementation is the 2026-09-23 metered camera companion ported out of
the retired demo host.  Isaac/RTX is deliberately behind the small
``CameraCaptureBackend`` protocol; the default static backend is pure Python.
"""

from .communication import CommunicationRuntime, validate_communication_report
from .ondemand import FakeOnDemandCameraBackend, OnDemandRenderLifecycle, OrsusOnDemandCameraBackend
from .telemetry import GoalCameraTelemetry
from .tools import CAMERA_TOOL_SCHEMA, CameraCaptureBackend, GoalCameraTools, normalize_sensor_catalog
from .runtime import SKILL_ID, CAPTURE_TICKS, camera_call
from .builder_backend import BuilderCameraBackend, BuilderCameraBinding

__all__ = [
    "BuilderCameraBackend", "BuilderCameraBinding", "CAMERA_TOOL_SCHEMA",
    "CameraCaptureBackend", "CAPTURE_TICKS", "CommunicationRuntime", "SKILL_ID",
    "camera_call", "FakeOnDemandCameraBackend",
    "GoalCameraTelemetry", "GoalCameraTools",
    "OnDemandRenderLifecycle", "OrsusOnDemandCameraBackend", "normalize_sensor_catalog",
    "validate_communication_report",
]

# NOTE: rgb.py (the parallel dependency-light RGB workflow) is intentionally
# NOT re-exported here: it imports RGB_OBSERVATION_SKILL_ID, which is not
# defined anywhere in the tree yet (that workflow is uncommitted WIP).  Its
# consumers import it directly as algorithm.skills.observation.rgb.
