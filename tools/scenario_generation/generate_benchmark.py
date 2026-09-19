"""Generate benchmark candidates through the local admission loop."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from tools.scenario_generation.benchmark_generator import (
    BenchmarkGenerator,
    openai_candidate_provider,
)
from tools.scenario_generation.common import write_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene-key", required=True,
                        choices=["factory", "warehouse_nvd", "modern_house_nvd"])
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=os.environ.get("EAI_SCENARIO_MODEL", "gpt-4o"))
    parser.add_argument("--payload-policy", required=True,
                        help="JSON object or JSON file mapping robot types to capacities")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--task-id", choices=["F-INS-01", "F-TRAFFIC-01", "F-COV-01"],
                        help="Factory task-family contract; omit for a generic scene candidate")
    args = parser.parse_args(argv)
    raw = Path(args.payload_policy).read_text(encoding="utf-8") if Path(args.payload_policy).exists() else args.payload_policy
    policy = json.loads(raw)
    from EAI.scenario_spec.geometry import provider_map
    occupancy = provider_map(scene_key=args.scene_key)
    generator = BenchmarkGenerator(args.scene_key, occupancy=occupancy,
                                   payload_policy=policy, require_team_necessity=True,
                                   require_long_horizon=True, task_id=args.task_id)
    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is required for LLM generation")
    from openai import OpenAI
    with OpenAI(timeout=240, max_retries=0) as client:
        provider = openai_candidate_provider(client, model=args.model,
                                             scene_key=args.scene_key, occupancy=occupancy,
                                             instruction=args.instruction, task_id=args.task_id)
        result = generator.run(provider, max_rounds=args.rounds).as_dict()
    if not result["pool"]:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    write_json(output, result["pool"][0]["scenario"])
    result["published"] = str(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
