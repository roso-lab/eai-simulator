"""D3 relation-specific supports, joint instance resampling and unknown bounds.

Display labels never enter the statistical partition. Absent relations are
structural non-membership; None within an eligible relation is infrastructure
unknown. All repeats and all terms of an instance are drawn together.
"""
from dataclasses import dataclass, field
import math
import random
from typing import Mapping, Sequence
from .records_v3 import ObligationOutcomeV3, canonical_digest
from .statistics_v3 import quantile
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3


SHARES = {"P2": 1/3, "P3J": 1/6, "P3S": 1/6, "P4": 1/3}
PARTITION_MODES = {
    "domain_difficulty_condition",
    "domain_primary_difficulty_condition",
}


@dataclass(frozen=True)
class RelationRunV3:
    instance_id: str
    domain: str
    difficulty: str
    condition: str
    repeat_id: str
    values: Mapping[str, float | None]
    evidence_digest: str
    stream_id: str = ""
    primary_label: str = ""  # display only
    obligation_ledger_digest: str = ""
    obligation_dispositions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    obligation_ledger_records: tuple[Mapping, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "values", dict(self.values))
        dispositions = {key: tuple(items) for key, items in self.obligation_dispositions.items()}
        object.__setattr__(self, "obligation_dispositions", dispositions)
        records = tuple(dict(item) for item in self.obligation_ledger_records)
        object.__setattr__(self, "obligation_ledger_records", records)
        if bool(self.obligation_ledger_digest) != bool(dispositions):
            raise ValueError("D3 obligation ledger digest and dispositions must be supplied together")
        if self.obligation_ledger_digest:
            if len(self.obligation_ledger_digest) != 64 or any(c not in "0123456789abcdef" for c in self.obligation_ledger_digest):
                raise ValueError("D3 obligation ledger digest must be a lowercase SHA-256")
            if not records:
                raise ValueError("D3 obligation ledger records are required for digest verification")
            if canonical_digest(list(records)) != self.obligation_ledger_digest:
                raise ValueError("D3 obligation ledger digest does not match canonical records")
            parsed = tuple(ObligationOutcomeV3.from_dict(item) for item in records)
            if len({row.obligation_id for row in parsed}) != len(parsed):
                raise ValueError("D3 obligation ledger identities must be unique")
            identity = parsed[0].identity
            if any(row.identity != identity for row in parsed):
                raise ValueError("D3 obligation ledger records must share one run identity")
            derived = {}
            for row in parsed:
                key = row.relation + (row.p3_subtype or "")
                derived.setdefault(key, []).append(row.disposition)
            if {key: tuple(items) for key, items in derived.items()} != dispositions:
                raise ValueError("D3 obligation dispositions are not derived from ledger records")
            if identity.base_instance_id != self.instance_id or identity.condition_id != self.condition or identity.repeat_id != self.repeat_id:
                raise ValueError("D3 ledger identity does not match relation run")
            from .protocol_v3 import obligation_components
            low = obligation_components(parsed, unknown=0)
            high = obligation_components(parsed, unknown=1)
            expected_values = {
                key: (low[key] if math.isclose(low[key], high[key]) else None)
                for key in low
            }
            if set(expected_values) != set(self.values) or any(
                    (expected_values[key] is None) != (self.values[key] is None)
                    or (expected_values[key] is not None and not math.isclose(expected_values[key], self.values[key]))
                    for key in expected_values):
                raise ValueError("D3 relation values are not derived from obligation ledger records")
        if dispositions:
            if set(dispositions) != set(self.values):
                raise ValueError("D3 obligation dispositions must match relation values")
            allowed = {"completed", "violated", "unreached", "unknown", "incomplete"}
            for relation, items in dispositions.items():
                if not items or any(item not in allowed for item in items):
                    raise ValueError("unsupported or empty D3 obligation disposition")
                unresolved = any(item in {"unknown", "incomplete"} for item in items)
                if unresolved != (self.values[relation] is None):
                    raise ValueError("D3 unknown disposition must remain an unknown relation value")

    @property
    def cell(self):
        return (self.domain, self.difficulty, self.condition)

    def registered_cell(self, partition_mode: str):
        if partition_mode == "domain_difficulty_condition":
            return (self.domain, self.difficulty, self.condition)
        if partition_mode == "domain_primary_difficulty_condition":
            if not self.primary_label:
                raise ValueError("primary-label relation partition requires primary_label")
            return (self.domain, self.primary_label, self.difficulty, self.condition)
        raise ValueError("D3 relation partition must be explicitly registered")


