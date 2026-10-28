from __future__ import annotations
from types import SimpleNamespace

import pytest

import simulator


SCHEMA = "heterocoord.eai-room06-runtime-hook-source.v1"


def _source(schema: str = SCHEMA):
    return SimpleNamespace(
        schema=schema,
        object_bindings=lambda **_: [],
        collect_room06=lambda **_: None,
    )


def _args(**kwargs):
    values = dict(
        base_env=SimpleNamespace(),
        env_cfg=SimpleNamespace(),
        config=SimpleNamespace(room06_runtime_hook_source_factory=None),
        env_name="room_06",
        selection_data={"scene_key": "room_06"},
        possible_agents=["carter_1"],
    )
    values.update(kwargs)
    return values


def test_room06_registration_requires_explicit_factory_and_attaches_source():
    source = _source()
    config = SimpleNamespace(room06_runtime_hook_source_factory=lambda **kwargs: source)
    base_env = SimpleNamespace()

    simulator._register_room06_runtime_hook_source(**_args(config=config, base_env=base_env))

    assert base_env.room06_runtime_hook_source is source


def test_room06_registration_is_fail_closed_for_invalid_source():
    config = SimpleNamespace(room06_runtime_hook_source_factory=lambda **kwargs: _source("wrong"))

    with pytest.raises(RuntimeError, match="did not return a valid"):
        simulator._register_room06_runtime_hook_source(**_args(config=config))


def test_legacy_prim_reader_cannot_replace_logical_bindings():
    source = SimpleNamespace(schema=SCHEMA, object_path_bindings=lambda **_: {},
                             collect_room06=lambda **_: None)
    config = SimpleNamespace(room06_runtime_hook_source_factory=lambda **_: source)
    with pytest.raises(RuntimeError, match="logical object_bindings"):
        simulator._register_room06_runtime_hook_source(**_args(config=config))


def test_actual_source_factory_registers_and_reads_frozen_logical_objects():
    from EAI.room06_runtime_source import create_room06_runtime_source
    from test_room06_runtime_hook import _fixture
    session, package, agents = _fixture()
    del session.base_env.room06_runtime_hook_source
    config = SimpleNamespace(room06_runtime_hook_source_factory=lambda **kwargs:
        create_room06_runtime_source(package=package, instance_agent_map=agents, **kwargs))
    simulator._register_room06_runtime_hook_source(**_args(
        config=config, base_env=session.base_env, possible_agents=list(agents.values())))
    source = session.base_env.room06_runtime_hook_source
    assert len(source.object_bindings(package=package, instance_agent_map=agents)) == 7


def test_room06_registration_does_not_synthesize_source_when_factory_missing():
    base_env = SimpleNamespace()

    simulator._register_room06_runtime_hook_source(**_args(base_env=base_env))

    assert not hasattr(base_env, "room06_runtime_hook_source")


def test_non_room06_session_does_not_attach_room06_source():
    source = _source()
    config = SimpleNamespace(room06_runtime_hook_source_factory=lambda **kwargs: source)
    base_env = SimpleNamespace()

    simulator._register_room06_runtime_hook_source(
        **_args(
            base_env=base_env,
            config=config,
            env_name="factory",
            selection_data={"scene_key": "factory"},
        )
    )

    assert not hasattr(base_env, "room06_runtime_hook_source")
