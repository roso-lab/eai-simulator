from __future__ import annotations
from types import SimpleNamespace

import pytest

import simulator


SCHEMA = "heterocoord.eai-room06-runtime-hook-source.v1"


def _source(schema: str = SCHEMA):
    return SimpleNamespace(
        schema=schema,
        object_path_bindings=lambda **_: {},
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
