"""CPU contact callback measurements with strict tensor parity and slot checks."""

from __future__ import annotations

import numpy as np
import re
import time


def _host_array(value, *, dtype=None):
    """Convert callback values from Isaac's CUDA tensor wrappers to NumPy."""
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    elif hasattr(value, "cpu") and not isinstance(value, np.ndarray):
        try:
            value = value.cpu().numpy()
        except Exception:
            pass
    return np.asarray(value, dtype=dtype)


def slot_owner(path):
    match = re.match(r"^/World/envs/env_(\d+)(?:/|$)", str(path))
    return int(match.group(1)) if match else None


class CallbackContactMeasurements:
    def __init__(self, sensors, filters, dt, capacity=16384):
        self.sensors = {path: index for index, path in enumerate(sensors)}
        self.filters = {path: index for index, path in enumerate(filters)}
        self.dt = dt
        self.scale = np.float32(1.0 / dt)
        self.capacity = capacity
        self.net_impulses = np.zeros((len(sensors), 3), dtype=np.float32)
        self.clear()

    def clear(self):
        self.net_impulses.fill(0)
        self.normal_pairs = {}
        self.friction_pairs = {}
        self.normal_count = 0
        self.friction_count = 0

    def add(self, paths, contacts, friction):
        if len(paths) != 4:
            raise ValueError("contact header must contain two actor and two collider paths")
        for side, sign in ((0, 1), (1, -1)):
            if paths[side] not in self.sensors:
                continue
            sensor = self.sensors[paths[side]]
            for point in contacts:
                impulse = _host_array(point.impulse, dtype=np.float32)
                self.net_impulses[sensor] += sign * impulse
            other = paths[1 - side]
            if other not in self.filters:
                other = paths[3 - side]
            if other not in self.filters:
                continue
            pair = (sensor, self.filters[other])
            self.normal_count += len(contacts)
            self.friction_count += len(friction)
            if max(self.normal_count, self.friction_count) >= self.capacity:
                raise ValueError("contact callback capacity reached; refusing truncated measurements")
            for point in contacts:
                impulse = _host_array(point.impulse, dtype=np.float32)
                force = np.sqrt(np.sum(impulse * impulse)) * self.scale
                self.normal_pairs.setdefault(pair, []).append((
                    force, _host_array(point.position, dtype=np.float32),
                    sign * _host_array(point.normal, dtype=np.float32), np.float32(point.separation),
                ))
            for anchor in friction:
                self.friction_pairs.setdefault(pair, []).append((
                    sign * _host_array(anchor.impulse, dtype=np.float32) * self.scale,
                    _host_array(anchor.position, dtype=np.float32),
                ))

    def arrays(self):
        forces = self.net_impulses * self.scale
        result = {"contacts/net_force_N": forces, "contacts/net_impulse_Ns": forces * self.dt}
        for kind, pairs, fields in (
            ("normal", self.normal_pairs, (("force_N", 1), ("point_m", 3), ("normal", 3), ("separation_m", 1))),
            ("friction", self.friction_pairs, (("force_N", 3), ("point_m", 3))),
        ):
            counts = np.zeros((len(self.sensors), len(self.filters)), dtype=np.uint32)
            indices = []
            ordered = []
            for pair, records in sorted(pairs.items()):
                counts[pair] = len(records)
                indices.extend([pair] * len(records))
                ordered.extend(sorted(records, key=lambda record: tuple(record[1])))
            prefix = f"contacts/{kind}"
            result[f"{prefix}/counts"] = counts
            result[f"{prefix}/pair_indices"] = np.asarray(indices, dtype=np.int64).reshape(-1, 2)
            for index, (name, width) in enumerate(fields):
                result[f"{prefix}/{name}"] = np.asarray(
                    [record[index] for record in ordered], dtype=np.float32,
                ).reshape(-1, width)
        return result


