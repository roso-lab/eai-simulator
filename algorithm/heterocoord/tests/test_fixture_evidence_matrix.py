from pathlib import Path
import json

from tools.validate_fixture_evidence_matrix import validate


def test_fixture_evidence_matrix_is_complete_and_receipts_exist():
    root = Path(__file__).resolve().parents[3]
    matrix_path = root / "records/algorithm_adapters/FIXTURE_EVIDENCE_MATRIX_20261029.json"
    errors = validate(json.loads(matrix_path.read_text(encoding="utf-8")), root)
    assert errors == []


def test_fixture_validator_rejects_pending_receipt_and_missing_reason():
    root = Path(__file__).resolve().parents[3]
    matrix_path = root / "records/algorithm_adapters/FIXTURE_EVIDENCE_MATRIX_20261029.json"
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    data["algorithms"]["SADCHER"]["execution_evidence_boundary"] = {
        "status": "pending",
        "receipt": "records/algorithm_adapters/sadcher/native_parity_and_feedback.json",
    }
    errors = validate(data, root)
    assert any("pending entry cannot have receipt" in error for error in errors)
    assert any("pending entry needs reason" in error for error in errors)
