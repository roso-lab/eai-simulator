# Pegasus Simulator Attribution

The Pegasus asset bundle published at `usd/robot/pegasus/` and the multirotor
controller bundle published at `controller/traditional/pegasus_multirotor/`
in the configured asset provider are adapted from Pegasus Simulator:

- Upstream: https://github.com/PegasusSimulator/PegasusSimulator
- Revision: `e13dc659686b09fffb05275988b70e5dc66983da`
- Retrieved: 2026-08-12
- Upstream license: BSD 3-Clause, reproduced in `LICENSE`

The 3DR Iris model has an additional PX4 BSD 3-Clause attribution, reproduced
in `IRIS_LICENSE.rst`. The provider bundle retains `pegasus.usd` with the
optimized `pegasus_optimized.usdc`; EAI uses the optimized file at runtime and
keeps the source USD and material bundle available for inspection.

EAI-specific changes include a pure Torch implementation, Isaac Lab
`ArticulationCfg` assets, an EAI `ControllerCfg` adapter, batched environments,
position/yaw goal routing, direct rotor-speed mode, reset handling, and tests.
