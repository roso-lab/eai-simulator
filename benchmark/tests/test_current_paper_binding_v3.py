from __future__ import annotations

import pytest

from benchmark.registration_v3 import ExperimentRegistrationV3, formal_readiness
from benchmark.scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from benchmark.formal_matrix_v3 import BaselineQualificationV3


CURRENT_PAPER = "1f329494e755523692f5f96c8e0889c6cec15a82"
STALE_PAPERS = (
    "75ceff6f55202b6b1a31b2e2e0f6de56023940fd",
    "bb82b7bdb1352f1633bc405f41595a4affc79567",
    "af12f6d0d435a41b61f67b591487d63aade0f41c",
    "07dcd4000e836f7e40178bb6df8fed985e6dadd0",
)


def test_scorebook_is_bound_to_current_overleaf_commit():
    assert PAPER_COMMIT == CURRENT_PAPER
    assert PAPER_COMMIT not in STALE_PAPERS
    assert default_scorebook_v3().paper_commit == CURRENT_PAPER


@pytest.mark.parametrize("stale_paper", STALE_PAPERS)
def test_registration_rejects_stale_paper_identity(stale_paper):
    with pytest.raises(ValueError, match="frozen paper"):
        ExperimentRegistrationV3(stale_paper, *([None] * 11))


def test_formal_readiness_no_longer_blocks_on_resolved_d3_partition():
    registration = ExperimentRegistrationV3(PAPER_COMMIT, *([None] * 11))
    report = formal_readiness(
        registration,
        case_report={"status": "na"},
        baseline_report={"status": "na"},
        calibration_report=None,
    )
    assert not report["ready"]
    assert "paper_d3_partition_unresolved" not in report["blockers"]


def test_baseline_qualification_needs_nonempty_unique_condition_coverage():
    import hashlib
    digests = tuple(hashlib.sha256(label.encode()).hexdigest()
                    for label in ("system", "task", "team", "budget", "observation", "manifest"))
    values = ("baseline", "source", "adapter", "checkpoint", "qualified", "observation")
    assert not BaselineQualificationV3(*values, ()).rankable
    assert not BaselineQualificationV3(*values, ("C0", "C0")).rankable
    assert not BaselineQualificationV3(*values, ("C0",)).rankable
    assert BaselineQualificationV3(*values, ("C0",), *digests).rankable
