"""工厂 EMOS 仿真 — 键盘 + 遥控器输入处理"""

import queue


class InputHandler:
    """统一处理物理键盘和网页遥控器输入，提供 just_pressed 边缘检测。"""

    _ALL_KEYS = [1, 2, 3, 4, 5, 6, 7, 8, 9, "K"]

    def __init__(self, remote_key_queue):
        self._remote_queue = remote_key_queue
        self._last = {k: False for k in self._ALL_KEYS}

    def poll(self):
        """返回 {key: (pressed, just_pressed)} 字典，每帧调用一次。"""
        remote = set()
        while True:
            try:
                k = self._remote_queue.get_nowait()
            except queue.Empty:
                break
            if k == "K":
                remote.add("K")
            elif k in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
                remote.add(int(k))

        phys = {k: False for k in self._ALL_KEYS}
        try:
            import carb.input

            ii = carb.input.acquire_input_interface()
            _km = {
                1: carb.input.KeyboardInput.KEY_1,
                2: carb.input.KeyboardInput.KEY_2,
                3: carb.input.KeyboardInput.KEY_3,
                4: carb.input.KeyboardInput.KEY_4,
                5: carb.input.KeyboardInput.KEY_5,
                6: carb.input.KeyboardInput.KEY_6,
                7: carb.input.KeyboardInput.KEY_7,
                8: carb.input.KeyboardInput.KEY_8,
                9: carb.input.KeyboardInput.KEY_9,
                "K": carb.input.KeyboardInput.K,
            }
            for hid, kcode in _km.items():
                try:
                    phys[hid] = ii.get_keyboard_value(None, kcode) > 0
                except Exception:
                    pass
        except Exception:
            pass

        result = {}
        for hid in self._ALL_KEYS:
            pressed = phys[hid] or (hid in remote)
            just = pressed and not self._last[hid]
            result[hid] = (pressed, just)
            self._last[hid] = pressed
        return result
