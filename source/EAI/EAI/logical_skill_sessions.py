"""Host-owned logical skill sessions, authenticated by live controller reads.

No task rigid body or attachment exists. A local operation is an atomic host
session at an arrived semantic point. Transport membership persists until a
release/transfer or observed controller failure; it never requires remaining
at the pickup point. This owner does not navigate or advance physics.
"""
from copy import deepcopy
from dataclasses import asdict

from task.logical_objects import ACQUIRE, RELEASE, ControllerObservation, SkillSessionResult, digest, tick
from .logical_object_controller import _vector


class IsaacLogicalSkillSessions:
    def __init__(self, *, base_env, instance_agent_map, arrival_reader):
        if not callable(arrival_reader):
            raise TypeError("live arrival reader required")
        self.env, self.agents = base_env, dict(instance_agent_map)
        self.arrival = arrival_reader
        self.members = {}
        self.retired = {}
        self.records = {}
        self.requests = {}
        self.generation = self._generation()

    def _generation(self):
        values = self.env._manipulation_reset_generation
        if len(values) != 1:
            raise ValueError("single environment reset generation required")
        return tick(values[0])

    @staticmethod
    def _flag(value):
        for method in ('detach', 'cpu', 'tolist'):
            if callable(getattr(value, method, None)):
                value = getattr(value, method)()
        if not isinstance(value, (list, tuple)) or len(value) != 1 or type(value[0]) is not bool:
            raise ValueError("lifecycle flag must contain one boolean")
        return value[0]

    def _health(self, robot):
        agent = self.agents[robot]
        if self.env.num_envs != 1:
            raise ValueError("logical skill session requires single environment")
        state = _vector(self.env.scene.articulations[agent].data.root_state_w[0], 13)
        generation = self._generation()
        lifecycle = {"reset": self._flag(self.env.reset_buf)}
        for field in ('terminated_dict', 'time_out_dict'):
            rows = getattr(self.env, field, None)
            # DirectMARLEnv creates these dictionaries on its first step.
            if rows is None and tick(self.env.common_step_counter) == 0:
                lifecycle[field] = False
            else:
                lifecycle[field] = self._flag(rows[agent])
        active = generation == self.generation and not any(lifecycle.values())
        if not active:
            self.cancel_robot(robot)
        return {"robot_id": robot, "agent": agent, "root_state": state,
                "reset_generation": generation, "lifecycle": lifecycle, "active": active}

    def run(self, command):
        command = deepcopy(command)
        if set(command) != {"request_id", "operation", "object_id", "robot_ids", "semantic_point"}:
            raise ValueError("logical session command schema mismatch")
        request = command["request_id"]
        if request in self.requests:
            raise ValueError("host session request already consumed")
        operation = command["operation"]
        if operation not in ACQUIRE | RELEASE | {"join", "leave"}:
            raise ValueError("unsupported logical session operation")
        robots = command["robot_ids"]
        if not robots or len(set(robots)) != len(robots) or set(robots) - self.agents.keys():
            raise ValueError("invalid logical session participants")
        now = tick(self.env.common_step_counter)
        observations = []
        for robot in robots:
            observation = self.arrival(robot, command["semantic_point"])
            if (not isinstance(observation, ControllerObservation) or observation.arrived is not True
                    or type(observation.tick) is not int or observation.tick != now
                    or observation.robot_id != robot or observation.semantic_point != command["semantic_point"]):
                raise ValueError("logical session requires live controller arrival")
            health = self._health(robot)
            if not health["active"]:
                raise ValueError("terminated or reset controller cannot complete a skill")
            observations.append({"arrival": asdict(observation), "health": health})
        if tick(self.env.common_step_counter) != now:
            raise ValueError("simulation advanced during logical skill session")
        evidence = {"command": command, "tick": now, "observations": observations}
        key = digest(evidence)
        session = "logical-session:" + key
        obj = command["object_id"]
        if operation == "receive":
            self.retired.update({(r, value[0], value[1]): now
                                 for r, value in self.members.items() if value[1] == obj})
            self.members = {r: value for r, value in self.members.items() if value[1] != obj}
        if operation in ACQUIRE | {"join"}:
            self.members.update({robot: (session, obj) for robot in robots})
        else:
            for robot in robots:
                previous = self.members.get(robot)
                if previous is not None:
                    self.retired[(robot, previous[0], previous[1])] = now
                self.members.pop(robot, None)
        self.requests[request] = session
        self.records[key] = evidence
        return SkillSessionResult(session, request, tuple(robots), obj, operation,
                                  command["semantic_point"], now, "completed", key)

    def monitor(self, session, robot, obj, at):
        now = tick(self.env.common_step_counter)
        if tick(at) != now:
            raise ValueError("hold observation must use live host tick")
        health = self._health(robot)
        if tick(self.env.common_step_counter) != now:
            raise ValueError("simulation advanced during hold observation")
        # Ledger validates continuity through the release tick before committing
        # the new ownership. A retired session cannot survive the next tick.
        active = health["active"] and (self.members.get(robot) == (session, obj)
                  or self.retired.get((robot, session, obj)) == now)
        evidence = {"session_id": session, "robot_id": robot, "object_id": obj,
                    "tick": now, "active": active, "health": health}
        key = digest(evidence)
        self.records[key] = evidence
        return {k: evidence[k] for k in ("session_id", "robot_id", "object_id", "tick", "active")} | {"evidence_digest": key}

    def cancel_robot(self, robot):
        """Host lifecycle/skill cancellation removes ongoing membership."""
        self.members.pop(robot, None)
        self.retired = {k: v for k, v in self.retired.items() if k[0] != robot}

    def evidence_record(self, key):
        return deepcopy(self.records[key])
