"""Read frozen scheduled-run JSON and write reproducible v3 score reports.

Legacy input values for D1/D4 are external-evaluator normalized run scores, not
planner claims. D3 has a separate relation-support record. D2 holds four
success levels. D5/D6 bind normalized values to a frozen directory of runs.
Unknowns stay in the scheduled directory.
New D1 payloads with an ``instances`` roster evaluate first-commit journals,
semantic observations and separate feasibility-class coverage. The legacy D1
scalar path cannot qualify formal release or apply that class-specific rule.
This analysis does not grant physical qualification or catalog certification.
"""
from dataclasses import dataclass, asdict
import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence

from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from .statistics_v3 import (InstanceV3, ClusterDesignV3, cluster_interval,
                            d2_headline_interval, d2_unknown_identification_interval, fixed_identification,
                            identification_interval)


UNKNOWN_RUN_STATUSES = frozenset({"infrastructure_unknown", "unknown-record", "pair-invalid"})


@dataclass(frozen=True)
class ScheduledRunV3:
    run_id: str
    instance_id: str
    stratum: str
    repeat_id: str
    checkpoint_digest: str
    evidence_digest: str
    values: tuple[float | None, ...]
    status: str = "observed"
    stream_id: str = ""
    weight: float | None = None
    paired_block_id: str = ""
    paired_attempt_id: str = ""

    def __post_init__(self):
        for name in ("run_id", "instance_id", "stratum", "repeat_id", "checkpoint_digest", "evidence_digest"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"{name} is required; unknown records retain original failure evidence")
        if self.status not in {"observed", "method_failure", *UNKNOWN_RUN_STATUSES}:
            raise ValueError("unsupported scheduled-run status")
        if self.status in UNKNOWN_RUN_STATUSES - {"pair-invalid"} and not any(v is None for v in self.values):
            raise ValueError("unknown record must mark unknown outcomes")
        if self.status not in UNKNOWN_RUN_STATUSES and any(v is None for v in self.values):
            raise ValueError("only registered unknown dispositions permit missing values")
        if self.status in {"unknown-record", "pair-invalid"} and not all((self.paired_block_id, self.paired_attempt_id)):
            raise ValueError("paired unknown disposition requires block and attempt identity")
        if bool(self.paired_block_id) != bool(self.paired_attempt_id):
            raise ValueError("paired block and attempt identities must be supplied together")

    @classmethod
    def from_paired_attempt(cls, receipt, *, ledger, branch_id: str, values, **identity):
        from .protocol_v3 import PairedBlockAttemptReceiptV3, PairedBlockRerunLedgerV3
        if not isinstance(receipt, PairedBlockAttemptReceiptV3):
            raise ValueError("validated paired attempt receipt required")
        if not isinstance(ledger, PairedBlockRerunLedgerV3):
            raise ValueError("paired rerun ledger is required")
        # The receipt must carry the complete frozen registration identity and
        # must have been issued by the supplied append-only ledger.
        ledger.validate_receipt(receipt)
        if branch_id not in receipt.executed_branch_ids:
            raise ValueError("scheduled branch is absent from paired attempt receipt")
        if identity.get("checkpoint_digest") != receipt.checkpoint_digest:
            raise ValueError("scheduled run checkpoint is not bound to paired receipt")
        branch_index = receipt.executed_branch_ids.index(branch_id)
        if identity.get("evidence_digest") != receipt.branch_result_digests[branch_index]:
            raise ValueError("scheduled run evidence is not bound to paired branch result")
        status = "observed" if receipt.status == "valid-pair" else receipt.status
        paired = {"paired_block_id": receipt.block_id, "paired_attempt_id": receipt.attempt_id}
        return cls(values=tuple(values), status=status, **paired, **identity)


def _status_counts(runs: Sequence[ScheduledRunV3]) -> dict[str, int]:
    return {
        "method_failures": sum(r.status == "method_failure" for r in runs),
        "infrastructure_unknown": sum(r.status == "infrastructure_unknown" for r in runs),
        "unknown_records": sum(r.status == "unknown-record" for r in runs),
        "pair_invalid": sum(r.status == "pair-invalid" for r in runs),
    }


