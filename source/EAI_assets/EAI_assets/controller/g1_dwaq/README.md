# G1 DWAQ deployment bundle

`deployment.py` implements `dwaq_named_body_v1`: 100-D named body frame,
5-frame history, 0.8 s gait phase with 0.5 offset, encoder mean context, and
29 body-joint actions. The actor frame excludes the critic-only height scan;
`critic_height_scan` is provided for runtime diagnostics and requires 187 finite
collision hits from the 17x11 scanner.

`policy.pt` (TorchScript) and `policy.onnx` include the encoder, mean velocity
and latent heads, actor and history update. They are exported from
`model_287.pt` (SHA256 `3833940ddff303effdddee016bef2878aa47a8bbb63253ca8ebfb387c9e3b1a0`).
Decoder/critic and random latent sampling are not used in deterministic deployment.

Inputs: `frame [N,100]`, `history [N,500]`, `reset [N] bool`.
Outputs: `actions [N,29]` (raw), `next_history [N,500]`.
All batch dimensions are dynamic. Feed returned history into the next call;
set reset=true only for reset environments. Initial history may be zeros.
History contains oldest to newest frames, including the current frame. At
reset, all five slots receive the current frame. Raw history stays unclipped;
the actor's current frame is clipped to ±100, exactly as in training.

## Observation fields

| Slice | Field | Scale |
|---|---|---|
| 0:3 | base angular velocity in body coordinates | 1 |
| 3:6 | projected gravity in body coordinates | 1 |
| 6:9 | command: body vx, vy, yaw rate | 1 |
| 9:38 | named joint position minus default position | 1 |
| 38:67 | named joint velocity minus default velocity | 1 |
| 67:96 | previous raw, pre-clip action | 1 |
| 96:98 | sin(2π × left/right gait phase) | 1 |
| 98:100 | cos(2π × left/right gait phase) | 1 |

At reset, episode step=0, left phase=0, right phase=.5. Each control call advances
the episode step once; phase = `(step * .02 % .8) / .8`. `JOINT_NAMES` is the
explicit 29-name order from the checkpoint contract, not USD traversal order.
The full USD has 53 joints: 29 controlled body joints and 24 Inspire finger joints
held at zero. Targets = default + .25 × clip(action, ±5), clamped to soft limits.

## Playback and integration

From the repository root in the Isaac Lab environment:

```bash
python tools/locomotion_deployment/play.py --robot g1 --num-envs 2 --steps 250 --device cpu --headless
```

Default asset root is this repository's `usd/`; pass `--asset-root /path/to/usd`
if assets are stored separately. Remove `--headless` for a window.
Use `robot.make_robot_cfg(usd_path)` for the exact actuator gains/defaults,
32/4 solver iterations and authored self-collision setting. Physics dt=.001,
control dt=.02 (20 substeps). `G1DwaqController` exposes compute/apply/reset;
`integration.G1_DWAQ_CFG` exposes the HeteroCoord ControllerCfg interface.
Call reset for episode resets and policy switches. No global robot registration
or controller default is changed by this bundle.

The 17×11 height scan is critic-only: `(scanner_z - hit_z - .5)`, scale 1,
clip ±100. It is not needed by deployed G1 inference. Navigation may separately
use real scene scans, but must not append them to actor inputs.

## Validation boundary

See `tools/locomotion_deployment/README.md` for export checks and real USD playback.
This is a deployment of the retained model_287 baseline, not new training.
Historical School entrance long-stair ascent was 19/32, not a fully reliable
stair policy. No fall-recovery capability is claimed.
