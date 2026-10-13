from benchmark.evidence import BranchLedger, EvidenceRecord, EvidenceStatus
from benchmark.protocol import BranchRole, RunKey


def _run_key() -> RunKey:
    return RunKey("base", "system", "condition", "r0", "grid0", BranchRole.FACT)


def test_branch_ledger_is_hash_chained_and_branch_local() -> None:
    ledger = BranchLedger(_run_key())
    first = ledger.append("decision", tick=0, source="brain", payload={"id": "d0"})
    second = ledger.append("milestone", tick=2, source="evaluator", payload={"id": "release"})
    assert second.previous_hash == first.event_hash
    digest = ledger.seal()
    assert digest == ledger.digest()
    assert ledger.sealed


def test_unknown_evidence_is_explicit() -> None:
    evidence = EvidenceRecord(
        predicate_id="terminal",
        predicate_version="v1",
        status=EvidenceStatus.UNKNOWN,
        tick=3,
        source="evaluator",
        reason="missing external sample",
    )
    assert evidence.status is EvidenceStatus.UNKNOWN
    assert evidence.reason
