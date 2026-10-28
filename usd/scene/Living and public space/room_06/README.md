# room_06 — Isaac Sim 5.1 场景

这是原先来自 `the_interior_14` 的 room_05 顺延命名版本。打开同目录的 `room_06.usda`，并保留旁边的 `textures/`。

- 网格碰撞：77 个（静态三角网格保持独立，避免跨空凸包）。
- 动态刚体组：8 个；语义小件组：0 个。
- 可动门候选：0 组；已写入关节：0 个。
- 静态支撑地面：z=0.000，用于补足原场景缺失的可行走平面。

- 场景灯光：10 个 USD 灯光节点（Dome、Key、3 个 bounds 补光和 5 个室内局部补光）。

Go2 键盘检验：`/home/pupil/miniconda3/envs/env_isaaclab/bin/python tools/blender/run_go2_office.py --scene "usd/benchmark/Living and public space/room_06/room_06.usda" --spawn-position 2.33 0.17 0.65 --motion-test-steps 150 --test-seconds 1`。

键盘控制说明见 `tools/blender/GO2_OFFICE_KEYBOARD.md`。
