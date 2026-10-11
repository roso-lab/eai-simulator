"""Frozen-support descriptive estimators, with cluster-preserving resampling."""

from __future__ import annotations

from collections import Counter
import hashlib
import random
import platform
from pathlib import Path
from statistics import fmean
from typing import Any

from . import ANALYSIS_VERSION
from .schema import Dataset, Estimand, record_key, digest
from .primitives import competing_risks, percentile


def _mean_vectors(vectors: list[list[float]]) -> list[float]:
    return [fmean(column) for column in zip(*vectors)]


def _outcome(est: Estimand, vectors: list[list[float]], *, cell: bool = False) -> float | None:
    means = _mean_vectors(vectors)
    if est.kind in {"mean", "cif", "rmst"}:
        return means[0]
    if est.kind == "linear":
        return sum(a * b for a, b in zip(means, est.coefficients))
    if est.kind in {"ratio_of_means", "mean_of_cell_ratios"}:
        if means[1] <= 0 or means[1] < est.denominator_floor:
            return None
        return means[0] / means[1]
    raise ValueError(f"not a panel estimand: {est.kind}")


def _aligned(dataset: Dataset, est: Estimand, system: str) -> tuple[dict, list, set]:
    index = {record_key(r): r for r in dataset.records}
    bundles, audit, used = {}, [], set()
    for unit in sorted(dataset.design.units, key=lambda u: u.id):
        if est.support_id not in unit.supports:
            continue
        repeats, reasons, missing = [], Counter(), []
        for repeat in sorted(unit.repeats):
            values, selected = [], []
            for term_index, term in enumerate(est.terms):
                key = (unit.id, term.member, term.system_id or system, term.condition_id,
                       term.branch_role, term.checkpoint_round, repeat)
                row = index.get(key)
                if row is None:
                    reasons["missing_record"] += 1
                    missing.append({"repeat_id": repeat, "term": term_index, "identity": list(key)})
                    continue
                used.add(key)
                if row["status"] != "valid":
                    reasons[row["status"] + ":" + row["reason"]] += 1
                    continue
                if term.measure not in row["values"]:
                    reasons["missing_measure:" + term.measure] += 1
                    continue
                values.append(row["values"][term.measure])
                selected.append(row)
            if len(values) != len(est.terms):
                continue
            # Conditions/branches from the same system must share the frozen
            # evaluator pairing envelope. Across systems checkpoints may differ.
            contexts: dict[str, set[str]] = {}
            for row in selected:
                contexts.setdefault(row["system_id"], set()).add(row["pair_context_digest"])
            if any(len(v) != 1 for v in contexts.values()):
                reasons["pair_context_mismatch"] += 1
                continue
            repeats.append({"repeat_id": repeat, "values": values})
        valid = unit.infrastructure_status == "valid" and len(repeats) >= est.min_repeats
        if unit.infrastructure_status != "valid":
            reasons["unit_infrastructure:" + str(unit.reason)] += 1
        elif len(repeats) < est.min_repeats:
            reasons["repeat_floor"] += 1
        audit.append({"unit_id": unit.id, "cell_id": unit.cell_id, "family_id": unit.family_id,
                      "planned_repeats": len(unit.repeats), "aligned_repeats": len(repeats),
                      "included": valid, "exclusions": dict(sorted(reasons.items())),
                      "missing": missing})
        if valid:
            bundles[unit.id] = repeats
    return bundles, audit, used


def _cell_vector(dataset: Dataset, est: Estimand, ids: list[str], bundles: dict) -> tuple[list | None, dict]:
    if est.kind in {"cif", "rmst"}:
        measure = next(m for m in dataset.design.measures if m.id == est.terms[0].measure)
        samples = [r["values"][0] for uid in ids for r in bundles[uid]]
        curve = competing_risks(samples, horizon=est.horizon, causes=measure.causes)
        if curve["status"] != "computed":
            return None, curve
        value = curve["cif"][est.cause] if est.kind == "cif" else curve["rmst"]
        return [value], curve
    vectors = [_mean_vectors([r["values"] for r in bundles[uid]]) for uid in ids]
    return _mean_vectors(vectors), {"instance_term_sums": [sum(v[j] for v in vectors) for j in range(len(est.terms))]}