def _design(runs, weights, endpoint=None):
    groups = {}
    for row in runs:
        groups.setdefault(row.instance_id, []).append(row)
    instances = []
    for iid, rows in groups.items():
        if len(rows) != 3 or len({r.repeat_id for r in rows}) != 3:
            raise ValueError("all three scheduled repeats required; missing rows are not silently deleted")
        if len({(r.stratum, r.stream_id) for r in rows}) != 1:
            raise ValueError("instance strata/stream identity changes across repeats")
        if len({len(r.values) for r in rows}) != 1:
            raise ValueError("repeat vector shape mismatch")
        def completed(row, index):
            if row.values[index] is not None:
                return row.values[index]
            if isinstance(endpoint, Mapping):
                return endpoint[(row.run_id, index)]
            return endpoint[index]
        values = tuple((sum(completed(r, j) for r in rows)/3
                        if endpoint is not None else
                        (None if any(r.values[j] is None for r in rows) else sum(r.values[j] for r in rows)/3))
                       for j in range(len(rows[0].values)))
        instances.append(InstanceV3(iid, rows[0].stratum, values, rows[0].stream_id))
    return ClusterDesignV3(instances, weights)


def _d2_unknown_designs(runs, weights):
    unknown = tuple(any(row.values[index] is None for row in runs) for index in range(4))
    all_failure = _design(runs, weights, (0, 0, 0, 0))
    all_success = _design(runs, weights, (1, 1, 1, 1))
    low = _design(runs, weights, (1, 0, 0, 0))
    high_fill = {(row.run_id, index): (0 if index == 0 else 1)
                 for row in runs for index in range(4) if row.values[index] is None}
    preliminary = _design(runs, weights, high_fill)
    undefined_possible = preliminary.means()[0] == 0 and unknown[0]
    if preliminary.means()[0] == 0 and unknown[0]:
        instance_counts = {}
        for row in runs:
            instance_counts.setdefault(row.stratum, set()).add(row.instance_id)
        candidates = []
        for row in runs:
            if row.values[0] is None:
                run_weight = weights[row.stratum] / (len(instance_counts[row.stratum]) * 3)
                candidates.append((run_weight, row.run_id))
        _, selected = min(candidates)
        high_fill[(selected, 0)] = 1
    high = _design(runs, weights, high_fill)
    return low, high, all_failure, all_success, unknown, undefined_possible


def analyze_dimension(score_id: str, runs: Sequence[ScheduledRunV3], *, stratum_weights: Mapping[str, float], alpha=.05, seed=0, resamples=2000, fixed_catalog=None):
    if score_id not in {f"D{i}" for i in range(1,7)} or not runs or len({r.run_id for r in runs}) != len(runs):
        raise ValueError("supported score and unique scheduled run directory required")
    if score_id == "D3":
        raise ValueError("D3 requires obligation relation supports; use d3_relation_report")
    width = 4 if score_id == "D2" else 1
    if any(len(r.values) != width for r in runs):
        raise ValueError("unexpected dimension outcome vector")
    # Validate values before averaging, so out-of-range repeats cannot cancel.
    import math
    if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for r in runs for v in r.values):
        raise ValueError("normalized evaluator values must lie in [0,1]")
    if score_id in {"D2", "D5"} and any(v is not None and v not in (0, 1) for r in runs for v in r.values):
        raise ValueError("D2 mission success and D5 unit acceptance must be binary per run")
    has_unknown = any(v is None for r in runs for v in r.values)
    if has_unknown and score_id in {"D1", "D2", "D4"}:
        # Validate the frozen three-repeat directory before endpoint
        # completion; a missing scheduled row is not an unknown observation.
        if score_id == "D2":
            low, high, all_failure, all_success, unknown, undefined = _d2_unknown_designs(runs, stratum_weights)
            result = d2_unknown_identification_interval(
                low, high, all_failure, all_success,
                unknown_components=unknown,
                undefined_completion_possible=undefined,
                alpha=alpha,
            )
        else:
            design = _design(runs, stratum_weights, None)
            result = identification_interval(
                design, lambda means: 100 * means[0], d2=False, alpha=alpha,
                seed=seed, resamples=resamples)
        if result.get("status") == "range":
            if any(r.status == "pair-invalid" for r in runs):
                reason = "paired_block_invalid_identification_range"
            elif any(r.status == "unknown-record" for r in runs):
                reason = "residual_unknown_record_identification_range"
            else:
                reason = "residual_infrastructure_unknown_identification_range"
            result = {**result, "reason": reason}
        return {"score_id": score_id, **result,
                "scheduled_runs": len(runs),
                **_status_counts(runs),
                "input_digest": canonical_digest([asdict(r) for r in runs]),
                "paper_commit": PAPER_COMMIT,
                "scorebook_digest": default_scorebook_v3().digest(),
                "physical_qualified": False}
    if score_id in {"D5", "D6"}:
        from .fixed_catalog_v3 import FixedCatalogV3
        if not isinstance(fixed_catalog, FixedCatalogV3) or fixed_catalog.score_id != score_id:
            raise ValueError("D5/D6 require a frozen catalog, not self-reported run weights")
        result = fixed_catalog.analyze(runs, alpha=alpha)
    else:
        endpoints = ((1,0,0,0), (0,1,1,1)) if score_id == "D2" else ((0,), (1,))
        designs = [_design(runs, stratum_weights, end) for end in endpoints]
        if score_id == "D2":
            headline_design = _design(runs, stratum_weights, (0, 0, 0, 0))
            headline = d2_headline_interval(headline_design, alpha=alpha, seed=seed, resamples=resamples)
            result = {"status": headline["status"], **headline, "endpoints": [headline, headline]}
            if headline["point"] is not None:
                result["identification"] = [headline["point"], headline["point"]]
            if headline["lower"] is not None and headline["upper"] is not None:
                result["interval"] = [headline["lower"], headline["upper"]]
        else:
            ends = [asdict(cluster_interval(d, lambda m: 100*m[0], alpha=alpha, seed=seed, resamples=resamples)) for d in designs]
            result = {"status": "range" if ends[0]["point"] != ends[1]["point"] else "valid", "identification": [e["point"] for e in ends], "interval": [ends[0]["lower"], ends[1]["upper"]], "endpoints": ends}
    return {"score_id": score_id, **result, "scheduled_runs": len(runs), **_status_counts(runs), "input_digest": canonical_digest([asdict(r) for r in runs]), "paper_commit": PAPER_COMMIT, "scorebook_digest": default_scorebook_v3().digest(), "inference": {"alpha": alpha, "seed": seed, "resamples": resamples, "quantile_rule": "empirical_inverse_cdf", "lower_endpoint_lower_tail": alpha/2, "upper_endpoint_upper_tail": alpha/2}, "physical_qualified": False}


