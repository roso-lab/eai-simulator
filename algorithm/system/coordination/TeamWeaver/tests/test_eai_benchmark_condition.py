from algorithm.system.coordination.emos.policy import AssignmentRequest
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request
from algorithm.system.coordination.TeamWeaver.eai_adapter.benchmark_condition import (
    ALGORITHM_ID,
    assign,
    build_fire_rescue_world,
    condition_manifest,
)


def _request(**changes):
    base = build_fire_rescue_assignment_request()
    values = {field: getattr(base, field) for field in base.__dataclass_fields__}
    values.update(changes)
    return AssignmentRequest(**values)


def test_manifest_identifies_adapted_scope_and_no_checkpoint():
    manifest = condition_manifest()
    assert manifest["algorithm_id"] == ALGORITHM_ID
    assert manifest["implementation"] == "adapted"
    assert manifest["checkpoint"] == "not_applicable_no_learned_weights"
    assert manifest["execution_scope"] == "allocation_only_fire_rescue_hook"
    assert "D2_paired_communication_retention" in manifest["unsupported_dimensions"]


def test_full_pipeline_condition_can_be_declared_explicitly():
    manifest = condition_manifest(
        execution_scope="full_pipeline",
        model_or_api="deepseek:test-model",
    )
    assert manifest["execution_scope"] == "full_pipeline"
    assert manifest["model_or_api"] == "deepseek:test-model"
    assert manifest["planner"].startswith("DeepSeek_semantic_decomposition")


def test_fire_rescue_hook_uses_dynamic_allocator_and_hard_roles():
    result = assign(_request())
    assert set(result.values()) == {"red", "green", "blue", "yellow"}
    assert result["scout_1"] == "green"
    assert result["m20_1"].startswith(("red", "yellow"))


def test_fire_rescue_hook_respects_excluded_work_and_does_not_reintroduce_it():
    request = _request(excluded_subtask_ids=("yellow",), completed_extinguisher=("m20_2",))
    result = assign(request)
    assert "yellow" not in result.values()
    assert "m20_2" not in result


def test_symbolic_world_exposes_adapter_source():
    world, tasks = build_fire_rescue_world(_request())
    assert world.observations["adapter"] == ALGORITHM_ID
    assert {task.task_id for task in tasks} == {"red", "green", "blue", "yellow"}
