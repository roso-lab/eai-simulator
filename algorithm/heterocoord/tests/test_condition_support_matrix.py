import json
from pathlib import Path


def test_condition_support_matrix_is_explicit_and_fail_closed():
    root = Path(__file__).resolve().parents[3]
    path = root / "records/algorithm_adapters/CONDITION_SUPPORT_MATRIX_20261029.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == "heterocoord.condition_support_matrix.v1"
    assert [row["algorithm_id"] for row in data["algorithms"]] == [
        "FMC-ATA", "BT-capabilities", "SADCHER", "HARL-HAPPO",
        "TeamWeaver-adapted-to-EAI", "EMOS",
    ]
    for row in data["algorithms"]:
        assert set(row["conditions"]) == {"P1", "P2", "P3-J", "P3-S", "P4"}
        for condition in row["conditions"].values():
            assert condition["status"] in {"supported", "unregistered", "not_applicable", "pending"}
            assert condition["reason"]
    sadcher = next(row for row in data["algorithms"] if row["algorithm_id"] == "SADCHER")
    assert sadcher["conditions"]["P3-J"]["status"] == "pending"
