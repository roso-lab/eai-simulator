"""Scene-only Env DIY selections: a scene with zero robots must run."""

from __future__ import annotations

import pytest

from EAI.hmrs_env.env_diy.flow import (
    choose_terminal_interactive_selection,
    interactive_selection_from_dict,
    interactive_selection_to_dict,
    parse_robot_digit_selection,
)
from EAI.hmrs_env.env_diy.storage import load_task, save_task, task_from_visual_state


def test_parse_robot_digit_selection_accepts_scene_only_tokens() -> None:
    assert parse_robot_digit_selection("0") == ()
    assert parse_robot_digit_selection("none") == ()


def test_parse_robot_digit_selection_still_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        parse_robot_digit_selection("999")
    with pytest.raises(ValueError):
        parse_robot_digit_selection("carterx")


def test_scene_only_selection_round_trip() -> None:
    selection = interactive_selection_from_dict({"scene_key": "plane", "robots": []})
    assert selection.scene_key == "plane"
    assert selection.robots == ()
    data = interactive_selection_to_dict(selection)
    assert data["scene_key"] == "plane"
    assert data["robots"] == []


def test_missing_scene_is_still_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        save_task("scene_only_check", {"robots": []}, repo_root=tmp_path)
    with pytest.raises(KeyError):
        interactive_selection_from_dict({"robots": []})


def test_storage_round_trip_scene_only(tmp_path) -> None:
    save_task("scene_only_check", {"scene_key": "plane", "robots": []}, repo_root=tmp_path)
    loaded = load_task("scene_only_check", repo_root=tmp_path)
    assert loaded["scene_key"] == "plane"
    assert loaded["robots"] == []


def test_task_from_visual_state_scene_only() -> None:
    payload = task_from_visual_state("scene_only_check", {"scene": "plane", "robots": []})
    assert payload["scene_key"] == "plane"
    assert payload["robots"] == []


def test_terminal_flow_completes_scene_only() -> None:
    inputs = iter(["1", "0", ""])
    lines: list[str] = []
    selection = choose_terminal_interactive_selection(
        input_func=lambda _prompt: next(inputs),
        print_func=lines.append,
    )
    assert selection is not None
    assert selection.scene_key == "plane"
    assert selection.robots == ()
    assert interactive_selection_to_dict(selection)["robots"] == []


def test_authoring_model_exports_scene_only() -> None:
    from EAI_env_diy.model import AuthoringModel

    model = AuthoringModel("plane")
    selection = model.to_selection()
    assert selection.scene_key == "plane"
    assert selection.robots == ()
    assert model.to_selection_dict()["robots"] == []