def _panel_value(dataset: Dataset, est: Estimand, groups: dict, bundles: dict) -> tuple[float | None, dict]:
    vectors, details = [], {}
    for cell in sorted(groups):
        vector, detail = _cell_vector(dataset, est, groups[cell], bundles)
        details[cell] = detail
        if vector is None:
            return None, details
        vectors.append(vector)
    if est.kind == "mean_of_cell_ratios":
        values = [_outcome(est, [v], cell=True) for v in vectors]
        value = fmean(values) if all(v is not None for v in values) else None
    else:
        value = _outcome(est, vectors)
    return value, {"cells": details, "panel_term_means": _mean_vectors(vectors),
                   "cell_weights": {cell: 1 / len(groups) for cell in sorted(groups)}}


def _family_value(est: Estimand, family_ids: list[str], groups: dict, bundles: dict) -> tuple[float, dict]:
    families = {f.id: f for f in est.families}
    source, target, raw_source, raw_target = [], [], [], []
    details = {}
    for fid in family_ids:
        family = families[fid]
        means = _mean_vectors([_mean_vectors([r["values"] for r in bundles[uid]]) for uid in groups[fid]])
        src, tgt = ((family.orientation * value - family.anchor) / family.scale for value in means)
        source.append(src)
        target.append(tgt)
        raw_source.append(means[0])
        raw_target.append(means[1])
        details[fid] = {"source": src, "target": tgt, "gap": tgt - src,
                        "source_raw": means[0], "target_raw": means[1]}
    return fmean(target) - fmean(source), {"families": details, "source": fmean(source), "target": fmean(target),
                                            "source_raw": fmean(raw_source), "target_raw": fmean(raw_target)}


def _bootstrap(dataset: Dataset, est: Estimand, system: str, groups: dict, bundles: dict) -> dict:
    seed = int(hashlib.sha256(f"{dataset.design.seed}\x1f{est.id}\x1f{system}".encode()).hexdigest(), 16)
    rng = random.Random(seed)
    family = est.kind == "family_gap"
    cluster_count = len(groups) if family else sum(len(ids) for ids in groups.values())
    if (family and cluster_count < 2) or (not family and any(len(ids) < 2 for ids in groups.values())):
        return {"status": "N/A", "reason": "insufficient_independent_clusters", "low": None, "high": None,
                "cluster": "family" if family else "base_instance", "independent_clusters": cluster_count}
    draws, undefined = [], 0
    for _ in range(dataset.design.resamples):
        if family:
            ids = sorted(groups)
            sampled = [rng.choice(ids) for _ in ids]
            value, _ = _family_value(est, sampled, groups, bundles)
        else:
            sampled = {cell: [rng.choice(ids) for _ in ids] for cell, ids in sorted(groups.items())}
            value, _ = _panel_value(dataset, est, sampled, bundles)
        if value is None:
            undefined += 1
        else:
            draws.append(value)
    # Reject a truncated bootstrap distribution: silently dropping undefined
    # ratios or unsupported event horizons creates selection bias.
    return {"status": "N/A" if undefined else "computed", "reason": "undefined_resamples" if undefined else None,
            "low": None if undefined else percentile(draws, .025),
            "high": None if undefined else percentile(draws, .975),
            "resamples": dataset.design.resamples, "undefined_resamples": undefined,
            "cluster": "family" if family else "base_instance", "independent_clusters": cluster_count,
            "method": "stratified_cluster_percentile", "confidence": .95, "seed": dataset.design.seed}


