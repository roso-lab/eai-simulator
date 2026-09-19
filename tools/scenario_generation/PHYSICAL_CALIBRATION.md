# Benchmark physical calibration

Scenario V2 benchmarks use **unscaled asset-authored arm masses and inertias**.
`env_builder.py` copies UR5/Z1 mount profiles through `benchmark_mount_profile`
whenever `scenario_data` is present. This applies to Factory, Warehouse and Modern
House, including the `simulator.py --scenario-file` entry point. Normal saved Env
DIY selections use the same unscaled arm profiles. There is no simulator option
to reduce an arm's mass to make a task pass; historical scaled reports must be
rerun before reuse.

`asset_authored_unscaled_v1` is the physics contract. Unscaled assets are the
starting point for hardware alignment, not proof of hardware equivalence. In
particular, local URDF, USD, optional end effectors, manufacturer variants, drive
limits and controller tuning may differ. The UR5 USD has six arm joints and no
gripper. A UR5 attachment alone cannot establish contact grasp capability.

## Evidence boundaries

| Evidence | What it establishes | What it cannot establish |
| --- | --- | --- |
| Manufacturer reference (E0) | Published specification for a named product variant and condition | Matching simulator capacity or measured hardware performance |
| Unscaled runtime audit | Per-link PhysX mass and inertia agree with source USD | Correct manufacturer mass or drive model |
| Ideal grasp and engineering screens | Feasibility under explicit capacity, load sharing, force and uncertainty assumptions | Contact, physical success or hardware certification |
| Kinematic execution (E6) | Navigation and scripted object-ownership workflow | Grasping or payload dynamics |
| Standalone calibration (scoped E7 when all gates pass) | Contact grasp, lift, transfer and release for tested masses on one fixed-base component fixture | Mobile robot, team, scene task, general capacity, wind or collision safety |

`runtime_evidence.merge_runtime_evidence` rejects generic E7 robot promotion.
Setting `tier=E7` and `physical_validated` in JSON is insufficient. Standalone
results remain separate component evidence; they cannot certify a robot carrying
that component. New frozen protocols require the current mass audit. Old reports
remain historical records and must be rerun for the unscaled benchmark.

## Reproduce the contact experiment

Use the prepared Isaac Lab environment. The Z1 asset must already be available.
This fixture is independent of EAI scene assembly and creates one Isaac
application; benchmark scene runs continue to use `open_simulator_session`.

```bash
EAI_ASSETS_AUTO_DOWNLOAD=0 python tools/scenario_generation/calibrate_z1.py \
  --masses 0.2 2 8 30 --repeats 3 \
  --report tmp/physical_calibration/z1_run.json
```

Choose a new report path for each run. `--gui` enables rendering. The default
physics device is CPU, timestep is 1/240 second, and the arm's mass scale is 1.
Continuous collision detection is enabled for the payload, including its release
and floor impact. The commanded motion is a 10 cm lift and 15 cm lateral transfer.
Every mass has paired closed-jaw and open-jaw trials with three deterministic
repeats. These repeats measure reproducibility, not independent randomized
trials or statistical robustness. No payload pose writes occur after trial
initialization; no fixed joint attaches the object to the gripper. Gravity and
collision remain enabled. Joint targets drive the arm through position and
orientation IK. The object is prepositioned, so this does not verify approach or
autonomous scene pickup.

The force-driven arm limits follow the local URDF: joint2 60 N m, other arm
joints 30 N m, gripper joint 30 N m. These are **asset declarations**, not measured
actuator or gripper ratings. Force readings are aggregate pairwise normal-contact
forces from PhysX, not calibrated hardware squeeze force or friction capacity.
Geometry is the asset's convex-hull collision approximation. The standard fixture
uses a 30 × 70 × 25 mm cuboid and static/dynamic payload friction of 0.8/0.6.
Changing its mass to 8 or 30 kg isolates mass effects; this small test object is
not a realistic shipping box.

The independent verifier requires complete per-step sampling of close, lift,
transfer, hold and release, each for at least one second. It checks:

- Runtime payload mass and per-link arm mass/inertia match the recorded unscaled sources.
- At least 5 cm of lift and 5 cm of lateral transfer throughout hold.
- Bilateral jaw contact during at least 95% of hold and no external support.
- Hold slip at most 15 mm and maximum contact penetration at most 5 mm.
- Release contact disappears, the object drops at least 5 cm and settles.
- All positive repeats pass and every paired open-jaw control fails retained transport.

