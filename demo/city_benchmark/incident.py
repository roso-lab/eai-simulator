"""Evidence-preserving incident report handoff for the city task."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable


@dataclass(frozen=True)
class IncidentReport:
    report_id: str
    observation_refs: tuple[str, ...]
    recipient_service_id: str
    submitted_time_s: float
    status: str = "pending_receipt"
    receipt_time_s: float | None = None


class IncidentReportRuntime:
    """Require fresh observation references and an explicit receipt."""

    def __init__(self):
        self._reports: dict[str, IncidentReport] = {}
        self._observations: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def register_observation(self, observation_ref: str, *, time_s: float,
                             valid_until_s: float | None = None, source: str = "runtime") -> None:
        """Register a delivered observation before it can support a report."""
        ref = str(observation_ref)
        timestamp = float(time_s)
        expiry = math.inf if valid_until_s is None else float(valid_until_s)
        if (not ref or not math.isfinite(timestamp)
                or (not math.isfinite(expiry) and expiry != math.inf)):
            raise ValueError("observation registration requires finite identifiers and times")
        if expiry < timestamp:
            raise ValueError("observation validity cannot end before publication")
        if ref in self._observations:
            raise ValueError(f"observation '{ref}' is already registered")
        self._observations[ref] = {"time_s": timestamp, "valid_until_s": expiry,
                                   "source": str(source)}

    def submit(self, observation_refs: Iterable[str], recipient_service_id: str, *, time_s: float) -> IncidentReport:
        refs = tuple(str(ref) for ref in observation_refs)
        if not refs or any(not ref for ref in refs):
            raise ValueError("incident reports require non-empty observation references")
        if not recipient_service_id:
            raise ValueError("incident reports require a recipient service")
        timestamp = float(time_s)
        if not math.isfinite(timestamp):
            raise ValueError("incident report time must be finite")
        unknown = [ref for ref in refs if ref not in self._observations]
        if unknown:
            raise ValueError(f"incident report references unknown observations: {unknown}")
        expired = [ref for ref in refs if timestamp > self._observations[ref]["valid_until_s"]]
        if expired:
            raise ValueError(f"incident report references expired observations: {expired}")
        self._counter += 1
        report = IncidentReport(f"incident_report_{self._counter:03d}", refs,
                                str(recipient_service_id), timestamp)
        self._reports[report.report_id] = report
        return report

    def acknowledge(self, report_id: str, *, time_s: float) -> IncidentReport:
        try:
            report = self._reports[str(report_id)]
        except KeyError as exc:
            raise KeyError(f"unknown incident report: {report_id}") from exc
        if report.status != "pending_receipt":
            raise ValueError(f"incident report '{report_id}' is already resolved")
        if float(time_s) < report.submitted_time_s:
            raise ValueError("incident receipt cannot predate report submission")
        receipt = IncidentReport(report.report_id, report.observation_refs,
                                 report.recipient_service_id, report.submitted_time_s,
                                 status="received", receipt_time_s=float(time_s))
        self._reports[report.report_id] = receipt
        return receipt

    def evidence(self, report_id: str) -> dict[str, Any]:
        try:
            report = self._reports[str(report_id)]
        except KeyError as exc:
            raise KeyError(f"unknown incident report: {report_id}") from exc
        return {"report_id": report.report_id, "observation_refs": list(report.observation_refs),
                "recipient_service_id": report.recipient_service_id, "status": report.status,
                "receipt_time_s": report.receipt_time_s,
                "observations_validated": all(ref in self._observations for ref in report.observation_refs)}


__all__ = ["IncidentReport", "IncidentReportRuntime"]
