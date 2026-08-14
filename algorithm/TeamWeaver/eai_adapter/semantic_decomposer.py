from __future__ import annotations

from dataclasses import dataclass
import json
import os
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from TeamWeaver.eai_adapter.capability_ontology import (
    PlanValidationError,
    _get_capabilities,
    validate_plan_payload,
)
from TeamWeaver.eai_adapter.task_models import (
    SemanticTask,
    SymbolicWorldState,
    TaskType,
)


DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"

SYSTEM_PROMPT = """You are the semantic mission planner in TeamWeaver.
Convert the incident instruction and current symbolic world state into the
smallest sufficient executable task DAG. Return one JSON object with a tasks
array and no Markdown or explanatory prose. Choose a dynamic number of tasks;
do not reproduce a fixed color-coded task list. Do not assign robots. You may
set preferred_agent only as a soft preference. Use only the supplied task
types, capability names, target references, and robot names. Infer priority,
capability minimums, hard versus soft requirements, dependencies, parallelism,
and bounded duration from the story and current state. Do not claim physical
success: navigation, manipulation, object, and channel facts are authoritative
in the supplied world state and execution feedback. Do not weaken hardware
safety requirements. Omit work that is already factually complete.

When world_state contains an active obstacle with blocking_task_id, return
exactly one remove_obstacle task for that obstacle. It must have priority 1,
can_parallel=true so unrelated tasks may continue. It must not assign or prefer a robot.
The replacement of the blocked task must use exactly blocking_task_id as
its task_id and must list the remove_obstacle task as a prerequisite. Never
return remove_obstacle for an inactive, removed, or non-blocking obstacle."""


@dataclass(frozen=True)
class SemanticDecompositionResult:
    tasks: tuple[SemanticTask, ...]
    source: str
    attempts: int


@dataclass(frozen=True)
class DecompositionAttemptError:
    attempt: int
    error_type: str
    message: str
    validation_errors: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "attempt": self.attempt,
            "error_type": self.error_type,
            "message": self.message,
            "validation_errors": list(self.validation_errors),
        }


class DecompositionError(RuntimeError):
    def __init__(self, errors: Sequence[DecompositionAttemptError]) -> None:
        self.errors = tuple(errors)
        detail = "; ".join(
            f"attempt {item.attempt} {item.error_type}: {item.message}"
            for item in self.errors
        )
        super().__init__(f"DeepSeek semantic decomposition failed: {detail}")


class DecompositionCancelled(RuntimeError):
    pass


