"""Small native Kit replay toolbar; callbacks never write simulation state."""

from __future__ import annotations

from typing import Any


class NativeReplayWindow:
    def __init__(self, controller: Any):
        import carb.input
        import omni.appwindow
        import omni.ui as ui

        self.controller = controller
        self.ui = ui
        self.window = ui.Window("EAI Physics Checkpoints", width=540, height=250)
        self.input = carb.input.acquire_input_interface()
        self.keyboard = omni.appwindow.get_default_app_window().get_keyboard()
        self.keys = {
            carb.input.KeyboardInput.F5: "save",
            carb.input.KeyboardInput.F9: "restore",
            carb.input.KeyboardInput.F8: "pause",
            carb.input.KeyboardInput.F10: "step",
        }
        self.press_type = carb.input.KeyboardEventType.KEY_PRESS
        self.subscription = self.input.subscribe_to_keyboard_events(self.keyboard, self._on_key)
        self._signature = None
        self._status = None
        self._detail = None
        self.refresh()

    def _on_key(self, event: Any, *args: Any) -> bool:
        if event.type == self.press_type and event.input in self.keys:
            self.controller.request(self.keys[event.input])
            return False
        return True

    def _build(self) -> None:
        ui = self.ui
        with self.window.frame:
            with ui.VStack(spacing=6):
                self._status = ui.Label("", height=24)
                with ui.HStack(height=30, spacing=4):
                    ui.Button("Save [F5]", clicked_fn=lambda: self.controller.request("save"))
                    ui.Button("Restore [F9]", clicked_fn=lambda: self.controller.request("restore"))
                    ui.Button("Pause/Run [F8]", clicked_fn=lambda: self.controller.request("pause"))
                    ui.Button("Step [F10]", clicked_fn=lambda: self.controller.request("step"))
                checkpoints = self.controller.backend.checkpoints
                if checkpoints:
                    identifiers = [item.identifier for item in checkpoints]
                    selected = identifiers.index(self.controller.selected) if self.controller.selected in identifiers else 0
                    choices = [f"{item.label} | step {item.step_index}" for item in checkpoints]
                    selector = ui.ComboBox(selected, *choices, height=26)
                    selector.model.add_item_changed_fn(
                        lambda model, item: self.controller.request(
                            "select", identifiers[model.get_item_value_model().as_int]
                        )
                    )
                else:
                    ui.Label("Task milestones save automatically. F5 adds a manual checkpoint.", height=26)
                self._detail = ui.Label("", word_wrap=True, height=54)
                ui.Button("Verify 20 steps (runs twice, then pauses)", clicked_fn=lambda: self.controller.request("verify", 20), height=26)
                ui.Label(
                    "Restore stays PAUSED. Read-back is checked; PhysX contact cache is NOT serialized.",
                    word_wrap=True, height=38,
                )

    def refresh(self) -> None:
        signature = (tuple(item.identifier for item in self.controller.backend.checkpoints), self.controller.selected)
        if signature != self._signature:
            self._signature = signature
            self._build()
        phase = "BLOCKED" if self.controller.blocked else "PAUSED" if self.controller.paused else "RUNNING"
        self._status.text = f"{phase} | step {self.controller.step_index} | tolerance {self.controller.backend.tolerance:g}"
        self._detail.text = self.controller.message

    def close(self) -> None:
        if self.subscription is not None:
            self.input.unsubscribe_to_keyboard_events(self.keyboard, self.subscription)
            self.subscription = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
