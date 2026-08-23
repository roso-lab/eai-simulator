from __future__ import annotations

from demo.fire_rescue.runtime import llm_presets


def test_presets_reference_api_keys_only_by_environment_variable() -> None:
    forbidden = {"api_key", "api_key_default", "llm_api_key_default"}
    for preset in llm_presets.EMOS_LLM_PRESETS.values():
        assert forbidden.isdisjoint(preset)
        assert preset["api_key_env"].strip()


def test_preset_mapping_never_returns_literal_api_key() -> None:
    preset_id = next(iter(llm_presets.EMOS_LLM_PRESETS))
    original = llm_presets.EMOS_LLM_PRESETS[preset_id]
    llm_presets.EMOS_LLM_PRESETS[preset_id] = {
        **original,
        "api_key_default": "must-not-be-forwarded",
    }
    try:
        kwargs = llm_presets.preset_to_group_discussion_kwargs(preset_id)
    finally:
        llm_presets.EMOS_LLM_PRESETS[preset_id] = original

    assert kwargs["llm_api_key_env"] == original["api_key_env"]
    assert "llm_api_key_default" not in kwargs
