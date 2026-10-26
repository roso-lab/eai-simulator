import copy

import pytest

from benchmark.paper_projection_v1 import PaperProjectionError, _digest, _project_d6


def _binding():
    return {
        "calibration": [
            {"instance_id": "i-a", "reference_bits": 100.0, "reference_time": 1.0},
            {"instance_id": "i-b", "reference_bits": 200.0, "reference_time": 2.0},
        ],
        "runs": [{"instance_id": "i-a"}, {"instance_id": "i-b"}],
    }


def _package(binding):
    return {
        "structured": {
            "d6": {
                "binding_digest": _digest(binding),
                "calibration": copy.deepcopy(binding["calibration"]),
                "runs": [
                    {"instance_id": "i-a", "success": True, "bits": 100.0, "time": 1.0},
                    {"instance_id": "i-b", "success": True, "bits": 200.0, "time": 2.0},
                ],
            }
        }
    }


def test_projection_preserves_instance_calibration_binding():
    result = _project_d6(_package(_binding()), _binding())
    assert result["calibration"] == _binding()["calibration"]
    assert result["runs"][1]["instance_id"] == "i-b"


@pytest.mark.parametrize("mutate, message", [
    (lambda b: b["calibration"].append({"instance_id": "i-a", "reference_bits": 1.0, "reference_time": 1.0}), "duplicate"),
    (lambda b: b["runs"].__setitem__(0, {"instance_id": "i-b"}), "instance_id does not match"),
    (lambda b: b["calibration"].__setitem__(0, {"instance_id": "i-a", "reference_bits": -1.0, "reference_time": 1.0}), "lower bound"),
])
def test_projection_rejects_bad_instance_calibration(mutate, message):
    binding = _binding()
    mutate(binding)
    package = _package(_binding())
    # A changed binding has a changed digest; update it so the intended schema
    # validation is exercised rather than an unrelated digest mismatch.
    package["structured"]["d6"]["binding_digest"] = _digest(binding)
    if "calibration" in binding:
        package["structured"]["d6"]["calibration"] = copy.deepcopy(binding["calibration"])
    with pytest.raises(PaperProjectionError, match=message):
        _project_d6(package, binding)
