from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import simulator


def _selection(*attachments: str):
    return {
        "robots": [
            {
                "type": "carter",
                "attachments": [{"type": attachment} for attachment in attachments],
            }
        ]
    }


def test_manipulator_attachment_robot_models_includes_ur5_and_z1():
    assert simulator.manipulator_attachment_robot_models(
        _selection("ur5", "z1"),
        possible_agents=["carter_1"],
    ) == (("carter_1", "ur5"), ("carter_1", "z1"))


def test_manipulator_attachment_robot_models_uses_controller_fallback():
    class Z1IkCfg:
        pass

    env_cfg = SimpleNamespace(controllers={"carter_1": (object(), Z1IkCfg())})
    assert simulator.manipulator_attachment_robot_models(
        None,
        possible_agents=["carter_1"],
        env_cfg=env_cfg,
    ) == (("carter_1", "z1"),)


def test_setup_manipulator_graph_manager_registers_each_model(monkeypatch):
    calls = []

    class FakeManager:
        registered_instances = ()

        def setup_robot(self, robot_name, model):
            calls.append((robot_name, model.model))
            return True

        def close(self):
            raise AssertionError("active manager must not be closed")

    manager = FakeManager()
    attached = []
    manipulator_module = ModuleType("EAI.hmrs_ros.manipulator_omnigraph")
    manipulator_module.ManipulatorOmniGraphManager = lambda: manager
    manipulator_module.get_manipulator_graph_manager = lambda env: None
    manipulator_module.attach_manipulator_graph_manager = lambda env, value: attached.append((env, value))
    ur5_module = ModuleType("EAI.hmrs_ros.ur5_omnigraph")
    ur5_module.UR5_MODEL_SPEC = SimpleNamespace(model="ur5")
    z1_module = ModuleType("EAI.hmrs_ros.z1_omnigraph")
    z1_module.Z1_MODEL_SPEC = SimpleNamespace(model="z1")
    monkeypatch.setitem(sys.modules, "EAI.hmrs_ros.manipulator_omnigraph", manipulator_module)
    monkeypatch.setitem(sys.modules, "EAI.hmrs_ros.ur5_omnigraph", ur5_module)
    monkeypatch.setitem(sys.modules, "EAI.hmrs_ros.z1_omnigraph", z1_module)

    base_env = SimpleNamespace()
    result = simulator._setup_manipulator_graph_manager(
        base_env=base_env,
        selection_data=_selection("ur5", "z1"),
        possible_agents=["carter_1"],
        env_cfg=SimpleNamespace(controllers={}),
    )

    assert result is manager
    assert calls == [("carter_1", "ur5"), ("carter_1", "z1")]
    assert attached == [(base_env, manager)]
    assert base_env._manipulator_setup_instances == {
        ("carter_1", "ur5"),
        ("carter_1", "z1"),
    }
