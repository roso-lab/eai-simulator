"""Import an audited one-row public checkpoint into a live Isaac batch.

The live snapshot supplies the batch shape and process-local resource identities.
Every saved array must either match that shape or have exactly one environment
row on the same axis; other shape changes fail closed.
"""

from __future__ import annotations

from copy import deepcopy

import numpy as np


def _equal(first, second):
    if isinstance(first, np.ndarray) or isinstance(second, np.ndarray):
        return bool(np.array_equal(first, second))
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(
            _equal(first[key], second[key]) for key in first)
    if isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)):
        return len(first) == len(second) and all(
            _equal(a, b) for a, b in zip(first, second))
    return first == second


def _missing_runtime_field_template(value, count):
    """Infer the batch shape only for fields created after the first step."""
    if isinstance(value, dict):
        if value.get("kind") in {"tensor", "numpy"}:
            array = np.asarray(value["value"])
            if not array.ndim or array.shape[0] != 1:
                raise ValueError("late runtime field has no canonical one-row shape")
            return {**value, "value": np.repeat(array, count, axis=0)}
        return {key: _missing_runtime_field_template(item, count)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_missing_runtime_field_template(item, count) for item in value)
    return deepcopy(value)


def expand_encoded(saved, live, count: int, *, path=()):
    if (live is None and path[-1:] == ("_buffer",)
            and "actuators" in path and isinstance(saved, dict)
            and saved.get("kind") in {"tensor", "numpy"}):
        original = np.asarray(saved["value"])
        if original.ndim < 2 or original.shape[1] != 1:
            raise ValueError(f"delayed controller history is not one-row at {path}")
        return {**saved, "value": np.repeat(original, count, axis=1)}
    if isinstance(saved, dict) and isinstance(live, dict):
        if set(saved) != set(live):
            raise ValueError(f"checkpoint structure changed at {path}")
        if saved.get("kind") in {"tensor", "numpy"}:
            if saved["kind"] != live["kind"]:
                raise ValueError(f"checkpoint array kind changed at {path}")
            original = np.asarray(saved["value"])
            current = np.asarray(live["value"])
            if original.dtype != current.dtype:
                raise ValueError(f"checkpoint array dtype changed at {path}")
            if path[-1:] == ("_ALL_INDICES",):
                if not np.array_equal(original, np.arange(len(original))):
                    raise ValueError("saved actuator indices are not canonical")
                return deepcopy(live)
            if original.shape == current.shape:
                return deepcopy(saved)
            axes = [index for index, (before, after) in enumerate(zip(original.shape, current.shape))
                    if before == 1 and after == count]
            if len(original.shape) != len(current.shape) or len(axes) != 1 or any(
                    before != after for index, (before, after) in enumerate(zip(original.shape, current.shape))
                    if index != axes[0]):
                raise ValueError(f"checkpoint array shape changed at {path}: {original.shape} -> {current.shape}")
            axis = axes[0]
            if axis != (1 if path[-1:] == ("_buffer",) else 0):
                raise ValueError(f"unexpected checkpoint environment axis at {path}")
            result = deepcopy(saved)
            result["value"] = np.repeat(original, count, axis=axis)
            return result
        if saved.get("kind") == "dict":
            if path == ("controllers", "iris_1") or path == ("buffers",) or (len(path) > 1 and path[:1] == ("buffers",)
                                             and path[1] in {
                                                 "_last_computed_actions", "obs_dict",
                                                 "reward_dict", "terminated_dict",
                                                 "time_out_dict", "_commands_dict",
                                                 "actions_dict", "extras",
                                             }):
                source_items = dict(saved["items"])
                current_items = dict(live["items"])
                missing = set(source_items) - set(current_items)
                if path == ("controllers", "iris_1") and not missing <= {"rotor_velocities"}:
                    raise ValueError(f"unknown late Iris controller state: {missing}")
                if path == ("buffers",) and not missing <= {
                        "reward_dict", "terminated_dict", "time_out_dict", "_commands_dict"}:
                    raise ValueError(f"unknown late runtime fields: {missing}")
                live = deepcopy(live)
                live["items"].extend((key, _missing_runtime_field_template(source_items[key], count))
                                     for key in sorted(missing))
            if len(saved["items"]) != len(live["items"]):
                raise ValueError(f"checkpoint mapping keys changed at {path}: "
                                 f"saved={[key for key, _ in saved['items']]}; "
                                 f"live={[key for key, _ in live['items']]}")
            live_items = {repr(key): value for key, value in live["items"]}
            result = deepcopy(saved)
            result["items"] = []
            for key, value in saved["items"]:
                if repr(key) not in live_items:
                    raise ValueError(f"checkpoint mapping key changed at {path}: {key}")
                field = key if isinstance(key, str) else repr(key)
                result["items"].append((deepcopy(key), expand_encoded(
                    value, live_items[repr(key)], count, path=(*path, field))))
            return result
        return {key: expand_encoded(value, live[key], count, path=(*path, key))
                for key, value in saved.items()}
    if isinstance(saved, (list, tuple)) and isinstance(live, (list, tuple)):
        if len(saved) != len(live):
            raise ValueError(f"checkpoint sequence changed at {path}")
        return type(saved)(expand_encoded(a, b, count, path=(*path, index))
                           for index, (a, b) in enumerate(zip(saved, live)))
    if path[-1:] in {("_num_envs",), ("_batch_size",)}:
        if saved != 1 or live != count:
            raise ValueError(f"checkpoint batch cardinality changed at {path}")
        return live
    if isinstance(saved, str) and isinstance(live, str) and path[-1:] == ("device",):
        if saved != live:
            raise ValueError(f"checkpoint tensor device changed at {path}")
    if type(saved) is not type(live):
        raise ValueError(f"checkpoint value type changed at {path}")
    return deepcopy(saved)


