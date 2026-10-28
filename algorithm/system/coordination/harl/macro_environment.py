"""Declared symbolic environment shared by native training and adapter smoke.

No physics or simulator acceptance is represented by macro completion.
"""
from __future__ import annotations

from copy import deepcopy
import numpy as np
from gymnasium.spaces import Box, Discrete

from algorithm.common_coordination.contract import CoordinationTask, ExecutionFeedback, RobotCapability, TaskGraph
from task.contracts import EaiTaskPackage, RobotState
from .native_adapter import CONTRACT, SCHEMA, encode


class EaiMacroEnvironment:
    n_agents = 2

    def __init__(self, seed=11, *, split="train"):
        self.rng = np.random.default_rng(seed)
        self.seed_value, self.split, self.episode = seed, split, 0
        self.observation_space = [Box(-np.inf, np.inf, (9,), dtype=np.float32) for _ in range(2)]
        self.share_observation_space = [Box(-np.inf, np.inf, (18,), dtype=np.float32) for _ in range(2)]
        self.action_space = [Discrete(2), Discrete(2)]
        self.reset()

    def reset(self):
        self.episode += 1
        tasks, robots = [], []
        for actor in CONTRACT["actors"]:
            xy = self.rng.uniform(0, 1, 2)
            target = self.rng.uniform(0, 1, 2)
            robots.append(RobotState(RobotCapability(actor["robot_id"], frozenset(actor["capabilities"])),
                                     (float(xy[0]), float(xy[1]), 0.0)))
            tasks.append(CoordinationTask(actor["task_id"], "Symbolic inspection macro",
                                          frozenset(actor["capabilities"]),
                                          metadata={"target_xy": target.tolist()}))
        self.package = EaiTaskPackage(
            task_id=f"{self.split}-{self.seed_value}-{self.episode}", scene_id=CONTRACT["scene_id"],
            graph=TaskGraph(tuple(tasks), source=SCHEMA), robots=tuple(robots), observation_schema=SCHEMA,
            metadata={"episode_id": str(self.episode), "happo_contract": deepcopy(CONTRACT), "split": self.split},
        )
        self.completed, self.last_completed, self.tick = set(), set(), 0
        return self.observe()

    def observe(self):
        obs, available = encode(self.package, self.package.robots, completed=self.completed,
                                last_completed=self.last_completed, step=min(self.tick, CONTRACT["horizon"]-1))
        return obs, np.repeat(obs.reshape(1, -1), 2, axis=0), available

    def step(self, actions):
        raw = np.asarray(actions)
        if raw.shape != (2, 1) or not np.isfinite(raw).all() or not np.isin(raw, [0, 1]).all():
            raise ValueError("macro actions must be two discrete actor outputs")
        available = self.observe()[2]
        newly = set()
        for i, action in enumerate(raw[:, 0].astype(int)):
            if not available[i, action]:
                raise ValueError("macro action violates availability")
            if action == 1:
                newly.add(CONTRACT["actors"][i]["task_id"])
        self.completed.update(newly)
        self.last_completed = newly
        self.tick += 1
        finished = len(self.completed) == 2
        done = finished or self.tick >= CONTRACT["horizon"]
        reward = len(newly) - 0.05 + 0.5 * finished
        obs, shared, available = self.observe()
        infos = [{"symbolic_only": True, "bad_transition": bool(done and not finished)} for _ in range(2)]
        return obs, shared, np.full((2, 1), reward, dtype=np.float32), np.full(2, done), infos, available

    def close(self):
        pass


class SymbolicMacroExecutor:
    """TaskRunner dispatch recorder; outcome is explicitly nonphysical."""

    def __init__(self):
        self.dispatched = []

    def execute(self, assignment, task, *, scene):
        self.dispatched.append((assignment.robot_id, assignment.task_id))
        return ExecutionFeedback(task.task_id, assignment.robot_id, "macro_completed",
                                 message="Symbolic macro boundary only; no physical execution.")
