# On-demand camera observation

The only observation Skill is `eai.observation.camera`. A typed
`SkillRequest(camera_ref, mode=capture, deadline, resolution, quality)`
captures one metered RGB frame from a robot's builder-registered default
camera; continuous subscription (M2) and discovery anchors are out of scope.

`camera_call` maps the typed request onto the frozen tool-call envelope
without discarding unknown fields. `GoalCameraTools` owns admission (sensor
rights, encoding whitelist, byte and deadline budgets), the per-request
terminal state machine (delivered / timeout / cancelled / failed /
inactive_robot / episode_ended), the episode frame cache, and the idle render
lifecycle. Metered bytes never roll back, late frames after a terminal state
are suppressed, and `payload_ref`/local paths are rejected at the telemetry
boundary. Every capture costs `CAPTURE_TICKS = 5` simulation ticks.

Capture backends sit behind the small `CameraCaptureBackend` protocol:
`FakeOnDemandCameraBackend` for static CPU tests, `BuilderCameraBackend` to
attach a render product and RGB annotator to an existing builder camera prim
(live GPU path, gate C evidenced on carter's default D455), and
`OrsusOnDemandCameraBackend` as the legacy payload sample. Static tests never
stand in for live capture evidence.

`communication.py` is the deterministic transport metering (offered/link
bytes, windowed delivery); `telemetry.py` publishes validated frames through
it. The benchmark side (`benchmark.observation_runtime`,
`benchmark.observation_episode`, `benchmark.observation_artifacts`) wraps the
typed boundary in the episode driver, branch ledger rows and content-addressed
artifact storage; receipts carry digests and metadata, never payload bytes.