Thresholds are fixture acceptance criteria, not industrial safety standards.
Reports retain all failed masses; the verifier reports discrete successful masses
and never extrapolates a maximum rated capacity. Changes to the runner or source
assets during execution reject the run. Reports include runtime versions, source
layer hashes, configuration hash, raw samples, and a local hash manifest.

For an independent recheck, use hashes from the trusted run manifest, not a
candidate's self-declared certificate:

```bash
python -m tools.scenario_generation.physical_evidence \
  tmp/physical_calibration/z1_run.json \
  --report-sha256 <canonical-report-sha256> \
  --runner-sha256 <runner-sha256> \
  --out tmp/physical_calibration/z1_recheck.json
```

Hash binding detects substitution relative to a trusted ledger; it does not
authenticate arbitrary third-party measurements. Keep raw reports in `tmp/`.

## Scenario mass and inertia audit

`settle_smoke.py` and `execute_episode.py` compare every mounted arm body with its
source USD. Principal-axis inertia is transformed into body-frame tensor form
before comparison with PhysX. Missing, nonfinite, scaled or mismatched values
fail the audit. Both reports carry `benchmark_mass_audit` with source hashes and
per-link readings. The execution harness rejects before running the task if the
audit fails. A successful settle is still only E5.

## Real specifications and conditional feasibility

`manufacturer_specs.py` records reviewed manufacturer references, keeping Go2
AIR/PRO/X/EDU, B2 standing/walking, Z1 AIR/PRO, UR5 CB3 and brushed Crazyflie 2.1
separate. Dossiers default to the benchmark context; explicitly requesting
`physics_context="simulator"` returns the interactive mount tuning. Manufacturer
numbers never populate `payload.capacity_kg` automatically. Unidentified hardware
variants remain unverified.

The manufacturer sources are:

- [Go2 specifications](https://www.unitree.com/go2/): approximate back payload 7 kg for AIR and 8 kg for PRO/X/EDU, with separate maximum figures.
- [B2 specifications](https://www.unitree.com/b2/): walking load greater than 40 kg; standing load at least 120 kg. Neither is an arm end-load rating.
- [Z1 specifications](https://www.unitree.com/z1/): AIR 2 kg, PRO at least 3 kg, subject to test and control conditions.
- [UR5 CB3 datasheet](https://www.universal-robots.com/media/50588/ur5_en.pdf): maximum payload 5 kg and arm weight 18.4 kg.
- [UR deployment guide](https://www.universal-robots.com/media/1810042/cb3_deployment_workshop_online_en_1c.pdf): UR5 payload is derated with tool COM offset; examples are 5 kg at 350 mm and 3.75 kg at 500 mm.
- [Crazyflie 2.1 datasheet](https://www.bitcraze.io/documentation/hardware/crazyflie_2_1/crazyflie_2_1-datasheet.pdf): maximum recommended added payload 15 g. This is not the brushless model.

`physical_limits.transport_load_screen` requires each carrier's explicit share,
base capacity, total mounted assembly mass, arm capacity **at the actual COM**,
tool mass, total jaw normal force and friction coefficient. It checks base and
arm capacity separately and uses `share * hypot(g + az, ax) * safety_factor` for
retention demand. A large base capacity cannot bypass a smaller arm limit. Joint
torque in N m cannot be substituted for jaw force in N. Pass these inputs through
`evaluate_grasp(..., policy={..., "physical_limits": {...}})` to require the
screen before ideal attachment. Missing data fail closed. Omitting this section
retains the explicitly abstract ideal-grasp model; it makes no physical claim.

`clearance_screen` subtracts geometry, tracking and sampling error budgets from
measured swept robot+arm+payload clearance. It cannot turn a planning radius into
a collision safety margin. `wind_screen` checks steady drag against thrust and
tilt limits only when mass, aerodynamic coefficient, projected area, density and
wind speed are supplied. Neither screen estimates missing parameters or certifies
gust rejection. There are currently no measured robot wind-tolerance conclusions.

Hardware-aligned mobile manipulation still requires the correct gripper,
manufacturer variant/COM curves, verified drive/torque limits, loaded base
stability, motion and braking, swept collision measurements, and task-level
contact trials in the selected scene. Failed trials must not be rescued by
scaling masses or silently promoting ideal execution to physical evidence.
