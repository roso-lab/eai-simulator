import pytest

from benchmark.dynamic_v3 import DynamicDispositionV3, DynamicEventDispositionV3
from benchmark.physical_evidence_v3 import PhysicalEvidenceCertificateV3


def event(status="valid", disposition=DynamicDispositionV3.RECOVERED, closure="closure"):
    return DynamicEventDispositionV3("loss", disposition, 4, closure, ("operation",), physical_status=status, safe_stop_certificate_digest=("stop-cert" if disposition is DynamicDispositionV3.SAFE_STOP else None))


def certificate(**changes):
    values = dict(composition_digest="composition", source_commit="commit", closure_digest="closure", event_disposition=event(), operation_receipt_digest="operation", remaining_feasibility_digest="feasibility", guard_digest="guard", world_preservation_digest="world")
    values.update(changes)
    return PhysicalEvidenceCertificateV3(**values)


def test_physical_certificate_requires_closure_binding_and_resolved_evidence():
    assert certificate().qualified is True
    assert certificate().digest()
    with pytest.raises(ValueError, match="closure digest"):
        certificate(closure_digest="other")
    with pytest.raises(ValueError, match="resolved"):
        certificate(operation_receipt_digest="unknown")


def test_safe_stop_certificate_is_a_qualified_event_but_unknown_is_not():
    safe = certificate(event_disposition=event(status="unknown", disposition=DynamicDispositionV3.SAFE_STOP),)
    assert safe.qualified is True
    with pytest.raises(ValueError, match="unknown event"):
        certificate(event_disposition=event(status="unknown", disposition=DynamicDispositionV3.UNKNOWN))
