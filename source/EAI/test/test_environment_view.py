import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import torch



def _load_view_modules():
    package_name = "_eai_environment_view_test"
    controllers_dir = Path(__file__).resolve().parents[1] / "EAI" / "controllers"
    package = ModuleType(package_name)
    package.__path__ = [str(controllers_dir)]
    sys.modules[package_name] = package
    loaded = []
    for module_name in ("environment_view", "scalar_manipulation_controller"):
        qualified = f"{package_name}.{module_name}"
        spec = importlib.util.spec_from_file_location(
            qualified, controllers_dir / f"{module_name}.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[qualified] = module
        spec.loader.exec_module(module)
        loaded.append(module)
    return loaded


environment_view, scalar_controller = _load_view_modules()
SingleEnvironmentView = environment_view.SingleEnvironmentView
apply_scalar_action = scalar_controller.apply_scalar_action
compute_scalar_action = scalar_controller.compute_scalar_action
load_scalar_controller = scalar_controller.load_scalar_controller
reset_scalar_controller = scalar_controller.reset_scalar_controller


class _Asset:
    def __init__(self):
        self.device = torch.device("cpu")
        self.cfg = SimpleNamespace(prim_path="/World/envs/env_.*/box")
        self.data = SimpleNamespace(
            root_pos_w=torch.tensor([[1.0, 2.0, 3.0], [11.0, 22.0, 33.0]]),
            root_pose_w=torch.tensor(
                [
                    [1.0, 2.0, 3.0, 1.0, 0.0, 0.0, 0.0],
                    [11.0, 22.0, 33.0, 1.0, 0.0, 0.0, 0.0],
                ]
            ),
        )
        self.writes = []

    def write_root_pose_to_sim(self, root_pose, env_ids=None):
        self.writes.append(("pose", root_pose.clone(), env_ids.clone()))

    def write_root_state_to_sim(self, root_state, env_ids=None):
        self.writes.append(("state", root_state.clone(), env_ids.clone()))


def _env(num_envs=2):
    origins = torch.tensor(
        [[0.0, 0.0, 0.0], [10.0, 20.0, 30.0]][:num_envs]
    )
    asset = _Asset()
    scene = SimpleNamespace(
        env_origins=origins,
        articulations={},
        rigid_objects={"box": asset},
        extras={},
    )
    env = SimpleNamespace(
        num_envs=num_envs,
        device=torch.device("cpu"),
        scene=scene,
        actions_dict={},
        _last_computed_actions={},
    )
    return env, asset


def test_local_coordinate_reads_and_root_writes_round_trip_selected_row():
    env, asset = _env()
    view = SingleEnvironmentView(env, 1, local_coordinates=True)
    box = view.scene.rigid_objects["box"]

    torch.testing.assert_close(box.data.root_pos_w, torch.tensor([[1.0, 2.0, 3.0]]))
    torch.testing.assert_close(
        box.data.root_pose_w,
        torch.tensor([[1.0, 2.0, 3.0, 1.0, 0.0, 0.0, 0.0]]),
    )
    assert box.cfg.prim_path == "/World/envs/env_1/box"

    local_pose = torch.tensor([[4.0, 5.0, 6.0, 1.0, 0.0, 0.0, 0.0]])
    box.write_root_pose_to_sim(local_pose, env_ids=torch.tensor([0]))
    local_state = torch.tensor(
        [[7.0, 8.0, 9.0, 1.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]]
    )
    box.write_root_state_to_sim(local_state)

    assert [item[0] for item in asset.writes] == ["pose", "state"]
    torch.testing.assert_close(
        asset.writes[0][1],
        torch.tensor([[14.0, 25.0, 36.0, 1.0, 0.0, 0.0, 0.0]]),
    )
    torch.testing.assert_close(
        asset.writes[1][1][..., :3], torch.tensor([[17.0, 28.0, 39.0]])
    )
    assert asset.writes[0][2].tolist() == [1]
    assert asset.writes[1][2].tolist() == [1]
    torch.testing.assert_close(local_pose[..., :3], torch.tensor([[4.0, 5.0, 6.0]]))
    torch.testing.assert_close(local_state[..., :3], torch.tensor([[7.0, 8.0, 9.0]]))


def test_view_stays_scoped_when_legacy_code_unwraps_and_blocks_lifecycle():
    env, _ = _env()
    view = SingleEnvironmentView(env, 1)

    assert view.base_env is view
    for name in ("step", "reset", "close"):
        with pytest.raises(RuntimeError, match="shared environment owner"):
            getattr(view, name)()


def test_clone_support_extra_is_rebound_to_concrete_row_path(monkeypatch):
    constructed = []

    class XformPrimView:
        num_instances = 1

        def __init__(self, path, *, device, validate_xform_ops, stage):
            constructed.append((path, device, validate_xform_ops, stage))
            self.device = device
            self.prim_paths = [path]

        def get_world_poses(self):
            return torch.tensor([[11.0, 22.0, 33.0]]), torch.tensor(
                [[1.0, 0.0, 0.0, 0.0]]
            )

    views_module = ModuleType("isaaclab.sim.views")
    views_module.XformPrimView = XformPrimView
    monkeypatch.setitem(sys.modules, "isaaclab.sim.views", views_module)
    stage = object()
    support = SimpleNamespace(
        prim_paths=["/World/envs/env_0/support"], device="cpu"
    )
    env, _ = _env()
    env.scene.stage = stage
    env.scene.extras = {"support": support}

    view = SingleEnvironmentView(env, 1, local_coordinates=True)
    row_support = view.scene.extras["support"]

    assert constructed == [
        ("/World/envs/env_1/support", "cpu", False, stage)
    ]
    assert row_support.num_instances == 1
    assert row_support._asset.prim_paths == ["/World/envs/env_1/support"]
    position, _ = row_support.get_world_poses()
    torch.testing.assert_close(position, torch.tensor([[1.0, 2.0, 3.0]]))


class PepperPickPlaceControllerCfg:
    def __init__(self):
        self.loaded = []

    def load(self, robot_name, task_name, device, env):
        state = {
            "load_env_id": env.env_id,
            "computed": [],
            "applied": [],
            "resets": [],
        }
        self.loaded.append(state)
        return state

    def compute_action_from_command(self, env, robot_name, command, state):
        state["computed"].append(command.clone())
        return command + 10 * env.env_id

    def apply_action(self, env, robot_name, action, state):
        state["applied"].append(action.clone())

    def reset(self, env, robot_name, state, env_ids):
        state["resets"].append((env.env_id, list(env_ids)))


def test_scalar_controller_owns_state_and_processes_each_row_independently():
    env, _ = _env()
    cfg = PepperPickPlaceControllerCfg()
    state = load_scalar_controller(cfg, "pepper", "task", "cpu", env)

    rows = state["environment_states"]
    views = state["environment_views"]
    assert len(rows) == len(views) == 2
    assert rows[0] is not rows[1]
    assert views[0] is not views[1]
    assert [view.env_id for view in views] == [0, 1]
    assert all(view.base_env is view for view in views)

    command = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    result = compute_scalar_action(cfg, env, "pepper", command, state)
    torch.testing.assert_close(result, torch.tensor([[1.0, 2.0], [13.0, 14.0]]))
    torch.testing.assert_close(rows[0]["computed"][0], command[0:1])
    torch.testing.assert_close(rows[1]["computed"][0], command[1:2])

    action = torch.tensor([[5.0, 6.0], [7.0, 8.0]])
    apply_scalar_action(cfg, env, "pepper", action, state)
    torch.testing.assert_close(rows[0]["applied"][0], action[0:1])
    torch.testing.assert_close(rows[1]["applied"][0], action[1:2])

    reset_scalar_controller(cfg, env, "pepper", state, torch.tensor([1]))
    assert rows[0]["resets"] == []
    assert rows[1]["resets"] == [(1, [0])]


def test_single_row_scalar_result_preserves_original_state_alias():
    env, _ = _env(num_envs=1)
    cfg = PepperPickPlaceControllerCfg()

    state = load_scalar_controller(cfg, "pepper", "task", "cpu", env)

    assert state is cfg.loaded[0]
    assert state["environment_states"] == [state]
    state["execution_enabled"] = True
    assert cfg.loaded[0]["execution_enabled"] is True
