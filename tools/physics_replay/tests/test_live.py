from collections import deque
from dataclasses import dataclass
from enum import Enum
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tools.physics_replay.live import CheckpointBusy, LiveCheckpoint, LiveReplayController, RestoreRejected, state_errors
from tools.physics_replay.replayer import PhysicsReplayer
from tools.physics_replay.runtime_state import ObjectStateComponent, StateCodec


class Backend:
    def __init__(self, directory):
        self.output_dir = directory
        self.checkpoints = []
        self.state = 0.0
        self.saved = {}
        self.busy = False
        self.fail_save = False
        self.fail_restore = False
        self.restores = []

    def capture(self, step_index):
        if self.busy:
            raise CheckpointBusy("worker running")
        if self.fail_save:
            raise ValueError("unsupported state")
        checkpoint = LiveCheckpoint(len(self.checkpoints), step_index, "test")
        self.checkpoints.append(checkpoint)
        self.saved[checkpoint.identifier] = self.state
        return checkpoint

    def restore(self, identifier):
        if self.fail_restore:
            raise RuntimeError("read-back failed")
        self.restores.append(identifier)
        self.state = self.saved[identifier]
        return {"step_index": self.checkpoints[identifier].step_index, "readback_errors": {"robot/velocity": 0.0}}

    def read_physics(self):
        return {"robot/velocity": np.array([self.state])}


def test_multiple_checkpoints_and_restore_pause(tmp_path):
    backend = Backend(tmp_path)
    controller = LiveReplayController(backend)
    controller.request("save")
    assert controller.poll()
    backend.state = 2.0
    controller.after_step()
    controller.request("save")
    assert controller.poll()
    assert [item.step_index for item in backend.checkpoints] == [0, 1]
    controller.request("select", 0)
    controller.request("restore")
    assert not controller.poll()
    assert backend.state == 0.0
    assert controller.step_index == 0
    controller.request("step")
    assert controller.poll()
    controller.after_step()
    assert not controller.poll()
    assert controller.step_index == 1
    controller.request("pause")
    assert controller.poll()


def test_unsafe_save_keeps_stepping_but_unsupported_state_does_not_retry(tmp_path):
    backend = Backend(tmp_path)
    backend.busy = True
    controller = LiveReplayController(backend)
    controller.request("save")
    assert controller.poll()
    assert controller.pending_save
    backend.busy = False
    assert controller.poll()
    assert not controller.pending_save
    backend.fail_save = True
    controller.request("save")
    assert controller.poll()
    assert not controller.pending_save
    assert "unsupported state" in controller.save_error


def test_restore_failure_blocks_run_and_single_step(tmp_path):
    backend = Backend(tmp_path)
    controller = LiveReplayController(backend)
    controller.request("save")
    controller.poll()
    backend.fail_restore = True
    controller.request("restore")
    assert not controller.poll()
    for command in ("step", "pause"):
        controller.request(command)
        assert not controller.poll()
    backend.fail_restore = False
    controller.request("restore")
    assert not controller.poll()
    assert not controller.blocked


@pytest.mark.parametrize("intermediate_error", [0.0, 0.01])
def test_verification_checks_every_frame_not_only_endpoint(tmp_path, intermediate_error):
    backend = Backend(tmp_path)
    controller = LiveReplayController(backend)
    controller.request("save")
    controller.poll()
    controller.request("verify", 2)
    assert controller.poll()
    for state in (1.0, 2.0, 1.0 + intermediate_error, 2.0):
        assert controller.poll()
        backend.state = state
        controller.after_step()
    assert not controller.poll()
    report = json.loads((tmp_path / "trajectory_verification.json").read_text())
    assert report["passed"] is (intermediate_error == 0)
    assert report["trajectory_errors"]["robot/velocity"] == pytest.approx(intermediate_error)
    assert controller.blocked is (intermediate_error != 0)


def test_verification_rejects_midflight_commands(tmp_path):
    controller = LiveReplayController(Backend(tmp_path))
    controller.request("save")
    controller.poll()
    controller.request("verify", 2)
    controller.poll()
    controller.request("restore")
    assert controller.poll()
    assert len(controller.backend.restores) == 0
    assert "verification is running" in controller.message