def d5_run_weights(directory: Mapping[str, Mapping[str, Sequence[str]]]):
    """Equal family/case/unit means, then three independent complete repeats."""
    if not directory or any(not cases or any(not units or len(units) != len(set(units)) for units in cases.values()) for cases in directory.values()):
        raise ValueError("nonempty unique certified family/case/unit directory required")
    return {(family, case, unit, str(repeat)): 1/(len(directory)*len(cases)*len(units)*3) for family,cases in directory.items() for case,units in cases.items() for unit in units for repeat in range(1,4)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    if payload["paper_commit"] != PAPER_COMMIT:
        raise ValueError("input binds a different paper version")
    if payload["score_id"] == "D1" and "instances" in payload:
        from .organization_v3 import organization_report_from_payload
        result = organization_report_from_payload(payload)
    elif payload["score_id"] == "D3":
        from .relations_v3 import RelationRunV3, d3_relation_report
        result = d3_relation_report([RelationRunV3(**r) for r in payload["runs"]], **payload.get("inference", {}))
    else:
        catalog = None
        if payload["score_id"] in {"D5", "D6"}:
            from .fixed_catalog_v3 import FixedCatalogV3, FixedRunSpecV3
            raw = payload["fixed_catalog"]
            catalog = FixedCatalogV3(
                raw["score_id"],
                tuple(FixedRunSpecV3(**r) for r in raw["entries"]),
                excluded_families=tuple(raw.get("excluded_families", ())),
                excluded_family_case_counts=dict(raw.get("excluded_family_case_counts", {})),
            )
        result = analyze_dimension(payload["score_id"], [ScheduledRunV3(**r) for r in payload["runs"]], stratum_weights=payload["stratum_weights"], fixed_catalog=catalog, **payload.get("inference", {}))
    # JSON has no infinity; retention upper bounds are represented explicitly.
    result["scope"] = payload.get("scope", "unverified_evaluator_input")
    def portable(value):
        if isinstance(value, float) and value == float("inf"): return "infinity"
        if isinstance(value, dict): return {k: portable(v) for k,v in value.items()}
        if isinstance(value, (list, tuple)): return [portable(v) for v in value]
        return value
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(portable(result), ensure_ascii=False, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