def relation_run_from_obligations(obligations: Sequence[ObligationOutcomeV3], *,
                                  stream_id: str = "", primary_label: str = "") -> RelationRunV3:
    """Create one auditable D3 run without collapsing ledger dispositions."""
    from .protocol_v3 import obligation_components
    rows = tuple(sorted(obligations, key=lambda row: row.obligation_id))
    if not rows or len({canonical_digest(row.identity.to_dict()) for row in rows}) != 1:
        raise ValueError("one D3 run requires obligations with one shared run identity")
    low = obligation_components(rows, unknown=0)
    high = obligation_components(rows, unknown=1)
    values = {key: (low[key] if math.isclose(low[key], high[key]) else None) for key in low}
    dispositions = {}
    for row in rows:
        key = row.relation + (row.p3_subtype or "")
        dispositions.setdefault(key, []).append(row.disposition)
    ledger_digest = canonical_digest([row.to_dict() for row in rows])
    identity = rows[0].identity
    return RelationRunV3(
        identity.base_instance_id, identity.domain, identity.difficulty,
        identity.condition_id, identity.repeat_id, values, ledger_digest,
        stream_id=stream_id, primary_label=primary_label,
        obligation_ledger_digest=ledger_digest,
        obligation_dispositions={key: tuple(items) for key, items in dispositions.items()},
        obligation_ledger_records=tuple(row.to_dict() for row in rows),
    )


