import math

import pytest
import torch

from runtime_mount_physics import aggregate_runtime_physics


def inputs():
    return {
        "masses": torch.tensor([2., 2.], dtype=torch.float64),
        "local_coms": torch.tensor([[0., 0., 0.], [1., 0., 0.]], dtype=torch.float64),
        "inertias_at_com": torch.stack([torch.eye(3), torch.diag(torch.tensor([2., 3., 4.]))]).double(),
        "positions": torch.tensor([[0., 0., 0.], [0., 1., 0.]], dtype=torch.float64),
        "rotations": torch.stack([torch.eye(3),
                                 torch.tensor([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])]).double(),
    }


def test_com_and_inertia_include_body_rotation_and_offsets_once():
    result = aggregate_runtime_physics(**inputs())
    assert result["mass_kg"] == 4.
    assert result["center_of_mass_m"] == [0., 1., 0.]
    torch.testing.assert_close(torch.tensor(result["inertia_kg_m2"]), torch.diag(torch.tensor([8., 3., 9.])))
    assert result["status"] == "simulation_derived"


def test_inertia_is_invariant_under_global_translation():
    original = inputs()
    shifted = inputs()
    shifted["positions"] += torch.tensor([5., -7., 3.])
    first = aggregate_runtime_physics(**original)
    second = aggregate_runtime_physics(**shifted)
    assert first["inertia_kg_m2"] == second["inertia_kg_m2"]
    assert second["center_of_mass_m"] == [5., -6., 3.]


@pytest.mark.parametrize("name", ["masses", "local_coms", "inertias_at_com", "positions", "rotations"])
def test_nonfinite_state_is_not_reported_as_valid(name):
    values = inputs()
    values[name].reshape(-1)[0] = math.nan
    with pytest.raises(ValueError, match="finite"):
        aggregate_runtime_physics(**values)


def test_missing_body_coverage_is_rejected():
    values = inputs()
    values["positions"] = values["positions"][:1]
    with pytest.raises(ValueError, match="coverage"):
        aggregate_runtime_physics(**values)


def test_reflection_is_not_a_body_rotation():
    values = inputs()
    values["rotations"][0, 0, 0] = -1
    with pytest.raises(ValueError, match="proper"):
        aggregate_runtime_physics(**values)
