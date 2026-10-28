"""Execute original TeamWeaver replan against an explicit EAI world snapshot.

Only environment data and the OpenAI-compatible transport are supplied here.
Habitat motor skills are not executed; raw native outputs remain audit data.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


def bind_planning_schemas(agents):
    """Use native argument schemas without constructing Habitat motor policies."""
    from hydra.utils import get_class
    from habitat_llm.tools.motor_skills.motor_skill_tool import MotorSkillTool
    records = []
    for agent in agents:
        for name, original in list(agent.tools.items()):
            skill_type = get_class(original.skill_config.skill._target_)
            # The supported native skills declare constant schema properties.
            # A schema requiring instance state is not silently approximated.
            schema = skill_type.argument_types.fget(None)
            if not isinstance(schema, list) or not all(isinstance(x, str) for x in schema):
                raise ValueError("native motor argument schema is not a string list")

            class PlanningTool(MotorSkillTool):
                @property
                def argument_types(self):
                    return list(self.eai_argument_schema)

                def process_high_level_action(self, *args, **kwargs):
                    raise RuntimeError("planning-only tool; execution belongs to the EAI host")

            tool = PlanningTool(original.skill_config)
            tool.agent_uid = agent.uid
            tool.eai_argument_schema = tuple(schema)
            agent.tools[name] = tool
            records.append({"agent_uid": agent.uid, "tool": name,
                            "native_skill_class": original.skill_config.skill._target_,
                            "argument_types": schema, "motor_policy_instantiated": False})
    return records


def run_native_planner(root, payload, runtime):
    from omegaconf import OmegaConf
    from openai import OpenAI
    from habitat_llm.planner.llm_planner_comparison.llm_planner_miqp import LLMPlanner

    required = ("DONGLI_API_KEY", "DONGLI_BASE_URL", "DONGLI_MODEL", "DONGLI_REASONING_EFFORT")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ValueError("missing Dongli configuration: " + ", ".join(missing))
    base = os.environ["DONGLI_BASE_URL"]
    address = urlsplit(base)
    if address.scheme != "https" or not address.hostname or address.username or address.password or address.query:
        raise ValueError("Dongli base URL must be HTTPS without credentials/query")
    root = Path(root)
    config = OmegaConf.load(root / "TeamWeaver/conf/planner/llm_centralized_planner.yaml").plan_config
    config.llm = OmegaConf.load(root / "TeamWeaver/conf/llm/openai_chat.yaml")
    config.llm.generation_params.model = os.environ["DONGLI_MODEL"]
    config.instruct = OmegaConf.load(root / "TeamWeaver/conf/instruct/few_shot_centralized_full_obs_multiagent.yaml")
    calls = []
    sdk = OpenAI(api_key=os.environ["DONGLI_API_KEY"], base_url=base, max_retries=0, timeout=60)

    class Transport:
        def __init__(self):
            self.chat = SimpleNamespace(completions=self)

        def create(self, **kwargs):
            # User-authorized common benchmark model; keep original messages.
            original_model = kwargs.get("model")
            kwargs["model"] = os.environ["DONGLI_MODEL"]
            record = {"native_requested_model": original_model, "requested_model": kwargs["model"],
                      "reasoning_effort": os.environ["DONGLI_REASONING_EFFORT"],
                      "messages": kwargs.get("messages"), "status": "requested"}
            calls.append(record)
            try:
                response = sdk.chat.completions.create(**kwargs, extra_body={
                    "reasoning_effort": os.environ["DONGLI_REASONING_EFFORT"]})
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__, http_status=getattr(exc, "status_code", None))
                raise
            record.update(status="returned", response_id=response.id, returned_model=response.model,
                          text=response.choices[0].message.content,
                          usage=response.usage.model_dump() if response.usage else None)
            return response

    transport = Transport()
    env = SimpleNamespace(full_world_graph=runtime["graph"],
                          world_graph={a.uid: runtime["graph"] for a in runtime["agents"]})
    saved = {k: os.environ.get(k) for k in ("OPENAI_API_KEY", "OPENAI_ENDPOINT")}
    receipt = {"native_replan_invoked": False, "semantic_solver_invoked": False,
               "environment": "EAI_explicit_world_graph_no_Habitat_simulator",
               "motor_skills_executed": False, "calls": calls,
               "runtime_adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "planner_sha256": hashlib.sha256((root / "TeamWeaver/planner/llm_planner_comparison/llm_planner_miqp.py").read_bytes()).hexdigest()}
    try:
        os.environ["OPENAI_API_KEY"] = os.environ["DONGLI_API_KEY"]
        os.environ["OPENAI_ENDPOINT"] = address.netloc
        planner = LLMPlanner(config, env)
        planner.llm.client.close()
        planner.llm.client = transport
        receipt["planning_tool_schemas"] = bind_planning_schemas(runtime["agents"])
        planner.agents = runtime["agents"]
        connector = planner.perception_connector
        if connector.llm_client is not None:
            connector.llm_client.close()
        connector.llm_client = transport
        connector.phase_manager.llm_client = transport
        connector.matrix_updater.llm_client = transport
        planner.curr_prompt, planner.params = planner.prepare_prompt(payload["instruction"], runtime["graph"])
        if payload.get("output_seam"):
            planner.curr_prompt += runtime["output_suffix"]
        receipt["native_replan_invoked"] = True
        response = planner.replan(payload["instruction"], {}, env.world_graph)
        info = getattr(planner, "_last_response_info", {})
        def serial(value):
            if isinstance(value, dict): return {str(k): serial(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)): return [serial(v) for v in value]
            if hasattr(value, "tolist"): return serial(value.tolist())
            return value
        receipt.update(status="returned", response=response, native_info=serial(info),
                       semantic_solver_invoked="miqp_status" in info,
                       phases=serial(connector.phase_manager.task_execution_phases))
        parsed = planner.actions_parser(planner.agents, response)
        receipt["parsed_actions"] = serial(parsed)
        receipt["planning_success"] = (info.get("miqp_status") == "Optimal"
                                       and bool(info.get("task_decomposition_success"))
                                       and bool(calls) and all(c["status"] == "returned" for c in calls))
    except Exception as exc:
        # Do not return arbitrary exception text which can include credentials.
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        receipt.update(status="failed", planning_success=False, error_type=type(exc).__name__,
                       frames=[{"file": f.filename, "line": f.lineno, "function": f.name} for f in frames])
    finally:
        sdk.close()
        for key, value in saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    return receipt