def test_request_overflow_and_invalid_values_do_not_escape_ui(tmp_path):
    controller = LiveReplayController(Backend(tmp_path))
    for unused in range(40):
        controller.request("select", None)
    assert controller.poll()
    assert controller.selected is None


def test_pose_errors_ignore_quaternion_sign_and_measure_units():
    expected = {"robot/pose": np.array([0, 0, 0, 1, 0, 0, 0])}
    actual = {"robot/pose": np.array([0.003, 0.004, 0, -1, 0, 0, 0])}
    errors = state_errors(expected, actual)
    assert errors["robot/pose/position_m"] == pytest.approx(0.005)
    assert errors["robot/pose/rotation_rad"] == 0
    assert np.isinf(state_errors({"joint": [np.nan]}, {"joint": [0]})["joint"])
    with pytest.raises(ValueError, match="target set"):
        state_errors(expected, {})


class Phase(Enum):
    ACTIVE = 1


@dataclass(frozen=True)
class Task:
    location: tuple
    phase: Phase


def test_codec_roundtrips_typed_runtime_values_without_aliasing():
    codec = StateCodec()
    value = {
        ("robot", 1): deque([Task((1, 2), Phase.ACTIVE)], maxlen=3),
        "array": np.arange(5, dtype=np.float32),
        "selection": slice(None, 4, 2),
        "range": range(3),
        "members": frozenset({"robot"}),
    }
    encoded = codec.encode(value)
    value["array"][0] = 99
    restored = codec.decode(encoded)
    assert restored[("robot", 1)].maxlen == 3
    assert restored[("robot", 1)][0] == Task((1, 2), Phase.ACTIVE)
    assert restored["array"].dtype == np.float32
    assert restored["array"][0] == 0
    assert restored["selection"] == slice(None, 4, 2)
    assert restored["range"] == range(3)
    assert restored["members"] == frozenset({"robot"})


def test_codec_preserves_tensor_and_nested_history():
    torch = pytest.importorskip("torch")

    class History:
        pass

    history = History()
    history._pointer = 2
    history._buffer = torch.arange(12, dtype=torch.float64).reshape(3, 1, 4)
    history._num_pushes = torch.tensor([7], dtype=torch.int64)
    history._time_lags = torch.tensor([2], dtype=torch.int32)
    codec = StateCodec((History,))
    restored = codec.decode(codec.encode(history))
    assert restored._pointer == 2
    for name in ("_buffer", "_num_pushes", "_time_lags"):
        expected = getattr(history, name)
        actual = getattr(restored, name)
        assert expected.dtype == actual.dtype
        assert expected.device == actual.device
        assert torch.equal(expected, actual)
        assert expected.data_ptr() != actual.data_ptr()


def test_object_snapshot_rejects_unknown_resources_with_field_name():
    instance = SimpleNamespace(resource=lambda: None, values={1: "state"})
    with pytest.raises(ValueError, match="owner.resource"):
        ObjectStateComponent("owner", instance).capture_state()
    component = ObjectStateComponent("owner", instance, exclude={"resource"})
    state = component.capture_state()
    instance.values.clear()
    instance.extra = True
    component.restore_state(state)
    assert instance.values == {1: "state"}
    assert not hasattr(instance, "extra")
    assert callable(instance.resource)


@pytest.mark.parametrize("corruption", ["shape", "dtype", "nonfinite", "extra", "missing"])
def test_snapshot_is_validated_before_any_physics_write(corruption):
    target = SimpleNamespace(target_id="robot", writable_channels=("velocity",))
    initial = {"robot/velocity": np.zeros(6, dtype=np.float32)}
    writes = []
    adapter = SimpleNamespace(read=lambda *args: initial, write=lambda *args: writes.append(args))
    replayer = PhysicsReplayer(adapter, session=None, targets=[target])
    incoming = {key: value.copy() for key, value in initial.items()}
    if corruption == "shape":
        incoming["robot/velocity"] = np.zeros(3, dtype=np.float32)
    elif corruption == "dtype":
        incoming["robot/velocity"] = incoming["robot/velocity"].astype(np.float64)
    elif corruption == "nonfinite":
        incoming["robot/velocity"][0] = np.nan
    elif corruption == "extra":
        incoming["extra"] = np.zeros(1)
    else:
        incoming.clear()
    with pytest.raises(ValueError):
        replayer.restore_snapshot(incoming, 0)
    assert writes == []


