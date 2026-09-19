"""Pure-data evidence for commands submitted to env.step and its state readback.

The caller copies commands BEFORE env.step, reads articulation root states
immediately AFTER it (before any kinematic pose override), then calls
``ControlStepEvidence(dt).record(step, commands, post_root_states)``. Inputs
are ordinary dicts of flat lists, for a single environment. This module never
imports torch/Isaac or reads a simulator. Time fields are simulation times
derived from the step index, not independently observed wall-clock timestamps.

Each command robot needs a state; additional articulation states are allowed.
Both inventories are fixed by the first valid sample. A failed record latches
the recorder as invalid, preserves the valid prefix and records a JSON-safe
failure without storing NaN/Inf or malformed raw inputs. Further calls cannot
erase the failure. report() returns a detached, self-hashed snapshot; a hash
binds content but is not proof of actuator application or control causality.
"""

from __future__ import annotations

from copy import deepcopy
import math

from demo.benchmark.provenance import canonical_sha256

SCHEMA = "eai.benchmark.control_step_evidence"
COMMAND_SCOPE = "submitted_to_env_step"
STATE_SCOPE = "post_env_step_articulation_root_state"
ROOT_STATE_COMPONENTS = (
    "position_x", "position_y", "position_z",
    "quaternion_w", "quaternion_x", "quaternion_y", "quaternion_z",
    "linear_velocity_x", "linear_velocity_y", "linear_velocity_z",
    "angular_velocity_x", "angular_velocity_y", "angular_velocity_z",
)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _dt(value):
    if not _finite(value) or value <= 0:
        raise ValueError("control_dt_s must be finite and positive")
    return float(value)


def _vectors(values, length, label):
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{label} must be a nonempty dict")
    if any(not isinstance(key, str) or not key for key in values):
        raise ValueError(f"{label} requires nonempty string robot ids")
    result = {}
    for robot in sorted(values):
        vector = values[robot]
        if not isinstance(vector, list) or len(vector) != length:
            raise ValueError(f"{label}.{robot} requires a flat {length}-component list")
        for index, value in enumerate(vector):
            if not _finite(value):
                raise ValueError(f"{label}.{robot}[{index}] is nonfinite or nonnumeric")
        result[robot] = list(vector)
    return result


class ControlStepEvidence:
    """Record a contiguous sequence starting at step 1; record failures latch.

    Constructor configuration errors raise ValueError. Invalid ``record`` input
    does not raise: inspect ``report()['failure']`` / ``['validated']``. An empty
    recorder is also not validated. ``record`` always returns None.
    """

    def __init__(self, control_dt_s):
        self._control_dt_s = _dt(control_dt_s)
        self._samples = []
        self._failure = None
        self._robot_ids = []
        self._state_ids = []

    def record(self, step, commands, post_root_states):
        if self._failure is not None:
            return
        expected = len(self._samples) + 1
        try:
            if type(step) is not int or step != expected:
                raise ValueError("step must be contiguous from 1")
            submitted_at = (step - 1) * self._control_dt_s
            post_at = step * self._control_dt_s
            if not _finite(post_at) or post_at <= submitted_at:
                raise ValueError("step times must be finite and strictly increasing")
            commands = _vectors(commands, 3, "commands")
            states = _vectors(post_root_states, 13, "post_root_states")
            robot_ids, state_ids = list(commands), list(states)
            if not commands.keys() <= states.keys():
                raise ValueError("every commanded robot needs a post_root_state")
            if self._samples and (robot_ids != self._robot_ids or state_ids != self._state_ids):
                raise ValueError("command/state robot inventories changed during recording")
        except (ValueError, OverflowError) as error:
            self._failure = {
                "step": step if type(step) is int and 0 <= step <= 2**53 else None,
                "expected_step": expected, "reason": str(error),
            }
            return
        self._robot_ids, self._state_ids = robot_ids, state_ids
        self._samples.append({
            "step": step, "submitted_at_sim_time_s": submitted_at,
            "post_step_sim_time_s": post_at,
            "commands": commands, "post_root_states": states,
        })

    def report(self):
        """Return an independent JSON-safe snapshot with SHA-256 over its body."""
        result = {
            "schema": SCHEMA, "version": 1,
            "command_scope": COMMAND_SCOPE, "state_scope": STATE_SCOPE,
            "root_state_components": list(ROOT_STATE_COMPONENTS),
            "control_dt_s": self._control_dt_s,
            "robot_ids": list(self._robot_ids), "state_ids": list(self._state_ids),
            "sample_count": len(self._samples), "last_step": len(self._samples),
            "samples": deepcopy(self._samples), "failure": deepcopy(self._failure),
            "validated": bool(self._samples) and self._failure is None,
            "actuator_applied_validated": False, "physical_control_validated": False,
            "contact_physics_validated": False,
        }
        return {**result, "sha256": canonical_sha256(result)}


