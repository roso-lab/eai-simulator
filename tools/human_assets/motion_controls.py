"""Capability-aware keyboard state for the unified human validation demo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


MOTION_NUMBER_CATALOG = (
    "bow",
    "jog",
    "dance",
    "walk_and_look",
    "walk_backward",
    "walk",
    "phone_call",
    "long_stride_walk",
    "walk_and_text",
    "stagger_walk",
    "hit_reaction_retreat",
    "forward_dive",
)


@dataclass(frozen=True)
class ActorControlSpec:
    actor_id: str
    asset_id: str
    label: str
    can_play_actions: bool
    path_following: bool


class HumanDemoControlBackend(Protocol):
    def start_action(self, actor_id: str, motion_id: str) -> None: ...

    def start_movement(self, actor_id: str) -> None: ...

    def stop_and_restore(self, actor_id: str) -> None: ...


@dataclass(frozen=True)
class HumanDemoControlResult:
    buffer: str
    message: str
    selected_actor: ActorControlSpec
    submitted_motion_id: str | None
    started_movement: bool
    should_quit: bool


class HumanDemoControls:
    def __init__(
        self,
        backend: HumanDemoControlBackend,
        actors: Sequence[ActorControlSpec],
    ) -> None:
        actor_specs = tuple(actors)
        if not actor_specs:
            raise ValueError("at least one actor control spec is required")
        actor_ids = [actor.actor_id for actor in actor_specs]
        if len(set(actor_ids)) != len(actor_ids):
            raise ValueError("actor control IDs must be unique")
        self.backend = backend
        self.actors = actor_specs
        self._selected_index = 0
        self._buffer = ""
        self.should_quit = False

    @property
    def buffer(self) -> str:
        return self._buffer

    @property
    def selected_actor(self) -> ActorControlSpec:
        return self.actors[self._selected_index]

    def _result(
        self,
        message: str = "",
        *,
        motion_id: str | None = None,
        started_movement: bool = False,
    ) -> HumanDemoControlResult:
        return HumanDemoControlResult(
            buffer=self._buffer,
            message=message,
            selected_actor=self.selected_actor,
            submitted_motion_id=motion_id,
            started_movement=started_movement,
            should_quit=self.should_quit,
        )

    def _submit(self) -> HumanDemoControlResult:
        if not self._buffer:
            return self._result("Motion number is empty")
        number = int(self._buffer)
        self._buffer = ""
        actor = self.selected_actor

        if actor.can_play_actions:
            if not 1 <= number <= len(MOTION_NUMBER_CATALOG):
                return self._result(f"Invalid motion number: {number}")
            motion_id = MOTION_NUMBER_CATALOG[number - 1]
            self.backend.start_action(actor.actor_id, motion_id)
            return self._result(
                f"Playing {number}: {motion_id}", motion_id=motion_id
            )

        if actor.path_following:
            if number != 1:
                return self._result("Movable rigid actors only support movement 1")
            self.backend.start_movement(actor.actor_id)
            return self._result("Starting movement 1", started_movement=True)

        return self._result(f"Static actor '{actor.label}' does not support actions")

    def handle_key(self, key: str) -> HumanDemoControlResult:
        key = str(key).upper()
        if len(key) == 1 and key.isdigit():
            if len(self._buffer) >= 2:
                return self._result("Motion number accepts at most two digits")
            self._buffer += key
            return self._result(f"Motion number: {self._buffer}")

        if key == "BACKSPACE":
            self._buffer = self._buffer[:-1]
            return self._result(f"Motion number: {self._buffer or '(empty)'}")

        if key == "ENTER":
            return self._submit()

        if key == "Q":
            previous = self.selected_actor
            self._buffer = ""
            self.backend.stop_and_restore(previous.actor_id)
            self._selected_index = (self._selected_index + 1) % len(self.actors)
            selected = self.selected_actor
            return self._result(f"Selected {selected.label} ({selected.asset_id})")

        if key == "X":
            self._buffer = ""
            self.backend.stop_and_restore(self.selected_actor.actor_id)
            return self._result("Stopped and restored selected actor")

        if key == "ESCAPE":
            self.should_quit = True
            return self._result("Closing demo")

        return self._result()


__all__ = [
    "MOTION_NUMBER_CATALOG",
    "ActorControlSpec",
    "HumanDemoControlBackend",
    "HumanDemoControlResult",
    "HumanDemoControls",
]