def test_restore_waits_for_worker_without_advancing_physics(tmp_path):
    backend = Backend(tmp_path)
    original_restore = backend.restore
    waiting = [True]

    def restore(identifier):
        if waiting[0]:
            raise CheckpointBusy("worker running")
        return original_restore(identifier)

    backend.restore = restore
    controller = LiveReplayController(backend)
    controller.request("save")
    controller.poll()
    controller.request("restore")
    assert not controller.poll()
    assert not controller.poll()
    assert not controller.blocked
    assert controller.paused
    waiting[0] = False
    assert not controller.poll()
    assert not controller.blocked
    assert backend.restores == [0]


def test_invalid_checkpoint_selection_keeps_previous_selection(tmp_path):
    controller = LiveReplayController(Backend(tmp_path))
    controller.request("save")
    controller.poll()
    controller.request("select", 999)
    controller.poll()
    assert controller.selected == 0


def test_native_window_buttons_shortcuts_and_cleanup(monkeypatch, tmp_path):
    import sys
    from tools.physics_replay.native_ui import NativeReplayWindow

    buttons = {}
    unsubscribe = []

    class Widget:
        def __init__(self, *args, **kwargs):
            self.frame = self
            self.text = ""
            self.destroyed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def destroy(self):
            self.destroyed = True

    class ComboBox(Widget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.model = SimpleNamespace(add_item_changed_fn=lambda callback: None)

    def button(label, *, clicked_fn, **kwargs):
        buttons[label] = clicked_fn
        return Widget()

    interface = SimpleNamespace(
        subscribe_to_keyboard_events=lambda keyboard, callback: "subscription",
        unsubscribe_to_keyboard_events=lambda *args: unsubscribe.append(args),
    )
    inputs = SimpleNamespace(
        KeyboardInput=SimpleNamespace(F5="F5", F9="F9", F8="F8", F10="F10"),
        KeyboardEventType=SimpleNamespace(KEY_PRESS="press"),
        acquire_input_interface=lambda: interface,
    )
    appwindow = SimpleNamespace(get_default_app_window=lambda: SimpleNamespace(get_keyboard=lambda: "keyboard"))
    ui = SimpleNamespace(Window=Widget, Label=Widget, VStack=Widget, HStack=Widget, ComboBox=ComboBox, Button=button)
    for name, module in {
        "carb": SimpleNamespace(input=inputs), "carb.input": inputs,
        "omni": SimpleNamespace(appwindow=appwindow, ui=ui), "omni.ui": ui, "omni.appwindow": appwindow,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    backend = Backend(tmp_path)
    backend.tolerance = 1e-5
    controller = LiveReplayController(backend)
    window = NativeReplayWindow(controller)
    buttons["Save [F5]"]()
    assert not backend.checkpoints
    controller.poll()
    window.refresh()
    assert len(backend.checkpoints) == 1
    assert not window._on_key(SimpleNamespace(type="press", input="F9"))
    assert not controller.poll()
    assert controller.paused
    assert window._on_key(SimpleNamespace(type="release", input="F9"))
    window._on_key(SimpleNamespace(type="press", input="F10"))
    assert controller.poll()
    controller.after_step()
    assert not controller.poll()
    native_window = window.window
    window.close()
    window.close()
    assert native_window.destroyed
    assert unsubscribe == [("keyboard", "subscription")]


def test_live_backend_validates_all_physics_before_usd_mutation(monkeypatch, tmp_path):
    import tools.physics_replay.live as live_module
    from tools.physics_replay.components import CallbackComponent
    from tools.physics_replay.types import ResolvedTarget, TargetSpec

    spec = TargetSpec("/World/robot", ("pose",))
    target = ResolvedTarget(
        target_id="robot", spec=spec, kind="articulation", collection_name="robot",
        root_path=spec.prim_path, supported_channels=spec.channels, writable_channels=spec.channels,
    )
    physics = {"robot/pose": np.array([1, 2, 3, 1, 0, 0, 0], dtype=np.float32)}
    writes = []

    def write(target, values):
        writes.append(target.target_id)
        physics.update({key: value.copy() for key, value in values.items()})

    adapter = SimpleNamespace(read=lambda target, keys: {key: physics[key].copy() for key in keys}, write=write)
    entity = SimpleNamespace(cfg=SimpleNamespace(prim_path=spec.prim_path), data=SimpleNamespace())
    session = SimpleNamespace(
        base_env=SimpleNamespace(scene=SimpleNamespace(articulations={"robot": entity}), set_command=lambda *args: None), device="cpu",
    )
    monkeypatch.setattr(live_module, "TargetResolver", lambda base_env: SimpleNamespace(resolve_all=lambda specs: [target]))
    monkeypatch.setattr(live_module, "IsaacLabStateAdapter", lambda base_env: adapter)
    usd_restores = []
    component = CallbackComponent("usd", 1, lambda: {}, lambda state: usd_restores.append(state), priority=10)
    backend = live_module.LiveCheckpointBackend(session, tmp_path, components=[component])
    checkpoint = backend.capture(7)
    physics["robot/pose"][0] = 5
    assert backend.restore(checkpoint.identifier)["passed"]
    assert physics["robot/pose"][0] == 1
    assert len(usd_restores) == 1
    original_restore = component._restore

    def partial_failure(state):
        usd_restores.append(state)
        raise RuntimeError("partial USD restore failed")

    component._restore = partial_failure
    with pytest.raises(RuntimeError, match="partial USD restore failed"):
        backend.restore(checkpoint.identifier)
    failure = json.loads((tmp_path / "last_restore.json").read_text())
    assert failure["phase"] == "restore"
    assert failure["state_may_have_changed"] is True
    assert failure["passed"] is False
    component._restore = original_restore
    snapshots = backend.store.read(checkpoint.identifier)
    snapshots["physics"].state["state"]["robot/pose"] = np.zeros(3, dtype=np.float32)
    monkeypatch.setattr(backend.store, "read", lambda identifier: snapshots)
    writes.clear()
    usd_restores.clear()
    with pytest.raises(ValueError, match="schema mismatch"):
        backend.restore(checkpoint.identifier)
    assert not writes
    assert not usd_restores
    rejection = json.loads((tmp_path / "last_restore.json").read_text())
    assert rejection["passed"] is False
    assert rejection["phase"] == "validation"
    assert rejection["state_may_have_changed"] is False
    with pytest.raises(ValueError, match="live session"):
        backend.restore(999)


def test_actual_isaaclab_delay_ring_restores_subsequent_outputs(monkeypatch):
    import importlib
    import importlib.util
    from pathlib import Path
    import sys
    from types import ModuleType

    torch = pytest.importorskip("torch")
    specification = importlib.util.find_spec("isaaclab")
    if specification is None:
        pytest.skip("requires installed Isaac Lab buffer source, not a running Isaac application")
    package = ModuleType("replay_test_buffers")
    package.__path__ = [str(Path(next(iter(specification.submodule_search_locations))) / "utils" / "buffers")]
    monkeypatch.setitem(sys.modules, "replay_test_buffers", package)
    circular_module = importlib.import_module("replay_test_buffers.circular_buffer")
    delay_module = importlib.import_module("replay_test_buffers.delay_buffer")
    buffer = delay_module.DelayBuffer(3, 2, "cpu")
    buffer.set_time_lag(torch.tensor([1, 3], dtype=torch.int32))
    for index in range(7):
        buffer.compute(torch.full((2, 4), float(index)))
    codec = StateCodec((circular_module.CircularBuffer, delay_module.DelayBuffer))
    restored = codec.decode(codec.encode(buffer))
    assert restored._circular_buffer._pointer == buffer._circular_buffer._pointer
    assert torch.equal(restored.time_lags, buffer.time_lags)
    for index in range(7, 16):
        incoming = torch.full((2, 4), float(index))
        assert torch.equal(buffer.compute(incoming), restored.compute(incoming))


def test_sleep_flags_are_restored_without_an_extra_physics_step():
    from tools.physics_replay.stage_state import SleepStateComponent

    flags = {"/World/prop": False, "/World/robot": True}
    operations = []

    def set_sleeping(stage_id, identifier, sleeping):
        operations.append((identifier, sleeping))
        flags[identifier] = sleeping

    component = SleepStateComponent.__new__(SleepStateComponent)
    component.stage_id = 1
    component.path_id = lambda path: path
    component.interface = SimpleNamespace(
        is_sleeping=lambda stage_id, identifier: flags[identifier],
        put_to_sleep=lambda stage_id, identifier: set_sleeping(stage_id, identifier, True),
        wake_up=lambda stage_id, identifier: set_sleeping(stage_id, identifier, False),
    )
    saved = {"/World/prop": True, "/World/robot": False}
    component.validate_state(saved)
    component.restore_state(saved)
    assert flags == saved
    assert operations == [("/World/prop", True), ("/World/robot", False)]


@pytest.fixture
def stage_component(monkeypatch):
    import sys
    from tools.physics_replay.stage_state import StageStateComponent

    class Prim:
        def __init__(self, path, enabled):
            self.path = path
            self.enabled = enabled

        def GetPath(self):
            return self.path

        def HasAPI(self, api):
            return True

    class RigidBodyAPI:
        def __init__(self, prim):
            self.prim = prim

        def GetRigidBodyEnabledAttr(self):
            return SimpleNamespace(Get=lambda: self.prim.enabled)

    component = StageStateComponent.__new__(StageStateComponent)
    component.managed_paths = set()
    component.runtime_paths = ("/World/runtime",)
    component.rigid_api = RigidBodyAPI
    component.prims = {"/World/extinguisher": Prim("/World/extinguisher", True)}
    component.stage = SimpleNamespace(Traverse=lambda: component.prims.values())
    layer = SimpleNamespace(ImportFromString=lambda text: True)
    monkeypatch.setitem(sys.modules, "pxr", SimpleNamespace(Sdf=SimpleNamespace(Layer=SimpleNamespace(CreateAnonymous=lambda name: layer))))
    return component, Prim


@pytest.mark.parametrize("original_enabled", [True, False])
def test_rigid_body_enable_toggle_is_not_a_topology_change(stage_component, original_enabled):
    component, prim_type = stage_component
    component.prims["/World/extinguisher"].enabled = not original_enabled
    state = {
        "attributes": "saved attributes", "runtime": "saved objects", "present": [],
        "rigid": {"paths": ["/World/extinguisher"] if original_enabled else []},
        "rigid_topology": ["/World/extinguisher"],
    }
    component.validate_state(state)


@pytest.mark.parametrize("operation", ["remove", "add"])
def test_real_unregistered_topology_changes_still_fail_with_path(stage_component, operation):
    component, prim_type = stage_component
    state = {
        "attributes": "saved attributes", "runtime": "saved objects", "present": [],
        "rigid": {"paths": ["/World/extinguisher"]}, "rigid_topology": ["/World/extinguisher"],
    }
    if operation == "remove":
        component.prims.clear()
        changed_path = "/World/extinguisher"
    else:
        changed_path = "/World/new_body"
        component.prims[changed_path] = prim_type(changed_path, False)
    with pytest.raises(ValueError, match=changed_path):
        component.validate_state(state)


@pytest.mark.parametrize("command", ["restore", "verify"])
@pytest.mark.parametrize("already_blocked", [False, True])
def test_prewrite_rejection_preserves_current_state_and_previous_safety_lock(tmp_path, command, already_blocked):
    backend = Backend(tmp_path)
    controller = LiveReplayController(backend)
    controller.request("save")
    controller.poll()
    controller.after_step()
    backend.state = 5.0
    controller.blocked = already_blocked

    def reject(identifier):
        raise RestoreRejected("Restore rejected before writes")

    backend.restore = reject
    controller.request(command)
    assert not controller.poll()
    assert controller.blocked is already_blocked
    assert controller.paused
    assert backend.state == 5.0
    assert controller.step_index == 1
    controller.request("pause")
    assert controller.poll() is (not already_blocked)


def test_rejection_after_worker_wait_does_not_leave_an_invalid_state_lock(tmp_path):
    backend = Backend(tmp_path)
    controller = LiveReplayController(backend)
    controller.request("save")
    controller.poll()
    attempts = []

    def restore(identifier):
        attempts.append(identifier)
        if len(attempts) == 1:
            raise CheckpointBusy("worker running")
        raise RestoreRejected("Restore rejected before writes")

    backend.restore = restore
    controller.request("restore")
    assert not controller.poll()
    assert not controller.poll()
    assert not controller.blocked
    controller.request("step")
    assert controller.poll()