def validate_control_step_evidence(evidence, *, steps_taken, control_dt_s):
    """Validate self-hash, full loop coverage, chronology and numeric shapes.

    Return the input after validation; do not mutate it. An empty or failed
    recorder, missing steps, changed inventories or inconsistent times raises
    ValueError. Pose/scenario/provenance binding belongs to the caller.
    """
    if not isinstance(evidence, dict):
        raise ValueError("control step evidence must be an object")
    content = {k: v for k, v in evidence.items() if k != "sha256"}
    try:
        digest = canonical_sha256(content)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("control step evidence is not finite JSON data") from error
    if evidence.get("sha256") != digest:
        raise ValueError("control step evidence digest mismatch")
    if evidence.get("schema") != SCHEMA or type(evidence.get("version")) is not int or evidence["version"] != 1:
        raise ValueError("unsupported control step evidence schema/version")
    if (evidence.get("command_scope") != COMMAND_SCOPE or evidence.get("state_scope") != STATE_SCOPE
            or evidence.get("root_state_components") != list(ROOT_STATE_COMPONENTS)):
        raise ValueError("control step evidence scope/layout mismatch")
    for name in ("actuator_applied_validated", "physical_control_validated", "contact_physics_validated"):
        if evidence.get(name) is not False:
            raise ValueError("control step evidence cannot grant physical or actuator authority")
    if evidence.get("failure") is not None or evidence.get("validated") is not True:
        raise ValueError("control step evidence records failure or is not validated")
    if type(steps_taken) is not int or steps_taken <= 0:
        raise ValueError("control step evidence requires positive integer steps_taken")
    dt = _dt(control_dt_s)
    if _dt(evidence.get("control_dt_s")) != dt:
        raise ValueError("control step evidence/control_dt_s mismatch")
    samples = evidence.get("samples")
    if not isinstance(samples, list) or len(samples) != steps_taken:
        raise ValueError("control step evidence does not cover every env.step")
    if (type(evidence.get("sample_count")) is not int or evidence["sample_count"] != steps_taken
            or type(evidence.get("last_step")) is not int or evidence["last_step"] != steps_taken):
        raise ValueError("control step evidence count/last_step mismatch")
    robot_ids, state_ids = evidence.get("robot_ids"), evidence.get("state_ids")
    sample_keys = {"step", "submitted_at_sim_time_s", "post_step_sim_time_s", "commands", "post_root_states"}
    for expected, sample in enumerate(samples, 1):
        if not isinstance(sample, dict) or set(sample) != sample_keys:
            raise ValueError("malformed control step evidence sample")
        if type(sample["step"]) is not int or sample["step"] != expected:
            raise ValueError("control step evidence step sequence mismatch")
        before, after = sample["submitted_at_sim_time_s"], sample["post_step_sim_time_s"]
        if (not _finite(before) or not _finite(after) or before != (expected - 1) * dt
                or after != expected * dt or after <= before):
            raise ValueError("control step evidence simulation time mismatch")
        commands = _vectors(sample["commands"], 3, "commands")
        states = _vectors(sample["post_root_states"], 13, "post_root_states")
        if list(commands) != robot_ids or list(states) != state_ids or not commands.keys() <= states.keys():
            raise ValueError("control step evidence robot inventory mismatch")
    return evidence
