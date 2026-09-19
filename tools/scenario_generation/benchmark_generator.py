"""Pure-Python benchmark candidate generation and admission loop.

This module is deliberately separate from the command line generators and from
Isaac execution.  A caller supplies either candidate JSONs or a callable that
proposes one candidate per round.  Every candidate is admitted through the
same local gates before it can enter the returned pool:

* schema and scene contract;
* initial geometry and connectivity;
* navigation/capability feasibility oracle;
* benchmark team-necessity contract; and
* the deterministic executor contract.

No LLM client, network request, settle report, or execution report is created
here. Failed candidates remain in ``feedback`` only, so a caller can feed the
structured repair information into its next proposal round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import inspect
import json
from typing import Any, Callable, Iterable, Mapping

SUPPORTED_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")
GATE_NAMES = ("schema", "geometry", "connectivity", "oracle", "team_necessity", "executor_contract")


@dataclass(frozen=True)
class GateResult:
    """One local admission gate result, suitable for JSON serialization."""
    name: str
    status: str  # passed, failed, or blocked
    details: Any = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "status": self.status}
        if self.details is not None:
            result["details"] = self.details
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass
class CandidateFeedback:
    """Feedback sent to a subsequent proposal round."""
    round_index: int
    candidate_id: str
    accepted: bool
    gates: list[GateResult] = field(default_factory=list)
    repair_prompt: str = ""

    def as_dict(self) -> dict[str, Any]:
        failed = [gate for gate in self.gates if gate.status == "failed"]
        return {
            "round": self.round_index,
            "candidate_id": self.candidate_id,
            "accepted": self.accepted,
            "gates": [gate.as_dict() for gate in self.gates],
            "failed_gates": [gate.name for gate in failed],
            "repair_prompt": self.repair_prompt,
        }


def _scenario_sha256(data: Mapping[str, Any]) -> str:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class BenchmarkGenerationResult:
    """Result of a bounded proposal/admission loop."""
    scene_key: str
    pool: list[dict[str, Any]]
    feedback: list[dict[str, Any]]
    rounds: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "scene_key": self.scene_key,
            "pool": self.pool,
            "feedback": self.feedback,
            "rounds": self.rounds,
            "execution_evidence": "not_attempted",
        }


class BenchmarkGenerator:
    """Evaluate injected candidates against one scene's local benchmark gates."""

    def __init__(
        self,
        scene_key: str,
        *,
        occupancy: Any = None,
        template: Any = None,
        payload_policy: Mapping[str, float] | None = None,
        require_team_necessity: bool = True,
        transport_mode: str = "auto",
        executor_contract: Callable[..., Mapping[str, Any]] | None = None,
        require_long_horizon: bool = False,
        task_id: str | None = None,
    ) -> None:
        if scene_key not in SUPPORTED_SCENES:
            raise ValueError(f"unsupported scene_key {scene_key!r}; expected one of {SUPPORTED_SCENES}")
        if transport_mode not in {"auto", "none", "kinematic"}:
            raise ValueError("transport_mode must be auto, none, or kinematic")
        self.scene_key = scene_key
        self.payload_policy = dict(payload_policy) if payload_policy is not None else None
        self.require_team_necessity = bool(require_team_necessity)
        self.require_long_horizon = bool(require_long_horizon)
        if task_id is not None and (scene_key != "factory"
                                    or task_id not in {"F-INS-01", "F-TRAFFIC-01", "F-COV-01"}):
            raise ValueError(f"unsupported task_id {task_id!r} for scene {scene_key!r}")
        self.task_id = task_id
        self.transport_mode = transport_mode
        self._executor_contract = executor_contract
        self.occupancy = occupancy
        self.template = template
        if self.template is None or self.occupancy is None:
            from EAI.scenario_spec.geometry import provider_map
            from EAI.scenario_spec.templates import template_for
            if self.occupancy is None:
                self.occupancy = provider_map(scene_key=scene_key)
            if self.template is None:
                self.template = template_for(scene_key, self.occupancy)
        if getattr(self.template, "scene_key", scene_key) != scene_key:
            raise ValueError(
                f"template scene_key {getattr(self.template, 'scene_key', None)!r} does not match {scene_key!r}"
            )

    def _candidate_mapping(self, candidate: Any) -> dict[str, Any]:
        if isinstance(candidate, Mapping):
            return dict(candidate)
        if isinstance(candidate, (str, bytes, bytearray)):
            try:
                value = json.loads(candidate)
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"candidate is not valid JSON: {exc}") from exc
            if not isinstance(value, Mapping):
                raise ValueError("candidate JSON must be an object")
            return dict(value)
        content = getattr(candidate, "content", None)
        if content is not None:
            return self._candidate_mapping(content)
        choices = getattr(candidate, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            if message is not None:
                return self._candidate_mapping(getattr(message, "content", message))
        raise ValueError("candidate must be a mapping or a JSON object")

    @staticmethod
    def _candidate_id(data: Mapping[str, Any], round_index: int) -> str:
        value = data.get("task_name")
        return str(value) if value else f"round_{round_index}"

    def _transport_mode_for(self, data: Mapping[str, Any]) -> str:
        if self.transport_mode != "auto":
            return self.transport_mode
        nodes = data.get("phase_graph", {}).get("nodes", [])
        return "kinematic" if any(
            node.get("skill_key") in {"transport_object", "joint_transport"} for node in nodes
        ) else "none"

    def _run_executor_contract(self, data: dict[str, Any]) -> Mapping[str, Any]:
        checker = self._executor_contract
        if checker is None:
            from tools.scenario_generation.execute_episode import execution_contract
            checker = execution_contract
        return checker(data, transport_mode=self._transport_mode_for(data))

    def evaluate(self, candidate: Any, *, round_index: int = 1) -> CandidateFeedback:
        """Evaluate one candidate without mutating the candidate or the pool."""
        try:
            data = self._candidate_mapping(candidate)
        except Exception as exc:
            feedback = CandidateFeedback(
                round_index, f"round_{round_index}", False, [GateResult("schema", "failed", error=str(exc))]
            )
            feedback.repair_prompt = _repair_prompt(feedback)
            return feedback

        candidate_id = self._candidate_id(data, round_index)
        gates: list[GateResult] = []
        validated: dict[str, Any] | None = None

        def run(name: str, function: Callable[[], Any]) -> Any:
            if validated is None and name != "schema":
                gates.append(GateResult(name, "blocked", error="schema gate did not pass"))
                return None
            try:
                value = function()
            except Exception as exc:
                gates.append(GateResult(name, "failed", error=str(exc)))
                return None
            gates.append(GateResult(name, "passed", details=value))
            return value

        def schema_gate() -> dict[str, Any]:
            from EAI.scenario_spec.validation import validate_scenario
            if data.get("scene_key") != self.scene_key:
                raise ValueError(
                    f"candidate scene_key {data.get('scene_key')!r} does not match {self.scene_key!r}"
                )
            floors = tuple(level.floor_z for level in getattr(self.template, "levels", ()))
            validated = validate_scenario(data, known_floors=floors)
            from tools.scenario_generation.benchmark_experiment import SCENE_ASSET_KEYS
            allowed = set(SCENE_ASSET_KEYS[self.scene_key])
            invalid_assets = sorted({
                entity.get("asset_key") for entity in validated.get("composition", {}).get("entities", ())
                if entity.get("asset_key") not in allowed
            })
            if invalid_assets:
                raise ValueError(
                    f"scene {self.scene_key} does not allow assets {invalid_assets}; "
                    "use the scene asset allowlist"
                )
            return validated

        try:
            validated = schema_gate()
            gates.append(GateResult("schema", "passed", details={"version": validated.get("version")}))
        except Exception as exc:
            gates.append(GateResult("schema", "failed", error=str(exc)))

        if validated is not None:
            run("geometry", lambda: _geometry(validated, self.occupancy))
            run("connectivity", lambda: _connectivity(validated, self.occupancy))
            run("oracle", lambda: _oracle(validated, self.occupancy, self.payload_policy, self.template))
            contract = run("team_necessity", lambda: _team_contract(validated, self.payload_policy))
            if contract is not None and self.require_team_necessity and not contract.get("eligible", False):
                gates[-1] = GateResult(
                    "team_necessity", "failed", details=contract,
                    error="candidate is not eligible for heterogeneous-team benchmark admission",
                )
            if self.require_long_horizon and self.task_id is None:
                design = run("task_design", lambda: _task_design(validated, self.scene_key))
                if design is not None and not design.get("eligible", False):
                    gates[-1] = GateResult(
                        "task_design", "failed", details=design,
                        error="candidate does not meet the strong heterogeneous long-horizon task profile",
                    )
                if self.scene_key in {"warehouse_nvd", "modern_house_nvd"}:
                    run("authoring_review",
                        lambda: _authoring_review(validated, self.scene_key))
            run("executor_contract", lambda: self._run_executor_contract(validated))
            if self.task_id is not None:
                from demo.factory_benchmark.tasks import validate_factory_scenario
                try:
                    details = validate_factory_scenario(validated, self.task_id)
                except Exception as exc:
                    gates.append(GateResult("factory_task", "failed", error=str(exc)))
                else:
                    gates.append(GateResult("factory_task", "passed", details=details))
        else:
            for name in GATE_NAMES[1:]:
                gates.append(GateResult(name, "blocked", error="schema gate did not pass"))

        accepted = all(gate.status == "passed" for gate in gates)
        feedback = CandidateFeedback(round_index, candidate_id, accepted, gates)
        feedback.repair_prompt = _repair_prompt(feedback)
        return feedback

    def admit(self, candidate: Any, *, round_index: int = 1) -> tuple[dict[str, Any] | None, CandidateFeedback]:
        """Evaluate and return a pool entry only when every gate passes."""
        feedback = self.evaluate(candidate, round_index=round_index)
        if not feedback.accepted:
            return None, feedback
        data = self._candidate_mapping(candidate)
        contract_gate = next((gate for gate in feedback.gates if gate.name == "team_necessity"), None)
        from tools.scenario_generation.combination_profile import payload_policy_hash
        from tools.scenario_generation.robot_dossier import all_dossiers_snapshot_hash
        policy_hash = payload_policy_hash(self.payload_policy) if self.payload_policy else None
        robot_suitability = {}
        from tools.scenario_generation.robot_dossier import robot_dossier
        from tools.scenario_generation.robot_suitability import evaluate_suitability
        nodes = data.get("phase_graph", {}).get("nodes", ())
        for robot in data.get("robots", ()):
            robot_type = robot.get("type")
            robot_id = robot.get("id", robot_type)
            attachment_names = []
            for attachment in robot.get("attachments", ()):
                if isinstance(attachment, str):
                    attachment_names.append(attachment)
                elif isinstance(attachment, dict):
                    name = attachment.get("type", attachment.get("name"))
                    if name:
                        attachment_names.append(name)
            assigned = {robot_id}
            manip_tasks = any(node.get("skill_key") in {"transport_object", "joint_transport"}
                               and (node.get("preferred_agent") in assigned or
                                   robot_id in set(node.get("agents") or ())) for node in nodes)
            scan_task = any(node.get("skill_key") == "aerial_scan"
                            and node.get("preferred_agent") == robot_id for node in nodes)
            dossier = robot_dossier(robot_type, attachments=attachment_names)
            robot_suitability[robot_id] = evaluate_suitability(
                dossier, self.scene_key, requires_manipulation=manip_tasks,
                requires_aerial_scan=scan_task,
            )
        unsuitable = {
            robot_id: result for robot_id, result in robot_suitability.items()
            if result["status"] != "eligible"
            and any(
                node.get("skill_key") in {"transport_object", "joint_transport"}
                and (node.get("preferred_agent") == robot_id
                     or robot_id in set(node.get("agents") or ()))
                for node in nodes
            )
        }
        if unsuitable:
            feedback.gates.append(GateResult(
                "robot_suitability", "failed", details=unsuitable,
                error="dynamic manipulation requires an allowed deployment with matching evidence",
            ))
            feedback.accepted = False
            feedback.repair_prompt = _repair_prompt(feedback)
            return None, feedback
        entry = {
            "id": feedback.candidate_id,
            "scene_key": self.scene_key,
            "scenario": data,
            "scenario_sha256": _scenario_sha256(data),
            "template_key": getattr(self.template, "key", None),
            "map_sha256": getattr(self.template, "map_sha256", None),
            "robot_dossier_snapshot_sha256": all_dossiers_snapshot_hash(),
            "payload_policy_hash": policy_hash,
            "payload_policy": self.payload_policy,
            "robot_suitability": robot_suitability,
            "team_certificate": contract_gate.details if contract_gate else None,
            "admission": {"status": "admitted", "gates": [gate.as_dict() for gate in feedback.gates]},
        }
        return entry, feedback

    def run(
        self,
        candidate_provider: Callable[..., Any] | Iterable[Any],
        *,
        max_rounds: int = 1,
    ) -> BenchmarkGenerationResult:
        """Run bounded proposal rounds and return pool plus repair feedback."""
        if not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("max_rounds must be a positive integer")
        pool: list[dict[str, Any]] = []
        feedback: list[dict[str, Any]] = []
        iterator = None if callable(candidate_provider) else iter(candidate_provider)
        previous: list[dict[str, Any]] = []
        for round_index in range(1, max_rounds + 1):
            request = {
                "round": round_index,
                "scene_key": self.scene_key,
                "payload_policy": self.payload_policy,
                "previous_feedback": previous,
                "repair_prompt": previous[-1]["repair_prompt"] if previous else "",
            }
            if callable(candidate_provider):
                candidate = _call_provider(candidate_provider, request)
            else:
                try:
                    candidate = next(iterator)
                except StopIteration:
                    break
            entry, result = self.admit(candidate or {}, round_index=round_index)
            result_dict = result.as_dict()
            feedback.append(result_dict)
            previous = [result_dict]
            if entry is not None:
                pool.append(entry)
        return BenchmarkGenerationResult(self.scene_key, pool, feedback, len(feedback))


def inspect_candidate(candidate: Any, *, payload_policy: Mapping[str, float] | None = None,
                      occupancy: Any = None, scene_key: str | None = None,
                      require_long_horizon: bool = True) -> dict[str, Any]:
    """Inspect one candidate through the unified admission gates.

    This adapter keeps the legacy ``generate_factory.generate`` API while
    preventing it from using a weaker geometry-only validation path.
    """
    resolved_scene = scene_key or (candidate.get("scene_key") if isinstance(candidate, Mapping) else None)
    if resolved_scene is None:
        raise ValueError("candidate scene_key is required")
    generator = BenchmarkGenerator(
        resolved_scene, occupancy=occupancy, payload_policy=payload_policy,
        require_team_necessity=True, require_long_horizon=require_long_horizon,
    )
    feedback = generator.evaluate(candidate)
    return {
        "eligible": feedback.accepted,
        "feedback": feedback.as_dict(),
        "gates": [gate.as_dict() for gate in feedback.gates],
    }

def _geometry(data: dict[str, Any], occupancy: Any) -> Any:
    from EAI.scenario_spec.geometry import validate_geometry
    return validate_geometry(data, occupancy)


def _connectivity(data: dict[str, Any], occupancy: Any) -> Any:
    from EAI.scenario_spec.geometry import validate_connectivity
    return validate_connectivity(data, occupancy)


def _oracle(data: dict[str, Any], occupancy: Any, payload_policy: Any, template: Any) -> Any:
    from EAI.scenario_spec.oracle import validate_level_routing, validate_oracle
    oracle = validate_oracle(data, occupancy, payload_policy=payload_policy, template=template)
    return {"oracle": oracle, "level_routing": validate_level_routing(data, template)}


def _task_design(data: dict[str, Any], scene_key: str) -> Any:
    from tools.scenario_generation.benchmark_task_design import validate_long_horizon_task
    return validate_long_horizon_task(data, scene_key=scene_key)


def _authoring_review(data: dict[str, Any], scene_key: str) -> Any:
    """Apply the same business-role review used by NVD compilers."""
    if scene_key not in {"warehouse_nvd", "modern_house_nvd"}:
        raise ValueError(f"authoring review is not defined for {scene_key!r}")
    from demo.benchmark.authoring import validate_generated_business_scenario
    return validate_generated_business_scenario(data)

def _team_contract(data: dict[str, Any], payload_policy: Any) -> Any:
    from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract
    return benchmark_eligibility_contract(data, payload_policy=payload_policy)


def _repair_prompt(feedback: CandidateFeedback) -> str:
    failed = [gate for gate in feedback.gates if gate.status == "failed"]
    if not failed:
        return ""
    lines = ["Repair the candidate and return the complete scenario JSON."]
    for gate in failed:
        lines.append(f"- {gate.name}: {gate.error or 'gate failed'}")
    return "\n".join(lines)


def _call_provider(provider: Callable[..., Any], request: dict[str, Any]) -> Any:
    """Call fixtures with either request=, keyword, or one positional arg."""
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return provider(request)
    parameters = signature.parameters
    if "request" in parameters:
        return provider(request=request)
    if any(parameter.kind == parameter.VAR_KEYWORD for parameter in parameters.values()):
        return provider(**request)
    if len(parameters) == 1:
        return provider(request)
    return provider(**{key: request[key] for key in parameters if key in request})


def openai_candidate_provider(client: Any, *, model: str, scene_key: str,
                              occupancy: Any = None,
                              instruction: str = "",
                              task_id: str | None = None) -> Callable[[dict[str, Any]], Any]:
    """Create a stateful OpenAI candidate provider for ``BenchmarkGenerator``.

    The provider is optional and lazy: importing this module never imports the
    OpenAI package or performs a network request.  Every round receives the
    previous local gate feedback, so geometry and team failures can be repaired
    by the model rather than escaping after semantic proposal.
    """
    if scene_key not in SUPPORTED_SCENES:
        raise ValueError(f"unsupported scene_key {scene_key!r}")
    if occupancy is None:
        from EAI.scenario_spec.geometry import provider_map
        occupancy = provider_map(scene_key=scene_key)
    from EAI.scenario_spec.templates import template_for
    from EAI.scenario_spec.schema import scenario_schema
    from tools.scenario_generation.benchmark_task_design import SCENE_TASK_PROFILES, STRONG_TASK_BLUEPRINTS
    from demo.benchmark.authoring import build_authoring_contract
    from tools.scenario_generation.common import manifest
    template = template_for(scene_key, occupancy)
    context = manifest(occupancy, scene_key=scene_key)
    profile = SCENE_TASK_PROFILES[scene_key]
    context["task_profile"] = {**profile, "required_skills": sorted(profile["required_skills"])}
    context["task_blueprint"] = STRONG_TASK_BLUEPRINTS[scene_key]
    context["authoring_contract"] = build_authoring_contract(scene_key)
    context["template"] = {"key": template.key, "map_sha256": template.map_sha256,
                           "zones": [zone.id for zone in template.zones]}
    if task_id is not None:
        from demo.factory_benchmark.scene_contract import FACTORY_TASK_CONTRACTS
        if scene_key != "factory" or task_id not in FACTORY_TASK_CONTRACTS:
            raise ValueError(f"unsupported Factory task_id {task_id!r} for scene {scene_key!r}")
        context["factory_task_contract"] = FACTORY_TASK_CONTRACTS[task_id]
    task_instruction = (
        f"The selected Factory contract is {task_id}; follow its required phase ids and resources. "
        "Do not add aerial_scan or transport skills unless that contract requires them. "
        if task_id else
        "The candidate must satisfy the supplied long-horizon task profile. "
    )
    messages = [{"role": "system", "content": (
        "Generate one complete EAI Scenario V2 JSON for the requested scene. "
        "Use only the supplied assets, robot types, and map zones. The task "
        + task_instruction +
        "Include the reviewed semantic_task and preserve the supplied objective roles, causal "
        "predecessors and native asset roles. The candidate must pass the authoring review. "
        "The aerial scan, when present, must be a cf2x phase that "
        "unlocks downstream work, and its agent must be disjoint from the two-agent "
        "joint_transport team when the selected task contract requires it. For every phase node include agents as null except "
        "joint_transport, which must list exactly two distinct robot ids. Return JSON only."
    )}]

    def provider(request: dict[str, Any]) -> Any:
        messages.append({"role": "user", "content": json.dumps({
            "scene_key": scene_key,
            "benchmark_task_id": task_id,
            "instruction": instruction,
            "request": request,
            "manifest": context,
        }, ensure_ascii=False)})
        response = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema", "json_schema": {
                "name": f"{scene_key}_benchmark_candidate", "strict": True,
                "schema": scenario_schema(strict=True),
            }},
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ValueError("model refused the generation request")
        if getattr(choice, "finish_reason", "stop") != "stop":
            raise ValueError(f"model output was incomplete: {choice.finish_reason}")
        content = choice.message.content or ""
        messages.append({"role": "assistant", "content": content})
        return content

    return provider


