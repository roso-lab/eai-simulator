from demo.benchmark.visual_static import (
    retain_static_physics,
    validate_visual_composition,
)


def test_visual_static_helpers_are_available_without_isaac_imports():
    assert callable(retain_static_physics)
    assert callable(validate_visual_composition)
