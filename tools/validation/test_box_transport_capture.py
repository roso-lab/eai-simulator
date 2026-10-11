import gc
import weakref

import pytest

from tools.validation.box_transport_visual_probe import _RgbCapture


@pytest.mark.parametrize("fail", [False, True])
def test_close_drops_native_interfaces_before_framework_unload(fail):
    calls = []

    class Capture:
        def wait_async_capture(self):
            calls.append("wait")
            if fail:
                raise RuntimeError("capture failed")

    class App:
        pass

    probe = _RgbCapture.__new__(_RgbCapture)
    probe._capture, probe._app = Capture(), App()
    native, app = weakref.ref(probe._capture), weakref.ref(probe._app)
    if fail:
        with pytest.raises(RuntimeError, match="capture failed"):
            probe.close()
    else:
        probe.close()
    gc.collect()
    assert native() is None and app() is None
    probe.close()
    assert calls == ["wait"]
    with pytest.raises(RuntimeError, match="closed"):
        probe.save("late")