class DeepSeekSemanticDecomposer:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        client_factory: Callable[..., Any] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        system_prompt_extra: str = "",
    ) -> None:
        self._env = os.environ if env is None else env
        self._client_factory = client_factory
        self._sleep_fn = sleep_fn
        self._uses_default_sleep = sleep_fn is time.sleep
        self._cancelled = threading.Event()
        self._transport_lock = threading.Lock()
        self._active_http_client: Any | None = None
        self._system_prompt_extra = str(system_prompt_extra)

    def cancel(self) -> None:
        self._cancelled.set()
        with self._transport_lock:
            http_client = self._active_http_client
        if http_client is not None:
            try:
                http_client.close()
            except Exception:
                pass

    def decompose(
        self,
        instruction: str,
        world: SymbolicWorldState,
        *,
        max_attempts: int = 1,
        retry_delays: Sequence[float] = (),
    ) -> SemanticDecompositionResult:
        self._raise_if_cancelled()
        api_key = self._env.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY is required for TeamWeaver semantic decomposition"
            )
        normalized_instruction = str(instruction).strip()
        if not normalized_instruction:
            raise ValueError("instruction must be non-empty")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ValueError("max_attempts must be a positive integer")

        base_url = (
            self._env.get("TEAMWEAVER_DEEPSEEK_BASE_URL", "").strip()
            or DEFAULT_BASE_URL
        )
        model = (
            self._env.get("TEAMWEAVER_DEEPSEEK_MODEL", "").strip()
            or DEFAULT_MODEL
        )
        client = None
        http_client = None
        errors: list[DecompositionAttemptError] = []
        repair_messages: list[dict[str, str]] = []
        try:
            client_kwargs: dict[str, object] = {
                "api_key": api_key,
                "base_url": base_url,
                "timeout": 60.0,
            }
            client_factory = self._client_factory
            if client_factory is None:
                import httpx
                from openai import OpenAI

                http_client = httpx.Client(timeout=60.0, trust_env=False)
                with self._transport_lock:
                    self._raise_if_cancelled()
                    self._active_http_client = http_client
                client_kwargs["http_client"] = http_client
                client_factory = OpenAI
            client = client_factory(**client_kwargs)

            for attempt in range(1, max_attempts + 1):
                self._raise_if_cancelled()
                try:
                    response = client.chat.completions.create(
                        model=model,
                        messages=self.build_messages(
                            normalized_instruction,
                            world,
                            repair_messages=repair_messages,
                        ),
                        temperature=0.0,
                        max_tokens=2400,
                        response_format={"type": "json_object"},
                    )
                    content = response.choices[0].message.content
                    payload = json.loads(_strip_json_fence(content or ""))
                    tasks = validate_plan_payload(payload, world)
                    return SemanticDecompositionResult(
                        tasks=tasks,
                        source="deepseek",
                        attempts=attempt,
                    )
                except Exception as exc:
                    if self._cancelled.is_set():
                        raise DecompositionCancelled(
                            "DeepSeek semantic decomposition was cancelled"
                        ) from exc
                    validation_errors = (
                        tuple(exc.errors)
                        if isinstance(exc, PlanValidationError)
                        else (str(exc),)
                    )
                    errors.append(
                        DecompositionAttemptError(
                            attempt=attempt,
                            error_type=type(exc).__name__,
                            message=str(exc),
                            validation_errors=validation_errors,
                        )
                    )
                    if attempt >= max_attempts:
                        break
                    repair_payload = {
                        "validation_errors": list(validation_errors),
                        "instruction": "Return a corrected complete JSON object.",
                    }
                    obstacle_contract = _active_obstacle_contract(world)
                    if obstacle_contract is not None:
                        repair_payload["active_obstacle_contract"] = (
                            obstacle_contract
                        )
                    repair_messages.append(
                        {
                            "role": "user",
                            "content": json.dumps(
                                repair_payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    )
                    delay_index = attempt - 1
                    if delay_index < len(retry_delays):
                        self._retry_delay(float(retry_delays[delay_index]))
        finally:
            if http_client is not None:
                with self._transport_lock:
                    if self._active_http_client is http_client:
                        self._active_http_client = None
                try:
                    http_client.close()
                except Exception:
                    pass
        raise DecompositionError(errors)

    def _retry_delay(self, delay_s: float) -> None:
        if self._uses_default_sleep:
            if self._cancelled.wait(timeout=delay_s):
                self._raise_if_cancelled()
            return
        self._sleep_fn(delay_s)
        self._raise_if_cancelled()

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise DecompositionCancelled(
                "DeepSeek semantic decomposition was cancelled"
            )

    def build_messages(
        self,
        instruction: str,
        world: SymbolicWorldState,
        *,
        repair_messages: Sequence[Mapping[str, str]] = (),
    ) -> list[dict[str, str]]:
        world_payload = world.to_payload()
        visible_targets = [
            target
            for target in world.targets
            if not target.kind.startswith("system_")
        ]
        world_payload["targets"] = [
            target.to_payload() for target in visible_targets
        ]
        available_targets = [
            target.to_payload()
            for target in visible_targets
            if target.available
        ]
        user_payload = {
            "incident_instruction": instruction,
            "world_state": world_payload,
            "allowed_task_types": [item.value for item in TaskType],
            "capability_ontology": sorted(_get_capabilities()),
            "allowed_target_refs": [item["ref"] for item in available_targets],
            "available_targets": available_targets,
            "response_schema": {
                "tasks": [
                    {
                        "task_id": "unique string",
                        "task_type": "allowed task type",
                        "description": "natural-language action",
                        "target_ref": "allowed target ref",
                        "priority": "integer 1..5; 1 is highest",
                        "requirements": {
                            "capability_name": {
                                "minimum": "number 0..1",
                                "weight": "number 0..1",
                                "hard": "boolean",
                            }
                        },
                        "prerequisites": ["task_id"],
                        "can_parallel": "boolean",
                        "estimated_duration_s": "number 1..600",
                        "preferred_agent": "robot name or null",
                    }
                ]
            },
            "execution_feedback": world_payload["recent_feedback"],
        }
        obstacle_contract = _active_obstacle_contract(world)
        if obstacle_contract is not None:
            user_payload["active_obstacle_contract"] = obstacle_contract
        system_content = SYSTEM_PROMPT
        if self._system_prompt_extra:
            system_content += "\n\n" + self._system_prompt_extra
        messages = [
            {"role": "system", "content": system_content},
            {
                "role": "user",
                "content": json.dumps(
                    user_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
        messages.extend(
            {"role": str(item["role"]), "content": str(item["content"])}
            for item in repair_messages
        )
        return messages


def _active_obstacle_contract(
    world: SymbolicWorldState,
) -> dict[str, object] | None:
    obstacle = world.active_obstacle()
    if obstacle is None or obstacle.blocking_task_id is None:
        return None
    return {
        "obstacle_id": obstacle.obstacle_id,
        "blocked_task_id": obstacle.blocking_task_id,
        "required_remove_task_type": TaskType.REMOVE_OBSTACLE.value,
        "required_can_parallel": True,
        "replacement_task_id": obstacle.blocking_task_id,
        "replacement_prerequisite": (
            "the task_id of the remove_obstacle task"
        ),
    }


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise ValueError("Incomplete Markdown JSON fence")
    return "\n".join(lines[1:-1]).strip()
