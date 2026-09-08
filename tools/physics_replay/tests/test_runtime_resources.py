from copy import deepcopy
import importlib.util
from pathlib import Path
import random
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.components import RngStateComponent
from tools.physics_replay.runtime_state import ActuatorStateComponent, EnvironmentBuffersComponent, StateCodec


class MemoryPolicy:
    def __init__(self):
        self.hidden = 0.0

    def act(self, observation):
        self.hidden = self.hidden * 0.5 + observation
        return self.hidden

    def capture_replay_state(self):
        return {"hidden": self.hidden}

    def validate_replay_state(self, state):
        if set(state) != {"hidden"} or not np.isfinite(state["hidden"]):
            raise ValueError("invalid policy memory")

    def restore_replay_state(self, state):
        self.hidden = state["hidden"]


def test_onnx_policy_adapts_fixed_batch_one_models_to_multiple_environments():
    source = Path(__file__).resolve().parents[3] / "source/EAI/EAI/controllers/utils.py"
    spec = importlib.util.spec_from_file_location("EAI.controllers.utils", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="obs", shape=[1, 2])]

        def get_outputs(self):
            return [SimpleNamespace(name="action")]

        def run(self, outputs, feed):
            values = feed["obs"]
            assert values.shape == (1, 2)
            return [values[:, :1] * 2.0]

    policy = module.ONNXPolicy(Session())
    actions, state = policy.act({"states": np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)})
    np.testing.assert_array_equal(actions, [[2.0], [6.0]])
    assert state is None


@pytest.mark.parametrize("batch", [None, "batch", 2])
def test_onnx_policy_keeps_supported_batch_inference_single_call(batch):
    source = Path(__file__).resolve().parents[3] / "source/EAI/EAI/controllers/utils.py"
    spec = importlib.util.spec_from_file_location("EAI.controllers.utils", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(outputs, feed):
        calls.append(feed["obs"].copy())
        return [feed["obs"] * 2]

    session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name="obs", shape=[batch, 2])],
                              get_outputs=lambda: [SimpleNamespace(name="action")], run=run)
    inputs = np.asarray([[1, 2], [3, 4]], dtype=np.float32)
    actions, unused = module.ONNXPolicy(session).act({"states": inputs})
    assert len(calls) == 1
    np.testing.assert_array_equal(actions, inputs * 2)


