"""Connect the complete benchmark state registry to registered tool recovery.

The caller freezes this checkpoint and binds jobs BEFORE creating a BranchPlan
or run store. A backend emits the returned receipt before its first step.
This exact-state bridge does not authorize fresh-process reconstruction, new
controller substreams, or scientific pairing. Those need upstream registration.
It provides no native checkpoint implementation or physical qualification.
This integration remains diagnostic-only and in-process. Its detached snapshot
can be saved and reloaded with an externally pinned artifact digest, source
identity and recovery rule. Loading in a different process is deliberately
rejected: persistence alone does not restore native runtime/session ownership.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
import uuid
from typing import Any

from .replay_state_v3 import ReplayStateRegistryV3
from tools.benchmark_branch.models import BranchJob, CheckpointArtifact, canonical_digest
from tools.benchmark_branch.recovery import (
    REGISTRATION_KEY, SCHEMA, REQUIRED_PARTICIPANTS,
    recovery_registration, verify_recovery_readback,
)


# New processes have new native-runtime ownership. A disk artifact cannot
# authorize continuing the old experimental identity after process failure.
_PROCESS_NONCE = uuid.uuid4().hex


class RegisteredRecoveryV3:
    """One detached frozen checkpoint and its explicitly bounded retry rule.

    Construct only at a quiescent boundary; callbacks must not advance physics.
    The collector owns retry ancestry/eligibility. This object only binds its
    frozen checkpoint and produces a verified receipt for an admitted retry.
    """

    def __init__(self, registry: ReplayStateRegistryV3, *, registration_id: str,
                 infrastructure_reasons: tuple[str, ...], max_attempts: int):
        if not isinstance(registration_id, str) or not registration_id.strip():
            raise ValueError("registration_id is required")
        if (not isinstance(infrastructure_reasons, tuple) or not infrastructure_reasons
                or any(not isinstance(reason, str) or not reason.strip() for reason in infrastructure_reasons)):
            raise ValueError("explicit infrastructure_reasons are required")
        if type(max_attempts) is not int or max_attempts < 2:
            raise ValueError("max_attempts must include the original and at least one retry")
        snapshot = registry.capture()
        if not REQUIRED_PARTICIPANTS.issubset(snapshot):
            raise ValueError("recovery requires all tool participants")
        self._checkpoint = CheckpointArtifact.from_state(
            snapshot, checkpoint_id=registration_id,
            schema="heterocoord.registry_checkpoint.v3")
        participants = {name: canonical_digest(payload) for name, payload in snapshot.items()}
        self._rule = {
            "schema": SCHEMA, "mode": "same_checkpoint_random_stream",
            "registration_id": registration_id, "checkpoint_digest": self._checkpoint.digest,
            "random_stream_digest": participants["rng"],
            "participant_state_digests": participants,
            "infrastructure_reasons": list(infrastructure_reasons), "max_attempts": max_attempts,
        }
        self._process_owner = (os.getpid(), _PROCESS_NONCE)

    def _assert_process_owner(self):
        if self._process_owner != (os.getpid(), _PROCESS_NONCE):
            raise ValueError("diagnostic recovery belongs to its original live process")

    @property
    def checkpoint(self) -> CheckpointArtifact:
        self._assert_process_owner()
        return deepcopy(self._checkpoint)

    @staticmethod
    def _storage_metadata(rule, source_digest):
        if (not isinstance(source_digest, str) or len(source_digest) != 64
                or any(c not in "0123456789abcdef" for c in source_digest)):
            raise ValueError("frozen source_digest must be a lowercase SHA-256")
        return {
            "schema": "heterocoord.recovery_archive.v1",
            "source_digest": source_digest,
            # Include the live PID at use time: a fork inherits module globals
            # but must not inherit the parent's native execution authority.
            "process_owner": f"{os.getpid()}:{_PROCESS_NONCE}",
            "execution_scope": "measured_diagnostic",
            "recovery_rule": json.dumps(rule, sort_keys=True, separators=(",", ":"), allow_nan=False),
        }

    def save(self, path, *, source_digest: str) -> str:
        """Persist this frozen snapshot, refusing an existing destination.

        Preserve the returned digest outside the archive (in the frozen job
        input manifest). Loading never trusts a checksum copied from the same
        mutable artifact as its own authority.
        """
        self._assert_process_owner()
        from .checkpoint_store_v3 import write_registry_checkpoint
        return write_registry_checkpoint(path, self._checkpoint.state,
            metadata=self._storage_metadata(self._rule, source_digest))

    @classmethod
    def load(cls, path, *, expected_digest: str, source_digest: str, job: BranchJob):
        """Reload in the SAME live process; validate everything before restore.

        No callbacks or simulator operations are invoked. The native owner
        still has to supply the original runtime registry and admitted job to
        restore_receipt. Cross-process artifact decoding is a storage test,
        not permission to retry a physical branch under its old identity.
        """
        from .checkpoint_store_v3 import read_registry_checkpoint
        rule = recovery_registration(job)
        if job.execution_scope != "measured_diagnostic" or rule is None:
            raise ValueError("durable recovery requires the original diagnostic registration")
        snapshot = read_registry_checkpoint(path, expected_digest=expected_digest,
            expected_metadata=cls._storage_metadata(rule, source_digest))
        checkpoint = CheckpointArtifact.from_state(snapshot,
            checkpoint_id=rule["registration_id"], schema="heterocoord.registry_checkpoint.v3")
        participants = {name: canonical_digest(payload) for name, payload in snapshot.items()}
        if (checkpoint.digest != rule["checkpoint_digest"]
                or participants != rule["participant_state_digests"]
                or participants["rng"] != rule["random_stream_digest"]):
            raise ValueError("durable snapshot differs from the frozen recovery registration")
        loaded = cls.__new__(cls)
        loaded._checkpoint = checkpoint
        loaded._rule = deepcopy(dict(rule))
        loaded._process_owner = (os.getpid(), _PROCESS_NONCE)
        return loaded

    def bind(self, job: BranchJob) -> BranchJob:
        """Freeze recovery metadata without changing caller-owned identities."""
        self._assert_process_owner()
        if job.execution_scope != "measured_diagnostic":
            raise ValueError("registry recovery bridge is diagnostic-only; formal provider is not qualified")
        if job.attempt != 0:
            raise ValueError("bind recovery before the initial diagnostic plan is frozen")
        for digest in (job.checkpoint_digest, job.restore_checkpoint_digest):
            if digest and digest != self._checkpoint.digest:
                raise ValueError("job checkpoint differs from registry checkpoint")
        existing = job.metadata.get(REGISTRATION_KEY)
        if existing is not None and existing != self._rule:
            raise ValueError("cannot overwrite an existing recovery registration")
        bound = replace(job, checkpoint_digest=self._checkpoint.digest,
                        restore_checkpoint_digest=self._checkpoint.digest,
                        metadata={**deepcopy(job.metadata), REGISTRATION_KEY: deepcopy(self._rule)})
        recovery_registration(bound)  # same validation used before tool admission
        return bound

    def restore_receipt(self, registry: ReplayStateRegistryV3, job: BranchJob) -> dict[str, Any]:
        """Restore an admitted retry; return success only from fresh readback.

        The caller must run under BranchExecutor's registered-retry admission.
        Calling this method alone never grants permission to replace an outcome.
        Exact digest equality is deliberately stricter than physical tolerances;
        a tolerance-aware recovery schema would require separate registration.
        """
        self._assert_process_owner()
        rule = recovery_registration(job)
        if (job.execution_scope != "measured_diagnostic" or job.attempt < 1
                or rule != self._rule or job.attempt >= self._rule["max_attempts"]
                or job.restore_checkpoint_digest != self._checkpoint.digest):
            raise ValueError("retry does not match this frozen recovery checkpoint")
        try:
            registry.restore(deepcopy(self._checkpoint.state))
            observed = registry.readback_snapshot()
            # Type-sensitive equality also protects array dtype, tuple/list and
            # scalar distinctions which the generic JSON digest normalizes.
            if not registry.states_equal(self._checkpoint.state, observed):
                raise RuntimeError("recovery requires exact participant readback")
            digests = {name: canonical_digest(payload) for name, payload in observed.items()}
            receipt = {
                "restore_passed": True, "readback_digest": canonical_digest(observed),
                "recovery_registration_id": self._rule["registration_id"],
                "recovery_mode": self._rule["mode"],
                "random_stream_digest": digests["rng"], "participant_state_digests": digests,
            }
            if not verify_recovery_readback(job, receipt):
                raise RuntimeError("recovery readback differs from frozen registration")
            return receipt
        except BaseException:
            registry.quarantine()
            raise
