"""Deterministic, execution-free identities for the EMOS experiment matrix.

This module is deliberately a planning seam.  It expands one
``ExperimentCell`` into the stable identity needed by a runner, but never
starts Isaac, captures a checkpoint, or writes an artifact.  A generated
checkpoint is therefore explicitly marked ``planned``; a live runner may pass
an observed :class:`~benchmark.protocol.CheckpointRef` when resuming an
eligible cell.

The identity is split into three layers:

* ``base_instance_id`` identifies initial task/world/generator/seed/slot independent of a
  branch and checkpoint implementation;
* ``RunKey`` identifies each fact/reference/sham/intervention branch;
* the manifest digest identifies the complete immutable batch contract.

The latter is useful for idempotent scheduling: a caller can build the same
cell repeatedly or resume from a previously retained in-memory/public
manifest and receive the same identity without touching experiment output.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable, Mapping

from .experiment_plan import ExperimentCell, ExperimentPlan, default_experiment_plan
from .protocol import BranchRole, BranchSpec, CheckpointRef, RunKey
from .replay import PairedBranchPlan
from .task_catalog import TaskAnchor


BATCH_SCHEMA_VERSION = "emos-batch.v2"
BRANCH_ROLE_ORDER = (
    BranchRole.FACT,
    BranchRole.REFERENCE,
    BranchRole.SHAM,
    BranchRole.INTERVENTION,
)
STRICT_PAIR_ROLES = frozenset(BRANCH_ROLE_ORDER)


class BatchContractError(ValueError):
    """Raised when a cell or resumed manifest violates batch identity rules."""


def _json_safe(value: Any) -> Any:
    """Convert protocol records to a deterministic JSON-compatible value."""

    if isinstance(value, BranchRole):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (set, frozenset)):
        return [_json_safe(item) for item in sorted(value, key=lambda item: repr(item))]
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_safe(vars(value))
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _checkpoint_payload(checkpoint: CheckpointRef) -> dict[str, Any]:
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "kind": checkpoint.kind,
        "step": checkpoint.step,
        "artifact_uri": checkpoint.artifact_uri,
        "digest": checkpoint.digest,
        "source_run": checkpoint.source_run,
    }


def _run_key_payload(key: RunKey) -> dict[str, Any]:
    return {
        "base_instance_id": key.base_instance_id,
        "system_id": key.system_id,
        "condition_id": key.condition_id,
        "repeat_id": key.repeat_id,
        "checkpoint_round": key.checkpoint_round,
        "branch_role": key.branch_role.value,
        "untreated_history_id": key.untreated_history_id,
    }


def _branch_payload(branch: BranchSpec) -> dict[str, Any]:
    return {
        "run_key": _run_key_payload(branch.run_key),
        "checkpoint": _checkpoint_payload(branch.checkpoint),
        "treatment_id": branch.treatment_id,
        "authority": branch.authority,
        "non_target_budget": _json_safe(branch.non_target_budget),
    }


def _cell_payload(cell: ExperimentCell) -> dict[str, Any]:
    # ``ExperimentCell.to_dict`` is intentionally used instead of repr so
    # enum/tuple implementation details cannot change an identity digest.
    return _json_safe(cell.to_dict())


def _anchor_digest(anchor: TaskAnchor, *members: TaskAnchor) -> str:
    # The semantic contract is the cross-scenario identity; include the
    # anchor id so two independently authored anchors cannot collide merely
    # because their obligations happen to be equal.
    entries = tuple(members) or (anchor,)
    return _canonical_digest(
        {
            "anchor_id": anchor.anchor_id,
            "members": [
                {
                    "anchor_id": item.anchor_id,
                    "domain": item.domain,
                    "semantic_contract": item.semantic_contract(),
                }
                for item in sorted(entries, key=lambda item: (item.domain, item.anchor_id))
            ],
        }
    )


def _anchors_for(plan: ExperimentPlan, anchor_id: str) -> tuple[TaskAnchor, ...]:
    direct = tuple(anchor for anchor in plan.catalog if anchor.anchor_id == anchor_id)
    if direct:
        return direct
    # The D5 selector intentionally emits common-anchor group IDs (for
    # example C-P2-01), while catalog rows retain domain-specific anchor IDs.
    # Keep the group as one batch identity and expose a deterministic
    # representative for callers that need the single-anchor projection.
    grouped = tuple(anchor for anchor in plan.catalog if anchor.common_anchor_id == anchor_id)
    if grouped:
        return tuple(sorted(grouped, key=lambda item: (item.domain, item.anchor_id)))
    raise BatchContractError(f"cell anchor_id is not present in plan: {anchor_id!r}")


def _phase_condition(plan: ExperimentPlan, cell: ExperimentCell) -> tuple[Any, Any]:
    phases = tuple(phase for phase in plan.phases if phase.phase_id == cell.phase_id)
    if len(phases) != 1:
        raise BatchContractError(f"cell phase_id is not unique in plan: {cell.phase_id!r}")
    phase = phases[0]
    conditions = tuple(condition for condition in phase.conditions if condition.condition_id == cell.condition_id)
    if len(conditions) != 1:
        raise BatchContractError(
            f"cell condition_id is not unique in phase {cell.phase_id!r}: {cell.condition_id!r}"
        )
    if cell.anchor_id not in plan.anchors_for(phase):
        raise BatchContractError(f"anchor {cell.anchor_id!r} is not selected by phase {cell.phase_id!r}")
    return phase, conditions[0]


def _canonical_roles(raw_roles: Iterable[str], *, strict: bool) -> tuple[BranchRole, ...]:
    values = tuple(str(role).strip() for role in raw_roles)
    if not values:
        raise BatchContractError("branch_roles cannot be empty")
    try:
        roles = tuple(BranchRole(role) for role in values)
    except ValueError as exc:
        raise BatchContractError(f"unknown branch role in {values!r}") from exc
    if len(set(roles)) != len(roles):
        raise BatchContractError("branch_roles cannot contain duplicates")
    if strict and set(roles) != STRICT_PAIR_ROLES:
        raise BatchContractError(
            "strict paired cells require exactly one fact, reference, sham and intervention branch"
        )
    # Canonical ordering makes semantically equivalent role tuples produce
    # the same digest while retaining the protocol's four-role vocabulary.
    return tuple(role for role in BRANCH_ROLE_ORDER if role in roles)


def _validate_cell_against_plan(plan: ExperimentPlan, cell: ExperimentCell) -> tuple[tuple[TaskAnchor, ...], Any, Any, tuple[BranchRole, ...]]:
    if not isinstance(cell, ExperimentCell):
        raise BatchContractError("cell must be an ExperimentCell")
    phase, condition = _phase_condition(plan, cell)
    anchors = _anchors_for(plan, cell.anchor_id)
    roles = _canonical_roles(cell.branch_roles, strict=cell.strict_pair_required)
    if cell.repeat_id not in {f"r{i}" for i in range(1, plan.repeats + 1)}:
        raise BatchContractError(f"repeat_id {cell.repeat_id!r} is outside preregistered K={plan.repeats}")
    if cell.checkpoint_round != plan.checkpoint_round:
        raise BatchContractError("cell checkpoint_round does not match the experiment plan")
    if cell.dimension != phase.dimension:
        raise BatchContractError("cell dimension does not match its phase")
    if cell.treatment_id != condition.treatment_id:
        raise BatchContractError("cell treatment_id does not match its condition")
    if bool(cell.strict_pair_required) != bool(condition.strict_pair_required):
        raise BatchContractError("cell strict_pair_required does not match its condition")
    expected_evidence = phase.required_evidence + (("causal_replay", "sham_calibration") if condition.strict_pair_required else ())
    if tuple(cell.evidence_required) != tuple(expected_evidence):
        raise BatchContractError("cell evidence_required does not match its phase/strict-pair contract")
    return anchors, phase, condition, roles


def _planned_checkpoint(
    *,
    base_instance_id: str,
    system_id: str,
    condition_id: str,
    repeat_id: str,
    checkpoint_round: str,
    anchor_digest: str,
) -> CheckpointRef:
    payload = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "base_instance_id": base_instance_id,
        "system_id": system_id,
        "condition_id": condition_id,
        "repeat_id": repeat_id,
        "checkpoint_round": checkpoint_round,
        "anchor_digest": anchor_digest,
    }
    digest = _canonical_digest(payload)
    checkpoint_id = f"planned-{digest[:20]}"
    source_run = "/".join((base_instance_id, system_id, condition_id, repeat_id, checkpoint_round, "fact"))
    return CheckpointRef(
        checkpoint_id=checkpoint_id,
        kind="planned-base-instance",
        step=0,
        artifact_uri=f"planned://checkpoint/{digest}",
        digest=digest,
        source_run=source_run,
    )


@dataclass(frozen=True)
class ExperimentBatch:
    """Immutable identity and branch manifest for one experiment cell."""

    cell: ExperimentCell
    anchor: TaskAnchor
    anchors: tuple[TaskAnchor, ...]
    plan_digest: str
    base_instance_id: str
    checkpoint: CheckpointRef
    branches: tuple[BranchSpec, ...]
    strict_pair_required: bool
    manifest_digest: str

    @property
    def pair_key(self) -> tuple[str, str, str, str, str]:
        return self.branches[0].run_key.pair_key()

    @property
    def run_keys(self) -> tuple[RunKey, ...]:
        return tuple(branch.run_key for branch in self.branches)

    @property
    def base_run_key(self) -> RunKey:
        """Return the fact run key used as the branch-independent base key."""

        for branch in self.branches:
            if branch.run_key.branch_role is BranchRole.FACT:
                return branch.run_key
        # Non-empty roles are guaranteed by cell validation; this fallback is
        # defensive for manually constructed dataclass instances.
        return self.branches[0].run_key

    @property
    def checkpoint_ref(self) -> CheckpointRef:
        """Alias making the checkpoint seam explicit to runner adapters."""

        return self.checkpoint

    @property
    def branch_manifest(self) -> tuple[dict[str, Any], ...]:
        """Detached per-branch records suitable for a scheduler queue."""

        return tuple(_branch_payload(branch) for branch in self.branches)

    @property
    def strict_pair_gate(self) -> dict[str, Any]:
        if not self.strict_pair_required:
            return {"required": False, "status": "not-required", "reason": "cell_is_not_D5_strict_pair"}
        return {
            "required": True,
            "status": "pending",
            "reason": "causal_replay_and_sham_calibration_required",
            "required_roles": [role.value for role in BRANCH_ROLE_ORDER],
        }

    def manifest(self) -> dict[str, Any]:
        """Return a detached, JSON-compatible manifest with stable digest."""

        payload: dict[str, Any] = {
            "schema_version": BATCH_SCHEMA_VERSION,
            "plan_digest": self.plan_digest,
            "cell": _cell_payload(self.cell),
            "anchor_id": self.cell.anchor_id,
            "anchor_digest": _anchor_digest(self.anchor, *self.anchors),
            "anchor_members": [item.anchor_id for item in self.anchors],
            "base_instance_id": self.base_instance_id,
            "pair_key": list(self.pair_key),
            "checkpoint": _checkpoint_payload(self.checkpoint),
            "branches": [_branch_payload(branch) for branch in self.branches],
            "strict_pair_gate": self.strict_pair_gate,
        }
        payload["manifest_digest"] = _canonical_digest(payload)
        if payload["manifest_digest"] != self.manifest_digest:
            raise BatchContractError("internal manifest digest mismatch")
        return payload

    def to_dict(self) -> dict[str, Any]:
        return self.manifest()

    def paired_plan(self) -> PairedBranchPlan:
        """Project a strict four-branch batch into the replay gate contract."""

        if not self.strict_pair_required:
            raise BatchContractError("paired_plan is only available for strict paired cells")
        try:
            return PairedBranchPlan(self.checkpoint, self.branches)
        except ValueError as exc:
            raise BatchContractError(str(exc)) from exc


def _build_batch(
    cell: ExperimentCell,
    *,
    plan: ExperimentPlan,
    checkpoint: CheckpointRef | None = None,
) -> ExperimentBatch:
    anchors, _phase, condition, roles = _validate_cell_against_plan(plan, cell)
    anchor = anchors[0]
    anchor_digest = _anchor_digest(anchor, *anchors)
    base_payload = {
        "schema_version": "benchmark.base.v2",
        "generator_version": cell.generator_version,
        "anchor_id": cell.anchor_id,
        "anchor_digest": anchor_digest,
        "anchor_members": [item.anchor_id for item in anchors],
        "base_seed": cell.base_seed, "slot": cell.slot,
        "initial_state_digest": cell.initial_state_digest,
    }
    base_digest = _canonical_digest(base_payload)
    base_instance_id = f"inst-{base_digest[:24]}"
    base_key_values = (base_instance_id, plan.system_id, cell.condition_id, cell.repeat_id, cell.checkpoint_round)
    actual_checkpoint = checkpoint or _planned_checkpoint(
        base_instance_id=base_instance_id,
        system_id=plan.system_id,
        condition_id=cell.condition_id,
        repeat_id=cell.repeat_id,
        checkpoint_round=cell.checkpoint_round,
        anchor_digest=anchor_digest,
    )
    branches: list[BranchSpec] = []
    for role in roles:
        # A fact-only event condition carries its treatment on the fact branch.
        # For a paired nominal-control cell the intervention role still needs a
        # non-empty protocol treatment because BranchSpec intentionally rejects
        # an intervention named ``none``; ``control`` is explicitly a no-op
        # protocol label, not an effective intervention.
        treatment = "none"
        if len(roles) == 1 and role is BranchRole.FACT and cell.treatment_id != "none":
            treatment = cell.treatment_id
        elif role is BranchRole.INTERVENTION:
            treatment = cell.treatment_id if cell.treatment_id != "none" else "control"
        run_key = RunKey(*base_key_values, branch_role=role, untreated_history_id=cell.untreated_history_id)
        branches.append(BranchSpec(run_key, actual_checkpoint, treatment_id=treatment))
    if cell.strict_pair_required:
        try:
            PairedBranchPlan(actual_checkpoint, tuple(branches))
        except ValueError as exc:
            raise BatchContractError(str(exc)) from exc
    provisional = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "plan_digest": _canonical_digest(plan.manifest()),
        "cell": _cell_payload(cell),
        "anchor_id": cell.anchor_id,
        "anchor_digest": anchor_digest,
        "anchor_members": [item.anchor_id for item in anchors],
        "base_instance_id": base_instance_id,
        "pair_key": list(branches[0].run_key.pair_key()),
        "checkpoint": _checkpoint_payload(actual_checkpoint),
        "branches": [_branch_payload(branch) for branch in branches],
        "strict_pair_gate": {
            "required": bool(cell.strict_pair_required),
            "status": "pending" if cell.strict_pair_required else "not-required",
            "reason": "causal_replay_and_sham_calibration_required" if cell.strict_pair_required else "cell_is_not_D5_strict_pair",
            **({"required_roles": [role.value for role in BRANCH_ROLE_ORDER]} if cell.strict_pair_required else {}),
        },
    }
    manifest_digest = _canonical_digest(provisional)
    return ExperimentBatch(
        cell=cell,
        anchor=anchor,
        anchors=anchors,
        plan_digest=provisional["plan_digest"],
        base_instance_id=base_instance_id,
        checkpoint=actual_checkpoint,
        branches=tuple(branches),
        strict_pair_required=cell.strict_pair_required,
        manifest_digest=manifest_digest,
    )


def build_batch_identity(
    cell: ExperimentCell,
    *,
    plan: ExperimentPlan | None = None,
    checkpoint: CheckpointRef | None = None,
) -> ExperimentBatch:
    """Build one deterministic batch identity without executing or persisting."""

    return _build_batch(cell, plan=plan or default_experiment_plan(), checkpoint=checkpoint)


def build_batch_manifest(
    cell: ExperimentCell,
    *,
    plan: ExperimentPlan | None = None,
    checkpoint: CheckpointRef | None = None,
) -> dict[str, Any]:
    """Build and return a detached deterministic manifest mapping."""

    return build_batch_identity(cell, plan=plan, checkpoint=checkpoint).manifest()


def _cell_from_payload(payload: Mapping[str, Any]) -> ExperimentCell:
    try:
        return ExperimentCell(
            phase_id=str(payload["phase_id"]),
            dimension=payload.get("dimension"),
            anchor_id=str(payload["anchor_id"]),
            condition_id=str(payload["condition_id"]),
            repeat_id=str(payload["repeat_id"]),
            checkpoint_round=str(payload["checkpoint_round"]),
            branch_roles=tuple(str(item) for item in payload["branch_roles"]),
            treatment_id=str(payload["treatment_id"]),
            strict_pair_required=bool(payload["strict_pair_required"]),
            evidence_required=tuple(str(item) for item in payload["evidence_required"]),
            generator_version=str(payload["generator_version"]),
            base_seed=payload["base_seed"], slot=payload["slot"],
            initial_state_digest=str(payload["initial_state_digest"]),
            untreated_history_id=str(payload["untreated_history_id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BatchContractError("manifest contains an invalid cell") from exc


def resume_batch(
    manifest: Mapping[str, Any],
    *,
    plan: ExperimentPlan | None = None,
) -> ExperimentBatch:
    """Recompute a manifest identity and reject any non-idempotent mutation.

    This is intentionally in-memory and read-only.  A scheduler may call it
    before dispatching a worker, or after a process restart using a retained
    public manifest.  It does not inspect or create ``BranchArtifactStore``
    files.
    """

    if not isinstance(manifest, Mapping):
        raise BatchContractError("manifest must be a mapping")
    supplied_digest = str(manifest.get("manifest_digest", ""))
    if not supplied_digest:
        raise BatchContractError("manifest_digest is required for resume")
    payload = dict(manifest)
    payload.pop("manifest_digest", None)
    if supplied_digest != _canonical_digest(payload):
        raise BatchContractError("manifest digest mismatch; resume is not idempotent")
    cell_payload = manifest.get("cell")
    if not isinstance(cell_payload, Mapping):
        raise BatchContractError("manifest cell is missing")
    checkpoint_payload = manifest.get("checkpoint")
    if not isinstance(checkpoint_payload, Mapping):
        raise BatchContractError("manifest checkpoint is missing")
    try:
        checkpoint = CheckpointRef(
            checkpoint_id=str(checkpoint_payload["checkpoint_id"]),
            kind=str(checkpoint_payload["kind"]),
            step=int(checkpoint_payload["step"]),
            artifact_uri=str(checkpoint_payload["artifact_uri"]),
            digest=str(checkpoint_payload["digest"]),
            source_run=str(checkpoint_payload["source_run"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BatchContractError("manifest checkpoint is invalid") from exc
    batch = build_batch_identity(_cell_from_payload(cell_payload), plan=plan, checkpoint=checkpoint)
    if batch.manifest_digest != supplied_digest:
        raise BatchContractError("manifest does not match the current deterministic plan")
    return batch


class BatchIdentityIndex:
    """Small in-memory idempotency/resume index for a scheduler process."""

    def __init__(self, plan: ExperimentPlan | None = None):
        self.plan = plan or default_experiment_plan()
        self._items: dict[str, ExperimentBatch] = {}

    def ensure(self, cell: ExperimentCell, *, checkpoint: CheckpointRef | None = None) -> ExperimentBatch:
        batch = build_batch_identity(cell, plan=self.plan, checkpoint=checkpoint)
        existing = self._items.get(batch.manifest_digest)
        if existing is not None and existing.manifest() != batch.manifest():
            raise BatchContractError("manifest digest collision with a different batch")
        self._items.setdefault(batch.manifest_digest, batch)
        return self._items[batch.manifest_digest]

    def resume(self, manifest: Mapping[str, Any]) -> ExperimentBatch:
        batch = resume_batch(manifest, plan=self.plan)
        existing = self._items.get(batch.manifest_digest)
        if existing is not None and existing.manifest() != batch.manifest():
            raise BatchContractError("resumed manifest conflicts with indexed batch")
        self._items.setdefault(batch.manifest_digest, batch)
        return self._items[batch.manifest_digest]

    def get(self, manifest_digest: str) -> ExperimentBatch | None:
        return self._items.get(str(manifest_digest))

    def __len__(self) -> int:
        return len(self._items)


__all__ = [
    "BATCH_SCHEMA_VERSION",
    "BRANCH_ROLE_ORDER",
    "BatchContractError",
    "BatchIdentityIndex",
    "ExperimentBatch",
    "build_batch_identity",
    "build_batch_manifest",
    "resume_batch",
]
