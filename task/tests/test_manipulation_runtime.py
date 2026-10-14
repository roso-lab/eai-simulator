import sys
from types import MethodType, ModuleType, SimpleNamespace

import pytest
import torch

from algorithm.skills.contracts import SkillRequest
from algorithm.skills.pick_place import PickPlaceProvider, SKILL_ID
from task.executors import manipulation as module
from task.resources import InvalidResourceLeaseError, ResourceManager


def _pick_place_request(request_id="pick-1", **overrides):
    parameters = {
        "object_ref": "box",
        "pick_position": (0.1, 0.2, 0.3),
        "place_position": (0.4, 0.5, 0.6),
        "frame": "world",
    }
    parameters.update(overrides)
    return SkillRequest(request_id, SKILL_ID, "env_1/arm", parameters=parameters)


def test_pick_place_provider_accepts_strict_world_schema():
    plan = PickPlaceProvider().plan(_pick_place_request())

    assert plan.status == "planned"
    assert len(plan.commands) == 1
    assert plan.commands[0].payload["frame"] == "world"


@pytest.mark.parametrize(
    "parameters, reason",
    [
        (
            {
                "pick_position": (0.1, 0.2, 0.3),
                "place_position": (0.4, 0.5, 0.6),
                "frame": "world",
            },
            "missing",
        ),
        (
            {
                "object_ref": None,
                "pick_position": (0.1, 0.2, 0.3),
                "place_position": (0.4, 0.5, 0.6),
                "frame": "world",
            },
            "null",
        ),
        (
            {
                "object_ref": "box",
                "pick_position": (float("inf"), 0.2, 0.3),
                "place_position": (0.4, 0.5, 0.6),
                "frame": "world",
            },
            "finite",
        ),
        (
            {
                "object_ref": "box",
                "pick_position": (0.1, 0.2, 0.3),
                "place_position": (0.4, 0.5, 0.6),
                "frame": "local",
            },
            "world",
        ),
    ],
    ids=("missing", "null", "nonfinite", "nonworld"),
)
def test_pick_place_provider_rejects_invalid_schema(parameters, reason):
    request = SkillRequest("pick-1", SKILL_ID, "env_1/arm", parameters=parameters)

    plan = PickPlaceProvider().plan(request)

    assert plan.status == "invalid_request"
    assert reason in plan.metadata["reason"]


class _SteppingEnv:
    def __init__(self):
        self.num_envs = 2
        self.device = torch.device("cpu")
        self.cfg = SimpleNamespace(
            possible_agents=("arm", "mobile"),
            action_spaces={"arm": 2, "mobile": 3},
        )
        self.calls = []

    def step(self, actions):
        self.calls.append(actions)
        return "shared-step-result"


def _step_runtime():
    env = _SteppingEnv()
    runtime = object.__new__(module.ManipulationSkillRuntime)
    runtime.env = env
    runtime.bindings = {
        "env_0/arm": SimpleNamespace(robot_name="arm", env_id=0),
        "env_1/arm": SimpleNamespace(robot_name="arm", env_id=1),
    }
    return runtime, env


def test_scoped_commands_merge_rows_into_one_shared_step():
    runtime, env = _step_runtime()

    result = runtime.step(
        env,
        {
            "env_0/arm": torch.tensor([[1.0, 2.0]]),
            "env_1/arm": torch.tensor([[3.0, 4.0]]),
        },
    )

    assert result == "shared-step-result"
    assert len(env.calls) == 1
    torch.testing.assert_close(
        env.calls[0]["arm"], torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    )
    torch.testing.assert_close(env.calls[0]["mobile"], torch.zeros((2, 3)))


