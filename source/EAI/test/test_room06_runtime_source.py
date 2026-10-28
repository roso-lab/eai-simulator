from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from EAI.room06_runtime_source import Room06RuntimeSourceError, create_room06_runtime_source
from test_room06_runtime_hook import _fixture

ROOT = Path(__file__).resolve().parents[3]


def test_source_binds_logical_definitions_without_usd_objects():
    session, package, agents = _fixture()
    source = create_room06_runtime_source(base_env=session.base_env, package=package, instance_agent_map=agents)
    assert len(source.object_bindings(package=package, instance_agent_map=agents)) == 7
    with pytest.raises(Room06RuntimeSourceError, match="refusing synthetic"):
        source.collect_room06(package=package, instance_agent_map=agents, collector=object())


def test_runtime_source_loads_through_actual_file_loader():
    from tools.validation.room06_livt04_gpu_probe import _load_runtime_hook
    path = ROOT / "source/EAI/EAI/room06_runtime_source.py"
    factory = _load_runtime_hook({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "factory": "create_room06_runtime_source"})
    assert callable(factory)


@pytest.mark.parametrize("callback", ["object_bindings", "collect_room06"])
def test_changed_package_with_same_ids_rejected(callback):
    from benchmark.records_v3 import canonical_digest
    session, package, agents = _fixture()
    source = create_room06_runtime_source(base_env=session.base_env, package=package, instance_agent_map=agents)
    changed = deepcopy(package)
    changed["task_setup"]["objects"][1]["definition"]["mass_kg"] = 9
    changed["package_digest"] = canonical_digest({k: v for k, v in changed.items() if k != "package_digest"})
    with pytest.raises(Room06RuntimeSourceError, match="task package changed"):
        getattr(source, callback)(package=changed, instance_agent_map=agents, collector=object())


def test_source_rejects_missing_host_logical_runtime():
    session, package, agents = _fixture()
    source = create_room06_runtime_source(base_env=SimpleNamespace(), package=package, instance_agent_map=agents)
    with pytest.raises(Room06RuntimeSourceError, match="not registered"):
        source.object_bindings(package=package, instance_agent_map=agents)
