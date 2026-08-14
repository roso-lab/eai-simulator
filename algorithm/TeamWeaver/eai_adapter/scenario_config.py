"""Scenario configuration types for the TeamWeaver multi-robot coordination algorithm.

Each scenario (factory fire, search-rescue, logistics, etc.) constructs a
``TeamWeaverScenarioConfig`` dataclass and passes it to the pipeline.  The
algorithm itself contains **no** domain-specific branches — all of that
knowledge lives in the config and its optional callbacks.

The design follows the same principle as :mod:`algorithm.emos`: scenarios are
**data**, not code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class TaskSpec:
    """Declares the invariant properties of one task type.

    Every field here is a **requirement**, not a preference — the validator
    and the feasibility checker both enforce these.
    """

    task_type: str
    """e.g. ``"pick_extinguisher"`` — must match a :class:`TaskType` value."""

    equipment: frozenset[str] = frozenset()
    """Hardware that a robot MUST carry to execute this task type."""

    invariant_requirements: Mapping[str, Mapping[str, object]] = field(
        default_factory=MappingProxyType
    )
    """Hard capability floors per task type.

    *Keys* are capability names (``navigation``, ``manipulation``, …).
    *Values* are dicts with ``minimum``, ``weight``, and ``hard`` keys.
    """

    arrival_radius_m: float = 0.35
    """Navigation arrival radius override for this task type."""

    dwell_s: float = 2.0
    """How long the robot must dwell at the target before the task completes."""

    can_suspend: bool = False
    """Whether a running task of this type can be preempted (suspended)."""


@dataclass(frozen=True)
class TeamWeaverScenarioConfig:
    """Pluggable domain configuration for the TeamWeaver algorithm.

    Every scenario-specific concept — task vocabulary, capability tables,
    world-fact schemas, hook callbacks — lives here.  The algorithm reads
    this config; it never knows about concrete task names or domain rules.

    All fields are optional with sensible defaults so that a minimal
    scenario (navigate + inspect + wait) works out-of-the-box.
    """

    scenario_id: str = ""
    """Human-readable label used in log messages (e.g. ``"factory_fire"``)."""

    # -- Task vocabulary ---------------------------------------------------
    extra_task_types: frozenset[str] = frozenset()
    """Additional :class:`TaskType` values beyond the built-in set
    (``navigate``, ``inspect``, ``wait``)."""

    task_specs: Mapping[str, TaskSpec] = field(default_factory=MappingProxyType)
    """Per-task-type invariant tables (equipment, capability floors, radii)."""

    # -- Capability vocabulary ---------------------------------------------
    capabilities: frozenset[str] = frozenset(
        {"navigation", "sensing", "relay", "payload", "agility",
         "manipulation", "button_press", "extinguisher_handling",
         "obstacle_handling"}
    )
    """Closed set of capability names recognised by the scenario."""

    # -- World fact schema -------------------------------------------------
    world_fact_keys: frozenset[str] = frozenset()
    """Whitelist of keys that may appear in :attr:`SymbolicWorldState.facts`."""

    # -- Pluggable hooks ---------------------------------------------------
    feasibility_filter: (
        Callable[[Any, Any, Any], bool] | None
    ) = None
    """Extra feasibility check beyond ``hard_feasible``.

    ``(robot: RobotSnapshot, task: SemanticTask, world: SymbolicWorldState) -> bool``
    """

    post_decomposition: (
        Callable[[Sequence[Any], Any], tuple[Any, ...]] | None
    ) = None
    """Post-processing hook called after every LLM decomposition.

    ``(tasks: tuple[SemanticTask, ...], world: SymbolicWorldState) -> tuple[SemanticTask, ...]``

    The factory-fire scenario uses this to inject fire-return continuations.
    """

    decomposer_contract: (
        Callable[[Any], dict[str, Any] | None] | None
    ) = None
    """Returns a contract dict inserted into the LLM prompt when an active
    obstacle blocks a task, or ``None`` when no contract is applicable.

    ``(world: SymbolicWorldState) -> dict | None``
    """

    entities_payload_fn: (
        Callable[[Mapping[str, object]], dict[str, object]] | None
    ) = None
    """Renders generic world facts into the ``entities`` section of the
    LLM-facing world-state payload.

    ``(facts: Mapping[str, object]) -> dict[str, object]``
    """

    extra_system_prompt: str = ""
    """Paragraph appended to the DeepSeek system prompt for this scenario."""

    # -- Phase-scheduler domain knobs --------------------------------------
    relay_task_types: frozenset[str] = frozenset()
    """Task types that grant a *relay lease* on success (e.g. ``establish_relay``)."""

    preemptible_task_types: frozenset[str] = frozenset(
        {"navigate", "inspect"}
    )
    """Task types whose navigation can be suspended for obstacle removal."""

    # -- Duration override -------------------------------------------------
    physical_duration_fn: (
        Callable[..., float | None] | None
    ) = None
    """Overrides the LLM's estimated duration with a physically-grounded lower
    bound.  Returns ``None`` to keep the LLM estimate.

    ``(task_type: str, duration_s: float | None, world: SymbolicWorldState,
    errors: list[str]) -> float | None``
    """


# -- Module-level active scenario --------------------------------------------
# The pipeline sets the active scenario before any planning call so that
# stateless utility functions (``hard_feasible``, ``required_equipment``,
# ``validate_plan_payload``) can consult it without threading it through
# every signature.

_active_scenario: TeamWeaverScenarioConfig | None = None


def set_active_scenario(config: TeamWeaverScenarioConfig | None) -> None:
    """Set the active scenario config (called by the pipeline on init)."""
    global _active_scenario
    _active_scenario = config


def get_active_scenario() -> TeamWeaverScenarioConfig | None:
    """Return the currently-active scenario, or ``None``."""
    return _active_scenario