def test_scoped_manipulation_and_full_batch_teammate_share_one_step():
    runtime, env = _step_runtime()
    teammate = torch.tensor([[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])

    runtime.step(
        env,
        {
            "env_1/arm": torch.tensor([[7.0, 8.0]]),
            "mobile": teammate,
        },
    )

    assert len(env.calls) == 1
    torch.testing.assert_close(
        env.calls[0]["arm"], torch.tensor([[0.0, 0.0], [7.0, 8.0]])
    )
    torch.testing.assert_close(env.calls[0]["mobile"], teammate)


@pytest.mark.parametrize(
    "actions",
    [
        {
            "env_0/arm": torch.tensor([[1.0, 2.0]]),
            "arm": torch.tensor([[3.0, 4.0], [5.0, 6.0]]),
        },
        {
            "arm": torch.tensor([[3.0, 4.0], [5.0, 6.0]]),
            "env_0/arm": torch.tensor([[1.0, 2.0]]),
        },
    ],
    ids=("scoped-then-full", "full-then-scoped"),
)
def test_scoped_and_full_batch_commands_conflict_in_either_order(actions):
    runtime, env = _step_runtime()

    with pytest.raises(ValueError, match="conflict|duplicate"):
        runtime.step(env, actions)

    assert env.calls == []


class _EffectHandle:
    def __init__(self, effects):
        self.effects = effects

    def command(self):
        self.effects.append("command")
        return "command"

    def after_step(self):
        self.effects.append("after_step")
        return None

    def cancel(self, reason):
        self.effects.append(("cancel", reason))


def _binding_runtime(monkeypatch, resources=None, request_id="pick-1"):
    env = SimpleNamespace(
        num_envs=2,
        cfg=SimpleNamespace(action_spaces={"arm": 2}),
        _manipulation_reset_generation=[0, 0],
        _idealized_manipulation_runtime=object(),
    )
    robot_id = "env_1/arm"
    object_path = "/World/envs/env_1/box"
    robot_binding = SimpleNamespace(backend="arm", robot_name="arm", env_id=1)
    view = object()
    runtime = object.__new__(module.ManipulationSkillRuntime)
    runtime.env = env
    runtime.stage = None
    runtime.resources = resources or ResourceManager()
    runtime.bindings = {robot_id: robot_binding}
    runtime.views = {robot_id: view}
    runtime.handles = {}
    runtime.g1_runtime = None
    runtime.validate = MethodType(
        lambda self, request: (robot_binding, view, object_path), runtime
    )
    effects = []

    def make_handle(*args, **kwargs):
        effects.append("constructed")
        return _EffectHandle(effects)

    monkeypatch.setattr(module, "_ArmHandle", make_handle)
    request = _pick_place_request(request_id=request_id)
    binding = runtime.binding(request)
    plan = PickPlaceProvider().plan(request)
    keys = ("robot:" + robot_id, "object:" + object_path)
    return runtime, env, binding, plan, keys, effects


def test_binding_start_accepts_authentic_active_scoped_lease(monkeypatch):
    runtime, env, binding, plan, keys, effects = _binding_runtime(monkeypatch)
    lease = runtime.resources.acquire("pick-1", keys)

    handle = binding.start(plan, scene=env, lease=lease)

    assert effects == ["constructed"]
    assert handle.command() == "command"
    assert effects == ["constructed", "command"]
    assert env._active_manipulation_handles[(1, "arm")] is handle

    runtime.resources.release(lease)
    with pytest.raises(InvalidResourceLeaseError, match="not active"):
        handle.command()
    with pytest.raises(InvalidResourceLeaseError, match="not active"):
        handle.after_step()
    assert effects == ["constructed", "command"]


def test_binding_start_requires_lease_owner_to_match_request(monkeypatch):
    runtime, env, binding, plan, keys, effects = _binding_runtime(monkeypatch)
    lease = runtime.resources.acquire("another-request", keys)

    with pytest.raises(ValueError, match="lease owner"):
        binding.start(plan, scene=env, lease=lease)

    assert effects == []
    assert runtime.handles == {}


def test_runtimes_on_one_environment_reuse_one_resource_manager(monkeypatch):
    environment_view = ModuleType("EAI.controllers.environment_view")
    environment_view.validate_env_id = lambda env, env_id: env_id
    environment_view.SingleEnvironmentView = lambda env, env_id: SimpleNamespace(
        env=env, env_id=env_id
    )
    controllers = ModuleType("EAI.controllers")
    controllers.__path__ = []
    eai = ModuleType("EAI")
    eai.__path__ = []
    monkeypatch.setitem(sys.modules, "EAI", eai)
    monkeypatch.setitem(sys.modules, "EAI.controllers", controllers)
    monkeypatch.setitem(
        sys.modules, "EAI.controllers.environment_view", environment_view
    )
    env = SimpleNamespace(num_envs=2)
    first_binding = module.ManipulationRobotBinding(
        "arm", "arm", env_id=0, objects={"box": "/World/envs/env_0/box"}
    )
    second_binding = module.ManipulationRobotBinding(
        "arm", "arm", env_id=1, objects={"box": "/World/envs/env_1/box"}
    )

    first = module.ManipulationSkillRuntime(env, [first_binding])
    second = module.ManipulationSkillRuntime(env, [second_binding])

    assert first.resources is second.resources
    assert env._manipulation_resource_manager is first.resources
    with pytest.raises(ValueError, match="same resource manager"):
        module.ManipulationSkillRuntime(
            env, [second_binding], resources=ResourceManager()
        )


def test_second_runtime_cannot_take_unresolved_robot_row(monkeypatch):
    first_resources = ResourceManager()
    first, env, first_binding, first_plan, first_keys, first_effects = _binding_runtime(
        monkeypatch, resources=first_resources, request_id="pick-1"
    )
    first_lease = first_resources.acquire("pick-1", first_keys)
    first_handle = first_binding.start(first_plan, scene=env, lease=first_lease)
    assert first_handle.finished is False

    second, _, second_binding, second_plan, second_keys, second_effects = _binding_runtime(
        monkeypatch, resources=ResourceManager(), request_id="pick-2"
    )
    second.env = env
    second_lease = second.resources.acquire("pick-2", second_keys)

    with pytest.raises(ValueError, match="unresolved manipulation owner"):
        second_binding.start(second_plan, scene=env, lease=second_lease)

    assert second_effects == []
    assert env._active_manipulation_handles[(1, "arm")] is first_handle
    assert first_effects == ["constructed"]


@pytest.mark.parametrize("lease_kind", ("foreign", "incomplete", "released"))
def test_binding_start_rejects_invalid_lease_before_runtime_effects(
    monkeypatch, lease_kind
):
    runtime, env, binding, plan, keys, effects = _binding_runtime(monkeypatch)
    if lease_kind == "foreign":
        lease = ResourceManager().acquire("pick-1", keys)
    elif lease_kind == "incomplete":
        lease = runtime.resources.acquire("pick-1", keys[:1])
    else:
        lease = runtime.resources.acquire("pick-1", keys)
        runtime.resources.release(lease)

    with pytest.raises(InvalidResourceLeaseError):
        binding.start(plan, scene=env, lease=lease)

    assert effects == []
    assert runtime.handles == {}


def test_lease_handle_is_invalidated_only_by_its_own_row_reset():
    resources = ResourceManager()
    keys = ("robot:env_1/arm", "object:/World/envs/env_1/box")
    lease = resources.acquire("pick-1", keys)
    env = SimpleNamespace(num_envs=2, _manipulation_reset_generation=[0, 0])
    runtime = SimpleNamespace(
        env=env,
        resources=resources,
        bindings={"env_1/arm": SimpleNamespace(env_id=1)},
    )
    request = SkillRequest("pick-1", SKILL_ID, "env_1/arm")
    effects = []
    handle = module._LeaseHandle(runtime, request, lease, keys, _EffectHandle(effects))

    env._manipulation_reset_generation[0] += 1
    assert handle.command() == "command"
    assert handle.after_step() is None
    assert effects == ["command", "after_step"]

    env._manipulation_reset_generation[1] += 1
    with pytest.raises(RuntimeError, match="reset invalidated"):
        handle.command()
    with pytest.raises(RuntimeError, match="reset invalidated"):
        handle.after_step()
    assert effects == ["command", "after_step"]


def test_g1_selective_reset_drops_only_reset_row_and_allows_replacement():
    from task.executors.g1_live import G1LiveRuntime

    env = SimpleNamespace(num_envs=2)
    runtime = G1LiveRuntime(env)
    kept = SimpleNamespace(receipt=None, error=None, attached=True, _target={"j": 1})
    reset = SimpleNamespace(receipt=None, error=None, attached=True, _target={"j": 2})
    runtime.handles = {(0, "g1"): kept, (1, "g1"): reset}

    runtime.reset([1])

    assert runtime.handles == {(0, "g1"): kept}
    assert reset.error == "environment reset invalidated the G1 task; reconcile task leases"
    assert reset.attached is False
    assert reset._target is None
    replacement = SimpleNamespace(receipt=None)
    assert runtime.add(1, "g1", replacement) is replacement
