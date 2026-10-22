"""Frozen D5/D6 scheduled keys and complete-repeat Hoeffding groups."""
from dataclasses import dataclass, asdict, field
import math
from typing import Mapping, Sequence
from .records_v3 import canonical_digest
from .statistics_v3 import hoeffding


@dataclass(frozen=True)
class FixedRunSpecV3:
    run_id: str
    instance_id: str
    stratum: str
    repeat_id: str
    checkpoint_digest: str
    weight: float
    independence_group: str
    family_id: str = ""
    case_id: str = ""
    unit_id: str = ""


@dataclass(frozen=True)
class FixedCatalogV3:
    score_id: str
    entries: tuple[FixedRunSpecV3, ...]
    excluded_families: tuple[str, ...] = ()
    excluded_family_case_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        if self.score_id not in {"D5", "D6"} or not self.entries or len({e.run_id for e in self.entries}) != len(self.entries):
            raise ValueError("unique frozen D5/D6 directory required")
        if any(not isinstance(family, str) or not family for family in self.excluded_families) or len(set(self.excluded_families)) != len(self.excluded_families):
            raise ValueError("excluded family identities must be unique nonempty strings")
        object.__setattr__(self, "excluded_families", tuple(self.excluded_families))
        object.__setattr__(self, "excluded_family_case_counts", dict(self.excluded_family_case_counts))
        if set(self.excluded_family_case_counts) != set(self.excluded_families) or any(type(count) is not int or count < 0 or count >= 30 for count in self.excluded_family_case_counts.values()):
            raise ValueError("excluded D5 families require disclosed below-floor case counts")
        # Validate weights and identifiers before any outcomes are read.
        hoeffding([0.]*len(self.entries), [e.weight for e in self.entries])
        for entry in self.entries:
            if not all((entry.run_id, entry.instance_id, entry.stratum, entry.repeat_id, entry.checkpoint_digest, entry.independence_group)):
                raise ValueError("frozen run identity is incomplete")
            if self.score_id == "D5" and not all((entry.family_id, entry.case_id, entry.unit_id)):
                raise ValueError("D5 needs explicit family/case/unit identities")
        if self.score_id == "D5":
            families = {}
            for e in self.entries:
                families.setdefault(e.family_id, {}).setdefault(e.case_id, {}).setdefault(e.unit_id, []).append(e)
            if set(families).intersection(self.excluded_families):
                raise ValueError("excluded D5 family cannot retain scored entries")
            for family, cases in families.items():
                if len(cases) < 30:
                    raise ValueError("D5 scored families require at least 30 certified cases")
                for case, units in cases.items():
                    repeats = None
                    for unit, entries in units.items():
                        ids = {e.repeat_id for e in entries}
                        if len(entries) != 3 or len(ids) != 3 or (repeats is not None and ids != repeats):
                            raise ValueError("D5 requires aligned three complete repeats across units")
                        repeats = ids
                        expected = 1/(len(families)*len(cases)*len(units)*3)
                        if any(not math.isclose(e.weight, expected, abs_tol=1e-12) for e in entries):
                            raise ValueError("D5 weights must follow equal family/case/unit hierarchy")
                    for repeat in repeats:
                        group = {e.independence_group for entries in units.values() for e in entries if e.repeat_id == repeat}
                        if len(group) != 1:
                            raise ValueError("D5 units in a case-repeat must be grouped before inference")
        else:
            if len({e.independence_group for e in self.entries}) != len(self.entries):
                raise ValueError("D6 requires independent registered runs")
            cells = {}
            for entry in self.entries:
                cells.setdefault(entry.stratum, {}).setdefault(entry.instance_id, []).append(entry)
            for instances in cells.values():
                for entries in instances.values():
                    if len(entries) != 3 or len({e.repeat_id for e in entries}) != 3:
                        raise ValueError("D6 needs three registered repeats per base instance")
                    expected = 1/(len(cells)*len(instances)*3)
                    if any(not math.isclose(e.weight, expected, abs_tol=1e-12) for e in entries):
                        raise ValueError("D6 weights must follow equal cell/instance/repeat hierarchy")

    def digest(self):
        return canonical_digest(asdict(self))

    def paired_interval(self, candidate, reference, *, alpha=.05):
        """Paired complete-repeat differences in [-1,1], shared frozen keys."""
        from .statistics_v3 import IntervalV3
        self.analyze(candidate, alpha=alpha)
        self.analyze(reference, alpha=alpha)
        a, b = ({r.run_id: r for r in rows} for rows in (candidate, reference))
        groups = {}
        for e in self.entries:
            left, right = a[e.run_id].values[0], b[e.run_id].values[0]
            if left is None or right is None:
                raise ValueError("paired unknowns require explicit endpoint contrasts")
            groups.setdefault(e.independence_group, []).append((e.weight, left-right))
        weights = [sum(w for w,v in rows) for rows in groups.values()]
        values = [sum(w*v for w,v in rows)/weight for rows,weight in zip(groups.values(),weights)]
        result = hoeffding(values, weights, alpha=alpha, bounds=(-1., 1.))
        return IntervalV3(100*result.point, 100*result.lower, 100*result.upper, "paired_complete_repeat_hoeffding", alpha)

    def analyze(self, runs, *, alpha=.05):
        by_id = {r.run_id: r for r in runs}
        if len(by_id) != len(runs) or set(by_id) != {e.run_id for e in self.entries}:
            raise ValueError("observations must exactly match frozen scheduled directory")
        grouped = {}
        for entry in self.entries:
            row = by_id[entry.run_id]
            if (row.instance_id, row.stratum, row.repeat_id, row.checkpoint_digest) != (entry.instance_id, entry.stratum, entry.repeat_id, entry.checkpoint_digest):
                raise ValueError("fixed-catalog observation identity mismatch")
            if row.weight is not None and row.weight != entry.weight:
                raise ValueError("run-supplied weight differs from frozen catalog")
            grouped.setdefault(entry.independence_group, []).append((entry.weight, row.values[0]))
        weights = [sum(w for w,v in group) for group in grouped.values()]
        ends = []
        for endpoint in (0., 1.):
            values = [sum(w*(endpoint if v is None else v) for w,v in group)/total for group,total in zip(grouped.values(), weights)]
            ends.append(hoeffding(values, weights, alpha=alpha))
        return {"identification": [100*e.point for e in ends], "interval": [100*ends[0].lower, 100*ends[1].upper], "endpoints": [asdict(e) for e in ends], "method": "fixed_catalog_weighted_hoeffding", "catalog_digest": self.digest(), "independent_units": len(grouped), "scheduled_attempts": len(self.entries), "status": "range" if ends[0].point != ends[1].point else "valid", "excluded_families": list(self.excluded_families), "excluded_family_case_counts": dict(self.excluded_family_case_counts), "error_allocation": {"lower_endpoint_lower_tail": alpha/2, "upper_endpoint_upper_tail": alpha/2}}


def d5_catalog(directory: Mapping[str, Mapping[str, Sequence[str]]], checkpoints: Mapping[tuple[str,str,str], str], *, repeats=("1","2","3")):
    entries = []
    eligible = {family: cases for family, cases in directory.items() if len(cases) >= 30}
    excluded = tuple(sorted(set(directory) - set(eligible)))
    excluded_counts = {family: len(directory[family]) for family in excluded}
    if not eligible:
        raise ValueError("D5 catalog has no family meeting the 30-case floor")
    for family,cases in eligible.items():
        for case,units in cases.items():
            if not units or len(set(units)) != len(units):
                raise ValueError("D5 unit directory must be nonempty and unique")
            for unit in units:
                for repeat in repeats:
                    entries.append(FixedRunSpecV3(canonical_digest((family,case,unit,repeat)), canonical_digest((family,case,unit)), family, repeat, checkpoints[(family,case,unit)], 1/(len(eligible)*len(cases)*len(units)*3), canonical_digest((family,case,repeat)), family,case,unit))
    return FixedCatalogV3("D5", tuple(entries), excluded_families=excluded,
                          excluded_family_case_counts=excluded_counts)
