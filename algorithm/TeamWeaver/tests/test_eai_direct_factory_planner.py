from types import SimpleNamespace


class SuccessfulDecomposer:
    def decompose(self, hazard_id):
        from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

        return SimpleNamespace(
            tasks=build_factory_task_specs(hazard_id),
            source="deepseek",
        )


def test_direct_factory_planner_combines_decomposition_and_allocation():
    from TeamWeaver.eai_adapter.direct_factory_planner import FactoryTeamPlanner
    from TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator, RobotState

    robots = [
        RobotState(str(index), (float(index), 0.0), {"navigation": 1.0})
        for index in range(4)
    ]
    result = FactoryTeamPlanner(
        decomposer=SuccessfulDecomposer(),
        allocator=DynamicFactoryAllocator(prefer_miqp=False),
    ).plan(hazard_id=1, robots=robots)

    assert result.hazard_id == 1
    assert result.decomposition_source == "deepseek"
    assert not hasattr(result, "decomposition_fallback_reason")
    assert result.allocation.solver == "hungarian"
    assert len(result.allocation.assignments) == 4


def test_direct_factory_planner_propagates_decomposition_error():
    import pytest
    from TeamWeaver.eai_adapter.direct_factory_planner import FactoryTeamPlanner

    class FailingDecomposer:
        def decompose(self, _hazard_id):
            raise RuntimeError("deepseek unavailable")

    class NeverAllocator:
        def allocate(self, *_args):
            raise AssertionError("allocator must not run")

    with pytest.raises(RuntimeError, match="deepseek unavailable"):
        FactoryTeamPlanner(
            decomposer=FailingDecomposer(),
            allocator=NeverAllocator(),
        ).plan(hazard_id=1, robots=[])


def test_legacy_direct_planner_requires_explicit_legacy_decomposer():
    import pytest
    from TeamWeaver.eai_adapter.direct_factory_planner import FactoryTeamPlanner

    with pytest.raises(RuntimeError, match="explicit legacy decomposer"):
        FactoryTeamPlanner()


def test_legacy_direct_planner_is_not_exported_as_active_adapter_api():
    import TeamWeaver.eai_adapter as adapter

    assert not hasattr(adapter, "FactoryTeamPlanner")
