"""GUI cleanup must release callbacks before the Kit manager/ USD are gone."""
import sys
from types import SimpleNamespace

import pytest
import simulator


def test_existing_legacy_hooks_released_while_manager_alive(monkeypatch):
    events = []

    class Hook:
        def __del__(self):
            events.append("release_hook")

    class EditorMenu:
        def __init__(self):
            self._hooks = [(Hook(), Hook())]

    menu = EditorMenu()
    module = SimpleNamespace(Menu=SimpleNamespace(static_class=menu), EditorMenu=EditorMenu)
    monkeypatch.setitem(sys.modules, "omni.kit.ui", module)
    manager = SimpleNamespace(is_extension_enabled=lambda name: True,
        set_extension_enabled_immediate=lambda name, enabled: events.append((name, enabled)))
    app = SimpleNamespace(app=SimpleNamespace(get_extension_manager=lambda: manager))
    simulator._prepare_gui_for_app_close(app)
    assert events == [("isaacsim.gui.menu", False), "release_hook", "release_hook"]
    assert menu._hooks == []
    simulator._prepare_gui_for_app_close(app)
    assert events.count("release_hook") == 2


def test_headless_cleanup_does_not_import_or_create_gui(monkeypatch):
    monkeypatch.delitem(sys.modules, "omni.kit.ui", raising=False)
    simulator._prepare_gui_for_app_close(object())
    assert "omni.kit.ui" not in sys.modules


def test_menu_removal_failure_still_releases_native_hooks(monkeypatch):
    class EditorMenu:
        _hooks = [object()]

    menu = EditorMenu()
    monkeypatch.setitem(sys.modules, "omni.kit.ui", SimpleNamespace(
        EditorMenu=EditorMenu, Menu=SimpleNamespace(static_class=menu)))
    simulator._prepare_gui_for_app_close(object())
    assert menu._hooks == []


def test_workspace_callbacks_released_even_without_legacy_menu(monkeypatch):
    callbacks = {"Browser": object(), "Hidden window": object()}
    removed = []

    def clear(title, callback):
        assert callback is None
        callbacks.pop(title)
        removed.append(title)

    monkeypatch.delitem(sys.modules, "omni.kit.ui", raising=False)
    monkeypatch.setitem(sys.modules, "omni.ui", SimpleNamespace(Workspace=SimpleNamespace(
        get_show_window_titles=lambda: callbacks.keys(), set_show_window_fn=clear)))
    simulator._prepare_gui_for_app_close(object())
    assert not callbacks
    assert removed == ["Browser", "Hidden window"]


@pytest.mark.parametrize("fail_enumeration", [False, True])
def test_workspace_failure_does_not_skip_other_cleanup(monkeypatch, fail_enumeration):
    events = []

    def titles():
        if fail_enumeration:
            raise RuntimeError("workspace unavailable")
        return ["broken", "working"]

    def clear(title, callback):
        if title == "broken":
            raise RuntimeError("window unavailable")
        events.append(title)

    class EditorMenu:
        def __init__(self):
            self._hooks = [object()]

    menu = EditorMenu()
    monkeypatch.setitem(sys.modules, "omni.ui", SimpleNamespace(Workspace=SimpleNamespace(
        get_show_window_titles=titles, set_show_window_fn=clear)))
    monkeypatch.setitem(sys.modules, "omni.kit.ui", SimpleNamespace(
        EditorMenu=EditorMenu, Menu=SimpleNamespace(static_class=menu)))
    manager = SimpleNamespace(is_extension_enabled=lambda name: True,
        set_extension_enabled_immediate=lambda name, enabled: events.append(name))
    app = SimpleNamespace(app=SimpleNamespace(get_extension_manager=lambda: manager))
    simulator._prepare_gui_for_app_close(app)
    assert events == ([] if fail_enumeration else ["working"]) + ["isaacsim.gui.menu"]
    assert menu._hooks == []


def test_physics_stop_removes_interactive_stop_handler_first():
    events = []

    class Backend:
        def __del__(self):
            events.append("release_native_view")

    view = SimpleNamespace(_backend=Backend(), invalidate=lambda: events.append("invalidate_shared_view"))
    sim = SimpleNamespace(clear_instance=lambda: events.append("unsubscribe"),
                          stop=lambda: events.append("invalidate_views"), physics_sim_view=view)
    env = SimpleNamespace(unwrapped=SimpleNamespace(sim=sim), close=lambda: events.append("close_env"))
    simulator._close_environment_for_app_close(env)
    assert events == ["unsubscribe", "close_env", "invalidate_views", "invalidate_shared_view", "release_native_view"]
    assert view._backend is None
    simulator._close_environment_for_app_close(None)


def test_menu_service_shutdown_without_dependency_cascade(monkeypatch):
    events = []

    class EditorMenu:
        _hooks = []

    monkeypatch.setitem(sys.modules, "omni.kit.ui", SimpleNamespace(
        EditorMenu=EditorMenu, Menu=SimpleNamespace(static_class=EditorMenu())))
    instance = SimpleNamespace(on_shutdown=lambda: events.append("menu_service_closed"))
    monkeypatch.setitem(sys.modules, "omni.kit.menu.utils", SimpleNamespace(get_instance=lambda: instance))
    manager = SimpleNamespace(is_extension_enabled=lambda name: True,
        set_extension_enabled_immediate=lambda name, enabled: events.append(name))
    app = SimpleNamespace(app=SimpleNamespace(get_extension_manager=lambda: manager))
    simulator._prepare_gui_for_app_close(app)
    assert events == ["isaacsim.gui.menu", "menu_service_closed"]


@pytest.mark.parametrize("failure_phase", ["stop", "invalidate"])
def test_invalidated_view_releases_native_owner_even_on_error(failure_phase):
    def fail():
        raise RuntimeError("already invalid")

    view = SimpleNamespace(_backend=object(), invalidate=fail if failure_phase == "invalidate" else lambda: None)
    sim = SimpleNamespace(physics_sim_view=view, clear_instance=lambda: None,
                          stop=fail if failure_phase == "stop" else lambda: None)
    simulator._close_environment_for_app_close(SimpleNamespace(sim=sim, close=lambda: None))
    assert view._backend is None


@pytest.mark.parametrize("close_fails", [False, True])
def test_environment_close_clears_global_view_but_retained_owner_is_released(close_fails):
    view = SimpleNamespace(_backend=object(), invalidate=lambda: None)
    sim = SimpleNamespace(physics_sim_view=view, clear_instance=lambda: None, stop=lambda: None)

    def close():
        sim.physics_sim_view = None
        if close_fails:
            raise RuntimeError("environment cleanup failed")

    env = SimpleNamespace(sim=sim, close=close)
    if close_fails:
        with pytest.raises(RuntimeError, match="environment cleanup failed"):
            simulator._close_environment_for_app_close(env)
    else:
        simulator._close_environment_for_app_close(env)
    assert view._backend is None
