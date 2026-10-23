"""Pure-Python configuration checks for the Unitree D1 arm payload.

Nothing here launches Isaac Sim.  The ``isaaclab`` configuration classes used
at module scope by ``EAI_assets.robots.d1``/``d1_mount`` are replaced with
plain attribute holders, ``torch`` is stubbed for the gripper-hold path, and
the ``hmrs_ros``/``controllers`` modules are loaded through synthetic
packages (the pattern of ``source/EAI/test``) because the real
``EAI.hmrs_ros`` package imports torch and omni at import time.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
for _source_root in (REPO_ROOT / "source" / "EAI_assets", REPO_ROOT / "source" / "EAI"):
    if str(_source_root) not in sys.path:
        sys.path.insert(0, str(_source_root))


class _StubConfig:
    def __init__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)


class _StubArticulationCfg:
    InitialStateCfg = _StubConfig

    def __init__(self, **kwargs):
        for name, value in kwargs.items():
            setattr(self, name, value)


def _unavailable(name):
    def _raise(*args, **kwargs):
        raise RuntimeError(f"isaaclab.{name} is unavailable in the stub environment")

    return _raise


def _stub_configclass(cls=None, **_kwargs):
    if cls is not None:
        return cls
    return lambda inner: inner


def _define_module(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        parent = sys.modules.get(parent_name) or _define_module(parent_name)
        setattr(parent, child, module)
    return module


def _install_isaaclab_stubs():
    if getattr(sys.modules.get("isaaclab"), "__eai_test_stub__", False):
        return
    _define_module("isaaclab", __eai_test_stub__=True)
    _define_module(
        "isaaclab.sim",
        UsdFileCfg=_StubConfig,
        RigidBodyPropertiesCfg=_StubConfig,
        ArticulationRootPropertiesCfg=_StubConfig,
        spawn_from_usd=_unavailable("spawn_from_usd"),
        get_current_stage=_unavailable("get_current_stage"),
        modify_articulation_root_properties=_unavailable("modify_articulation_root_properties"),
    )
    _define_module("isaaclab.actuators", ImplicitActuatorCfg=_StubConfig)
    _define_module("isaaclab.assets", ArticulationCfg=_StubArticulationCfg)
    _define_module("isaaclab.assets.articulation", ArticulationCfg=_StubArticulationCfg)
    _define_module("isaaclab.utils", configclass=_stub_configclass)


def _elementwise(operation, a, b):
    a = a.rows if isinstance(a, _Matrix) else a
    b = b.rows if isinstance(b, _Matrix) else b
    if isinstance(a, list) and a and isinstance(a[0], (list, tuple)):
        if isinstance(b, list) and b and isinstance(b[0], (list, tuple)):
            return [_elementwise(operation, row_a, row_b) for row_a, row_b in zip(a, b)]
        return [_elementwise(operation, row_a, b) for row_a in a]
    if isinstance(b, list):
        return [operation(x, y) for x, y in zip(a, b)]
    return [operation(x, b) for x in a]


def _full_like(structure, value):
    rows = structure.rows if isinstance(structure, _Matrix) else structure
    return _Matrix([[value] * len(row) for row in rows])


def _install_torch_stub():
    if getattr(sys.modules.get("torch"), "__eai_test_stub__", False):
        return
    torch = types.ModuleType("torch")
    torch.__eai_test_stub__ = True
    torch.full_like = _full_like
    torch.minimum = lambda a, b: _elementwise(lambda x, y: x if x < y else y, a, b)
    torch.maximum = lambda a, b: _elementwise(lambda x, y: x if x >= y else y, a, b)
    sys.modules["torch"] = torch


class _Matrix:
    """Minimal stand-in for the tensor indexing used by hold_gripper_position."""

    def __init__(self, rows):
        self.rows = [list(row) for row in rows]

    def __getitem__(self, key):
        if isinstance(key, tuple) and key and key[0] is Ellipsis:
            index = key[1]
            return self._map_last_axis(self.rows, index)
        if isinstance(key, tuple) and len(key) == 2:
            rows = self.rows if isinstance(key[0], slice) else [self.rows[key[0]]]
            columns = key[1]
            if isinstance(columns, (list, tuple)):
                return _Matrix([[row[column] for column in columns] for row in rows])
            return _Matrix([[row[columns]] for row in rows])
        raise KeyError(key)

    @staticmethod
    def _map_last_axis(rows, index):
        if rows and isinstance(rows[0], (list, tuple)) and isinstance(rows[0][0], (list, tuple)):
            return [_Matrix._map_last_axis(row, index) for row in rows]
        return [cell[index] for cell in rows]


class _FakeArm:
    def __init__(self, joint_names, joint_pos, joint_limits):
        self.joint_names = list(joint_names)
        self.data = SimpleNamespace(
            joint_pos=_Matrix(joint_pos),
            soft_joint_pos_limits=_Matrix(joint_limits),
        )
        self.targets = []

    def find_joints(self, names, preserve_order=False):
        return [self.joint_names.index(str(name)) for name in names], list(names)

    def set_joint_position_target(self, values, joint_ids=None):
        values = values.rows if isinstance(values, _Matrix) else values
        self.targets.append((values, list(joint_ids or [])))


def _load_package_modules(directory, module_names, package_suffix):
    package_name = f"_eai_d1_config_test_{package_suffix}"
    package = types.ModuleType(package_name)
    package.__path__ = [str(directory)]
    sys.modules[package_name] = package
    modules = {}
    for module_name in module_names:
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(qualified, directory / f"{module_name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        modules[module_name] = module
    return modules


_install_isaaclab_stubs()
_install_torch_stub()

_hmrs = _load_package_modules(
    REPO_ROOT / "source" / "EAI" / "EAI" / "hmrs_ros",
    ("manipulator_omnigraph", "d1_omnigraph"),
    "hmrs",
)
_controllers = _load_package_modules(
    REPO_ROOT / "source" / "EAI" / "EAI" / "controllers",
    ("ik_controller", "idealized_manipulation"),
    "controllers",
)

import EAI_assets.robots.d1 as d1_module
import EAI_assets.robots.d1_mount as d1_mount_module
import EAI_assets.robots.z1_mount as z1_mount_module

manipulator_omnigraph = _hmrs["manipulator_omnigraph"]
d1_omnigraph = _hmrs["d1_omnigraph"]
ik_controller = _controllers["ik_controller"]
idealized_manipulation = _controllers["idealized_manipulation"]

D1_FINGER_LIMITS = {
    "Joint7_1": (0.0, 0.03),
    "Joint7_2": (-0.03, 0.0),
}


def test_manipulator_model_spec_gripper_field_is_backward_compatible():
    model_spec = manipulator_omnigraph.ManipulatorModelSpec
    single = model_spec(model="z1", joint_names=("joint1",), ee_body_names=("link06",), gripper_joint_name="jointGripper")
    assert single.gripper_joint_names == ()
    assert single.all_gripper_joints == ("jointGripper",)
    none_spec = model_spec(model="bare", joint_names=("joint1",), ee_body_names=("tool",))
    assert none_spec.gripper_joint_names == ()
    assert none_spec.all_gripper_joints == ()
    multi = model_spec(
        model="d1",
        joint_names=("Joint1", "Joint2"),
        ee_body_names=("Link6",),
        gripper_joint_names=("Joint7_1", " Joint7_2 "),
    )
    assert multi.gripper_joint_names == ("Joint7_1", "Joint7_2")
    assert multi.all_gripper_joints == ("Joint7_1", "Joint7_2")


def test_manipulator_model_spec_rejects_invalid_gripper_joint_names():
    model_spec = manipulator_omnigraph.ManipulatorModelSpec
    for gripper_joint_names in (("Joint7_1", ""), ("Joint7_1", "Joint7_1")):
        try:
            model_spec(
                model="d1",
                joint_names=("Joint1",),
                ee_body_names=("Link6",),
                gripper_joint_names=gripper_joint_names,
            )
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {gripper_joint_names}")


def test_d1_model_spec_uses_wrist_link_and_two_finger_gripper():
    spec = d1_omnigraph.D1_MODEL_SPEC
    assert spec.model == "d1"
    assert spec.joint_names == ("Joint1", "Joint2", "Joint3", "Joint4", "Joint5", "Joint6")
    assert spec.ee_body_names == ("Link6",)
    assert spec.gripper_joint_name is None
    assert spec.gripper_joint_names == ("Joint7_1", "Joint7_2")
    assert spec.all_gripper_joints == ("Joint7_1", "Joint7_2")
    assert d1_omnigraph.d1_topic_names("go2_1") == {
        "target_pose": "/go2_1/d1/target_pose",
        "joint_command": "/go2_1/d1/joint_command",
        "joint_states": "/go2_1/d1/joint_states",
        "ee_pose": "/go2_1/d1/ee_pose",
    }


def test_d1_gripper_targets_maps_closure_to_mirrored_finger_strokes():
    d1_gripper_targets = d1_omnigraph.d1_gripper_targets
    assert d1_gripper_targets(0.0) == (0.03, -0.03)
    assert d1_gripper_targets(1.0) == (0.0, 0.0)
    assert d1_gripper_targets(0.5) == (0.015, -0.015)
    for invalid in (-0.1, 1.1, float("nan"), float("inf")):
        try:
            d1_gripper_targets(invalid)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for closure {invalid}")


def test_d1_default_joint_positions_stay_inside_limits():
    defaults = d1_module.D1_DEFAULT_JOINT_POS
    assert set(defaults) == set(d1_module.D1_JOINT_NAMES) | set(d1_module.D1_GRIPPER_JOINT_NAMES)
    for joint_name in d1_module.D1_JOINT_NAMES:
        lower, upper = d1_module.D1_JOINT_LIMITS[joint_name]
        assert lower <= defaults[joint_name] <= upper, joint_name
    for joint_name, (lower, upper) in D1_FINGER_LIMITS.items():
        assert lower <= defaults[joint_name] <= upper, joint_name
    assert d1_module.D1_CFG.init_state.joint_pos == dict(defaults)
    assert d1_module.D1_CFG.actuators["arm"].joint_names_expr == list(d1_module.D1_JOINT_NAMES)
    assert d1_module.D1_CFG.actuators["gripper"].joint_names_expr == list(d1_module.D1_GRIPPER_JOINT_NAMES)
    for attribute, value in d1_module.D1_ARM_ACTUATOR.items():
        assert getattr(d1_module.D1_CFG.actuators["arm"], attribute) == value
    for attribute, value in d1_module.D1_GRIPPER_ACTUATOR.items():
        assert getattr(d1_module.D1_CFG.actuators["gripper"], attribute) == value


def test_d1_go2_mount_profile_matches_reference_install_pose():
    profile = d1_mount_module.D1_MOUNT_PROFILES["go2"]
    assert profile.robot_type == "go2"
    assert profile.mount_body_path == "base"
    assert profile.mount_position == (0.0, 0.0, 0.15)
    assert profile.mount_rotation == (1.0, 0.0, 0.0, 0.0)
    assert profile.mass_scale == 1.0
    assert profile.enable_self_collisions is False

    cfg = d1_mount_module.build_mounted_d1_asset_cfg("/World/envs/.*/go2_1_arm", profile)
    assert cfg.prim_path == "/World/envs/.*/go2_1_arm"
    assert cfg.articulation_root_prim_path == "/base_link"
    assert cfg.init_state.joint_pos == dict(d1_module.D1_DEFAULT_JOINT_POS)
    assert cfg.spawn.func.func is d1_mount_module.spawn_mounted_d1
    assert cfg.spawn.func.keywords["profile"] is profile
    assert d1_mount_module.MountedD1ArmCfg.asset_dependencies == (d1_mount_module.D1_STANDALONE_USD_PATH,)


def test_z1_mount_profiles_and_asset_cfg_keep_their_contract():
    go2 = z1_mount_module.Z1_MOUNT_PROFILES["go2"]
    assert go2.mount_position == (0.0, 0.0, 0.056)
    assert go2.mount_rotation == (0.0, 0.0, 0.0, 1.0)
    assert go2.mass_scale == 1.0
    assert set(z1_mount_module.Z1_MOUNT_PROFILES) == {"carter", "go2", "b2", "m20", "scout", "lite3"}
    cfg = z1_mount_module.build_mounted_z1_asset_cfg("/World/envs/.*/robot_arm", go2)
    assert cfg.articulation_root_prim_path == "/link00"
    assert cfg.spawn.func.func is z1_mount_module.spawn_mounted_z1
    assert cfg.spawn.func.keywords["profile"] is go2


def test_planar_ik_spec_d1_branch_mirrors_z1_joint_roles():
    spec = idealized_manipulation.planar_ik_spec_for_model(d1_omnigraph.D1_MODEL_SPEC)
    assert spec.model == "d1"
    assert spec.yaw_joint_name == "Joint1"
    assert spec.ik_joint_names == ("Joint2", "Joint3", "Joint4", "Joint5")
    assert spec.fixed_joint_names == ("Joint6",)
    assert spec.plane_offset_m == 0.0
    assert spec.gripper_open_position_rad is None
    assert spec.gripper_closed_position_rad is None
    z1_spec = idealized_manipulation.planar_ik_spec_for_model(
        manipulator_omnigraph.ManipulatorModelSpec(
            model="z1",
            joint_names=("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"),
            ee_body_names=("link06",),
        )
    )
    assert z1_spec.gripper_open_position_rad == idealized_manipulation.DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD
    assert idealized_manipulation.DEFAULT_D1_GRIPPER_OPEN_CLOSURE == 0.0
    assert idealized_manipulation.DEFAULT_D1_GRIPPER_CLOSED_CLOSURE == 1.0


def _d1_arm_limits_rows():
    return [[
        *[list(d1_module.D1_JOINT_LIMITS[name]) for name in d1_module.D1_JOINT_NAMES],
        *[list(D1_FINGER_LIMITS[name]) for name in d1_module.D1_GRIPPER_JOINT_NAMES],
    ]]


def _d1_arm(joint_pos):
    return _FakeArm(
        (*d1_module.D1_JOINT_NAMES, *d1_module.D1_GRIPPER_JOINT_NAMES),
        joint_pos=joint_pos,
        joint_limits=_d1_arm_limits_rows(),
    )


def test_hold_gripper_position_holds_each_finger_at_its_measured_value():
    arm = _d1_arm([[0.0, -0.4, 0.0, 0.6, 0.0, 0.0, 0.05, -0.05]])
    assert ik_controller.hold_gripper_position(arm, d1_omnigraph.D1_MODEL_SPEC)
    values, joint_ids = arm.targets[-1]
    assert joint_ids == [6, 7]
    assert values == [[0.03, -0.03]]  # measured 0.05/-0.05 clamp per-finger

    arm.data.joint_pos = _Matrix([[0.0, -0.4, 0.0, 0.6, 0.0, 0.0, 0.01, -0.02]])
    assert ik_controller.hold_gripper_position(arm, d1_omnigraph.D1_MODEL_SPEC)
    values, joint_ids = arm.targets[-1]
    assert joint_ids == [6, 7]
    assert values == [[0.01, -0.02]]  # opposite signs preserved per finger


def test_hold_gripper_position_multi_finger_explicit_target_clamps_per_finger():
    arm = _d1_arm([[0.0, -0.4, 0.0, 0.6, 0.0, 0.0, 0.03, -0.03]])
    assert ik_controller.hold_gripper_position(arm, d1_omnigraph.D1_MODEL_SPEC, position_rad=0.0)
    values, joint_ids = arm.targets[-1]
    assert joint_ids == [6, 7]
    assert values == [[0.0, 0.0]]


def test_hold_gripper_position_single_joint_path_is_unchanged():
    arm = _FakeArm(
        ("joint1", "jointGripper"),
        joint_pos=[[0.1, -0.8]],
        joint_limits=[[[-2.35, 2.35], [-1.0, 0.0]]],
    )
    spec = SimpleNamespace(gripper_joint_name="jointGripper", gripper_joint_names=())
    assert ik_controller.hold_gripper_position(arm, spec)
    values, joint_ids = arm.targets[-1]
    assert joint_ids == [1]
    assert values == [[-0.8]]
    assert ik_controller.hold_gripper_position(arm, spec, position_rad=-0.2)
    values, joint_ids = arm.targets[-1]
    assert joint_ids == [1]
    assert values == [[-0.2]]
    assert ik_controller.hold_gripper_position(arm, SimpleNamespace()) is True
    assert len(arm.targets) == 2
    assert not math.isnan(0.0)


# ---------------------------------------------------------------------------
# Stage-3 wiring: catalog / env_builder / simulator registration for the
# mounted D1 payload.  Source-text assertions keep these checks free of the
# isaaclab/torch imports that ``env_builder`` and ``simulator`` pull in.
# ---------------------------------------------------------------------------

from EAI.hmrs_env.env_diy import catalog as env_diy_catalog  # noqa: E402


def _source_segment(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_d1_is_a_registered_manipulator_type_for_policy_hosts():
    assert "d1" in env_diy_catalog.MANIPULATOR_TYPES
    entry = env_diy_catalog.attachment_entry("d1")
    assert entry.asset_cfg == "MountedD1ArmCfg"
    assert entry.controller_cfg == "D1_IK_CFG"
    # 2026-10-22 装配政策：go2 与 lite3 仅可装 D1；原 Z1 宿主 carter/b2/m20/
    # scout 保留 Z1 资格并并列放开 D1。
    assert entry.supported_robots == ("carter", "go2", "b2", "m20", "scout", "lite3")
    assert entry.category == "manipulator"
    assert not entry.visual_only
    assert "D1_IK_CFG" in env_diy_catalog.controller_cfg_names()
    for host in entry.supported_robots:
        assert env_diy_catalog.validate_attachment_types(host, ["d1"]) == ("d1",)
    # Z1 资格保留在其余原宿主上，go2/lite3 不再放行。
    assert env_diy_catalog.attachment_entry("z1").supported_robots == ("carter", "b2", "m20", "scout")
    assert env_diy_catalog.validate_attachment_types("carter", ["z1"]) == ("z1",)
    for host in ("go2", "lite3"):
        try:
            env_diy_catalog.validate_attachment_types(host, ["z1"])
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {host} carrying z1")
    assert (REPO_ROOT / "usd" / "payloads" / "manipulators" / "d1" / "d1.usd").is_file()


def test_d1_mount_conflict_rules_keep_the_single_manipulator_slot():
    assert env_diy_catalog.payload_mount_conflict("go2", {"d1", "z1"}) == ("d1", "z1")
    assert env_diy_catalog.payload_mount_conflict("go2", {"d1", "ur5"}) == ("d1", "ur5")
    assert env_diy_catalog.payload_mount_conflict("go2", {"d1"}) is None
    assert env_diy_catalog.payload_mount_conflict("go2", {"d1", "realsense_d455"}) is None
    # 2026-10-22 政策把 D1 放开到全部原 Z1 宿主，同槽互斥随之扩展宿主覆盖。
    for host in ("carter", "b2", "m20", "scout", "lite3"):
        assert env_diy_catalog.payload_mount_conflict(host, {"d1", "z1"}) == ("d1", "z1")
        assert env_diy_catalog.payload_mount_conflict(host, {"d1", "ur5"}) == ("d1", "ur5")
    try:
        env_diy_catalog.validate_attachment_types("go2", ["d1", "z1"])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for go2 carrying d1 and z1")


def test_d1_mount_profiles_cover_policy_hosts_reusing_z1_mount_points():
    # go2 保留其既有参考安装位；carter/b2/m20/scout 复用各自 Z1 挂点（同一
    # 背板位置装 D1）；lite3 按 TORSO 背部高度独立取值。
    go2 = d1_mount_module.D1_MOUNT_PROFILES["go2"]
    assert (go2.mount_body_path, go2.mount_position, go2.mount_rotation) == (
        "base", (0.0, 0.0, 0.15), (1.0, 0.0, 0.0, 0.0),
    )
    assert set(d1_mount_module.D1_MOUNT_PROFILES) == {
        "carter", "go2", "b2", "m20", "scout", "lite3",
    }
    for host in ("carter", "b2", "m20", "scout"):
        d1_profile = d1_mount_module.D1_MOUNT_PROFILES[host]
        z1_profile = z1_mount_module.Z1_MOUNT_PROFILES[host]
        assert d1_profile.robot_type == host
        assert d1_profile.mount_body_path == z1_profile.mount_body_path
        assert d1_profile.mount_position == z1_profile.mount_position
        assert d1_profile.mount_rotation == z1_profile.mount_rotation
        assert d1_profile.mass_scale == 1.0
        assert d1_profile.enable_self_collisions is False
    lite3 = d1_mount_module.D1_MOUNT_PROFILES["lite3"]
    assert lite3.mount_body_path == "TORSO"
    assert lite3.mount_position == (0.0, 0.0, 0.12)
    assert 0.10 <= lite3.mount_position[2] <= 0.15
    # Z1 挂点数据（含 go2/lite3）保留完整；政策性放行由 catalog hosts 控制。
    assert set(z1_mount_module.Z1_MOUNT_PROFILES) == {"carter", "go2", "b2", "m20", "scout", "lite3"}
    cfg = d1_mount_module.build_mounted_d1_asset_cfg("/World/envs/.*/lite3_1_arm", lite3)
    assert cfg.spawn.func.func is d1_mount_module.spawn_mounted_d1
    assert cfg.spawn.func.keywords["profile"] is lite3


def test_env_builder_registers_d1_loader_and_go2_mount():
    source = (REPO_ROOT / "source" / "EAI_hmrs" / "EAI_hmrs" / "env_builder.py").read_text(encoding="utf-8")
    assert "from EAI_assets.robots.d1_mount import (" in source
    assert '"D1_IK_CFG": ("traditional/d1_ik/d1_ik.py", "D1_IK_CFG")' in source
    assert "d1_mount_profile: D1MountProfile | None = None" in source
    assert 'd1_mount_profile=D1_MOUNT_PROFILES["go2"],' in source
    # 2026-10-22 政策：全部 D1 宿主都有构建器挂载接线；go2 的 Z1 接线保留
    # （放行由 catalog z1_hosts 控制）。
    for host in ("carter", "b2", "m20", "scout", "lite3"):
        assert f'd1_mount_profile=D1_MOUNT_PROFILES["{host}"],' in source
    assert 'z1_mount_profile=Z1_MOUNT_PROFILES["go2"],' in source
    assert "build_mounted_d1_asset_cfg(" in source
    assert 'fallback="D1_IK_CFG"' in source
    assert "cannot attach both Z1 and D1" in source


def test_simulator_source_enumerates_d1_everywhere_z1_is_listed():
    source = (REPO_ROOT / "simulator.py").read_text(encoding="utf-8")
    omnigraph_gate = _source_segment(
        source, "def _selection_requires_omnigraph", "def _selection_has_attachment"
    )
    assert '"d1"' in omnigraph_gate
    models = _source_segment(
        source, "def manipulator_attachment_robot_models", "def _setup_manipulator_graph_manager"
    )
    assert models.count('("ur5", "ur5_bare", "z1", "d1")') == 2
    assert '(agent_name, "d1") for model in ("d1",)' in models
    graph_manager = _source_segment(
        source, "def _setup_manipulator_graph_manager", "def _is_manipulator_ros2_auxiliary"
    )
    assert "from EAI.hmrs_ros.d1_omnigraph import D1_MODEL_SPEC" in graph_manager
    assert '"d1": D1_MODEL_SPEC' in graph_manager


def test_selection_flow_defaults_d1_attachment_to_d1_ik_cfg():
    flow_source = (
        REPO_ROOT / "source" / "EAI" / "EAI" / "hmrs_env" / "env_diy" / "flow.py"
    ).read_text(encoding="utf-8")
    assert '"Z1_IK_CFG" if attachment_type == "z1" else "D1_IK_CFG"' in flow_source
