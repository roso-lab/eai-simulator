"""Deterministic resource reservation and simulated-time node deadlines."""

import math


TERMINAL_STATES = {"succeeded", "failed", "timed_out", "cancelled", "skipped"}
FAILURE_STATES = {"failed", "timed_out", "cancelled"}


def node_resources(entry):
    agents = entry.get("team") or [entry["agent"]]
    resources = {f"robot:{agent}" for agent in agents}
    if entry.get("object"):
        resources.add(f"object:{entry['object']}")
    resources.update(f"shared:{resource}" for resource in entry.get("resources", ()))
    return frozenset(resources)


class EpisodeScheduler:
    def __init__(self, plan):
        self.entries = {entry["node"]: entry for entry in plan}
        if len(self.entries) != len(plan):
            raise ValueError("duplicate scheduler node")
        self.states = {
            identifier: {"status": "pending", "ready_at_s": None, "started_at_s": None,
                         "finished_at_s": None, "reason": None}
            for identifier in self.entries
        }
        self.reservations = {}
        self.events = []
        self.time_s = 0.0
        self.recoveries = {}
        for identifier, entry in self.entries.items():
            timeout = entry["timeout_s"]
            if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
                raise ValueError(f"{identifier}: timeout_s must be finite and positive")
            if set(entry["prerequisites"]) - self.entries.keys():
                raise ValueError(f"{identifier}: unknown scheduler prerequisite")
            recovery_for = entry.get("recovery_for")
            if recovery_for:
                if recovery_for == identifier or recovery_for not in entry["prerequisites"]:
                    raise ValueError(f"{identifier}: recovery_for must name another prerequisite")
                self.recoveries.setdefault(recovery_for, []).append(identifier)

    @property
    def recovered_by(self):
        return {
            original: list(replacements) for original, replacements in self.recoveries.items()
            if self.states[original]["status"] in FAILURE_STATES
            and all(self.states[node]["status"] == "succeeded" for node in replacements)
        }

    @property
    def satisfied_nodes(self):
        return {node for node, state in self.states.items()
                if state["status"] in {"succeeded", "skipped"}} | self.recovered_by.keys()

    def _can_recover(self, identifier):
        replacements = self.recoveries.get(identifier, ())
        return bool(replacements) and all(
            self.states[node]["status"] not in FAILURE_STATES | {"skipped"}
            for node in replacements
        )

    def set_time(self, time_s):
        if not math.isfinite(time_s) or time_s < self.time_s:
            raise ValueError("scheduler time must be finite and monotonic")
        self.time_s = time_s

    def _transition(self, identifier, status, reason=None):
        state = self.states[identifier]
        previous = state["status"]
        state["status"] = status
        state["reason"] = reason
        if status == "running":
            state["started_at_s"] = self.time_s
        if status in TERMINAL_STATES:
            state["finished_at_s"] = self.time_s
            self.reservations = {resource: owner for resource, owner in self.reservations.items()
                                 if owner != identifier}
        self.events.append({"node": identifier, "previous_status": previous,
                            "status": status, "time_s": self.time_s, "reason": reason})

    def advance(self, time_s):
        self.set_time(time_s)
        for identifier, entry in self.entries.items():
            state = self.states[identifier]
            if state["status"] in TERMINAL_STATES:
                continue
            recovery_for = entry.get("recovery_for")
            if recovery_for:
                original_status = self.states[recovery_for]["status"]
                if original_status in {"succeeded", "skipped"}:
                    self._transition(identifier, "skipped", "recovery_not_required")
                    continue
                if original_status == "cancelled":
                    self._transition(identifier, "cancelled", "recovery_source_cancelled")
                    continue
                if original_status not in {"failed", "timed_out"}:
                    continue
            dependencies = [dependency for dependency in entry["prerequisites"]
                            if dependency != recovery_for]
            if any(self.states[dependency]["status"] == "skipped" for dependency in dependencies):
                self._transition(identifier, "skipped", "prerequisite_skipped")
                continue
            unresolved = [dependency for dependency in dependencies
                          if self.states[dependency]["status"] != "succeeded"
                          and dependency not in self.recovered_by]
            if any(self.states[dependency]["status"] in FAILURE_STATES
                   and not self._can_recover(dependency) for dependency in unresolved):
                self._transition(identifier, "cancelled", "prerequisite_failed")
                continue
            if unresolved:
                continue
            if state["ready_at_s"] is None:
                state["ready_at_s"] = time_s
                self._transition(identifier, "waiting_resource")
            if time_s - state["ready_at_s"] > entry["timeout_s"] + 1e-9:
                self._transition(identifier, "timed_out", "node_deadline_exceeded")
                continue
            if state["status"] == "running":
                continue
            resources = node_resources(entry)
            if any(resource in self.reservations for resource in resources):
                continue
            for resource in sorted(resources):
                self.reservations[resource] = identifier
            self._transition(identifier, "running")
        return [identifier for identifier, state in self.states.items() if state["status"] == "running"]

    def complete(self, identifier):
        if self.states[identifier]["status"] != "running":
            raise ValueError(f"{identifier}: only a running node can complete")
        self._transition(identifier, "succeeded")

    def fail(self, identifier, reason, *, time_s=None):
        if time_s is not None:
            self.set_time(time_s)
        if self.states[identifier]["status"] not in TERMINAL_STATES:
            self._transition(identifier, "failed", reason)

    def cancel_remaining(self, reason):
        for identifier, state in self.states.items():
            if state["status"] not in TERMINAL_STATES:
                self._transition(identifier, "cancelled", reason)

    def elapsed_running(self, identifier):
        started = self.states[identifier]["started_at_s"]
        return 0.0 if started is None else self.time_s - started

    def report(self):
        return {"policy": "deterministic_plan_order_atomic_resources",
                "timeout_clock": "simulation_time_since_prerequisites_satisfied_including_resource_wait",
                "failure_policy": "declared_recovery_then_cancel_remaining",
                "time_s": self.time_s,
                "states": {identifier: dict(state) for identifier, state in self.states.items()},
                "recovery_requirements": {node: list(members) for node, members in self.recoveries.items()},
                "recovered_by": self.recovered_by,
                "recovered_nodes": sorted(self.recovered_by),
                "skipped_nodes": sorted(node for node, state in self.states.items()
                                        if state["status"] == "skipped"),
                "resource_requirements": {
                    identifier: sorted(node_resources(entry))
                    for identifier, entry in self.entries.items()
                },
                "reservations": dict(self.reservations), "events": list(self.events)}
