"""Generate paired Factory instance families on the frozen V2 episode contract.

Sampler mode (default, offline): a skeleton recipe is materialized by the
seeded sampler -- one base placement, per-condition deltas, full local gates.
Proposal mode (--instruction): the LLM proposes the semantic task spec, the
sampler still owns geometry. Every emitted instance is a launchable V2 JSON.
"""

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    from common import write_json
else:
    from .common import write_json

from EAI.scenario_spec.geometry import provider_map
from EAI.scenario_spec.operators import (
    SKELETONS,
    expand_family,
    skeleton_family,
    task_spec_manifest,
    task_spec_schema,
    validate_task_spec,
)
from EAI.scenario_spec.templates import template_for
from EAI.scenario_spec.validation import ScenarioError


def propose_task_spec(client, instruction, *, model, template, attempts=3):
    """LLM proposes a semantic task spec; local gates judge and drive repair."""
    if not instruction.strip():
        raise ValueError("instruction must be non-empty")
    messages = [
        {"role": "system", "content":
            "Design a task over the supplied robot roster, scene template and asset manifest. "
            "You choose zones, registered assets, robot teams, and a phase DAG over refs. "
            "The sampler places everything on free anchors; local gates verify semantics, "
            "placement, and connectivity. A prescribed multi-robot allocation is not proof "
            "that a team or heterogeneous team is necessary; do not make that claim unless "
            "the returned minimum-team certificate establishes it. Do not invent coordinates, "
            "paths, payload capacities, or capabilities."},
        {"role": "user", "content": json.dumps(
            {"instruction": instruction, "manifest": task_spec_manifest(template)},
            ensure_ascii=False)},
    ]
    errors = []
    for attempt in range(attempts):
        response = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema", "json_schema": {
                "name": "factory_task_spec", "strict": True,
                "schema": task_spec_schema(template),
            }},
        )
        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            raise ValueError("model refused the proposal request")
        if choice.finish_reason != "stop":
            raise ValueError(f"model output was incomplete: {choice.finish_reason}")
        content = choice.message.content or ""
        try:
            spec = json.loads(content)
            validate_task_spec(spec, template)
            return spec, {"model": model, "attempts": attempt + 1, "repair_errors": errors}
        except (ValueError, ScenarioError) as exc:
            errors.append(str(exc))
            messages.extend([
                {"role": "assistant", "content": content},
                {"role": "user", "content": json.dumps(
                    {"validation_error": str(exc),
                     "instruction": "Repair this error and return the complete task spec JSON."})},
            ])
    raise ValueError("proposal failed local validation: " + errors[-1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skeleton", choices=sorted(SKELETONS), help="Recipe family to materialize")
    parser.add_argument("--scene-key", choices=["factory", "warehouse_nvd", "modern_house_nvd"], default="factory")
    parser.add_argument("--instruction", help="LLM proposal mode; requires OPENAI_API_KEY")
    parser.add_argument("--model", default=os.environ.get("EAI_SCENARIO_MODEL", "gpt-4o"))
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--family-id")
    parser.add_argument("--out-dir")
    parser.add_argument("--levels", nargs="+", choices=["low", "high"],
                        help="TRN-01 mass sweep conditions (default: low high)")
    parser.add_argument("--target", help="scientific_target recorded for --instruction families")
    parser.add_argument("--payload-policy",
                        help="JSON/file mapping robot types to experimental payload capacities")
    parser.add_argument("--manifest", action="store_true",
                        help="Print the template/asset/skeleton manifest without generating")
    args = parser.parse_args(argv)

    try:
        occupancy = provider_map(scene_key=args.scene_key)
        template = template_for(args.scene_key, occupancy)
        if args.skeleton and args.scene_key != "factory":
            raise ValueError("Factory skeleton recipes cannot be silently reused for NVD scenes; use --instruction")
        from tools.scenario_generation.validate_factory import _load_payload_policy
        payload_policy = _load_payload_policy(args.payload_policy)
        if args.manifest:
            print(json.dumps(task_spec_manifest(template), ensure_ascii=False, indent=2))
            return 0
        if not args.skeleton and not args.instruction:
            parser.error("one of --skeleton or --instruction is required")

        proposal_meta = None
        if args.instruction:
            if not os.environ.get("OPENAI_API_KEY"):
                raise ValueError("OPENAI_API_KEY is required for --instruction")
            from openai import OpenAI

            with OpenAI(timeout=240, max_retries=1) as client:
                spec, proposal_meta = propose_task_spec(
                    client, args.instruction, model=args.model, template=template)
            family_spec = {
                "family_id": args.family_id or f"llm_{args.seed}",
                "scene_key": args.scene_key,
                "seed": args.seed,
                "scientific_target": args.target,
                "base_spec": spec,
                "conditions": [{"id": "base", "level": "base", "delta": []}],
            }
        else:
            family_spec = skeleton_family(
                args.skeleton, family_id=args.family_id or f"{args.skeleton.lower().replace('-', '_')}_{args.seed}",
                seed=args.seed,
                mass_levels=tuple(args.levels) if args.levels else ("low", "high"))

        out_dir = args.out_dir or os.path.join("tmp", "generated_scenarios", family_spec["family_id"])
        record, instances = expand_family(
            family_spec, template, occupancy, payload_policy=payload_policy)

        if Path(out_dir).exists() and any(Path(out_dir).iterdir()):
            raise FileExistsError(f"output directory is not empty: {out_dir}")
        outputs = {}
        for condition_id, data in instances.items():
            name = f"{family_spec['family_id']}_{condition_id}.json"
            write_json(os.path.join(out_dir, name), data)
            outputs[condition_id] = os.path.join(out_dir, name)
        write_json(os.path.join(out_dir, "family.json"), record)

        report = {
            "family_id": family_spec["family_id"],
            "template": template.key,
            "map_sha256": template.map_sha256,
            "seed": args.seed,
            "scientific_target": record["scientific_target"],
            "payload_policy": payload_policy,
            "conditions": {key: {"path": value} for key, value in outputs.items()},
            "launch": [f"python simulator.py --scenario-file {value}" for value in outputs.values()],
        }
        if proposal_meta:
            report["proposal"] = proposal_meta
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, ScenarioError, OSError) as exc:
        print(f"Family generation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