def evaluate_candidate(
    candidate: Any,
    scene_key: str,
    *,
    occupancy: Any,
    template: Any,
    payload_policy: Mapping[str, float] | None = None,
    require_team_necessity: bool = True,
    transport_mode: str = "auto",
) -> dict[str, Any]:
    """Convenience API for one pure candidate evaluation."""
    generator = BenchmarkGenerator(
        scene_key, occupancy=occupancy, template=template, payload_policy=payload_policy,
        require_team_necessity=require_team_necessity, transport_mode=transport_mode,
    )
    return generator.evaluate(candidate).as_dict()


def generate_benchmark(
    scene_key: str,
    candidate_provider: Callable[..., Any] | Iterable[Any],
    *,
    occupancy: Any = None,
    template: Any = None,
    payload_policy: Mapping[str, float] | None = None,
    max_rounds: int = 1,
    require_team_necessity: bool = True,
    transport_mode: str = "auto",
) -> dict[str, Any]:
    """Convenience API returning a JSON-ready unified generation result."""
    generator = BenchmarkGenerator(
        scene_key,
        occupancy=occupancy,
        template=template,
        payload_policy=payload_policy,
        require_team_necessity=require_team_necessity,
        transport_mode=transport_mode,
    )
    return generator.run(candidate_provider, max_rounds=max_rounds).as_dict()


run_generation_loop = generate_benchmark

__all__ = [
    "SUPPORTED_SCENES", "GATE_NAMES", "GateResult", "CandidateFeedback",
    "BenchmarkGenerationResult", "BenchmarkGenerator", "evaluate_candidate",
    "openai_candidate_provider", "generate_benchmark", "run_generation_loop",
]
