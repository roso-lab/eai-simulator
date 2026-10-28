"""Frozen predicate-to-obligation reduction; never infer semantic bindings."""
from collections.abc import Mapping

from tools.benchmark_branch.oracle_predicates import (
    CauseCode, VerdictStatus, judge_anchor, predicate_scoring_role,
)


class ObligationReductionError(ValueError):
    pass


def reduce_obligations(anchor_id, streams, binding):
    """Return one outcome per registered obligation in fixed row order.

    The binding belongs in the frozen plan, whose digest is checked against
    the case manifest. Multiple predicates may constrain one obligation;
    each source group may feed only one scored obligation. This v1 rejects
    unresolved records rather than manufacturing a point estimate.
    """
    if not isinstance(binding, Mapping) or set(binding) != {"schema", "anchor_id", "obligations"}:
        raise ObligationReductionError("obligation binding schema mismatch")
    if binding["schema"] != "heterocoord.oracle-obligation-binding.v1" or binding["anchor_id"] != anchor_id:
        raise ObligationReductionError("obligation binding identity mismatch")
    rows = binding["obligations"]
    if not isinstance(rows, list):
        raise ObligationReductionError("registered obligations must be a list")
    verdicts = {v.predicate_id: v for v in judge_anchor(anchor_id, streams)}
    required = {
        pid for pid in verdicts
        if predicate_scoring_role(anchor_id, pid) == "obligation"
    }
    seen_ids, seen_predicates, group_owners = set(), set(), {}
    results = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) not in (
            {"obligation_id", "relation", "predicate_ids"},
            {"obligation_id", "relation", "subtype", "predicate_ids"},
        ):
            raise ObligationReductionError("registered obligation schema mismatch")
        oid = row["obligation_id"]
        if not isinstance(oid, str) or not oid.strip() or oid in seen_ids:
            raise ObligationReductionError("duplicate or invalid obligation identity")
        seen_ids.add(oid)
        relation = (row["relation"], row.get("subtype"))
        if relation not in {("P2", None), ("P3", "J"), ("P3", "S"), ("P4", None)}:
            raise ObligationReductionError("invalid obligation relation")
        pids = row["predicate_ids"]
        if not isinstance(pids, list) or not pids or any(not isinstance(p, str) for p in pids):
            raise ObligationReductionError("predicate_ids must be nonempty text list")
        if len(set(pids)) != len(pids) or seen_predicates.intersection(pids):
            raise ObligationReductionError("predicate counted more than once")
        if not set(pids).issubset(required):
            raise ObligationReductionError("unknown or non-D3 predicate in obligation")
        seen_predicates.update(pids)
        values = [verdicts[p] for p in pids]
        for v in values:
            group = v.evidence.get("dedupe_group")
            if group and group_owners.setdefault(group, oid) != oid:
                raise ObligationReductionError("source group counted by multiple obligations")
        if any(v.status is VerdictStatus.DATA_INSUFFICIENT and v.cause is not CauseCode.UNREACHED for v in values):
            raise ObligationReductionError("obligation evidence unresolved; no point estimate")
        outcome = ("violated" if any(v.status is VerdictStatus.VIOLATED for v in values)
                   else "unreached" if any(v.status is VerdictStatus.DATA_INSUFFICIENT for v in values)
                   else "completed")
        result = {"relation": row["relation"], "outcome": outcome}
        if "subtype" in row:
            result["subtype"] = row["subtype"]
        results.append(result)
    if seen_predicates != required:
        raise ObligationReductionError("registered obligations omit scored oracle predicates")
    return results
