"""Isaac-free validation of physical task binding; not simulation evidence."""
import json
from pathlib import Path

import pytest

from task.contracts import EaiTaskPackage
from tools.algorithm_adapters.isaac_pick_place import physical_options

CONFIG = Path(__file__).resolve().parents[1] / "configs/isaac_ur5_task.json"


def load_changed(tmp_path, change):
    document = json.loads(CONFIG.read_text())
    change(document)
    path = tmp_path / "task.json"
    path.write_text(json.dumps(document))
    return EaiTaskPackage.from_json(path)


def test_task_positions_angle_timeout_reach_host(tmp_path):
    def change(doc):
        metadata = doc["tasks"][0]["metadata"]
        metadata["target_position"] = [.44, -.02, .475]
        metadata["pick_place"].update(approach_degrees=7.5, timeout_s=63)
    task = load_changed(tmp_path, change)
    options = physical_options(task, tmp_path / "execution.json")
    assert options.place_position == [.44, -.02, .475]
    assert options.object_position == [.45, 0, .475]
    assert options.place_offset is None
    assert options.approach_degrees == 7.5 and options.timeout_s == 63


@pytest.mark.parametrize("change,message", [
    (lambda d: d["tasks"][0]["metadata"].pop("pick_place"), "object_position"),
    (lambda d: d["tasks"][0]["metadata"].update(position=[.6, 0, 0]), "disagrees"),
    (lambda d: d["tasks"][0]["metadata"].update(target_position=[.425, .01, .6]), "equal-height"),
    (lambda d: d["tasks"][0].update(target_ref="/World/WrongObject"), "target_ref"),
    (lambda d: d["robots"][0].update(position=[1, 0, 0]), "robot pose"),
])
def test_unsupported_or_inconsistent_physical_contract_fails(tmp_path, change, message):
    task = load_changed(tmp_path, change)
    with pytest.raises(ValueError, match=message):
        physical_options(task, tmp_path / "execution.json")
