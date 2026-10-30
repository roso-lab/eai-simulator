from __future__ import annotations


def test_factory_decomposer_compatibility_name_points_to_dynamic_decomposer():
    from TeamWeaver.eai_adapter.factory_decomposer import (
        DeepSeekSemanticDecomposer,
        FactoryTaskDecomposer,
    )

    assert FactoryTaskDecomposer is DeepSeekSemanticDecomposer


def test_factory_decomposer_module_exposes_no_fixed_task_contract():
    import TeamWeaver.eai_adapter.factory_decomposer as module

    assert not hasattr(module, "EXPECTED_TASK_IDS")
    assert not hasattr(module, "_parse_task_ids")
    assert not hasattr(module, "_factory_messages")
