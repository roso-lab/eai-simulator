"""Registered collector-to-analysis seam; never infers a design from observations."""
from pathlib import Path
from typing import Mapping, Any

from tools.experiment_analysis.batch import read_sources, assemble
from tools.experiment_analysis.bundle import export_batch, verify_bundle
from tools.experiment_analysis.engine import analyze
from tools.experiment_analysis.registration import load_registration
from tools.experiment_analysis.schema import AnalysisError, Design


def validate_paper_design(design_data: dict, *, repeat_ids: tuple[str, ...],
                          min_units: int, min_coverage: float) -> None:
    """Validate explicit frozen K3 and support floors, without inventing horizons."""
    design = Design.parse(design_data)
    if len(repeat_ids) != 3 or len(set(repeat_ids)) != 3:
        raise AnalysisError("paper collection requires three distinct frozen repeat IDs")
    if type(min_units) is not int or min_units < 30 or not .8 <= min_coverage <= 1:
        raise AnalysisError("paper support policy requires N>=30 and coverage>=0.8")
    if any(set(unit.repeats) != set(repeat_ids) for unit in design.units):
        raise AnalysisError("unit repeat support differs from frozen K3")
    if any(est.min_repeats != 3 or est.min_units < min_units or
           est.min_coverage < min_coverage for est in design.estimands):
        raise AnalysisError("estimand weakens frozen paper support policy")
    if any(cell.min_instances < min_units or cell.min_coverage < min_coverage for cell in design.cells):
        raise AnalysisError("required cell weakens frozen paper support policy")


def analyze_registered_collection(design_data: dict, registration: dict,
                                  source_manifest: dict, *, base_dir: str | Path,
                                  paper_policy: Mapping[str, Any] | None = None,
                                  destination: str | Path | None = None) -> dict:
    """Read verified live collector seals, select registered attempts, then analyze.

    Missing sources/terms/repeats remain in the frozen support. Method failures
    remain outcomes. This produces descriptive calculations and an audit, never
    a physical, causal-replay or inferential release certificate.
    """
    load_registration(design_data, registration)
    if paper_policy is not None:
        validate_paper_design(design_data, **dict(paper_policy))
    sources = read_sources(source_manifest, base_dir=Path(base_dir))
    data, audit = assemble(design_data, registration, sources)
    report = analyze(design_data, data)
    result = {"data": data, "audit": audit, "report": report,
              "qualification": "structural_collection_only", "inferentially_ready": False}
    if destination is not None:
        path = export_batch(destination, design_data, registration, sources)
        result["bundle"] = str(path)
        result["verification"] = verify_bundle(path, recompute=True)
    return result
