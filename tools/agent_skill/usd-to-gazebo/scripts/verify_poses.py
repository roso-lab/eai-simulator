#!/usr/bin/env python3
"""Gate 4 of the usd-to-gazebo skill: physics pose readback.

Injects the worldprobe plugin into a copy of the generated world, loads it
with gzserver, and compares every model's final WorldPose against the
manifest ground truth. This is the only gate that sees exactly what
sdformat9, physics and the GUI see -- it caught the production bug where
7-value quaternion poses were silently parsed as RPY.

Gazebo inserts <include> models asynchronously, so the driver waits until
the probe's model count is stable and equal to the expected include count
before comparing (the probe re-dumps a snapshot every 100 updates).

Usage:
    python verify_poses.py --gazebo-dir usd/scene/factory_gazebo

Requires scripts/build_native.sh to have built libworldprobe.so.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gazebo-dir", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-pos-err", type=float, default=0.001, help="meters")
    ap.add_argument("--max-ang-err", type=float, default=0.2, help="degrees")
    args = ap.parse_args()
    root = os.path.abspath(args.gazebo_dir)

    probe_so = os.path.join(HERE, "libworldprobe.so")
    if not os.path.isfile(probe_so):
        sys.exit("libworldprobe.so not built; run bash scripts/build_native.sh first")

    manifest = json.load(open(f"{root}/conversion_manifest.json"))
    world = ET.parse(f"{root}/world.sdf").getroot()
    inc_names = [i.find("name").text for i in world.iter("include") if i.find("name") is not None]
    expected = len(inc_names)

    content = open(f"{root}/world.sdf").read()
    content = re.sub(r'(<world\b[^>]*>)', r'\1\n  <plugin name="probe" filename="%s"/>' % probe_so,
                     content, count=1)
    probe_world = f"{root}/world_probe_tmp.sdf"
    open(probe_world, "w").write(content)

    # kill leftovers that would hold the gazebo master port
    subprocess.run(["pkill", "-9", "-x", "gzserver"], check=False)
    time.sleep(1)

    env = dict(os.environ)
    env["GAZEBO_MODEL_PATH"] = f"{root}/models" + (":" + env["GAZEBO_MODEL_PATH"] if env.get("GAZEBO_MODEL_PATH") else "")
    env["LD_LIBRARY_PATH"] = HERE + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")

    log = f"{root}/verify_poses_tmp.log"
    with open(log, "wb") as lf:
        proc = subprocess.Popen(["gzserver", probe_world], stdout=lf, stderr=lf, env=env)
        deadline = time.time() + args.timeout
        stable, last_count, found = 0, -1, False
        while time.time() < deadline:
            time.sleep(3)
            text = open(log, "rb").read().decode("utf-8", "replace")
            counts = re.findall(r"PROBE_SNAPSHOT models=(\d+)", text)
            if not counts:
                continue
            found = True
            count = int(counts[-1])
            if count == last_count == expected:
                stable += 1
                if stable >= 2:  # two consecutive complete snapshots ~6 s apart
                    break
            else:
                stable = 0
            last_count = count
        time.sleep(1)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    text = re.sub(r"\x1b\[[0-9;]*m", "", open(log, "rb").read().decode("utf-8", "replace"))
    actual = {}
    for m in re.finditer(
        r"PROBE_POSE (\S+) pos=([-\d.e+]+) ([-\d.e+]+) ([-\d.e+]+) rot_wxyz=([-\d.e+]+),([-\d.e+]+),([-\d.e+]+),([-\d.e+]+)",
        text,
    ):
        vals = [float(m.group(i)) for i in range(2, 9)]
        actual[m.group(1)] = (np.array(vals[0:3]), np.array(vals[3:7]))  # last snapshot wins

    if not found:
        sys.exit(f"world did not finish loading within {args.timeout}s; see {log}")

    worst_pos, worst_ang, bad = 0.0, 0.0, 0
    for inc_name, e in zip(inc_names, manifest["instances"]):
        if inc_name not in actual:
            print(f"MISSING: {inc_name}")
            bad += 1
            continue
        apos, arot = actual[inc_name]
        epos = np.array(e["pose"][:3])
        eq = np.array(e["pose"][3:])
        dpos = np.abs(apos - epos).max()
        dot = abs(float(np.dot(arot, eq)))
        dang = 2 * math.degrees(math.acos(min(1.0, dot)))
        worst_pos = max(worst_pos, dpos)
        worst_ang = max(worst_ang, dang)
        if dpos > args.max_pos_err or dang > args.max_ang_err:
            bad += 1
            print(f"MISMATCH {inc_name}: dpos={dpos:.4f} m dang={dang:.3f} deg")
    print(f"checked {len(inc_names)} instances: bad={bad}, worst_pos={worst_pos:.5f} m, worst_angle={worst_ang:.4f} deg")
    print(f"spawned models: {len(actual)} (expected {expected})")
    for tmp in (probe_world, log):
        if os.path.isfile(tmp):
            os.unlink(tmp)
    if bad or len(actual) != expected:
        sys.exit(1)


if __name__ == "__main__":
    main()