def _rebind_resource(saved, live, count, *, model=False):
    if saved["mode"] != live["mode"]:
        raise ValueError("checkpoint resource mode changed")
    # Resource identity is intentionally process-local.  Check its declared
    # mode and model configuration below, then expand only its mutable state.
    result = deepcopy(saved)
    result["values"] = expand_encoded(saved["values"], live["values"], count)
    result["identity"] = live["identity"]
    if model and saved["mode"] == "onnx":
        # Session identities are only meaningful inside their own process.
        old_values = deepcopy(saved["values"])
        new_values = deepcopy(live["values"])
        for values in (old_values, new_values):
            values["items"] = [entry for entry in values["items"]
                               if entry[0] != "session_identity"]
        if not _equal(old_values, new_values):
            raise ValueError("checkpoint ONNX graph or execution configuration changed")
        result["values"] = deepcopy(live["values"])
    return result


def expand_checkpoint(saved: dict, live: dict, count: int) -> dict:
    if count < 1:
        raise ValueError("batch must contain at least one row")
    if set(saved) != set(live):
        raise ValueError("checkpoint sections changed")
    # The task ledger belongs to the saved milestone; the just-created batch
    # has a different ledger by definition.  Compare all other state against
    # the live schema, then restore the source ledger unchanged.
    saved_values = deepcopy(saved["values"])
    live_values = deepcopy(live["values"])
    def without_task(encoded):
        encoded["items"] = [entry for entry in encoded["items"] if entry[0] != "task"]
        return encoded

    values = expand_encoded(without_task(saved_values), without_task(live_values), count)
    values["items"].append(("task", next(value for key, value in saved["values"]["items"]
                                           if key == "task")))
    result = {
        "values": values,
        "actuators": expand_encoded(saved["actuators"], live["actuators"], count),
        "resources": {},
    }
    if set(saved["resources"]) != set(live["resources"]):
        raise ValueError("checkpoint controller resources changed")
    if set(saved["actuators"]["sources"]) != set(live["actuators"]["sources"]):
        raise ValueError("checkpoint wrench adapters changed")
    result["resources"] = {name: _rebind_resource(source, live["resources"][name], count, model=True)
                           for name, source in saved["resources"].items()}
    result["actuators"]["sources"] = {
        name: _rebind_resource(source, live["actuators"]["sources"][name], count)
        for name, source in saved["actuators"]["sources"].items()
    }
    return result
