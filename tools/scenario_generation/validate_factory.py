"""Validate a scenario without starting Isaac Sim, downloading assets or calling an LLM."""

import argparse
import json
import sys

if __package__ in (None, ""):
    import common
else:
    from . import common

from EAI.scenario_spec import load_scenario
from EAI.scenario_spec.evaluation import validate_evaluation
from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
from EAI.scenario_spec.oracle import validate_level_routing, validate_oracle
from EAI.scenario_spec.templates import template_for
from EAI_assets.asset_requirements import resolve_selection


def _load_payload_policy(raw):
    if not raw:
        return None
    import json as _json
    from pathlib import Path

    text = Path(raw).read_text(encoding="utf-8") if Path(raw).exists() else raw
    policy = _json.loads(text)
    from EAI.scenario_spec.schema import ROBOT_PROFILES

    import math
    unknown = sorted(set(policy) - set(ROBOT_PROFILES)) if isinstance(policy, dict) else []
    if unknown:
        raise ValueError(f"payload policy has unknown robot types {unknown}; "
                         f"valid: {sorted(ROBOT_PROFILES)}")
    if not isinstance(policy, dict) or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v)) and float(v) > 0 for v in policy.values()):
        raise ValueError("payload policy must map robot types to finite positive capacities")
    return {k: float(v) for k, v in policy.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("--schema-only", action="store_true", help="Skip occupancy checks, not semantic checks")
    parser.add_argument("--payload-policy",
                        help="Inline JSON or file mapping robot types to capacity kg; enables "
                             "mass-aware eligibility and the forced-cooperation certificate")
    args = parser.parse_args(argv)
    try:
        data = load_scenario(args.scenario)
        payload_policy = _load_payload_policy(args.payload_policy)
        report = {"status": "schema_validated", "scene_key": data.get("scene_key", "factory")}
        if not args.schema_only:
            occupancy = provider_map(scene_key=data.get("scene_key", "factory"))
            template = template_for(data.get("scene_key", "factory"), occupancy)
            report = validate_geometry(data, occupancy)
            report["connectivity"] = validate_connectivity(data, occupancy)
            report["oracle_feasibility"] = validate_oracle(
                data, occupancy, payload_policy=payload_policy,
                template=template)
            report["level_routing"] = validate_level_routing(data, template)
            report["evaluation"] = validate_evaluation(data)
        from EAI.scenario_spec.benchmark_contract import benchmark_eligibility_contract
        report["benchmark_eligibility"] = benchmark_eligibility_contract(
            data, payload_policy=payload_policy)
        report["requirements"] = [item.id for item in resolve_selection(data).requirements]
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["benchmark_eligibility"]["eligible"] else 2
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