def _estimate(dataset: Dataset, est: Estimand, system: str) -> tuple[dict, set]:
    bundles, audit, used = _aligned(dataset, est, system)
    supported = [u for u in dataset.design.units if est.support_id in u.supports]
    cells = {c.id: c for c in dataset.design.cells}
    groups, coverage, blockers = {}, {}, []
    for cell_id in sorted(est.cells):
        cell = cells[cell_id]
        # Infrastructure coverage is independent of endpoint outcome/support.
        planned = [u for u in dataset.design.units if u.cell_id == cell_id]
        infra = [u for u in planned if u.infrastructure_status == "valid"]
        eligible = [u for u in supported if u.cell_id == cell_id]
        included = sorted(u.id for u in eligible if u.id in bundles)
        gamma = len(infra) / len(planned) if planned else 0
        common = len(included) / len(eligible) if eligible else 0
        passed = len(infra) >= cell.min_instances and gamma >= cell.min_coverage
        if not passed:
            blockers.append(f"infrastructure_coverage:{cell_id}")
        # Endpoint-specific supports never substitute for package coverage.
        if len(included) < est.min_units or common < est.min_coverage:
            blockers.append(f"endpoint_coverage:{cell_id}")
        groups[cell_id] = included
        coverage[cell_id] = {"planned": len(planned), "infrastructure_valid": len(infra), "fraction": gamma,
                             "support_planned": len(eligible), "included": len(included), "support_fraction": common,
                             "infrastructure_passed": passed, "unit_ids": included,
                             "aligned_repeats": sum(len(bundles[uid]) for uid in included)}
    family_coverage = {}
    if est.kind == "family_gap":
        groups = {}
        for family in sorted(est.families, key=lambda f: f.id):
            planned = [u for u in supported if u.family_id == family.id]
            ids = sorted(u.id for u in planned if u.id in bundles)
            fraction = len(ids) / len(planned) if planned else 0
            if len(ids) < family.min_pairs or fraction < family.min_coverage:
                blockers.append(f"family_coverage:{family.id}")
            groups[family.id] = ids
            family_coverage[family.id] = {"planned_pairs": len(planned), "valid_pairs": len(ids), "fraction": fraction,
                                          "certificate_digest": family.certificate_digest}
    measure_map = {m.id: m for m in dataset.design.measures}
    term_units = [measure_map[t.measure].unit for t in est.terms]
    output_unit = term_units[0]
    if est.kind in {"cif", "family_gap"}:
        output_unit = "dimensionless"
    elif est.kind in {"ratio_of_means", "mean_of_cell_ratios"}:
        output_unit = "dimensionless" if term_units[0] == term_units[1] else f"({term_units[0]})/({term_units[1]})"
    result: dict[str, Any] = {"estimand_id": est.id, "system_id": system, "kind": est.kind, "unit": output_unit,
                              "status": "N/A", "estimate": None, "reasons": sorted(blockers),
                              "coverage": coverage, "family_coverage": family_coverage, "units": audit,
                              "interval": {"status": "N/A", "reason": "point_not_estimable", "low": None, "high": None},
                              "qualification": "descriptive_only", "publishable": False}
    if blockers:
        return result, used
    if est.kind == "family_gap":
        value, detail = _family_value(est, sorted(groups), groups, bundles)
    else:
        value, detail = _panel_value(dataset, est, groups, bundles)
    result["components"] = detail
    if value is None:
        result["reasons"] = ["denominator_or_event_horizon_unsupported"]
        return result, used
    result.update(status="computed", estimate=value, interval=_bootstrap(dataset, est, system, groups, bundles))
    return result, used


def analyze(design: dict, data: dict) -> dict:
    """Compute descriptive results; this function never issues release approval."""
    dataset = Dataset.parse(data, design)
    results, used = [], set()
    for est in sorted(dataset.design.estimands, key=lambda e: e.id):
        systems = ("registered_contrast",) if est.terms[0].system_id else sorted(dataset.design.systems)
        for system in systems:
            result, consumed = _estimate(dataset, est, system)
            results.append(result)
            used.update(consumed)
    code_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(__file__).parent.glob("*.py"))}
    return {"schema": ANALYSIS_VERSION + ".report", "design_id": dataset.design.id,
            "design_digest": dataset.design.digest, "dataset_digest": dataset.digest,
            "protocol_digest": dataset.design.protocol_digest, "authority": dataset.design.authority,
            "provenance": dataset.provenance, "qualification": "synthetic_validation" if dataset.provenance["kind"] == "synthetic" else "measured_unverified",
            "analysis_environment": {"python": platform.python_version(), "source_files": code_hashes, "code_digest": digest(code_hashes)},
            "publishable": False, "release_blockers": ["external_evaluator_and_protocol_release_verification_required"],
            "records": len(dataset.records), "unreferenced_records": sorted([list(record_key(r)) for r in dataset.records if record_key(r) not in used]),
            "results": results, "results_digest": digest(results)}
