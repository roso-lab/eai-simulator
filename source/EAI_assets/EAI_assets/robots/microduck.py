"""MicroDuck biped configuration (Pollen Robotics microduck, 14x XL330 servos).

Asset provenance
----------------
- ``usd/robot/microduck/microduck_mjcf.usd`` is the PhysX-native articulation
  used here.  It was converted from the pollen ``microduck_rl`` MJCF
  (``robot_walk.xml`` semantics) through Isaac Sim 5.1's MJCF importer, so the
  14 revolute joints keep the official MJCF order, axes, ranges and inertials.
- The original Nucleus USDs (``microduck_walk.usd`` / ``microduck_allcollisions.usd``,
  plus LICENSE / ATTRIBUTION.txt) are archived alongside in ``usd/robot/microduck/``.
  They are authored for the Newton solver and are NOT usable as PhysX
  articulations in this stack; they remain as reference and license evidence.
- Licensing: the pretrained policies on HF ``pollen-robotics/microduck-policies``
  (incl. ``velstand.onnx``) are Apache-2.0; the robot model license terms are in
  ``usd/robot/microduck/LICENSE`` and ``ATTRIBUTION.txt``.

Actuator model
--------------
The physical robot drives XL330-M288-T servos (M6 internal gearing) through a
firmware position loop.  Two parameterizations exist upstream:

- MJCF ``chosen_actuator`` class (static fit): joint kp 0.55 N·m/rad, kv 0,
  forcerange ±0.96 N·m, joint damping 0.053, armature 0.0018,
  frictionloss 0.0048 N·m.
- IsaacLab develop ``microduck.py`` (BAM fit): firmware kp 200 with XL330-M6
  motor model (kt ≈ 0.366 N·m/A, max current 1.75 A, vin 7.4 V → torque
  ceiling ≈ ±0.96 N·m at stall, joint-side no-load speed ≈ vin/kt ≈ 20 rad/s).

PhysX has neither BAM nor MuJoCo's dof armature path here, so the actuator is
approximated with a DCMotorCfg: effort/saturation ±0.96 N·m from both fits,
damping 0.05 from the MJCF joint damping, friction 0.0048 from the MJCF
frictionloss, velocity_limit ≈ 20 rad/s from the BAM no-load speed.  Stiffness
starts at 2.0 N·m/rad — deliberately harder than the MJCF kp 0.55 (which pairs
with a near-zero kv and relies on MuJoCo's soft contact solve) because an
explicit-position PD this soft lets the 0.7 kg robot sag at rest under PhysX;
the BAM firmware loop (kp_fw 200 × kt 0.366 ≈ 73 N·m/rad at the motor, heavily
voltage-clipped to the ±0.96 N·m ceiling) behaves like a saturated hard
spring, and 2.0 is the soft end of that regime.  Retune from the stage-5
smoke test if the stand pose oscillates or sags.

Spawn height
------------
The official IsaacLab init pos is z = 0.125 (trunk ``trunk_base`` frame over
its own origin), but this USD's articulation root frame sits ≈ 0.12 m lower in
the authored hierarchy: spawning at 0.125 drops the robot and it settles at a
stable root height ≈ 0.244.  ``MICRODUCK_INIT_ROOT_Z`` is therefore 0.24 as a
calibration constant to be confirmed by the stage-5 PhysX smoke test (write
the measured value back here once taken).
"""

import isaaclab.sim as sim_utils
from isaaclab.actuators import DCMotorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path


usd_path = asset_path("robot/microduck/microduck_mjcf.usd")

# Official MJCF articulation order (also the velstand ONNX action/obs order).
MICRODUCK_JOINT_NAMES = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)

MICRODUCK_LEG_JOINT_NAMES = tuple(
    name for name in MICRODUCK_JOINT_NAMES if "_hip_" in name or "knee" in name or "ankle" in name
)
MICRODUCK_HEAD_JOINT_NAMES = ("neck_pitch", "head_pitch", "head_yaw", "head_roll")

# HOME / STAND2 pose — official values shared by the MJCF keyframes, the
# IsaacLab develop config and the policy export metadata (DEFAULT_POSE in
# microduck_rl scripts/infer_policy.py).  The right leg mirrors the left.
MICRODUCK_DEFAULT_JOINT_POS = {
    "left_hip_yaw": 0.0,
    "left_hip_roll": -0.0873,
    "left_hip_pitch": -0.4579,
    "left_knee": -0.0049,
    "left_ankle": 0.4530,
    "neck_pitch": 0.3491,
    "head_pitch": 0.3491,
    "head_yaw": 0.0,
    "head_roll": 0.0,
    "right_hip_yaw": 0.0,
    "right_hip_roll": 0.0873,
    "right_hip_pitch": 0.4579,
    "right_knee": 0.0049,
    "right_ankle": -0.4530,
}

# Spawn height calibration point (see module docstring): official IsaacLab
# 0.125 refers to a different root-frame convention than this USD; PhysX
# validation measured the stable root height at ≈ 0.244.
MICRODUCK_INIT_ROOT_Z = 0.125

MICRODUCK_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            retain_accelerations=False,
            linear_damping=0.0,
            angular_damping=0.0,
            max_linear_velocity=1000.0,
            max_angular_velocity=1000.0,
            max_depenetration_velocity=1.0,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False, solver_position_iteration_count=4, solver_velocity_iteration_count=0
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, MICRODUCK_INIT_ROOT_Z),
        joint_pos=dict(MICRODUCK_DEFAULT_JOINT_POS),
        joint_vel={".*": 0.0},
    ),
    soft_joint_pos_limit_factor=0.9,
    actuators={
        # All 14 XL330 servos share one group: legs and head have identical
        # motors, torque ceilings and firmware gains on the real robot.
        # Joint armature (MJCF 0.0018) travels with the USD dofs, not the
        # actuator cfg.
        "servos": DCMotorCfg(
            joint_names_expr=list(MICRODUCK_JOINT_NAMES),
            effort_limit=0.96,
            saturation_effort=0.96,
            velocity_limit=20.0,
            stiffness=2.0,
            damping=0.05,
            friction=0.0048,
        ),
    },
)
