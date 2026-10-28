from __future__ import annotations

from types import SimpleNamespace

import pytest

from EAI.room06_runtime_hook import HOOK_SOURCE_SCHEMA
from EAI.room06_runtime_source import (
    Room06RuntimeSourceError,
    create_room06_runtime_source,
)


PATHS = {
    "book_batch_01": "/World/envs/env_0/room06_task/book_batch_01",
    "heavy_object": "/World/envs/env_0/room06_task/heavy_object",
}
PACKAGE = {"task_object_bindings": {"objects": [{"entity_id": key} for key in PATHS]}}
AGENTS = {"hc-rt-006": "go2_1", "hc-rt-020": "iris_1", "hc-rt-021": "pegasus_1"}


class _Stage:
    def __init__(self, paths):
        self.paths = set(paths)

    def GetPrimAtPath(self, path):
        return SimpleNamespace(IsValid=lambda: path in self.paths)


class _Driver:
    def __init__(self):
        self.calls = []

    def collect_room06(self, **kwargs):
        self.calls.append(kwargs)
        return {"assignment": {"tick": 0}, "terminal_inputs": {}}


def test_source_binds_only_generated_task_bodies_and_delegates_to_live_driver():
    driver = _Driver()
    env = SimpleNamespace(stage=_Stage(PATHS.values()), room06_task_driver=driver)
    source = create_room06_runtime_source(
        base_env=env, package=PACKAGE, instance_agent_map=AGENTS
    )
    assert source.schema == HOOK_SOURCE_SCHEMA
    assert source.object_path_bindings(package=dict(PACKAGE), instance_agent_map=AGENTS) == PATHS
    result = source.collect_room06(package=dict(PACKAGE), instance_agent_map=dict(AGENTS), collector=object())
    assert result == {"assignment": {"tick": 0}, "terminal_inputs": {}}
    assert driver.calls[0]["instance_agent_map"] == AGENTS


def test_source_rejects_missing_driver_without_synthetic_observations():
    env = SimpleNamespace(stage=_Stage(PATHS.values()))
    source = create_room06_runtime_source(
        base_env=env, package=PACKAGE, instance_agent_map=AGENTS
    )
    with pytest.raises(Room06RuntimeSourceError, match="refusing synthetic"):
        source.collect_room06(package=dict(PACKAGE), instance_agent_map=dict(AGENTS), collector=object())


def test_source_rejects_missing_generated_body():
    env = SimpleNamespace(stage=_Stage([PATHS["book_batch_01"]]))
    source = create_room06_runtime_source(
        base_env=env, package=PACKAGE, instance_agent_map=AGENTS
    )
    with pytest.raises(Room06RuntimeSourceError, match="rigid body is unavailable"):
        source.object_path_bindings(package=dict(PACKAGE), instance_agent_map=AGENTS)
