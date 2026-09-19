"""Generate, validate and save a Factory V2 draft. LLM calls occur only here."""

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    from common import manifest, write_json
else:
    from .common import manifest, write_json

from EAI.scenario_spec.geometry import provider_map
from EAI.scenario_spec.schema import scenario_schema
from EAI.scenario_spec.validation import parse_scenario
from EAI.scenario_spec.templates import template_for

SUPPORTED_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")


def generate(client, instruction, *, model, occupancy, attempts=3, scene_key="factory",
             payload_policy=None):
    if scene_key not in SUPPORTED_SCENES:
        raise ValueError(f"unsupported scene_key {scene_key!r}")
    if not instruction.strip():
        raise ValueError("instruction must be non-empty")
    if not 1 <= attempts <= 5:
        raise ValueError("attempts must be between 1 and 5")
    template = template_for(scene_key, occupancy)
    messages = [
        {"role": "system", "content": (
            f"Design a heterogeneous robot scenario for the {scene_key} scene using "
            "only the supplied manifest. Return complete V2 JSON and use only listed "
            "assets, zones, capabilities and robots."
        )},
        {"role": "user", "content": json.dumps(
            {"instruction": instruction, "manifest": manifest(occupancy, scene_key=scene_key)},
            ensure_ascii=False)},
    ]
    errors = []
    for attempt in range(attempts):
        response = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema", "json_schema": {
                "name": f"{scene_key}_scenario_v2", "strict": True, "schema": scenario_schema(strict=True),
            }},
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ValueError("model refused the generation request")
        if choice.finish_reason != "stop":
            raise ValueError(f"model output was incomplete: {choice.finish_reason}")
        content = choice.message.content or ""
        try:
            data = parse_scenario(content)
            from tools.scenario_generation.benchmark_generator import inspect_candidate
            report = inspect_candidate(data, payload_policy=payload_policy, occupancy=occupancy)
            if not report["eligible"]:
                raise ValueError(json.dumps(report["feedback"], ensure_ascii=False))
            return data, {"model": model, "attempts": attempt + 1, "repair_errors": errors,
                          "benchmark": report}
        except Exception as exc:
            errors.append(str(exc))
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": json.dumps(
                    {"validation_feedback": errors[-1],
                     "instruction": "Repair and return the complete scenario JSON."},
                    ensure_ascii=False)},
            ])
    raise ValueError("generation failed local validation: " + errors[-1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruction")
    parser.add_argument("--output")
    parser.add_argument("--scene-key", choices=SUPPORTED_SCENES, default="factory")
    parser.add_argument("--payload-policy", help="JSON object or file mapping robot types to capacities")
    parser.add_argument("--model", default=os.environ.get("EAI_SCENARIO_MODEL", "gpt-4o"))
    parser.add_argument("--attempts", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--manifest", action="store_true", help="Print registry and local-map hints without an API call")
    parser.add_argument("--schema", action="store_true", help="Print JSON Schema without assets or an API call")
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(scenario_schema(), indent=2))
        return 0
    try:
        occupancy = provider_map(scene_key=args.scene_key)
        payload_policy = None
        if args.payload_policy:
            raw_policy = Path(args.payload_policy).read_text(encoding="utf-8") if Path(args.payload_policy).exists() else args.payload_policy
            payload_policy = json.loads(raw_policy)
        if args.manifest:
            print(json.dumps(manifest(occupancy, scene_key=args.scene_key), ensure_ascii=False, indent=2))
            return 0
        if not args.instruction or not args.output:
            parser.error("--instruction and --output are required")
        if Path(args.output).exists():
            raise FileExistsError(f"output already exists: {args.output}")
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required; --manifest and --schema work offline")
        from openai import OpenAI

        with OpenAI(timeout=60, max_retries=0) as client:
            data, report = generate(client, args.instruction, model=args.model,
                                    occupancy=occupancy, attempts=args.attempts,
                                    scene_key=args.scene_key,
                                    payload_policy=payload_policy)
        write_json(args.output, data)
        print(json.dumps({"output": str(args.output), **report}, ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(f"Factory generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
