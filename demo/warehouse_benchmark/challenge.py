"""Compile Warehouse scenarios into the shared four-layer challenge bundle."""

from __future__ import annotations

from pathlib import Path

from demo.benchmark.scene_challenge import audit_generated_scene_scenario, compile_scene_challenge, evaluate_challenge_report, generate_scene_scenario, review_task_design, write_scene_challenge
from . import config, scenario


# The challenge compiler defaults to the reviewed business task.  The
# navigation example remains available for a bounded smoke run, but must not
# silently become the published Warehouse benchmark.
DEFAULT_SCENARIO = Path(__file__).parent / "scenarios" / "business_logistics_01.json"


def generate_scenario(seed: int = 17):
    data = generate_scene_scenario(
        scene_key=config.SCENE_KEY,
        task_name="warehouse_nvd_coordinated_logistics",
        seed=seed,
        # Re-derived after the map_server PNG orientation was checked against
        # the generated USD walls (tools.assets.rebuild_nvd_products).
        origin=(-10.0, -11.75),
        secondary_ground_offset=(6.425, 0.0),
    )
    audit_generated_scene_scenario(data)
    design = review_task_design(data, scenario.TASK_PROFILE, scenario.TASK_BLUEPRINT)
    if not design["validated"]:
        raise ValueError(f"generated Warehouse task failed design review: {design['findings']}")
    return data


def evaluate_report(bundle, report):
    """Evaluate Warehouse-specific logistics predicates plus shared metrics."""
    from demo.benchmark.scene_challenge import evaluate_challenge_report
    result = evaluate_challenge_report(bundle, report)
    public = bundle["public_task"]
    skills = {objective["skill"] for objective in public["objectives"]}
    resources = set(public.get("resources", ()))
    result["scene_semantics"] = {
        "domain": "warehouse_logistics",
        "joint_transport_declared": "joint_transport" in skills,
        "transport_lane_declared": "transport_lane" in resources,
        "design_validated": {"joint_transport", "transport_object"} <= skills and "transport_lane" in resources,
        "validated": result["domain_evaluation"]["validated"],
    }
    return result


def _requirements(data: dict) -> list[dict]:
    return [{"id": node["id"], "domain": "aerial" if node["skill_key"] == "aerial_scan" else "ground",
             "navigation": node["skill_key"] in {"navigate", "aerial_scan", "transport_object", "joint_transport"},
             "manipulation_interface": node["skill_key"] == "transport_object",
             "skill": node["skill_key"], "resources": list(node.get("resources") or []),
             "prerequisites": list(node.get("prerequisites") or [])}
            for node in data["phase_graph"]["nodes"]]


def compile_challenge(scenario_path: Path = DEFAULT_SCENARIO):
    import json
    from demo.benchmark.authoring import build_public_task_skeleton, validate_authored_business_scenario
    data = json.loads(Path(scenario_path).read_text(encoding="utf-8"))
    authoring = validate_authored_business_scenario(data)
    admission = authoring["generation_admission"]
    return compile_scene_challenge(
        scenario_path,
        scene_asset=config.USD_RELATIVE_PATH,
        task_contract={"scene_key": config.SCENE_KEY, "profile": scenario.TASK_PROFILE,
                       "blueprint": scenario.TASK_BLUEPRINT},
        participant_requirements=_requirements(data),
        qa_requirements={"scene_contract": "warehouse_manifest", "task_profile": scenario.TASK_PROFILE,
                         "generation_admission": admission,
                         "authoring_admission": authoring,
                         "task_design_review": review_task_design(data, scenario.TASK_PROFILE, scenario.TASK_BLUEPRINT)},
        business_rules={"task_profile": scenario.TASK_PROFILE,
        "task_skeleton": build_public_task_skeleton(config.SCENE_KEY),
        "participant_choice": "Participant controls allocation, navigation, kinematic-transport team selection, reassignment, and consumption of observations actually delivered by the runtime communication queue. Wait remains benchmark-managed.",
        "object_ownership": "Object ownership changes only through runtime pickup/release events; reassignment is rejected while an object is carried."},
        baseline_entrypoint="demo.benchmark.scene_challenge",
    )


def main(argv=None):
    import argparse
    from tools.scenario_generation.common import write_json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generate-scenario", type=Path)
    args = parser.parse_args(argv)
    if args.generate_scenario:
        data = generate_scenario()
        write_json(args.generate_scenario, data)
        from demo.benchmark.diagnostic_audit import write_audit
        audit_path = write_audit(args.generate_scenario)
        print(f"scenario: {args.generate_scenario}")
        print(f"audit: {audit_path}")
        return 0
    if args.output_dir is None:
        parser.error("--output-dir is required unless --generate-scenario is used")
    for name, path in write_scene_challenge(compile_challenge(args.scenario), args.output_dir).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["DEFAULT_SCENARIO", "compile_challenge", "evaluate_report", "generate_scenario"]