class SlotContactReportStream:
    """Collect every physical substep, including non-default PhysX scenes.

    Isaac Sim 5.1's tensor reader may omit contacts from additional scenes.
    Callback measurements keep all twelve existing channels; slot zero must
    also match its tensor measurement exactly on every substep.
    """

    def __init__(self, simulation, *, progress_path=None, common_static=None,
                 strict_tensor_parity=True):
        from omni.physx import get_physx_simulation_interface
        from pxr import PhysicsSchemaTools

        self.simulation = simulation
        self.progress_path = progress_path
        self.common_static = common_static
        self.strict_tensor_parity = bool(strict_tensor_parity)
        self.decode = PhysicsSchemaTools.intToSdfPath
        self.builders = {}
        self.readers = {}
        self.active = False
        self._active_substep = 0
        self.closed = False
        self.report = {
            "physical_substeps": 0, "default_scene_tensor_parity_substeps": 0,
            "measured_substeps_by_slot": {}, "contact_headers_by_slot": {},
            "contact_points_by_slot": {}, "cross_slot_contacts": 0,
            "unscoped_contacts": 0, "error": None,
            "strict_tensor_parity": self.strict_tensor_parity,
            "callback_invocations": 0, "callback_last_monotonic": None,
            "callback_substeps": [], "callback_tensor_mismatch_count": 0,
            "callback_tensor_mismatches": [],
        }
        self.original_step = simulation.step
        self.owned_step = vars(simulation).get("step")
        self.subscription = get_physx_simulation_interface().subscribe_full_contact_report_events(self.contacts)

        def step(*args, **kwargs):
            if self.common_static is not None:
                self.common_static.validate_immutable()
            for builder in self.builders.values():
                builder.clear()
            self.active = True
            self._active_substep = self.report["physical_substeps"]
            try:
                return self.original_step(*args, **kwargs)
            finally:
                self.active = False
                self.report["physical_substeps"] += 1
                if self.report["physical_substeps"] % 200 == 0 or self.report["error"]:
                    self.persist()

        self.step = step
        simulation.step = step

    def register(self, reader, index):
        if type(index) is not int or index < 0:
            raise ValueError("contact reader requires a nonnegative slot")
        self.readers[index] = reader
        self.builders[index] = CallbackContactMeasurements(
            reader.contacts.sensor_paths, reader.filter_paths, reader.dt,
            reader.contacts.max_contact_data_count,
        )
        reader.contact_stream = self

    def contacts(self, headers, data, friction):
        if not self.active:
            return
        try:
            self.report["callback_invocations"] += 1
            self.report["callback_last_monotonic"] = time.monotonic()
            if self._active_substep not in self.report["callback_substeps"]:
                self.report["callback_substeps"].append(self._active_substep)
            for header in headers:
                paths = [str(self.decode(getattr(header, name)))
                         for name in ("actor0", "actor1", "collider0", "collider1")]
                owners = [slot_owner(path) for path in paths[:2]]
                common = getattr(self, "common_static", None)
                shared = [common is not None and common.contains_collider(paths[side + 2])
                          and (paths[side] == paths[side + 2] or common.contains_collider(paths[side]))
                          for side in (0, 1)]
                private_owners = [owner for owner, is_shared in zip(owners, shared) if not is_shared]
                if not private_owners or None in private_owners:
                    self.report["unscoped_contacts"] += 1
                    raise ValueError(f"unscoped contact actors: {paths[:2]}")
                if len(set(private_owners)) != 1:
                    self.report["cross_slot_contacts"] += 1
                    raise ValueError(f"cross-slot contact actors: {paths[:2]}")
                owner = private_owners[0]
                if any(not shared[side] and slot_owner(paths[side + 2]) != owner for side in (0, 1)):
                    self.report["cross_slot_contacts"] += 1
                    raise ValueError(f"contact collider ownership differs from its actor: {paths}")
                start, count = int(header.contact_data_offset), int(header.num_contact_data)
                fstart, fcount = int(header.friction_anchors_offset), int(header.num_friction_anchors_data)
                if not (0 <= start <= start + count <= len(data)
                        and 0 <= fstart <= fstart + fcount <= len(friction)):
                    raise ValueError("invalid callback contact/friction range")
                for key, increment in (("contact_headers_by_slot", 1), ("contact_points_by_slot", count)):
                    counts = self.report[key]
                    counts[str(owner)] = counts.get(str(owner), 0) + increment
                self.builders[owner].add(paths, data[start:start + count], friction[fstart:fstart + fcount])
        except Exception as error:
            self.report["error"] = f"{type(error).__name__}: {error}"

    def measurements(self, reader, original):
        if not self.active or self.closed:
            raise ValueError("callback contacts must be read inside the physical step")
        if self.report["error"]:
            raise ValueError(self.report["error"])
        index = reader.env_index
        if self.readers.get(index) is not reader:
            raise ValueError("stale contact reader after topology update")
        measured = self.builders[index].arrays()
        if set(measured) != {name for name in original if name.startswith("contacts/")}:
            raise ValueError("callback measurements changed the contact channel set")
        if index == 0:
            for name, values in measured.items():
                if (values.dtype != original[name].dtype or values.shape != original[name].shape
                        or not np.array_equal(values, original[name])):
                    mismatch = f"default-scene callback/tensor mismatch: {name}"
                    self.report["callback_tensor_mismatch_count"] += 1
                    if len(self.report["callback_tensor_mismatches"]) < 32:
                        self.report["callback_tensor_mismatches"].append({
                            "substep": self.report["physical_substeps"], "channel": name,
                            "callback_shape": list(values.shape), "tensor_shape": list(original[name].shape),
                            "callback_nonzero": int(np.count_nonzero(values)),
                            "tensor_nonzero": int(np.count_nonzero(original[name])),
                        })
                    self.report.setdefault("first_tensor_mismatch", {
                        "substep": self.report["physical_substeps"], "channel": name,
                    })
                    if self.progress_path is not None:
                        np.savez_compressed(self.progress_path.with_suffix(".mismatch.npz"),
                                            callback=values, tensor=original[name])
                    if self.strict_tensor_parity:
                        self.report["error"] = mismatch
                        self.persist()
                        raise ValueError(mismatch)
                    break
            self.report["default_scene_tensor_parity_substeps"] += 1
        counts = self.report["measured_substeps_by_slot"]
        counts[str(index)] = counts.get(str(index), 0) + 1
        return measured

    def persist(self):
        if self.progress_path is not None:
            from tools.physics_replay.store import _atomic_json_write

            _atomic_json_write(self.progress_path, self.report)

    def close(self):
        if self.closed:
            return
        if self.simulation.step is self.step:
            if self.owned_step is None:
                del self.simulation.step
            else:
                self.simulation.step = self.owned_step
        self.subscription = None
        self.closed = True
        self.persist()
