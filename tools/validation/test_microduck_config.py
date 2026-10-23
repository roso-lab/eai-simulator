"""Pure-Python configuration checks for the MicroDuck biped integration.

Nothing here launches Isaac Sim.  The ``isaaclab`` configuration classes used
at module scope by ``EAI_assets.robots.microduck`` and the velstand controller
are replaced with plain attribute holders (the pattern of
``tools/validation/test_d1_config.py``); ``onnxruntime``/``EAI.controllers``
are stubbed the same way when absent so the wiring checks run on any
interpreter.  The tensor-level observation/action unit tests require the real
``torch`` and the ONNX metadata cross-check requires ``onnxruntime`` — both
present in the project's env_isaaclab interpreter, skipped elsewhere.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _source_root in (REPO_ROOT / "source" / "EAI_assets", REPO_ROOT / "source" / "EAI"):
    if str(_source_root) not in sys.path:
        sys.path.insert(0, str(_source_root))


class _StubConfig:
    def __init__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)


class _StubArticulationCfg(_StubConfig):
    InitialStateCfg = _StubConfig


def _stub_configclass(cls=None, **_kwargs):
    if cls is not None:
        return cls
    return lambda inner: inner


def _define_module(name, **attributes):
    # If another pure-Python test module already installed a stub for this
    # name in the same process, merge into it instead of replacing it (its
    # already-loaded consumers keep their attributes).
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__eai_test_stub__", False):
        existing.__dict__.setdefault("__eai_test_stub__", True)
        existing.__dict__.update(attributes)
        return existing
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name) or _define_module(parent_name)
        setattr(parent, child, module)
    return module


def _install_isaaclab_stubs():
    # Merge rather than replace: another pure-Python test module collected
    # earlier in the same process (e.g. test_d1_config.py) installs its own
    # isaaclab stub whose submodules may lack the classes used here.
    _define_module("isaaclab", __eai_test_stub__=True)
    _define_module(
        "isaaclab.sim",
        UsdFileCfg=_StubConfig,
        RigidBodyPropertiesCfg=_StubConfig,
        ArticulationRootPropertiesCfg=_StubConfig,
    )
    _define_module("isaaclab.actuators", DCMotorCfg=_StubConfig, ImplicitActuatorCfg=_StubConfig)
    _define_module(
        "isaaclab.assets",
        ArticulationCfg=_StubArticulationCfg,
    )
    _define_module(
        "isaaclab.assets.articulation",
        ArticulationCfg=_StubArticulationCfg,
    )
    _define_module("isaaclab.utils", configclass=_stub_configclass)


def _install_optional_stub(name: str, **attributes):
    try:
        importlib.import_module(name)
        return False
    except ImportError:
        _define_module(name, **attributes)
        return True


_install_isaaclab_stubs()


def _torch_is_real() -> bool:
    torch_module = sys.modules.get("torch")
    if torch_module is None:
        try:
            import torch  # noqa: F401

            return True
        except ImportError:
            return False
    # Another pure-Python test module in the same process (e.g.
    # test_d1_config.py) may have installed its own minimal torch stub; that
    # stub cannot back the velstand controller's tensor usage.
    if getattr(torch_module, "__eai_test_stub__", False):
        return False
    return hasattr(torch_module, "zeros")


# torch is needed at controller import time (constants + function bodies); a
# real torch additionally unlocks the tensor-level unit tests below.
TORCH_AVAILABLE = _torch_is_real()
_REAL_TORCH = sys.modules.get("torch") if TORCH_AVAILABLE else None
if not TORCH_AVAILABLE:
    _define_module(
        "torch",
        __eai_test_stub__=True,
        float32="float32",
        long="long",
        device="device",
        Tensor="Tensor",
        tensor=lambda *args, **kwargs: args[0] if args else None,
    )
requires_torch = pytest.mark.skipif(not TORCH_AVAILABLE, reason="real torch unavailable")

# The controller module imports EAI.controllers (whose real chain needs torch
# + onnxruntime).  Stub only that submodule when the real one cannot import;
# the EAI package itself stays real (pure Python, lazy imports).
def _install_controllers_stub():
    try:
        from EAI.controllers import RSLControllerCfg  # noqa: F401

        return
    except Exception:
        pass

    import importlib

    eai_package = importlib.import_module("EAI")

    class _RSLControllerCfg:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    sys.modules.pop("EAI.controllers", None)
    _define_module("EAI.controllers", RSLControllerCfg=_RSLControllerCfg)
    eai_package.controllers = sys.modules["EAI.controllers"]


_install_controllers_stub()

import EAI_assets.robots.microduck as microduck_module
from EAI.hmrs_env.env_diy import catalog
from EAI_assets.asset_requirements import (
    _CONTROLLER_PATHS,
    _ROBOT_PATHS,
    resolve_selection,
)
from EAI_assets.asset_resolver import resolve_local_asset_path, controller_root

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

# Joint ranges parsed from the pollen microduck_rl robot_walk_merged.xml
# (the MJCF this USD was converted from).  Regenerate with:
#   python -c "import re;xml=open('robot_walk_merged.xml').read();print(
#     re.findall(r'name=\"([a-z_]+)\" type=\"hinge\" range=\"([^\"]+)\"', xml))"
MICRODUCK_MJCF_JOINT_LIMITS = {
    "left_hip_yaw": (-0.4363323129985824, 0.5235987755982988),
    "left_hip_roll": (-0.3839724354386992, 0.38397243543880577),
    "left_hip_pitch": (-1.570796326794949, 1.5707963267948442),
    "left_knee": (-1.5707963267948983, 1.5707963267948948),
    "left_ankle": (-1.5707963267949063, 1.5707963267948868),
    "neck_pitch": (-1.5707963267948974, 1.0471975511965967),
    "head_pitch": (-1.5707963267948966, 1.5707963267948966),
    "head_yaw": (-2.9670597283903613, 2.9670597283903595),
    "head_roll": (-0.43633231299858327, 0.4363323129985815),
    "right_hip_yaw": (-0.523598775598297, 0.43633231299858416),
    "right_hip_roll": (-0.3839724354387507, 0.38397243543875426),
    "right_hip_pitch": (-1.57079632679494, 1.570796326794853),
    "right_knee": (-1.5707963267949339, 1.5707963267948593),
    "right_ankle": (-1.5707963267949028, 1.5707963267948903),
}

CONTROLLER_PACKAGE = (
    REPO_ROOT / "source" / "EAI_assets" / "EAI_assets" / "controller" / "rl" / "microduck_velstand"
)


from contextlib import contextmanager


@contextmanager
def _real_torch_installed():
    """Run a block with the real torch module installed in sys.modules.

    Real torch ops redispatch through ``sys.modules["torch"]``; another test
    module's stub swapped in mid-process would break them.  Restores whatever
    module was installed before (or installs our minimal stub when no real
    torch exists).
    """
    previous = sys.modules.get("torch")
    if _REAL_TORCH is not None:
        sys.modules["torch"] = _REAL_TORCH
    try:
        yield
    finally:
        if previous is not None:
            sys.modules["torch"] = previous


@pytest.fixture()
def real_torch():
    if not TORCH_AVAILABLE:
        pytest.skip("real torch unavailable")
    with _real_torch_installed():
        yield _REAL_TORCH


def _load_controller_module():
    with _real_torch_installed():
        spec = importlib.util.spec_from_file_location(
            "_eai_microduck_velstand_test", CONTROLLER_PACKAGE / "microduck_velstand.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


# ─── Stage 2: articulation config ───────────────────────────────────────────


def test_joint_constants_match_official_microduck_definition():
    assert microduck_module.MICRODUCK_JOINT_NAMES == MICRODUCK_JOINT_NAMES
    assert microduck_module.MICRODUCK_LEG_JOINT_NAMES == tuple(
        name
        for name in MICRODUCK_JOINT_NAMES
        if "_hip_" in name or "knee" in name or "ankle" in name
    )
    assert microduck_module.MICRODUCK_HEAD_JOINT_NAMES == (
        "neck_pitch",
        "head_pitch",
        "head_yaw",
        "head_roll",
    )
    assert microduck_module.MICRODUCK_DEFAULT_JOINT_POS == MICRODUCK_DEFAULT_JOINT_POS


def test_default_pose_is_inside_the_mjcf_joint_limits():
    for name, position in MICRODUCK_DEFAULT_JOINT_POS.items():
        lower, upper = MICRODUCK_MJCF_JOINT_LIMITS[name]
        assert lower <= position <= upper, (name, position, lower, upper)


def test_hardcoded_limit_table_matches_microduck_rl_mjcf():
    mjcf = Path("/home/pupil/microduck_rl/src/mjlab_microduck/robot/microduck/robot_walk_merged.xml")
    if not mjcf.is_file():
        pytest.skip("microduck_rl checkout not present for limit-table cross-check")
    import re

    xml = mjcf.read_text(encoding="utf-8")
    parsed = {
        name: tuple(float(value) for value in rng.split())
        for name, rng in re.findall(
            r'<joint axis="[^"]*" name="([a-z_]+)" type="hinge" range="([^"]+)"', xml
        )
    }
    assert parsed == MICRODUCK_MJCF_JOINT_LIMITS


def test_articulation_cfg_shape_follows_go2_pattern():
    cfg = microduck_module.MICRODUCK_CFG
    assert cfg.spawn.usd_path == str(resolve_local_asset_path("robot/microduck/microduck_mjcf.usd"))
    assert Path(cfg.spawn.usd_path).is_file()
    assert cfg.spawn.activate_contact_sensors is True
    assert cfg.spawn.articulation_props.enabled_self_collisions is False
    assert cfg.spawn.articulation_props.solver_position_iteration_count == 4
    assert cfg.spawn.articulation_props.solver_velocity_iteration_count == 0
    assert cfg.soft_joint_pos_limit_factor == 0.9
    assert cfg.init_state.pos == (0.0, 0.0, microduck_module.MICRODUCK_INIT_ROOT_Z)
    assert cfg.init_state.joint_vel == {".*": 0.0}
    assert cfg.init_state.joint_pos == MICRODUCK_DEFAULT_JOINT_POS
    # Spawn-height calibration point: the EAI spawn settles at z≈0.117 with
    # the official-adjacent 0.125 init (0.24 was the pre-calibration estimate
    # that dropped the robot from above).
    assert microduck_module.MICRODUCK_INIT_ROOT_Z == pytest.approx(0.125)


def test_actuator_matches_xl330_fit():
    (actuator,) = microduck_module.MICRODUCK_CFG.actuators.values()
    assert actuator.joint_names_expr == list(MICRODUCK_JOINT_NAMES)
    assert actuator.effort_limit == pytest.approx(0.96)
    assert actuator.saturation_effort == pytest.approx(0.96)
    assert actuator.stiffness == pytest.approx(2.0)
    assert actuator.damping == pytest.approx(0.05)
    assert actuator.friction == pytest.approx(0.0048)
    assert actuator.velocity_limit == pytest.approx(20.0)


def test_usd_joint_order_matches_policy_order():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdPhysics

    stage = Usd.Stage.Open(microduck_module.MICRODUCK_CFG.spawn.usd_path)
    joints = [prim.GetName() for prim in stage.Traverse() if prim.IsA(UsdPhysics.RevoluteJoint)]
    assert joints == list(MICRODUCK_JOINT_NAMES)


# ─── Stage 3: velstand controller ───────────────────────────────────────────


def test_obs_layout_slices_partition_the_61_dimensions():
    module = _load_controller_module()
    slices = module.MICRODUCK_VELSTAND_OBS_SLICES
    expected_segments = (
        ("base_ang_vel", (0, 3)),
        ("projected_gravity", (3, 6)),
        ("joint_pos", (6, 20)),
        ("joint_vel", (20, 34)),
        ("actions", (34, 48)),
        ("command", (48, 51)),
        ("head_command", (51, 55)),
        ("body_command", (55, 61)),
    )
    assert tuple(slices.items()) == expected_segments
    cursor = 0
    for name, (start, end) in slices.items():
        assert start == cursor, name
        cursor = end
    assert cursor == module.MICRODUCK_VELSTAND_OBS_DIM == 61
    # Segment sizes: 3 + 3 + 14 + 14 + 14 + 3 + 4 + 6 = 61.
    assert [end - start for start, end in slices.values()] == [3, 3, 14, 14, 14, 3, 4, 6]
    assert module.MICRODUCK_VELSTAND_ACTION_DIM == 14
    assert module.MICRODUCK_VELSTAND_ACTION_SCALE == pytest.approx(1.0)
    assert module.MICRODUCK_VELSTAND_DECIMATION == 4


def test_controller_cfg_fields_follow_the_go2_rsl_pattern():
    module = _load_controller_module()
    cfg = module.MICRODUCK_VELSTAND_CFG
    model_path = Path(cfg.model_path)
    assert model_path.is_file() and model_path.name == "velstand.onnx"
    assert model_path.parent == CONTROLLER_PACKAGE / "model"
    assert (model_path.parent / "manifest.json").is_file()
    assert cfg.robot_type == "MicroDuck"
    assert cfg.command_name == "base_velocity"
    assert cfg.command_dim == 3
    assert cfg.observation_func is module.compute_microduck_velstand_observations
    assert cfg.apply_action_func is module.apply_microduck_velstand_action
    assert (
        cfg.compute_action_from_command_func
        is module.compute_microduck_velstand_action_from_command
    )
    assert module.MICRODUCK_VELSTAND_CMD_LIMITS == ((-0.4, 0.4), (-0.3, 0.3), (-1.0, 1.0))
    assert module.MICRODUCK_VELSTAND_USE_LINK_FRAME_GYRO is True


def test_policy_manifest_declares_the_velstand_contract():
    manifest = json.loads((CONTROLLER_PACKAGE / "model" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["obs_len"] == 61
    assert manifest["action_len"] == 14
    assert manifest["robot"]["control_hz"] == 50
    velstand = next(item for item in manifest["policies"] if item["file"] == "velstand.onnx")
    assert velstand["slot"] == "walk"
    assert velstand["kind"] == "perpetual"


def test_onnx_metadata_matches_controller_constants():
    pytest.importorskip("onnxruntime")
    import onnxruntime as ort

    module = _load_controller_module()
    session = ort.InferenceSession(str(CONTROLLER_PACKAGE / "model" / "velstand.onnx"))
    (obs_input,) = session.get_inputs()
    (action_output,) = session.get_outputs()
    assert obs_input.shape == [1, 61]
    assert action_output.shape == [1, 14]
    metadata = session.get_modelmeta().custom_metadata_map
    assert metadata["observation_names"] == ",".join(module.MICRODUCK_VELSTAND_OBS_SLICES)
    assert metadata["joint_names"] == ",".join(MICRODUCK_JOINT_NAMES)
    # The exporter writes default_joint_pos rounded to 3 decimals.
    assert [float(v) for v in metadata["default_joint_pos"].split(",")] == pytest.approx(
        [MICRODUCK_DEFAULT_JOINT_POS[name] for name in MICRODUCK_JOINT_NAMES], abs=2e-3
    )
    assert float(metadata["action_scale"]) == pytest.approx(module.MICRODUCK_VELSTAND_ACTION_SCALE)
    assert metadata["command_names"] == "twist,head_pose,body_pose"


@requires_torch
def test_observation_assembly_layout_and_command_clipping(real_torch):
    torch = real_torch
    module = _load_controller_module()

    num_envs = 2
    gyro = torch.tensor([[0.1, -0.2, 0.3], [0.0, 0.0, 0.0]])
    gravity = torch.tensor([[0.0, 0.0, -1.0], [0.0, 0.0, -1.0]])
    # Articulation order shuffled relative to policy order: neck_pitch first,
    # then the policy order without it — proves the name-based gather.
    articulation_names = ["neck_pitch", *[n for n in MICRODUCK_JOINT_NAMES if n != "neck_pitch"]]
    pos_row = [MICRODUCK_DEFAULT_JOINT_POS[name] for name in articulation_names]
    vel_row = [0.1] * 14
    joint_pos = torch.tensor([pos_row, pos_row])
    joint_vel = torch.tensor([vel_row, vel_row])
    default_joint_pos = torch.tensor([pos_row, pos_row])
    robot = SimpleNamespace(
        joint_names=articulation_names,
        num_joints=14,
        data=SimpleNamespace(
            root_link_ang_vel_b=gyro,
            root_ang_vel_b=torch.tensor([[9.0, 9.0, 9.0], [9.0, 9.0, 9.0]]),
            projected_gravity_b=gravity,
            root_ang_vel_w=SimpleNamespace(device=gyro.device),
            joint_pos=joint_pos,
            joint_vel=joint_vel,
            default_joint_pos=default_joint_pos,
        ),
    )
    last_action = torch.full((num_envs, 14), 0.25)
    env = SimpleNamespace(
        scene=SimpleNamespace(articulations={"microduck_1": robot}),
        actions_dict={"microduck_1": last_action},
        get_command=lambda robot_name, name: torch.tensor([[5.0, -5.0, 99.0], [0.1, 0.0, -0.5]]),
    )

    obs = module.compute_microduck_velstand_observations(env, "microduck_1")
    assert obs.shape == (num_envs, 61)
    assert torch.equal(obs[:, 0:3], gyro)  # link-frame gyro, never the COM-frame stand-in
    assert torch.equal(obs[:, 3:6], gravity)
    assert torch.allclose(obs[:, 6:20], torch.zeros(num_envs, 14))  # joints at default → zero rel
    assert torch.allclose(obs[:, 20:34], torch.full((num_envs, 14), 0.1))
    assert torch.equal(obs[:, 34:48], last_action)
    # Commands clipped into the trained envelope (vx ±0.4, vy ±0.3, yaw ±1.0).
    assert torch.equal(
        obs[:, 48:51], torch.tensor([[0.4, -0.3, 1.0], [0.1, 0.0, -0.5]])
    )
    assert torch.equal(obs[:, 51:55], torch.zeros(num_envs, 4))  # head command slot idle
    assert torch.equal(obs[:, 55:61], torch.zeros(num_envs, 6))  # body command slot idle


@requires_torch
def test_observation_handles_missing_command_and_two_dimensional_commands(real_torch):
    torch = real_torch
    module = _load_controller_module()

    gyro = torch.zeros(1, 3)
    gravity = torch.tensor([[0.0, 0.0, -1.0]])
    zeros14 = torch.zeros(1, 14)
    robot = SimpleNamespace(
        joint_names=list(MICRODUCK_JOINT_NAMES),
        num_joints=14,
        data=SimpleNamespace(
            root_link_ang_vel_b=gyro,
            projected_gravity_b=gravity,
            root_ang_vel_w=SimpleNamespace(device=gyro.device),
            joint_pos=zeros14,
            joint_vel=zeros14,
            default_joint_pos=zeros14,
        ),
    )
    env = SimpleNamespace(
        scene=SimpleNamespace(articulations={"microduck_1": robot}),
        actions_dict={},
        get_command=lambda robot_name, name: None,
        _base_velocity_microduck_1=torch.tensor([[0.2, 0.3]]),  # differential [vx, yaw]
    )
    obs = module.compute_microduck_velstand_observations(env, "microduck_1")
    assert torch.equal(obs[:, 48:51], torch.tensor([[0.2, 0.0, 0.3]]))
    assert torch.equal(obs[:, 34:48], zeros14)  # no previous action → zeros


@requires_torch
def test_action_application_maps_policy_order_targets_with_default_offset(real_torch):
    torch = real_torch
    module = _load_controller_module()
    articulation_names = list(reversed(MICRODUCK_JOINT_NAMES))
    default_row = [MICRODUCK_DEFAULT_JOINT_POS[name] for name in articulation_names]
    written = []

    robot = SimpleNamespace(
        joint_names=articulation_names,
        num_joints=14,
        data=SimpleNamespace(default_joint_pos=torch.tensor([default_row])),
        set_joint_position_target=lambda values, joint_ids=None: written.append(values.clone()),
    )
    env = SimpleNamespace(scene=SimpleNamespace(articulations={"microduck_1": robot}))
    action = torch.arange(-7, 7, dtype=torch.float32).unsqueeze(0)  # 14 offsets, scale 1.0

    module.apply_microduck_velstand_action(env, "microduck_1", action, {})
    (targets,) = written
    # Targets are scattered back into articulation order: column i carries the
    # default plus the policy action for articulation_names[i].
    offset_by_name = {
        name: float(offset) for name, offset in zip(MICRODUCK_JOINT_NAMES, action.squeeze(0).tolist())
    }
    expected = [
        MICRODUCK_DEFAULT_JOINT_POS[name] + offset_by_name[name] for name in articulation_names
    ]
    assert targets.shape == (1, 14)
    assert [round(value, 6) for value in targets.squeeze(0).tolist()] == [
        round(value, 6) for value in expected
    ]

    try:
        module.apply_microduck_velstand_action(env, "microduck_1", torch.zeros(1, 13), {})
    except ValueError:
        pass
    else:
        raise AssertionError("13-D action must be rejected")


# ─── Stage 4: wiring ────────────────────────────────────────────────────────


def _microduck_selection(**overrides):
    selection = {
        "scene_key": "plane",
        "robots": [
            {
                "type": "microduck",
                "controller": {"mode": "default", "cfg": "MICRODUCK_VELSTAND_CFG"},
                "attachments": [],
            }
        ],
    }
    selection["robots"][0].update(overrides)
    return selection


def test_catalog_registers_microduck_without_any_attachment_support():
    assert "microduck" in catalog.robot_keys()
    assert catalog.robot_label("microduck") == "MicroDuck biped (XL330)"
    assert catalog.default_controller_cfg("microduck") == "MICRODUCK_VELSTAND_CFG"
    assert catalog.controller_cfgs_for_robot("microduck") == ("MICRODUCK_VELSTAND_CFG",)
    assert "MICRODUCK_VELSTAND_CFG" in catalog.controller_cfg_names()
    # 裸足式机器人：不进入任何挂件宿主集合。
    for attachment_type in (
        "orsus",
        "realsense_d455",
        "lidar",
        "octagon_aio_c01",
        "ur5",
        "ur5_bare",
        "z1",
        "d1",
        "camera",
        "navigation_io",
        "keyboard",
    ):
        assert not catalog.attachment_supported("microduck", attachment_type), attachment_type
        with pytest.raises(ValueError):
            catalog.validate_attachment_types("microduck", (attachment_type,))


def test_asset_requirements_register_robot_and_controller_paths():
    robot_paths = (
        "robot/microduck/microduck_mjcf.usd",
        "robot/microduck/manifest.json",
        "robot/microduck/LICENSE",
        "robot/microduck/ATTRIBUTION.txt",
    )
    controller_paths = (
        "rl/microduck_velstand/__init__.py",
        "rl/microduck_velstand/microduck_velstand.py",
        "rl/microduck_velstand/model/velstand.onnx",
        "rl/microduck_velstand/model/manifest.json",
    )
    assert _ROBOT_PATHS["microduck"] == robot_paths
    assert _CONTROLLER_PATHS["MICRODUCK_VELSTAND_CFG"] == controller_paths
    for relative in robot_paths:
        assert Path(resolve_local_asset_path(relative)).is_file(), relative
    for relative in controller_paths:
        assert (controller_root() / relative).is_file(), relative

    graph = resolve_selection(_microduck_selection())
    requirements = {item.id: item for item in graph.requirements}
    assert set(requirements) == {"scene:plane", "robot:microduck", "controller:MICRODUCK_VELSTAND_CFG"}
    assert requirements["robot:microduck"].relative_paths == robot_paths
    assert requirements["controller:MICRODUCK_VELSTAND_CFG"].relative_paths == controller_paths


def test_env_builder_wires_the_microduck_robot_option_and_controller_import():
    builder = (REPO_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py").read_text(
        encoding="utf-8"
    )
    assert "from EAI_assets.robots.microduck import MICRODUCK_CFG, MICRODUCK_INIT_ROOT_Z" in builder
    assert (
        '"MICRODUCK_VELSTAND_CFG": ("rl/microduck_velstand/microduck_velstand.py", "MICRODUCK_VELSTAND_CFG"),'
        in builder
    )
    assert (
        '"microduck",\n'
        '        "MicroDuck biped",\n'
        "        MICRODUCK_CFG,\n"
        '        "MICRODUCK_VELSTAND_CFG",\n'
        "        MICRODUCK_INIT_ROOT_Z,\n"
        "    )," in builder
    )


def test_selection_example_json_resolves_and_matches_the_repo_contract():
    example_path = REPO_ROOT / "tmp" / "microduck_selection_example.json"
    if not example_path.is_file():
        pytest.skip("selection example not generated yet")
    selection = json.loads(example_path.read_text(encoding="utf-8"))
    graph = resolve_selection(selection)
    assert {item.id for item in graph.requirements} >= {
        "robot:microduck",
        "controller:MICRODUCK_VELSTAND_CFG",
    }
