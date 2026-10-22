from __future__ import annotations

import pytest

from benchmark.oracle_v3 import OracleFinding, OracleStatus, REQUIRED_SEMANTIC_CHECKS, make_oracle_certificate
from benchmark.skill_qualification_v3 import SkillQualificationStatus, SkillTrack, follow_proxy_diagnostic


def _findings(status="pass"):
    return [OracleFinding(item, status, item) for item in REQUIRED_SEMANTIC_CHECKS]


def test_partial_capability_oracle_witness_is_unverified():
    certificate = make_oracle_certificate(task_id="task", backend_id="eai-capability-v2", findings=_findings(), witness={"team": ["a", "b"]}, backend_complete=False)
    assert certificate.status is OracleStatus.UNVERIFIED
    assert certificate.witness_digest


def test_complete_backend_witness_is_deployable():
    certificate = make_oracle_certificate(task_id="task", backend_id="canonical-v3", findings=_findings(), witness={"team": ["a", "b"]}, backend_complete=True)
    assert certificate.status is OracleStatus.DEPLOYABLE
    assert certificate.digest() == certificate.digest()


def test_explicit_team_proof_is_infeasible():
    certificate = make_oracle_certificate(task_id="task", backend_id="canonical-v3", findings=_findings("fail"), infeasibility_proof={"conflict": "deadline"})
    assert certificate.status is OracleStatus.TEAM_STATE_INFEASIBLE


def test_follow_proxy_is_diagnostic_only():
    skill = follow_proxy_diagnostic(skill_id="pick_place", robot_id="ur5", controller_id="proxy", physics_dt_s=0.005)
    assert skill.track is SkillTrack.IDEALIZED_DIAGNOSTIC
    assert skill.status is SkillQualificationStatus.UNSUPPORTED
    with pytest.raises(ValueError):
        type(skill)(skill.skill_id, skill.robot_id, skill.controller_id, SkillTrack.IDEALIZED_DIAGNOSTIC, SkillQualificationStatus.QUALIFIED, 0.005, 1.0, ("x",), True, True, True, "bad", {})
