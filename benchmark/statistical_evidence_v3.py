"""Bind computed statistical reports to scores; a ready boolean is not proof."""
from dataclasses import dataclass
import math
from typing import Mapping
from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT


@dataclass(frozen=True)
class StatisticalEvidenceV3:
    source_commit: str
    scorebook_digest: str
    reports: tuple[Mapping, ...]

    def __post_init__(self):
        if not self.source_commit or not self.scorebook_digest or {r.get("score_id") for r in self.reports} != {f"D{i}" for i in range(1,7)} or len(self.reports) != 6:
            raise ValueError("six unique dimension reports and source identity required")
        for r in self.reports:
            if r.get("paper_commit") != PAPER_COMMIT or r.get("scorebook_digest") != self.scorebook_digest or not r.get("input_digest") or r.get("scheduled_runs", 0) <= 0:
                raise ValueError("statistical report provenance mismatch")
            if r.get("status") not in {"valid", "range", "incomplete", "unstable", "na"}:
                raise ValueError("invalid statistical status")
            # Every dimension can retain a fixed-weight identification range
            # when a registered infrastructure unknown survives reruns.  The
            # range is not a point-ready ranking result, but it is valid
            # statistical evidence and must remain attached to the report.
            if r["status"] in {"valid", "range"}:
                if r["score_id"] == "D3":
                    floor = r.get("minimum_instances")
                    supports = r.get("relation_support", {})
                    cells = r.get("registered_cells", {})
                    relations = {"P2", "P3J", "P3S", "P4"}
                    if (type(floor) is not int or floor < 30
                            or set(supports) != relations or set(cells) != relations
                            or any(not cells[h] or set(supports[h]) != {"/".join(c) for c in cells[h]}
                                   or any(type(n) is not int or n < floor for n in supports[h].values())
                                   for h in relations)):
                        raise ValueError("D3 registered relation support must meet the formal instance floor")
                    if r.get("obligation_ledgers_bound") is not True:
                        raise ValueError("D3 statistical evidence must retain obligation ledger dispositions")
                bounds = r.get("interval", ())
                ends = r.get("identification", ())
                if len(bounds) != 2 or len(ends) != 2 or any(not math.isfinite(v) for v in (*bounds, *ends)) or not 0 <= bounds[0] <= bounds[1] <= 100 or not 0 <= ends[0] <= ends[1] <= 100:
                    raise ValueError("statistical interval and identification endpoints must be ordered")
                expected = {"d2_finite_sample_retention_set"} if r["score_id"] == "D2" else {"weighted_hoeffding"} if r["score_id"] in {"D5", "D6"} else None
                endpoints = r.get("endpoints", ())
                allowed = expected if expected else {"stratified_cluster_percentile", "constant_cluster_hoeffding", "base_instance_cluster_percentile"}
                if len(endpoints) != 2 or any(e.get("method") not in allowed for e in endpoints):
                    raise ValueError("incorrect registered interval construction")
                if any(e.get("interval_status", "valid") != "valid" for e in endpoints):
                    raise ValueError("unresolved resampling denominator cannot qualify statistical release")

    def matches(self, bundle):
        if self.source_commit != bundle.source_commit or self.scorebook_digest != bundle.scorebook_digest:
            return False
        reports = {r["score_id"]: r for r in self.reports}
        for score in bundle.results:
            report = reports[score.score_id]
            if report["status"] != score.status: return False
            if score.status == "valid":
                ends = report["identification"]
                if not math.isclose(ends[0], ends[1], abs_tol=1e-9) or not math.isclose(score.point, ends[0], abs_tol=1e-9): return False
            if score.status == "range" and (score.lower, score.upper) != tuple(report["identification"]): return False
        return True

    def digest(self):
        # Encode infinite raw retention bands without nonstandard JSON.
        def clean(v):
            if isinstance(v, float) and math.isinf(v): return "infinity"
            if isinstance(v, Mapping): return {k: clean(x) for k,x in v.items()}
            if isinstance(v, (tuple, list)): return [clean(x) for x in v]
            return v
        return canonical_digest(clean({"source_commit": self.source_commit, "scorebook_digest": self.scorebook_digest, "reports": self.reports}))
