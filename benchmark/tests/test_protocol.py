from benchmark.protocol import (
    BranchRole,
    BranchSpec,
    CheckpointRef,
    ProtocolContract,
    RunKey,
)


def _checkpoint() -> CheckpointRef:
    return CheckpointRef(
        checkpoint_id="cp-001",
        kind="episode_step",
        step=4,
        artifact_uri="artifact://cp-001",
        digest="abc123",
        source_run="base/system/cond/r0/grid0/fact",
    )


def test_run_key_pair_identity_ignores_branch_role() -> None:
    fact = RunKey("base", "system", "condition", "r0", "grid0", BranchRole.FACT)
    sham = RunKey("base", "system", "condition", "r0", "grid0", BranchRole.SHAM)
    assert fact.pair_key() == sham.pair_key()
    assert fact.as_string().endswith("/fact")


def test_protocol_digest_is_stable_and_sham_has_no_treatment() -> None:
    contract = ProtocolContract(
        contract_id="p2-delivery",
        version="v1",
        domain="factory",
        primary_label="handoff",
        relation_signature=("precedence", "handoff"),
        observation_schema="obs.v1",
        milestones=("release", "accept", "terminal"),
    )
    assert contract.digest() == contract.digest()
    branch = BranchSpec(
        run_key=RunKey("base", "system", "condition", "r0", "grid0", BranchRole.SHAM),
        checkpoint=_checkpoint(),
    )
    assert branch.treatment_id == "none"
