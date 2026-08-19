# EAI db-CBS

This package is EAI Simulator's maintained db-CBS integration used by
`algorithm.multi_robot_navigation`. Its required upstream source is vendored;
runtime does not wrap or load another db-CBS checkout.

## Contents

- `native/src`: db-CBS C++ planner source.
- `native/ompl`: the customized OMPL revision required by db-CBS.
- `native/dynoplan`: dynoplan and dynobench source used for trajectory
  optimization and robot models.
- `native/motions`: the tracked double-integrator model and the ignored,
  checksum-verified motion-primitive payload.
- `fetch_motion_primitives.py`: downloads that payload directly from the
  provider recorded by upstream db-CBS.
- `planner.py`: typed problem/result conversion and execution of the native
  binary built inside this package.
- `map_environment.py`: EAI occupancy-map conversion.
- `session.py` and `trajectory.py`: mission state and synchronized playback.

Upstream license texts are retained in `native/licenses`.

## Dependencies

The Python map adapter reuses `algorithm.global_planner`, including the
NumPy, PyYAML, and Pillow dependencies listed in
`algorithm/global_planner/requirements.txt`.

The native build also needs CMake, a C++17 compiler, Boost, Eigen, FCL,
yaml-cpp, and Crocoddyl. `build_native.sh` resolves these only from the active
Conda environment plus `/opt/openrobots` and ROS Humble; it does not install
system packages.

OMPL, db-CBS, dynoplan, dynobench, and nlohmann/json are vendored source, not
generated build output. Their license texts live in `native/licenses`; the
base revisions and motion-primitive source are recorded in
`native/THIRD_PARTY.md`. The generated `native/build/` tree remains local and
ignored. The motion-primitive payload is also ignored and is not redistributed
through Git or Git LFS.

## Build

The project-specific source is vendored here. Generic native dependencies are
provided by EAI's `env_isaaclab` environment and the system ROS/FCL packages.
Do not build from a separate db-CBS source tree.

```bash
cd /home/airs/eai-simulator
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
EAI_DBCBS_BUILD_JOBS=8 algorithm/dbcbs/build_native.sh
```

The build script validates the required motion primitive locally. When it is
missing, the script downloads only
`double_integrator_0_sorted.msgpack` from the upstream TUB Cloud share and
installs it after the recorded size and SHA-256 both match. An existing file is
checked without network access; an invalid existing file is left untouched.

The build output is
`algorithm/dbcbs/native/build/db_cbs`. It is intentionally ignored by Git and
can be reproduced from the EAI repository source.

## Runtime boundary

`discover_dbcbs_root()` resolves only `algorithm/dbcbs/native`. The loader path
contains only the package build directory, the active Python/Conda environment,
and an existing `LD_LIBRARY_PATH`; it does not scan another checkout, the user
site, or other Conda environments.

The navigation plugin plans every selected ground robot as one conflict-aware
batch. Unselected ground robots are inserted as static boxes. Aerial robot
types are filtered by the EAI adapter before a mission is submitted.

Each problem robot carries an explicit planning-frame radius. The same radius
is used by discrete KCBS collision checking and dynoplan continuous trajectory
optimization. EAI rejects an optimized result if the linearly interpolated
trajectories violate any pair's summed radii.
