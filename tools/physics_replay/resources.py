"""Fail-closed adapters for live policy and noise resources, never model pickles."""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def _state_equal(expected: Any, actual: Any) -> bool:
    if expected is actual:
        return True
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, np.ndarray):
        return expected.dtype == actual.dtype and np.array_equal(expected, actual)
    if hasattr(expected, "detach"):
        return (expected.dtype == actual.dtype and expected.device == actual.device
                and np.array_equal(expected.detach().cpu().numpy(), actual.detach().cpu().numpy()))
    if isinstance(expected, Mapping):
        return set(expected) == set(actual) and all(_state_equal(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, (list, tuple, deque)):
        return (getattr(expected, "maxlen", None) == getattr(actual, "maxlen", None)
                and len(expected) == len(actual)
                and all(_state_equal(first, second) for first, second in zip(expected, actual)))
    if hasattr(expected, "__dict__"):
        return _state_equal(vars(expected), vars(actual))
    return bool(expected == actual)


class ReplayResource:
    """Preserve resource identity while restoring explicitly declared mutable state."""

    def __init__(self, name: str, instance: Any):
        from .runtime_state import StateCodec

        self.name = name
        self.instance = instance
        self.codec = StateCodec()
        self.identity = id(instance)
        methods = ("capture_replay_state", "validate_replay_state", "restore_replay_state")
        if all(callable(getattr(instance, method, None)) for method in methods):
            self.mode = "protocol"
        elif getattr(instance, "replay_stateless", None) is True:
            self.mode = "declared_stateless"
        else:
            self.mode = self._builtin_mode()

    def _builtin_mode(self) -> str:
        kind = type(self.instance)
        if kind.__module__ == "EAI.controllers.utils" and kind.__name__ == "ONNXPolicy":
            return "onnx"
        if kind.__module__ == "isaaclab.utils.noise.noise_model" and kind.__name__ in {
            "NoiseModel", "NoiseModelWithAdditiveBias",
        }:
            return "isaac_noise"
        raise ValueError(
            f"{self.name}: unsupported stateful resource {kind.__module__}.{kind.__qualname__}; "
            "provide capture_replay_state/validate_replay_state/restore_replay_state, "
            "or explicitly declare replay_stateless=True"
        )

    def _onnx_state(self) -> dict[str, Any]:
        try:
            import onnx
        except ImportError as exc:
            raise ValueError(f"{self.name}: ONNX checkpoint auditing requires the onnx package") from exc

        policy = self.instance
        if set(vars(policy)) != {"session", "device", "input_name", "output_name"}:
            raise ValueError(f"{self.name}: ONNX wrapper has unadapted mutable state")
        session = policy.session
        if len(session.get_inputs()) != 1 or len(session.get_outputs()) != 1:
            raise ValueError(f"{self.name}: recurrent/multi-output ONNX requires an explicit adapter")
        payload = getattr(session, "_model_bytes", None)
        if not payload:
            path = getattr(session, "_model_path", None)
            if not path:
                raise ValueError(f"{self.name}: ONNX graph unavailable for checkpoint coverage audit")
            payload = Path(path).read_bytes()
        model = onnx.load_model_from_string(payload)
        allowed = {
            "Add", "Sub", "Mul", "Div", "MatMul", "Gemm", "Relu", "Elu", "Tanh", "Sigmoid",
            "LeakyRelu", "Identity", "Constant", "Concat", "Slice", "Gather", "Reshape",
            "Flatten", "Transpose", "Shape", "Unsqueeze", "Squeeze", "Cast", "Clip",
            "Expand", "Pow", "Sqrt", "ReduceMean", "ReduceSum", "Neg", "Abs", "Max", "Min",
        }
        if model.functions or model.training_info or any(
            tensor.data_location == onnx.TensorProto.EXTERNAL for tensor in model.graph.initializer
        ):
            raise ValueError(f"{self.name}: ONNX functions/training/external weights need an adapter")
        for node in model.graph.node:
            if node.domain not in {"", "ai.onnx"} or node.op_type not in allowed:
                raise ValueError(f"{self.name}: unverified ONNX operator {node.domain}/{node.op_type}")
        return {
            "model_sha256": sha256(payload).hexdigest(), "session_identity": id(session),
            "device": str(policy.device), "input": policy.input_name, "output": policy.output_name,
            "providers": session.get_providers(),
        }

    def _noise_state(self) -> dict[str, Any]:
        from isaaclab.utils.noise import noise_model

        model = self.instance
        configs = {"noise": model._noise_model_cfg.noise_cfg}
        if hasattr(model, "_bias_noise_cfg"):
            configs["bias"] = model._bias_noise_cfg
        parameters = {}
        for name, config in configs.items():
            if config.func not in {
                noise_model.constant_noise, noise_model.uniform_noise, noise_model.gaussian_noise,
            }:
                raise ValueError(f"{self.name}: custom noise function needs an explicit adapter")
            parameters[name] = {
                "function": config.func.__name__,
                "values": {key: value for key, value in vars(config).items() if key != "func"},
            }
        return {
            "attributes": {key: value for key, value in vars(model).items()
                           if key not in {"_noise_model_cfg", "_bias_noise_cfg"}},
            "parameters": parameters,
        }

    def capture_state(self) -> Mapping[str, Any]:
        if self.mode == "protocol":
            values = self.instance.capture_replay_state()
        elif self.mode == "onnx":
            values = self._onnx_state()
        elif self.mode == "isaac_noise":
            values = self._noise_state()
        else:
            values = {}
        return {"identity": self.identity, "mode": self.mode, "values": self.codec.encode(values)}

    def validate_state(self, state: Mapping[str, Any]) -> None:
        if state["identity"] != self.identity or state["mode"] != self.mode:
            raise ValueError(f"{self.name}: policy/noise resource changed since checkpoint")
        values = self.codec.decode(state["values"])
        if self.mode == "protocol":
            self.instance.validate_replay_state(values)
        elif self.mode == "onnx" and values != self._onnx_state():
            raise ValueError(f"{self.name}: ONNX model or execution configuration changed")
        elif self.mode == "isaac_noise":
            current = self._noise_state()
            if set(values["parameters"]) != set(current["parameters"]):
                raise ValueError(f"{self.name}: noise configuration changed")
            for name, config in current["parameters"].items():
                if values["parameters"][name]["function"] != config["function"]:
                    raise ValueError(f"{self.name}: noise function changed")

    def restore_state(self, state: Mapping[str, Any]) -> None:
        values = self.codec.decode(state["values"])
        if self.mode == "protocol":
            self.instance.restore_replay_state(values)
        elif self.mode == "isaac_noise":
            model = self.instance
            for key in set(vars(model)) - {"_noise_model_cfg", "_bias_noise_cfg"} - set(values["attributes"]):
                delattr(model, key)
            for key, value in values["attributes"].items():
                setattr(model, key, value)
            for name, parameters in values["parameters"].items():
                config = model._noise_model_cfg.noise_cfg if name == "noise" else model._bias_noise_cfg
                for key, value in parameters["values"].items():
                    setattr(config, key, value)
        readback = self.codec.decode(self.capture_state()["values"])
        if not _state_equal(values, readback):
            raise RuntimeError(f"{self.name}: policy/noise resource restore failed exact state read-back")
