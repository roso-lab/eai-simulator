import copy
import json

import pytest

from benchmark.results_pipeline_v3 import (
    ResultsPipelineError,
    build_negative_fixtures,
    build_synthetic_fixture,
    compute_results,
    export_results,
    verify_export,
    _input_payload,
    main,
)
from benchmark.records_v3 import canonical_digest


def _report():
    return compute_results(build_synthetic_fixture())


def test_synthetic_fixture_computes_all_dimensions_with_scientific_boundaries():
    report = _report()
    assert report["qualification"] == "diagnostic_only"
    assert report["formal_gates"] == {"rankable": False, "paper_score_eligible": False, "physical_qualified": False}
    by_id = {item["score_id"]: item for item in report["dimensions"]}
    assert [by_id[key]["status"] for key in ("D1", "D2", "D3", "D4", "D5", "D6")] == ["valid"] * 6
    assert by_id["D1"]["point"] == pytest.approx(80.5)
    assert by_id["D2"]["point"] == pytest.approx(75.0)
    assert by_id["D3"]["point"] == pytest.approx(78.3333333333)
    assert by_id["D4"]["point"] == pytest.approx(80.0)
    assert by_id["D5"]["excluded_families"] == ["short-below-floor"]
    assert by_id["D5"]["support"] == {"count": 1, "denominator": 2}
    assert by_id["D6"]["point"] == pytest.approx(50.0)
    assert report["composite"]["status"] == "valid"
    assert report["composite"]["formal_gates"]["rankable"] is False
    assert len(report["profile_range"]["vertices"]) == 16
    assert report["sanity_checks"]["status"] == "pass"
    assert {item["id"] for item in report["sanity_checks"]["checks"]} == {
        "dimension_points_bounded",
        "d2_delivery_cannot_exceed_conditional",
        "composite_inside_profile_range",
        "d5_exclusions_disclosed",
        "support_weights_bounded",
    }


def test_synthetic_values_match_independent_paper_arithmetic():
    payload = build_synthetic_fixture()
    report = compute_results(payload)
    by_id = {item["score_id"]: item for item in report["dimensions"]}
    d1 = 100 * (0.50 * 0.80 + 0.30 * 0.75 + 0.20 * 0.90)
    d2 = 100 * ((0.80 / 0.80) + (0.60 / 0.80) + (0.40 / 0.80)) / 3
    d3 = 100 / 3 * (0.90 + (0.80 + 0.60) / 2 + 0.75)
    eligible_family = (22 * 1.0 + 8 * (2.0 / 3.0)) / 30
    d5 = 100 * eligible_family
    d6 = 100 * (1.0 + 0.5 + 0.0) / 3
    composite = (d1 + (0.80 * d2) + d3 + 80.0 + d5 + d6) / 6
    assert by_id["D1"]["point"] == pytest.approx(d1)
    assert by_id["D2"]["point"] == pytest.approx(d2)
    assert by_id["D3"]["point"] == pytest.approx(d3)
    assert by_id["D5"]["point"] == pytest.approx(d5)
    assert by_id["D6"]["point"] == pytest.approx(d6)
    assert report["composite"]["point"] == pytest.approx(composite)


def test_negative_fixtures_preserve_na_and_incomplete_states():
    reports = {name: compute_results(payload) for name, payload in build_negative_fixtures().items()}
    d2 = {row["score_id"]: row for row in reports["d2_nominal_zero_na"]["dimensions"]}["D2"]
    assert d2["status"] == "na" and d2["point"] is None and d2["reason"] == "nominal_success_zero"
    assert reports["d2_nominal_zero_na"]["composite"]["status"] == "incomplete"
    d4 = {row["score_id"]: row for row in reports["d4_empty_closure_na"]["dimensions"]}["D4"]
    assert d4["status"] == "na" and d4["reason"] == "empty_affected_work_closure"
    d5 = {row["score_id"]: row for row in reports["d5_no_family_floor_na"]["dimensions"]}["D5"]
    assert d5["status"] == "na" and d5["excluded_families"] == ["short"]
    d6 = {row["score_id"]: row for row in reports["d6_fixed_directory_incomplete"]["dimensions"]}["D6"]
    assert d6["status"] == "incomplete" and d6["point"] is None


def test_digest_and_paper_identity_are_fail_closed():
    payload = build_synthetic_fixture()
    changed = copy.deepcopy(payload)
    changed["plan"]["seed"] += 1
    with pytest.raises(ResultsPipelineError, match="plan digest mismatch"):
        compute_results(changed)
    changed = copy.deepcopy(payload)
    changed["paper_commit"] = "stale"
    with pytest.raises(ResultsPipelineError, match="different paper"):
        compute_results(changed)
    changed = copy.deepcopy(payload)
    changed["input_digest"] = "0" * 64
    with pytest.raises(ResultsPipelineError, match="input digest mismatch"):
        compute_results(changed)
    changed = copy.deepcopy(payload)
    changed["dimensions"]["D1"]["validity"] = True
    changed["input_digest"] = canonical_digest(_input_payload(changed))
    with pytest.raises(ResultsPipelineError, match="finite real"):
        compute_results(changed)
    changed = copy.deepcopy(payload)
    changed["dimensions"]["D6"]["reference_bits"] = 10.0
    changed["input_digest"] = canonical_digest(_input_payload(changed))
    with pytest.raises(ResultsPipelineError, match="not bound"):
        compute_results(changed)


def test_export_is_deterministic_and_reloadable(tmp_path):
    report = _report()
    first = tmp_path / "one"
    second = tmp_path / "two"
    payload = build_synthetic_fixture()
    export_results(report, first, input_payload=payload)
    export_results(report, second, input_payload=payload)
    for name in ("report.json", "metrics.csv", "metrics.md", "coverage_report.json", "provenance.json", "input_manifest.json", "input_package.json", "output_manifest.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert verify_export(first)["schema"] == "heterocoord.results-output-manifest.v1"
    with pytest.raises(ResultsPipelineError, match="destination must be empty"):
        export_results(report, first, input_payload=payload)
    (first / "metrics.csv").write_text((first / "metrics.csv").read_text() + "tampered\n")
    with pytest.raises(ResultsPipelineError, match="mismatch"):
        verify_export(first)
    loaded = json.loads((first / "report.json").read_text())
    assert loaded["provenance"]["input_digest"] == report["provenance"]["input_digest"]
    assert (first / "metrics.csv").read_text().splitlines()[0].startswith("metric_id,status,point")


def test_export_and_verify_reject_stale_or_unsafe_manifests(tmp_path):
    payload = build_synthetic_fixture()
    destination = tmp_path / "result"
    export_results(_report(), destination, input_payload=payload)

    manifest_path = destination / "output_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../outside.json"] = {"sha256": "0" * 64, "size_bytes": 0}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ResultsPipelineError, match="unsafe output manifest path"):
        verify_export(destination)

    manifest.pop("files")["report.json"] = {"sha256": "0" * 64, "size_bytes": 0}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ResultsPipelineError, match="schema mismatch"):
        verify_export(destination)


def test_cli_accepts_future_normalized_input_package(tmp_path):
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(build_synthetic_fixture()), encoding="utf-8")
    output = tmp_path / "output"
    assert main([str(payload_path), "--output", str(output)]) == 0
    assert verify_export(output)["schema"] == "heterocoord.results-output-manifest.v1"