def d3_relation_report(runs: Sequence[RelationRunV3], *, partition_mode: str | None = None,
                       registered_cells: Mapping[str, Sequence[tuple]] | None = None,
                       minimum_instances: int = 30, alpha=.05, seed=0,
                       resamples=2000):
    # The current manuscript defines relation cells without the display-only
    # primary label. Keep the alternate primary-stratified mode only for
    # explicitly registered diagnostic panels.
    if partition_mode not in PARTITION_MODES:
        return {
            "score_id": "D3", "status": "incomplete",
            "reason": "unregistered_relation_partition_mode",
            "relation_partition": partition_mode,
            "paper_partition_ambiguity": False,
            "scheduled_runs": len(runs), "input_digest": canonical_digest([r.__dict__ for r in runs]),
            "paper_commit": PAPER_COMMIT, "scorebook_digest": default_scorebook_v3().digest(),
            "physical_qualified": False,
        }
    if type(minimum_instances) is not int or minimum_instances < 1:
        raise ValueError("D3 minimum_instances must be a positive integer")
    if registered_cells is None:
        return {
            "score_id": "D3", "status": "incomplete",
            "reason": "frozen_relation_panel_required",
            "relation_partition": partition_mode,
            "paper_partition_ambiguity": False,
            "scheduled_runs": len(runs), "input_digest": canonical_digest([r.__dict__ for r in runs]),
            "paper_commit": PAPER_COMMIT, "scorebook_digest": default_scorebook_v3().digest(),
            "physical_qualified": False,
        }
    if not runs or not 0 < alpha < 1 or type(resamples) is not int or resamples < 100:
        raise ValueError("D3 needs scheduled relation runs and registered inference settings")
    by_instance = {}
    for row in runs:
        if not all((row.instance_id, row.domain, row.difficulty, row.repeat_id, row.evidence_digest)) or row.condition != "nominal" or not row.values or not set(row.values) <= SHARES.keys():
            raise ValueError("D3 primary requires nominal, evidenced relation support")
        if any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in row.values.values()):
            raise ValueError("relation values must lie in [0,1] or be unknown")
        by_instance.setdefault(row.instance_id, []).append(row)
    for rows in by_instance.values():
        if len(rows) != 3 or len({r.repeat_id for r in rows}) != 3 or len({(r.registered_cell(partition_mode), r.stream_id, tuple(sorted(r.values))) for r in rows}) != 1:
            raise ValueError("relation membership and three repeats must be frozen per instance")
    cells = {h: tuple(sorted({tuple(cell) for cell in registered_cells.get(h, ())})) for h in SHARES}
    if any(not c for c in cells.values()):
        raise ValueError("D3 needs registered support for all four relation terms")
    observed_cells = {r.registered_cell(partition_mode) for r in runs}
    registered_union = set().union(*(set(cells[h]) for h in SHARES))
    if not observed_cells <= registered_union:
        raise ValueError("observation contains a cell outside the frozen D3 panel")
    support_counts = {
        h: {cell: sum(r.registered_cell(partition_mode) == cell and h in r.values for r in runs) // 3 for cell in cells[h]}
        for h in SHARES
    }
    if any(count < minimum_instances for h in SHARES for count in support_counts[h].values()):
        return {
            "score_id": "D3", "status": "incomplete", "reason": "relation_cell_coverage_below_floor",
            "relation_partition": partition_mode, "registered_cells": {h: [list(c) for c in cells[h]] for h in SHARES},
            "support_counts": {h: {"/".join(cell): count for cell, count in support_counts[h].items()} for h in SHARES},
            "minimum_instances": minimum_instances,
            "scheduled_runs": len(runs), "input_digest": canonical_digest([r.__dict__ for r in runs]),
            "paper_commit": PAPER_COMMIT, "scorebook_digest": default_scorebook_v3().digest(),
            "physical_qualified": False,
        }
    groups = {}
    for iid,rows in by_instance.items():
        groups.setdefault(rows[0].stream_id or iid, []).append(iid)
    if any(len({by_instance[i][0].registered_cell(partition_mode) for i in group}) != 1 for group in groups.values()):
        raise ValueError("cross-stratum generator streams require joint-block registration")
    strata = {}
    for group in groups.values():
        strata.setdefault(by_instance[group[0]][0].registered_cell(partition_mode), []).append(group)
    def estimate(ids, endpoint):
        components, missing = {}, 0
        for relation, panel_cells in cells.items():
            estimates = []
            for cell in panel_cells:
                members = [i for i in ids if by_instance[i][0].registered_cell(partition_mode) == cell and relation in by_instance[i][0].values]
                if not members:
                    estimates.append(endpoint)
                    missing += 1
                else:
                    estimates.append(sum(sum(endpoint if r.values[relation] is None else r.values[relation] for r in by_instance[i])/3 for i in members)/len(members))
            components[relation] = sum(estimates)/len(estimates)
        return 100*sum(SHARES[h]*components[h] for h in SHARES), components, missing
    ids = sorted(by_instance)
    points = [estimate(ids, endpoint) for endpoint in (0,1)]
    rng = random.Random(seed)
    samples, empty = [[], []], 0
    for _ in range(resamples):
        draw = [i for cell in sorted(strata) for _ in strata[cell] for i in rng.choice(strata[cell])]
        for endpoint in (0,1):
            score, _, missing = estimate(draw, endpoint)
            samples[endpoint].append(score)
            empty += int(missing > 0) if endpoint == 0 else 0
    # Bounded-difference fallback with the total normalized coefficient of
    # each independent cluster, retaining overlap between relation panels.
    coefficients = {}
    for key, group in groups.items():
        coefficients[key] = sum(SHARES[h]/len(cells[h])*sum(1 for i in group if by_instance[i][0].registered_cell(partition_mode) == cell and h in by_instance[i][0].values)/sum(1 for i in ids if by_instance[i][0].registered_cell(partition_mode) == cell and h in by_instance[i][0].values) for h in SHARES for cell in cells[h])
    half = 100*math.sqrt(math.log(2/alpha)*sum(w*w for w in coefficients.values())/2)
    ends = []
    for endpoint in (0,1):
        point = points[endpoint][0]
        constant = min(samples[endpoint]) == max(samples[endpoint])
        ends.append({"point": point, "lower": max(0.,point-half) if constant else quantile(samples[endpoint], alpha/2), "upper": min(100.,point+half) if constant else quantile(samples[endpoint], 1-alpha/2), "method": "constant_cluster_hoeffding" if constant else "stratified_cluster_percentile"})
    # Sparse relation support remains visible as an interval-validity issue.
    # Extreme fills preserve those draws for diagnostics, not formal release.
    for end in ends:
        end["interval_status"] = "unresolved_empty_relation_resamples" if empty else "valid"
    has_unknown = any(value is None for row in runs for value in row.values.values())
    status = "incomplete" if empty else "range" if has_unknown else "valid"
    return {"score_id": "D3", "status": status,
            "reason": "empty_relation_support_in_bootstrap" if empty else None,
            "identification": [points[0][0], points[1][0]],
            "interval": ([ends[0]["lower"], ends[1]["upper"]] if not empty else []),
            "endpoints": ends, "components": [points[0][1], points[1][1]],
            "empty_relation_resample_fraction": empty/resamples,
            "relation_support": {h: {"/".join(cell): support_counts[h][cell] for cell in cells[h]} for h in SHARES},
            "relation_partition": partition_mode, "paper_partition_ambiguity": False,
            "registered_cells": {h: [list(c) for c in cells[h]] for h in SHARES},
            "obligation_ledgers_bound": all(bool(r.obligation_ledger_digest) for r in runs),
            "minimum_instances": minimum_instances,
            "seed": seed, "resamples": resamples,
            "quantile_rule": "empirical_inverse_cdf",
            "input_digest": canonical_digest([r.__dict__ for r in runs]),
            "scheduled_runs": len(runs), "paper_commit": PAPER_COMMIT,
            "scorebook_digest": default_scorebook_v3().digest(),
            "physical_qualified": False}
