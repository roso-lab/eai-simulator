"""IO boundary for the *upstream* EMOS discussion, not the in-tree rewrite.

The caller supplies real robot resumes and a native scene description (native
coordinates are [x, z, y], with height second). No capabilities, scene entities,
prompts, assignments or successful execution outcomes are invented here.

``initialize(H_r, o_r)`` binds request fields and public observation; ``__call__``
runs the original leader / robot reflection / reassignment loop. Upstream stage
one has no external history/feedback input. Evaluator history is retained, not
silently injected into its prompts. Stage two CrabAgent execution is out of scope.

Use a trusted EMOS checkout on branch embodied_mas, via source_root or
EMOS_SOURCE_ROOT (default ~/.cache/heterocoord/upstream/emos). Source provenance
verification belongs to the benchmark's common source gate. This module loads
the original function ASTs unchanged to avoid importing the Habitat simulator
policy harness. Native OpenAIModel, AgentArguments and interpreter are imported
from that same checkout, under a private package name. Only OpenAI's transport
constructor is bound to the supplied metered endpoint; no global monkeypatches.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Mapping
import uuid

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
from benchmark.emos import CaseAssignmentRequest, EmosDecisionAdapter
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

from .policy import AssignmentRequest, assignment_tasks


DISCUSSION_PATH = "habitat-baselines/habitat_baselines/rl/multi_agent/multi_llm_policy.py"
MODEL_PATH = "habitat-mas/habitat_mas/utils/models.py"
_FUNCTIONS = frozenset({
    "create_leader_prompt", "create_leader_start_message", "create_robot_prompt",
    "create_robot_start_message", "get_text_capabilities", "get_full_capabilities",
    "parse_leader_response", "parse_agent_response", "group_discussion",
})
_CONSTANTS = frozenset({
    "ROBOT_RESUME_TEMPLATE", "NO_MANIPULATION", "NO_MOBILITY", "NO_PERCEPTION",
    "DISCUSSION_TOOLS", "ROBOT_DESCRIPTION",
})


# This is an adapter-owned wire contract.  The upstream EMOS prompts, parsers,
# discussion loop and numerical interpreter remain loaded unchanged below.
OUTPUT_CONTRACT_VERSION = "emos.native-output.v1"
OUTPUT_CONTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["contract_version", "kind", "assignments", "feedback"],
    "properties": {
        "contract_version": {"const": OUTPUT_CONTRACT_VERSION},
        "kind": {"enum": ["assignment", "feedback", "computation"]},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["robot_id", "state", "task_ids", "subtask_description"],
                "properties": {
                    "robot_id": {"type": "string", "minLength": 1},
                    "state": {"enum": ["assigned", "idle", "refused"]},
                    "task_ids": {"type": "array", "items": {"type": "string"}},
                    "subtask_description": {"type": "string"},
                },
            },
        },
        "feedback": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["robot_id", "state", "reason", "python_code"],
            "properties": {
                "robot_id": {"type": "string"},
                "state": {"enum": ["yes", "no", "refused", "compute"]},
                "reason": {"type": ["string", "null"]},
                "python_code": {"type": ["string", "null"]},
            },
        },
    },
}
OUTPUT_CONTRACT_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(OUTPUT_CONTRACT_SCHEMA, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class NativeEmosError(RuntimeError):
    """No decision was produced; there is deliberately no fallback."""


class NativeEmosUnavailable(NativeEmosError):
    """Missing source, dependencies, or explicitly configured model endpoint."""


class NativeOutputScopeError(NativeEmosError):
    """Faithful native output cannot be represented by the current task runner."""

    def __init__(self, message: str, native_subtasks: Mapping[str, str],
                 native_structured_output: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.native_subtasks = deepcopy(dict(native_subtasks))
        self.native_structured_output = deepcopy(native_structured_output)


class MeteredNativeEndpoint:
    """OpenAI SDK transport seam; count *every* native completion attempt.

    Injected clients default to protocol-only evidence. ``from_environment``
    creates a real client with retries disabled, so one attempt is one request.
    Model selection is configuration, not a prompt or decision transformation.
    Provider exceptions are suppressed to avoid exposing credentials/headers.
    """

    def __init__(self, client: Any, *, model: str = "gpt-4o", max_calls: int = 32,
                 reasoning_effort: str | None = None, protocol_only: bool = True,
                 output_contract_version: str | None = None) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        if reasoning_effort is not None and reasoning_effort not in {"low", "medium", "high"}:
            raise ValueError("reasoning_effort must be low, medium, high, or unset")
        self.client = client
        self.model = model
        self.max_calls = max_calls
        self.reasoning_effort = reasoning_effort
        self.protocol_only = bool(protocol_only)
        if output_contract_version not in (None, OUTPUT_CONTRACT_VERSION):
            raise ValueError("unsupported EMOS output contract version")
        self.output_contract_version = output_contract_version
        self.calls = 0
        self.failed_calls = 0
        self.contract_rejections = 0
        self.total_tokens = 0
        self.response_records = []
        self.structured_outputs = []
        self._contract_context = None
        self._active_speaker = None
        self._structured_start_call = 0
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls, *, max_calls: int = 32, timeout: float = 30.0,
                         api_key_env: str | None = None,
                         base_url_env: str | None = None,
                         model_env: str | None = None,
                         reasoning_effort_env: str | None = None,
                         output_contract_version: str | None = None):
        """Use existing environment only; explicit names never fall back to others.

        Explicit base/model references must be populated. The CLI requires all
        three references; legacy callers can still use EMOS/OpenAI defaults.
        """
        for name in (api_key_env, base_url_env, model_env, reasoning_effort_env):
            if name is not None and (not isinstance(name, str) or
                                     not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)):
                raise ValueError("endpoint environment references must be variable names")
        if type(max_calls) is not int or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        key = (os.environ.get(api_key_env) if api_key_env is not None else
               os.environ.get("EMOS_API_KEY") or os.environ.get("OPENAI_API_KEY"))
        if not key:
            raise NativeEmosUnavailable("no configured EMOS/OpenAI model credential")
        base_url = (os.environ.get(base_url_env) if base_url_env is not None else
                    os.environ.get("EMOS_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
        model = (os.environ.get(model_env) if model_env is not None else
                 os.environ.get("EMOS_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o")
        reasoning_effort = (os.environ.get(reasoning_effort_env) if reasoning_effort_env is not None else
                            os.environ.get("EMOS_REASONING_EFFORT") or
                            os.environ.get("OPENAI_REASONING_EFFORT"))
        if base_url_env is not None and not base_url:
            raise NativeEmosUnavailable("configured native base URL environment variable is unset")
        if not model or not model.strip():
            raise NativeEmosUnavailable("configured native model environment variable is unset")
        if reasoning_effort_env is not None and not reasoning_effort:
            raise NativeEmosUnavailable("configured native reasoning effort environment variable is unset")
        if reasoning_effort is not None and reasoning_effort not in {"low", "medium", "high"}:
            raise NativeEmosUnavailable("configured native reasoning effort must be low, medium, or high")
        try:
            from openai import OpenAI
        except ImportError:
            raise NativeEmosUnavailable("native OpenAI SDK is unavailable") from None
        try:
            client = OpenAI(api_key=key, base_url=base_url, timeout=timeout, max_retries=0)
        except Exception:
            raise NativeEmosUnavailable("cannot construct configured native model client") from None
        return cls(client, model=model, max_calls=max_calls,
                   reasoning_effort=reasoning_effort, protocol_only=False,
                   output_contract_version=output_contract_version)

    def bind_output_contract(self, *, robot_ids, task_catalog: Mapping[str, str]) -> None:
        """Bind the caller-declared vocabulary before a native discussion.

        This is context for the wire contract only.  It does not change the
        upstream task text or select/rewrite a task after inference.
        """
        if self.output_contract_version is None:
            self._contract_context = None
            return
        robots = tuple(robot_ids)
        tasks = dict(task_catalog)
        if not robots or len(set(robots)) != len(robots):
            raise ValueError("structured output contract requires unique robot IDs")
        if any(not isinstance(item, str) or not item for item in robots):
            raise ValueError("structured output contract requires string robot IDs")
        if "leader" in robots or any(any(char in item for char in ("{", "}", "|", "\n", "\r")) for item in robots):
            raise ValueError("robot IDs conflict with the native output envelope")
        if not tasks or any(not isinstance(key, str) or not key or not isinstance(value, str)
               or not value for key, value in tasks.items()):
            raise ValueError("structured output contract requires a non-empty task catalog")
        self._contract_context = {"robot_ids": robots, "task_catalog": tasks}

    def contract_metadata(self):
        enabled = self.output_contract_version is not None
        prompts = ({speaker: self._contract_prompt(self._contract_context, speaker)
                    for speaker in ("leader", *self._contract_context["robot_ids"])}
                   if enabled and self._contract_context else {})
        return {
            "version": self.output_contract_version,
            "schema_sha256": OUTPUT_CONTRACT_SCHEMA_SHA256 if enabled else None,
            "prompt_sha256_by_speaker": {key: hashlib.sha256(value.encode()).hexdigest()
                                         for key, value in prompts.items()},
            "catalog": deepcopy(self._contract_context),
            "scope": "adapter-output-format-conditioned-native-planner" if enabled else "original-native-format",
        }

    def clear_structured_outputs(self) -> None:
        self.structured_outputs.clear()
        self._structured_start_call = self.calls

    @staticmethod
    def _contract_prompt(context: Mapping[str, Any], speaker: str | None = None) -> str:
        catalog = [{"task_id": key, "description": context["task_catalog"][key]}
                   for key in sorted(context["task_catalog"])]
        return (
            "\n\nADAPTER OUTPUT CONTRACT (version " + OUTPUT_CONTRACT_VERSION + "):\n"
            "Return exactly one JSON object and no markdown or prose. The object must "
            "have contract_version, kind, assignments, and feedback fields matching "
            "the versioned schema. "
            + ("This is a leader response: use kind=assignment. " if speaker == "leader" else
               "This is robot " + json.dumps(speaker) + ": use kind=feedback or computation. " if speaker else "") +
            "For leader allocation use kind=assignment, include "
            "each robot exactly once, and use only the registered task IDs. Use state "
            "assigned, idle, or refused. Put every task ID assigned to a robot in its "
            "task_ids array; repeat a task ID for every robot in a coalition and do not "
            "resolve or drop coalitions. Idle entries have empty task_ids. Refused entries "
            "retain the IDs being refused, if known. Preserve the description/reason in "
            "subtask_description. For assignment use feedback=null. For robot feasibility "
            "feedback use assignments=[], kind=feedback, feedback.robot_id matching your "
            "identity, state yes/no/refused, reason as text or null, and python_code=null. "
            "If you choose to use the original numerical interpreter, use kind=computation, "
            "assignments=[], feedback.robot_id matching your identity, state=compute, "
            "reason=null and python_code containing exactly your executable Python source "
            "without markdown fences. The original interpreter will execute it and return "
            "its result before your next response. Do not invent IDs. This changes only "
            "response serialization; retain all original allocation, feasibility, reflection "
            "and numerical reasoning instructions. It supersedes only the original brace "
            "output format.\n"
            "Registered robot IDs: " + json.dumps(list(context["robot_ids"]), ensure_ascii=False) + "\n"
            "Registered task catalog: " + json.dumps(catalog, ensure_ascii=False, sort_keys=True) + "\n"
            "Schema: " + json.dumps(OUTPUT_CONTRACT_SCHEMA, sort_keys=True) + "\n"
            "Schema hash: " + OUTPUT_CONTRACT_SCHEMA_SHA256
        )

    @staticmethod
    def _message_content(response):
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            raise NativeEmosError("native model response has no text content") from None

    def _validate_structured(self, raw_content: Any) -> tuple[dict, str]:
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise NativeEmosError("native structured output is empty")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise NativeEmosError("native structured output contains duplicate JSON keys")
                result[key] = value
            return result

        try:
            document = json.loads(raw_content, object_pairs_hook=unique_object)
        except (TypeError, json.JSONDecodeError):
            raise NativeEmosError("native structured output is not valid JSON") from None
        if not isinstance(document, Mapping):
            raise NativeEmosError("native structured output must be a JSON object")
        if document.get("contract_version") != OUTPUT_CONTRACT_VERSION:
            raise NativeEmosError("native structured output has an unsupported version")
        kind = document.get("kind")
        if not isinstance(kind, str) or kind not in {"assignment", "feedback", "computation"}:
            raise NativeEmosError("native structured output has an invalid kind")
        if self._active_speaker == "leader" and kind != "assignment":
            raise NativeEmosError("leader structured output must use assignment kind")
        if self._active_speaker not in (None, "leader") and kind not in {"feedback", "computation"}:
            raise NativeEmosError("robot structured output must use feedback or computation kind")
        if set(document) != {"contract_version", "kind", "assignments", "feedback"}:
            raise NativeEmosError("native structured output has unexpected fields")
        assignments = document["assignments"]
        feedback = document["feedback"]
        context = self._contract_context
        if context is None:
            raise NativeEmosError("structured output context is not bound")
        if not isinstance(assignments, list) or not isinstance(feedback, (Mapping, type(None))):
            raise NativeEmosError("native structured output has invalid fields")
        if kind == "assignment":
            if feedback is not None or len(assignments) != len(context["robot_ids"]):
                raise NativeEmosError("assignment output must contain one entry per robot")
            seen = set()
            known_tasks = set(context["task_catalog"])
            for item in assignments:
                if not isinstance(item, Mapping) or set(item) != {
                    "robot_id", "state", "task_ids", "subtask_description"
                }:
                    raise NativeEmosError("assignment entry does not match the output contract")
                robot_id = item["robot_id"]
                state = item["state"]
                task_ids = item["task_ids"]
                description = item["subtask_description"]
                if not isinstance(robot_id, str) or robot_id not in context["robot_ids"] or robot_id in seen:
                    raise NativeEmosError("assignment output has an unknown or duplicate robot")
                if not isinstance(state, str) or state not in {"assigned", "idle", "refused"}:
                    raise NativeEmosError("assignment output has an invalid state")
                if not isinstance(task_ids, list) or any(
                    not isinstance(task_id, str) or not task_id for task_id in task_ids
                ) or len(set(task_ids)) != len(task_ids):
                    raise NativeEmosError("assignment output has invalid task IDs")
                if set(task_ids) - known_tasks:
                    raise NativeEmosError("assignment output contains an unknown task ID")
                if not isinstance(description, str):
                    raise NativeEmosError("assignment output has an invalid description")
                if state != "idle" and (not description.strip() or description == "Nothing to do"):
                    raise NativeEmosError("non-idle output requires an explicit non-idle description")
                if "```" in description or any(char in robot_id + description for char in ("{", "}", "\n", "\r")):
                    raise NativeEmosError("description or robot ID cannot round-trip the native brace parser")
                if state == "assigned" and not task_ids:
                    raise NativeEmosError("assigned output must retain at least one task ID")
                if state == "idle" and task_ids:
                    raise NativeEmosError("idle output cannot contain task IDs")
                seen.add(robot_id)
            if seen != set(context["robot_ids"]):
                raise NativeEmosError("assignment output does not cover the robot roster")
            legacy = "".join(
                "{" + item["robot_id"] + "||" +
                (item["subtask_description"] if item["state"] != "idle" else "Nothing to do") + "}"
                for item in assignments
            )
        else:
            if assignments or not isinstance(feedback, Mapping) or set(feedback) != {
                "robot_id", "state", "reason", "python_code"
            }:
                raise NativeEmosError("feedback output does not match the output contract")
            if feedback["robot_id"] != self._active_speaker or self._active_speaker not in context["robot_ids"]:
                raise NativeEmosError("feedback robot identity does not match the caller")
            if not isinstance(feedback["state"], str) or feedback["state"] not in {"yes", "no", "refused", "compute"}:
                raise NativeEmosError("feedback output has an invalid state")
            if feedback["reason"] is not None and not isinstance(feedback["reason"], str):
                raise NativeEmosError("feedback output has an invalid reason")
            state = feedback["state"]
            reason = feedback["reason"] or ""
            if kind == "computation":
                code = feedback["python_code"]
                if state != "compute" or feedback["reason"] is not None or not isinstance(code, str) or not code.strip() or "```" in code:
                    raise NativeEmosError("invalid structured computation")
                legacy = "```python\n" + code + "\n```"
            else:
                if state == "compute" or feedback["python_code"] is not None:
                    raise NativeEmosError("feedback must not contain computation")
                if "```" in reason or any(char in reason for char in ("{", "}", "\n", "\r")):
                    raise NativeEmosError("feedback reason cannot round-trip the native brace parser")
                legacy = "{" + ("yes" if state == "yes" else "no||" + reason) + "}"
        return dict(document), legacy

    def _contract_request(self, kwargs: dict) -> tuple[dict, str | None]:
        context = self._contract_context
        if self.output_contract_version is None:
            return kwargs, None
        if context is None or self._active_speaker not in ("leader", *context["robot_ids"]):
            raise NativeEmosError("structured output context/speaker is not bound")
        messages = deepcopy(kwargs.get("messages"))
        if not isinstance(messages, list) or not messages:
            raise NativeEmosError("structured output requires a non-empty messages list")
        instruction = self._contract_prompt(context, self._active_speaker)
        first = messages[0]
        if not isinstance(first, Mapping) or first.get("role") != "system" or not isinstance(first.get("content"), str):
            raise NativeEmosError("structured output requires the native system message")
        messages[0] = {**first, "content": first["content"] + instruction}
        request = dict(kwargs)
        request["messages"] = messages
        return request, hashlib.sha256(instruction.encode()).hexdigest()

    def create(self, **kwargs):
        request = {**kwargs, "model": self.model}
        if self.reasoning_effort is not None:
            request["reasoning_effort"] = self.reasoning_effort
        request, prompt_hash = self._contract_request(request)
        with self._lock:
            if self.calls >= self.max_calls:
                raise NativeEmosError("native model completion budget exhausted")
            self.calls += 1
            call_id = self.calls
        started = time.monotonic()
        record = {"call": call_id, "requested_model": self.model,
                  "reasoning_effort": self.reasoning_effort}
        try:
            record["output_contract_version"] = self.output_contract_version
            if prompt_hash is not None:
                record["contract_prompt_sha256"] = prompt_hash
                record["speaker"] = self._active_speaker
                record["output_contract_schema_sha256"] = OUTPUT_CONTRACT_SCHEMA_SHA256
                record["effective_messages"] = [item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                                                 for item in request["messages"]]
                record["effective_messages_sha256"] = hashlib.sha256(json.dumps(
                    record["effective_messages"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            response = self.client.chat.completions.create(**request)
            # Retain actual provider output even if original parsing later fails.
            # No headers, URL, input credentials or provider exception text.
            record["response"] = response.model_dump(mode="json")
            with self._lock:
                self.total_tokens += int(response.usage.total_tokens)
            if self.output_contract_version is not None:
                if len(response.choices) != 1 or response.choices[0].finish_reason != "stop" or response.choices[0].message.tool_calls or response.choices[0].message.refusal:
                    raise NativeEmosError("structured output requires one complete non-tool response")
                raw_content = self._message_content(response)
                document, legacy_content = self._validate_structured(raw_content)
                record["structured_output"] = deepcopy(document)
                record["native_projection"] = legacy_content
                self.structured_outputs.append({"call": call_id, "document": deepcopy(document),
                                                "raw_content": raw_content,
                                                "speaker": self._contract_speaker})
                response = response.model_copy(deep=True)
                response.choices[0].message.content = legacy_content
            record["status"] = "response_received"
            return response
        except NativeEmosError as exc:
            if record.get("response") is not None:
                record["status"] = "contract_rejected"
                record["contract_error"] = str(exc)
                self.contract_rejections += 1
            raise
        except Exception as exc:
            with self._lock:
                self.failed_calls += 1
            record.update(status="failed", error_type=type(exc).__name__)
            if isinstance(getattr(exc, "status_code", None), int):
                record["http_status"] = exc.status_code
            raise NativeEmosError("native model endpoint failed; no fallback") from None
        finally:
            record["elapsed_wall_s"] = time.monotonic() - started
            with self._lock:
                self.response_records.append(record)

    def native_client(self):
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self.create)))

    @property
    def _contract_speaker(self):
        return getattr(self, "_active_speaker", None)

    def set_contract_speaker(self, speaker: str | None) -> None:
        self._active_speaker = speaker

    def receipt(self) -> dict[str, object]:
        return {"model": self.model, "completion_attempts": self.calls,
                "failed_attempts": self.failed_calls, "total_tokens": self.total_tokens,
                "max_calls": self.max_calls, "reasoning_effort": self.reasoning_effort,
                "protocol_only": self.protocol_only,
                "output_contract_version": self.output_contract_version,
                "output_contract_schema_sha256": (
                    OUTPUT_CONTRACT_SCHEMA_SHA256 if self.output_contract_version else None
                ),
                "contract_rejections": self.contract_rejections,
                "structured_output_calls": len(self.structured_outputs),
                "contract_prompt_sha256": sorted({record["contract_prompt_sha256"]
                    for record in self.response_records if "contract_prompt_sha256" in record
                    and record["call"] > self._structured_start_call})}


class NativeDiscussion:
    """Load original planner components without the unrelated Habitat harness."""

    def __init__(self, source_root: str | Path, endpoint: MeteredNativeEndpoint):
        root = Path(source_root).expanduser().resolve()
        paths = (DISCUSSION_PATH, MODEL_PATH,
                 "habitat-mas/habitat_mas/utils/__init__.py",
                 "habitat-mas/habitat_mas/utils/python_interpreter.py",
                 "habitat-mas/habitat_mas/agents/crab_core.py")
        if any(not (root / path).is_file() for path in paths):
            raise NativeEmosUnavailable("native EMOS checkout is missing required planner files")
        self.source_root = root
        self.source_hashes = {path: hashlib.sha256((root / path).read_bytes()).hexdigest()
                              for path in paths}
        self._prefix = "_heterocoord_native_emos_" + uuid.uuid4().hex
        package = ModuleType(self._prefix)
        package.__path__ = [str(root / "habitat-mas/habitat_mas")]
        sys.modules[self._prefix] = package
        try:
            arguments = importlib.import_module(self._prefix + ".utils")
            models = importlib.import_module(self._prefix + ".utils.models")
            # A module-private IO dependency, not a replacement model/chat loop.
            models.openai = SimpleNamespace(OpenAI=endpoint.native_client)
            self.model_class = models.OpenAIModel
            original_model_class = models.OpenAIModel
            endpoint_ref = endpoint

            class ContractOpenAIModel(original_model_class):
                """Preserve upstream chat while adding only adapter wire context."""

                def __init__(self, *args, **kwargs):
                    logging_file = kwargs.get("logging_file", "")
                    agent_name = kwargs.get("agent_name", "unknown")
                    suffix = "_group_chat_history.json"
                    if Path(logging_file).name == "leader_group_chat_history.json":
                        speaker = "leader"
                    elif Path(logging_file).name.endswith(suffix):
                        speaker = Path(logging_file).name[:-len(suffix)]
                    else:
                        speaker = agent_name
                    self._contract_speaker_name = speaker
                    super().__init__(*args, **kwargs)

                def chat(self, content, crab_planning=False):
                    endpoint_ref.set_contract_speaker(self._contract_speaker_name)
                    try:
                        return super().chat(content, crab_planning=crab_planning)
                    finally:
                        endpoint_ref.set_contract_speaker(None)

            module = ModuleType(self._prefix + ".discussion")
            module.__file__ = str(root / DISCUSSION_PATH)
            module.__dict__.update(os=os, json=json, re=re, Dict=Dict,
                                   AgentArguments=arguments.AgentArguments,
                                   OpenAIModel=ContractOpenAIModel if endpoint.output_contract_version else models.OpenAIModel)
            parsed = ast.parse((root / DISCUSSION_PATH).read_text(), filename=module.__file__)
            nodes = []
            found = set()
            for node in parsed.body:
                if isinstance(node, ast.FunctionDef) and node.name in _FUNCTIONS:
                    nodes.append(node)
                    found.add(node.name)
                elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                    if isinstance(target, ast.Name) and target.id in _CONSTANTS:
                        # Constants must remain literal data, not harness imports.
                        ast.literal_eval(node.value)
                        nodes.append(node)
                        found.add(target.id)
            if found != _FUNCTIONS | _CONSTANTS:
                raise NativeEmosUnavailable("unsupported native EMOS discussion module layout")
            # The selected nodes are not rewritten; prompts and decision logic
            # are executed directly from the source tree with original locations.
            exec(compile(ast.Module(body=nodes, type_ignores=[]), module.__file__, "exec"),
                 module.__dict__)
            if module.DISCUSSION_TOOLS:
                raise NativeEmosUnavailable("native discussion requires additional action bindings")
            self.module = module
            self.endpoint = endpoint
            self.last_structured_assignment = None
        except Exception as exc:
            self.close()
            if isinstance(exc, NativeEmosUnavailable):
                raise
            raise NativeEmosUnavailable("cannot load native EMOS dependencies/components") from None

    def close(self):
        for name in list(sys.modules):
            if name == self._prefix or name.startswith(self._prefix + "."):
                del sys.modules[name]

    def prepare_structured_output(self, *, robot_ids, task_catalog):
        self.endpoint.bind_output_contract(robot_ids=robot_ids, task_catalog=task_catalog)
        self.endpoint.clear_structured_outputs()
        self.last_structured_assignment = None

    def finish_structured_output(self):
        assignments = [item for item in self.endpoint.structured_outputs
                        if item["document"].get("kind") == "assignment"]
        self.last_structured_assignment = (
            deepcopy(assignments[-1]["document"]) if assignments else None
        )


class EmosNativeAdapter:
    """Benchmark DecisionProvider backed by original EMOS stage-one discussion.

    ``o_r.scene_description`` must be explicit native input text on every
    initialize/update. Resumes must use upstream robot_type/mobility/perception/
    manipulation schemas; the adapter does not generate a substitute resume.

    Upstream produces free text, not task IDs. Exact task IDs or unique exact
    graph descriptions are mapped losslessly. Optional ``output_bindings`` is a
    caller-declared vocabulary (exact native text -> task ID tuple), frozen before
    inference. Unknown/ambiguous output is rejected, never repaired or filled.
    Native "Nothing to do" means no assignment. Multiple tasks remain multiple
    Assignment records. No semantic matching, greedy allocation or default plan.
    """

    def __init__(self, request: AssignmentRequest | CaseAssignmentRequest, *,
                 robot_resume: Mapping[str, Any],
                 endpoint: MeteredNativeEndpoint | None = None,
                 source_root: str | Path | None = None,
                 log_dir: str | Path,
                 output_bindings: Mapping[str, str | tuple[str, ...]] | None = None,
                 initialization_budget_calls: int = 1):
        if not isinstance(request, (AssignmentRequest, CaseAssignmentRequest)):
            raise TypeError("request must be AssignmentRequest or CaseAssignmentRequest")
        if type(initialization_budget_calls) is not int or initialization_budget_calls < 0:
            raise ValueError("initialization_budget_calls must be a non-negative integer")
        if not isinstance(robot_resume, Mapping) or set(robot_resume) != set(request.robot_positions):
            raise ValueError("native robot resume roster mismatch")
        for resume in robot_resume.values():
            if not isinstance(resume, Mapping) or not isinstance(resume.get("robot_type"), str):
                raise ValueError("native robot resume requires robot_type")
            if not resume["robot_type"].strip() or not any(
                isinstance(resume.get(key), Mapping) and resume[key]
                for key in ("mobility", "perception", "manipulation")
            ):
                raise ValueError("native robot resume requires explicit capabilities")
        self.request = deepcopy(request)
        self._resume = deepcopy(dict(robot_resume))
        self.endpoint = endpoint
        self.source_root = Path(source_root or os.environ.get("EMOS_SOURCE_ROOT") or
                                Path.home() / ".cache/heterocoord/upstream/emos")
        self.log_dir = Path(log_dir).expanduser().resolve()
        self._bindings = {}
        for text, ids in (output_bindings or {}).items():
            ids = (ids,) if isinstance(ids, str) else tuple(ids)
            if not isinstance(text, str) or not text or not ids or text == "Nothing to do":
                raise ValueError("output bindings require exact non-idle text and task IDs")
            if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
                raise ValueError("output binding task IDs must be unique non-empty strings")
            self._bindings[text] = ids
        self.initialization_budget_calls = initialization_budget_calls
        self._initialization_calls = 0
        self._initialized = False
        self._history = {}
        self._observation = {}
        self._native_results = {}
        self._native = None
        self._discussion_calls = 0
        self._last_structured_output = None

    @property
    def history(self) -> dict:
        """Copied evaluator history; not a native planner-memory restoration."""
        return deepcopy(self._history)

    @property
    def native_history(self) -> dict:
        """Original per-robot discussion histories, including native SDK messages."""
        return {key: deepcopy(value.chat_history) for key, value in self._native_results.items()}

    @property
    def native_results(self) -> dict:
        """Unmodified native AgentArguments, retained even on mapping/scope failure."""
        return deepcopy(self._native_results)

    @property
    def native_structured_output(self):
        return deepcopy(self._last_structured_output)

    def _observation_copy(self, o_r):
        observation = deepcopy(EmosDecisionAdapter._validate_observation(
            o_r, set(self.request.robot_positions)))
        if not isinstance(observation.get("scene_description"), str) or not observation["scene_description"].strip():
            raise ValueError("o_r.scene_description requires explicit native scene text")
        return observation

    def initialize(self, H_r: Mapping[str, Any], o_r: Mapping[str, Any]):
        if self._initialized:
            raise NativeEmosError("native EMOS adapter is already initialized")
        if self._initialization_calls >= self.initialization_budget_calls:
            raise NativeEmosError("native EMOS initialization budget exhausted")
        if not isinstance(H_r, Mapping):
            raise TypeError("H_r must be a mapping")
        if H_r.get("native_chat_history") or H_r.get("chat_history"):
            raise NotImplementedError("upstream group_discussion cannot restore external chat history")
        observation = self._observation_copy(o_r)
        fields = (("task_ids", "completed_task_ids", "excluded_task_ids", "task_description")
                  if isinstance(self.request, CaseAssignmentRequest) else
                  ("subtask_positions", "completed_extinguisher", "completed_rescue",
                   "rescue_constraints", "excluded_subtask_ids", "task_description"))
        updates = {key: deepcopy(H_r[key]) for key in fields if key in H_r}
        request = replace(self.request, **updates, robot_positions=observation["robot_positions"])
        self.request = request
        self._history = deepcopy(dict(H_r))
        self._observation = observation
        self._initialization_calls += 1
        self._initialized = True
        return deepcopy(self.request)

    def update_observation(self, o_r: Mapping[str, Any]):
        if not self._initialized:
            raise NativeEmosError("native EMOS adapter must be initialized")
        observation = self._observation_copy(o_r)
        self.request = replace(self.request, robot_positions=observation["robot_positions"])
        self._observation = observation
        self._native_results = {}
        return deepcopy(self.request)

    def feedback(self, *_args, **_kwargs):
        raise NotImplementedError(
            "upstream group_discussion has no external execution-feedback hook; "
            "CrabAgent stage-two execution needs a separate action adapter")

    def close(self):
        if self._native is not None:
            self._native.close()
            self._native = None

    def _vocabulary(self, task):
        vocabulary = {item.task_id: (item.task_id,) for item in task.graph.tasks}
        candidates = {}
        for item in task.graph.tasks:
            candidates.setdefault(item.description, set()).add(item.task_id)
        for text, ids in candidates.items():
            if text in vocabulary:
                ids.update(vocabulary[text])
            vocabulary[text] = tuple(sorted(ids)) if len(ids) == 1 else None
        for text, ids in self._bindings.items():
            if text in vocabulary and vocabulary[text] != ids:
                raise ValueError("output binding conflicts with task vocabulary")
            vocabulary[text] = ids
        if "Nothing to do" in vocabulary:
            raise ValueError("task vocabulary conflicts with native idle marker")
        return vocabulary

    def __call__(self, task: EaiTaskPackage, robots: tuple[RobotState, ...]) -> AlgorithmDecision:
        previous_calls = self.endpoint.calls if self.endpoint is not None else 0
        self._last_structured_output = None
        if self.endpoint:
            self.endpoint.clear_structured_outputs()
        try:
            return self._decide(task, robots)
        except Exception as exc:
            if self._native is not None and self.endpoint and self.endpoint.output_contract_version:
                self._native.finish_structured_output()
                self._last_structured_output = deepcopy(self._native.last_structured_assignment)
            # Shared runners can retain rejected native output and actual usage
            # without changing the exception type or manufacturing a decision.
            exc.native_subtasks = {key: value.subtask_description
                                   for key, value in self._native_results.items()}
            exc.model_requests = self.endpoint.calls if self.endpoint is not None else 0
            exc.endpoint_receipt = self.endpoint.receipt() if self.endpoint is not None else None
            exc.native_result = {
                "native_subtasks": deepcopy(exc.native_subtasks),
                "structured_output": self.native_structured_output,
                "endpoint_receipt": deepcopy(exc.endpoint_receipt),
                "adaptation_condition": self._adaptation_condition(task),
                "structured_turns": deepcopy(self.endpoint.structured_outputs) if self.endpoint else [],
            }
            if not exc.native_subtasks and self._last_structured_output:
                exc.native_subtasks = {item["robot_id"]: item["subtask_description"]
                                       for item in self._last_structured_output["assignments"]}
                exc.native_result["native_subtasks"] = deepcopy(exc.native_subtasks)
            exc.adaptation_condition = self._adaptation_condition(task)
            raise
        finally:
            if self.endpoint is not None and self.endpoint.calls > previous_calls:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                trace = self.log_dir / ("endpoint_trace_" + uuid.uuid4().hex + ".json")
                trace.write_text(json.dumps({"endpoint": self.endpoint.receipt(),
                    "adaptation_condition": self._adaptation_condition(task), "responses": [
                    record for record in self.endpoint.response_records if record["call"] > previous_calls
                ]}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _decide(self, task: EaiTaskPackage, robots: tuple[RobotState, ...]) -> AlgorithmDecision:
        if not self._initialized:
            raise NativeEmosError("native EMOS adapter must be initialized")
        self._native_results = {}
        ids = [robot.capability.robot_id for robot in robots]
        if len(ids) != len(set(ids)) or set(ids) != set(self.request.robot_positions):
            raise ValueError("native EMOS request/runtime roster mismatch")
        if set(ids) != {robot.capability.robot_id for robot in task.robots}:
            raise ValueError("native EMOS task/runtime roster mismatch")
        scenario_id = (self.request.scenario_id if isinstance(self.request, CaseAssignmentRequest)
                       else self.request.scenario.scenario_id)
        if task.task_id != scenario_id and task.metadata.get("scenario_id") != scenario_id:
            raise ValueError("native EMOS task package scenario mismatch")
        task_ids = {item.task_id for item in task.graph.tasks}
        known_ids = (set(self.request.task_ids) if isinstance(self.request, CaseAssignmentRequest)
                     else set(self.request.scenario.subtasks))
        if not task_ids or task_ids - known_ids:
            raise ValueError("task package contains unknown request task IDs")
        vocabulary = self._vocabulary(task)
        if any(set(ids) - task_ids for ids in self._bindings.values()):
            raise ValueError("output binding contains unknown task IDs")
        if self.endpoint is None:
            raise NativeEmosUnavailable("native model endpoint is not configured; no fallback")
        if self._native is None:
            self._native = NativeDiscussion(self.source_root, self.endpoint)
        task_catalog = {item.task_id: item.description for item in task.graph.tasks}
        if self.endpoint.output_contract_version is not None:
            if self._bindings:
                raise ValueError("structured output uses explicit task IDs; output_bindings must be empty")
            self._native.prepare_structured_output(
                robot_ids=tuple(robot.capability.robot_id for robot in robots),
                task_catalog=task_catalog,
            )
        # Native has an unbound episode_save_dir with logging disabled. Use its
        # supported logging path, never patch the upstream function to hide it.
        self._discussion_calls += 1
        run_id = f"discussion-{self._discussion_calls}-{uuid.uuid4().hex}"
        try:
            results = self._native.module.group_discussion(
                robot_resume=json.dumps(self._resume),
                scene_description=self._observation["scene_description"],
                task_description=self.request.task_description,
                save_chat_history=True, save_chat_history_dir=str(self.log_dir),
                episode_id=run_id,
            )
        except NativeEmosError:
            raise
        except Exception:
            raise NativeEmosError("upstream EMOS discussion failed; no fallback") from None
        self._native.finish_structured_output()
        self._last_structured_output = deepcopy(self._native.last_structured_assignment)
        if not isinstance(results, Mapping) or set(results) != set(self.request.robot_positions):
            raise NativeEmosError("native discussion result roster mismatch")
        self._native_results = deepcopy(dict(results))
        raw_subtasks = {key: value.subtask_description for key, value in results.items()}
        active = {r.capability.robot_id for r in robots if r.capability.active} & {
            r.capability.robot_id for r in task.robots if r.capability.active}
        excluded = (set(self.request.completed_task_ids) | set(self.request.excluded_task_ids)
                    if isinstance(self.request, CaseAssignmentRequest) else
                    set(self.request.excluded_subtask_ids))
        assignments = []
        structured = self._last_structured_output
        if self.endpoint.output_contract_version is not None:
            if structured is None:
                raise NativeEmosError("structured EMOS discussion produced no leader allocation")
            structured_by_robot = {item["robot_id"]: item for item in structured["assignments"]}
            for robot_id, item in structured_by_robot.items():
                state = item["state"]
                if state == "idle":
                    continue
                if state == "refused":
                    raise NativeOutputScopeError(
                        "native structured output explicitly refused an assignment",
                        raw_subtasks, structured,
                    )
                if robot_id not in active:
                    raise NativeEmosError("native assignment targets inactive robot")
                if set(item["task_ids"]) & excluded:
                    raise NativeEmosError("native assignment targets an excluded task")
                for target in item["task_ids"]:
                    if isinstance(self.request, AssignmentRequest):
                        assignment_tasks(self.request, {robot_id: target})
                    assignments.append(Assignment(task_id=target, robot_id=robot_id,
                                                  source="EMOS-native-discussion",
                                                  plan_version="upstream-stage-one-v1+" + self.endpoint.output_contract_version))
        else:
            for robot_id, argument in results.items():
                if argument.robot_id != robot_id or not isinstance(argument.subtask_description, str):
                    raise NativeEmosError("invalid native AgentArguments")
                text = argument.subtask_description
                if text == "Nothing to do":
                    continue
                targets = vocabulary.get(text)
                if not targets:
                    raise NativeOutputScopeError(
                        "native subtask has no unambiguous exact task binding", raw_subtasks)
                if robot_id not in active or set(targets) & excluded:
                    raise NativeEmosError("native assignment targets inactive robot or excluded task")
                for target in targets:
                    if isinstance(self.request, AssignmentRequest):
                        assignment_tasks(self.request, {robot_id: target})
                    assignments.append(Assignment(task_id=target, robot_id=robot_id,
                                                  source="EMOS-native-discussion",
                                                  plan_version="upstream-stage-one-v1"))
        target_ids = [item.task_id for item in assignments]
        if len(set(target_ids)) != len(target_ids):
            raise NativeOutputScopeError(
                "native coalition assigns multiple robots to one task; TaskRunner cannot represent it",
                raw_subtasks, structured)
        assigned = {item.task_id for item in assignments}
        final_feedback = {}
        for turn in self.endpoint.structured_outputs:
            if turn["document"]["kind"] == "assignment":
                final_feedback.clear()
            elif turn["document"]["kind"] == "feedback":
                final_feedback[turn["speaker"]] = deepcopy(turn["document"]["feedback"])
        prompt_revision = self._native.source_hashes[DISCUSSION_PATH]
        condition_notes = "No native decentralized action execution or physical acceptance is claimed."
        if self.endpoint.output_contract_version is not None:
            contract_metadata = self.endpoint.contract_metadata()
            revision_inputs = {
                "upstream_prompt_sha256": prompt_revision,
                "output_contract_schema_sha256": contract_metadata["schema_sha256"],
                "speaker_suffix_sha256": contract_metadata["prompt_sha256_by_speaker"],
            }
            prompt_revision = hashlib.sha256(json.dumps(
                revision_inputs, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")).hexdigest()
            condition_notes += " Output contract: " + self.endpoint.output_contract_version + "."
        condition = AlgorithmCondition(
            algorithm_id="EMOS-native-discussion", source_reference=str(self._native.source_root),
            implementation="original-upstream-planner-components-with-io-adapter",
            execution_scope="discussion_and_assignment_only", planner="EMOS.group_discussion",
            model_or_api=self.endpoint.model,
            prompt_revision=prompt_revision,
            notes=condition_notes,
        )
        return AlgorithmDecision(
            condition=condition, assignments=tuple(assignments),
            unassigned_tasks=tuple(sorted(task_ids - assigned)),
            provenance={
                "adapter": __name__ + ".EmosNativeAdapter",
                "native_entrypoint": DISCUSSION_PATH + ":group_discussion",
                "source_hashes": dict(self._native.source_hashes),
                "initialized": True, "initialization_calls": self._initialization_calls,
                "discussion_calls": self._discussion_calls,
                "native_subtasks": raw_subtasks, "model_endpoint": self.endpoint.receipt(),
                "native_log_dir": str(self.log_dir / run_id),
                "history_restored_to_native": False, "external_feedback_supported": False,
                "evidence_kind": "protocol_only" if self.endpoint.protocol_only else "live_native_planner",
                "algorithm_acceptance": "not_evaluated", "physical_success": False,
                "fallback_used": False,
                "output_contract_version": self.endpoint.output_contract_version,
                "output_contract_schema_sha256": (
                    OUTPUT_CONTRACT_SCHEMA_SHA256 if self.endpoint.output_contract_version else None
                ),
                "contract_prompt_sha256": self.endpoint.receipt().get("contract_prompt_sha256", []),
                "native_structured_output": deepcopy(structured),
                "structured_turns": deepcopy(self.endpoint.structured_outputs),
                "final_robot_feedback": final_feedback,
                "final_unanimous_feasibility": (
                    set(final_feedback) == set(ids) and all(item["state"] == "yes" for item in final_feedback.values())
                    if structured is not None else None
                ),
                "adaptation_condition": self._adaptation_condition(task),
            },
        )

    def _adaptation_condition(self, task):
        endpoint = self.endpoint
        return {
            "adapter": __name__ + ".EmosNativeAdapter",
            "execution_scope": "discussion_and_assignment_only",
            "task_ids": [item.task_id for item in task.graph.tasks],
            "output_contract_version": endpoint.output_contract_version if endpoint else None,
            "output_contract_schema_sha256": (
                OUTPUT_CONTRACT_SCHEMA_SHA256 if endpoint and endpoint.output_contract_version else None
            ),
            "contract_prompt_sha256": endpoint.receipt().get("contract_prompt_sha256", []) if endpoint else [],
            "output_contract": endpoint.contract_metadata() if endpoint else None,
            "fallback_used": False,
        }


__all__ = ["EmosNativeAdapter", "MeteredNativeEndpoint", "NativeDiscussion",
           "NativeEmosError", "NativeEmosUnavailable", "NativeOutputScopeError",
           "OUTPUT_CONTRACT_VERSION", "OUTPUT_CONTRACT_SCHEMA",
           "OUTPUT_CONTRACT_SCHEMA_SHA256"]