def test_onnx_does_not_split_a_missing_batch_dimension_into_environments():
    source = Path(__file__).resolve().parents[3] / "source/EAI/EAI/controllers/utils.py"
    spec = importlib.util.spec_from_file_location("EAI.controllers.utils", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def run(outputs, feed):
        calls.append(feed["obs"].shape)
        raise ValueError("invalid rank")

    session = SimpleNamespace(get_inputs=lambda: [SimpleNamespace(name="obs", shape=[1, 2])],
                              get_outputs=lambda: [SimpleNamespace(name="action")], run=run)
    with pytest.raises(ValueError, match="invalid rank"):
        module.ONNXPolicy(session).act({"states": np.ones(2, dtype=np.float32)})
    assert calls == [(2,)]


def test_policy_memory_restored_in_place_and_validation_does_not_change_future():
    policy = MemoryPolicy()
    environment = SimpleNamespace(_controllers_dict={"robot": {"policy": policy}})
    component = EnvironmentBuffersComponent(environment)
    saved = component.capture_state()
    expected = policy.act(1.0)
    policy.act(1.0)
    component.validate_state(saved)
    assert policy.hidden == 1.5
    component.restore_state(saved)
    assert environment._controllers_dict["robot"]["policy"] is policy
    assert policy.act(1.0) == expected == 1.0


def test_unadapted_policy_and_noise_fail_closed():
    for environment in (
        SimpleNamespace(_controllers_dict={"robot": {"policy": SimpleNamespace(hidden=0)}}),
        SimpleNamespace(_controllers_dict={}, _action_noise_model={"robot": SimpleNamespace(bias=0.2)}),
    ):
        with pytest.raises(ValueError, match="unsupported stateful resource"):
            EnvironmentBuffersComponent(environment).capture_state()


def test_replaced_policy_rejected_before_environment_mutation():
    environment = SimpleNamespace(_controllers_dict={"robot": {"policy": MemoryPolicy()}}, common_step_counter=0)
    component = EnvironmentBuffersComponent(environment)
    saved = component.capture_state()
    environment.common_step_counter = 10
    environment._controllers_dict["robot"]["policy"] = MemoryPolicy()
    with pytest.raises(ValueError, match="resource changed"):
        component.restore_state(saved)
    assert environment.common_step_counter == 10


def test_broken_policy_adapter_is_not_reported_as_a_successful_restore():
    policy = MemoryPolicy()
    policy.restore_replay_state = lambda state: None
    component = EnvironmentBuffersComponent(SimpleNamespace(_controllers_dict={"robot": {"policy": policy}}))
    saved = component.capture_state()
    policy.act(1.0)
    with pytest.raises(RuntimeError, match="exact state read-back"):
        component.restore_state(saved)


def test_declared_stateless_policy_requires_no_memory_adapter():
    policy = SimpleNamespace(replay_stateless=True)
    environment = SimpleNamespace(_controllers_dict={"robot": {"policy": policy}})
    component = EnvironmentBuffersComponent(environment)
    saved = component.capture_state()
    component.restore_state(saved)
    assert environment._controllers_dict["robot"]["policy"] is policy


def test_active_external_force_requires_source_state_and_restores_force_history():
    body = SimpleNamespace(_external_force_b=np.array([[[2.0, 0.0, 0.0]]]),
                           _external_torque_b=np.zeros((1, 1, 3)), has_external_wrench=True)
    source = MemoryPolicy()
    environment = SimpleNamespace(scene=SimpleNamespace(articulations={}, rigid_objects={"body": body}))
    actuator = ActuatorStateComponent.__new__(ActuatorStateComponent)
    actuator.base_env = environment
    actuator.components = {}
    actuator.codec = StateCodec()
    with pytest.raises(ValueError, match="replay_external_wrench_sources"):
        actuator.capture_state()
    environment.replay_external_wrench_sources = {"gust": source}
    runtime = EnvironmentBuffersComponent(environment)
    runtime_state, physical_state = runtime.capture_state(), actuator.capture_state()
    expected_next_force = source.act(1.0)
    source.act(1.0)
    body._external_force_b.fill(8.0)
    runtime.restore_state(runtime_state)
    actuator.restore_state(physical_state)
    assert source.act(1.0) == expected_next_force
    np.testing.assert_array_equal(body._external_force_b, [[[2.0, 0.0, 0.0]]])


def test_named_generators_restore_sequences_and_keep_external_aliases():
    torch = pytest.importorskip("torch")
    streams = {"sensor/python": random.Random(7), "sensor/numpy": np.random.default_rng(8),
               "policy/torch": torch.Generator().manual_seed(9)}
    component = RngStateComponent(streams=streams)
    original_global = RngStateComponent().capture_state()

    def samples():
        return (streams["sensor/python"].random(), streams["sensor/numpy"].random(8),
                torch.rand(8, generator=streams["policy/torch"]))

    try:
        saved = component.capture_state()
        expected = samples()
        samples()
        before_validation = deepcopy(streams["sensor/numpy"].bit_generator.state)
        component.validate_state(saved)
        assert streams["sensor/numpy"].bit_generator.state == before_validation
        identities = {name: id(stream) for name, stream in streams.items()}
        component.restore_state(saved)
        actual = samples()
        assert identities == {name: id(stream) for name, stream in streams.items()}
        assert actual[0] == expected[0]
        np.testing.assert_array_equal(actual[1], expected[1])
        assert torch.equal(actual[2], expected[2])
        streams["new"] = random.Random(10)
        with pytest.raises(ValueError, match="stream set changed"):
            component.validate_state(saved)
    finally:
        RngStateComponent().restore_state(original_global)


def test_real_isaac_bias_noise_reproduces_initialization_and_future_samples(monkeypatch):
    torch = pytest.importorskip("torch")
    package_spec = importlib.util.find_spec("isaaclab")
    if package_spec is None:
        pytest.skip("installed Isaac Lab noise source is unavailable")
    source = Path(next(iter(package_spec.submodule_search_locations))) / "utils/noise/noise_model.py"
    name = "isaaclab.utils.noise.noise_model"
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for parent in ("isaaclab", "isaaclab.utils", "isaaclab.utils.noise"):
        package = ModuleType(parent)
        package.__path__ = []
        monkeypatch.setitem(sys.modules, parent, package)
    sys.modules["isaaclab.utils.noise"].noise_model = module
    config = SimpleNamespace(
        noise_cfg=SimpleNamespace(func=module.gaussian_noise, mean=0.0, std=0.2, operation="add"),
        bias_noise_cfg=SimpleNamespace(func=module.uniform_noise, n_min=-0.4, n_max=0.4, operation="add"),
        sample_bias_per_component=True,
    )
    noise = module.NoiseModelWithAdditiveBias(config, 1, "cpu")
    environment = SimpleNamespace(_controllers_dict={}, _action_noise_model={"robot": noise})
    component = EnvironmentBuffersComponent(environment)
    rng = RngStateComponent()
    initial_rng = rng.capture_state()
    try:
        snapshot = component.capture_state()
        expected = [noise(torch.ones(1, 4)).clone() for unused in range(3)]
        noise.reset()
        noise._bias.fill_(0.8)
        component.validate_state(snapshot)
        assert torch.all(noise._bias == 0.8)
        component.restore_state(snapshot)
        rng.restore_state(initial_rng)
        assert noise._num_components is None
        assert tuple(noise._bias.shape) == (1, 1)
        actual = [noise(torch.ones(1, 4)).clone() for unused in range(3)]
        assert all(torch.equal(first, second) for first, second in zip(expected, actual))
        assert not torch.equal(actual[0], actual[1])
        assert environment._action_noise_model["robot"] is noise
    finally:
        rng.restore_state(initial_rng)


@pytest.mark.parametrize("operation, accepted", [("Relu", True), ("RandomNormalLike", False)])
def test_onnx_adapter_audits_graph_instead_of_assuming_stateless(operation, accepted):
    onnx = pytest.importorskip("onnx")
    ort = pytest.importorskip("onnxruntime")
    source = Path(__file__).resolve().parents[3] / "source/EAI/EAI/controllers/utils.py"
    spec = importlib.util.spec_from_file_location("EAI.controllers.utils", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = onnx.helper.make_graph(
        [onnx.helper.make_node(operation, ["input"], ["output"])], "test",
        [onnx.helper.make_tensor_value_info("input", onnx.TensorProto.FLOAT, [1, 2])],
        [onnx.helper.make_tensor_value_info("output", onnx.TensorProto.FLOAT, [1, 2])],
    )
    model = onnx.helper.make_model(graph, opset_imports=[onnx.helper.make_opsetid("", 13)], ir_version=10)
    policy = module.ONNXPolicy(ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"]))
    component = EnvironmentBuffersComponent(SimpleNamespace(_controllers_dict={"robot": {"policy": policy}}))
    if accepted:
        state = component.capture_state()
        component.validate_state(state)
        component.restore_state(state)
    else:
        with pytest.raises(ValueError, match="unverified ONNX operator"):
            component.capture_state()
