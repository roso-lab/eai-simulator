"""Enumerate analysis dependencies before running any task or simulator."""

from .schema import Design, digest
from . import ANALYSIS_VERSION


def expected_observations(design: Design) -> dict[tuple[str, ...], tuple[str, ...]]:
    """Exact scientific observation keys and their required measure union."""
    expected = {}
    for est in design.estimands:
        for unit in design.units:
            if est.support_id not in unit.supports:
                continue
            for term in est.terms:
                for system in (term.system_id,) if term.system_id else design.systems:
                    for repeat in unit.repeats:
                        key = (unit.id, term.member, system, term.condition_id,
                               term.branch_role, term.checkpoint_round, repeat)
                        expected.setdefault(key, set()).add(term.measure)
    return {key: tuple(sorted(measures)) for key, measures in sorted(expected.items())}


def readiness(design_data: dict) -> dict:
    design = Design.parse(design_data)
    expected = set()
    impossible = []
    clusters = {}
    for est in design.estimands:
        support = [u for u in design.units if est.support_id in u.supports]
        clusters[est.id] = {"unit": "family" if est.kind == "family_gap" else "base_instance",
                            "planned_count": len(est.families) if est.kind == "family_gap" else len(support)}
        for unit in support:
            if len(unit.repeats) < est.min_repeats:
                impossible.append(f"{est.id}/{unit.id}: planned repeats below required floor")
            for term in est.terms:
                for system in (term.system_id,) if term.system_id else design.systems:
                    for repeat in unit.repeats:
                        expected.add((unit.id, dict(unit.members)[term.member], term.member, system,
                                      term.condition_id, term.branch_role, term.checkpoint_round, repeat))
        for cid in est.cells:
            units = [u for u in support if u.cell_id == cid]
            if not units:
                impossible.append(f"{est.id}/{cid}: empty planned support")
            if len(units) < est.min_units:
                impossible.append(f"{est.id}/{cid}: planned units below endpoint floor")
        for family in est.families:
            if sum(u.family_id == family.id for u in support) < family.min_pairs:
                impossible.append(f"{est.id}/{family.id}: planned pairs below family floor")
    for cell in design.cells:
        if sum(u.cell_id == cell.id for u in design.units) < cell.min_instances:
            impossible.append(f"{cell.id}: planned units below infrastructure floor")
    return {"schema": ANALYSIS_VERSION + ".readiness", "design_digest": design.digest,
            "status": "ready_for_collection" if not impossible else "design_insufficient",
            "registered_member_instances": sum(len(u.members) for u in design.units),
            "registered_units": len(design.units), "required_continuations": len(expected),
            "inference_clusters_by_estimand": clusters,
            "expected_identity_digest": digest(sorted(expected)), "reasons": sorted(set(impossible)),
            "qualification": "structural_planning_only", "publishable": False,
            "required_external_inputs": ["task_and_system_versions", "independent_evaluator",
                                         "attempt_selection_policy", "replay_and_transfer_certificates",
                                         "registered_inferential_tests_and_release_gates"]}
