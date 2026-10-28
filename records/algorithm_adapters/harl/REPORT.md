# HARL/HAPPO native adapter evidence — 2026-10-28

Implemented and executed `NativeHAPPOAdapter(manifest_path, *, source_root,
device="cpu")`, with `NativeHARLAdapter` as an alias. Entry module:
`algorithm.system.coordination.harl.native_adapter`. It returns original
checkpoint-loaded `HAPPO.act` outputs as `AlgorithmDecision` assignments for
`TaskRunner`. Explicit `update_feedback` and `reset` maintain recurrent state.

| Evidence level | Executed result |
| --- | --- |
| Contract | Exact nine-feature observations, actor/robot/task map, two macro actions, feedback and episode lifecycle validated; incompatible contracts fail closed. |
| Native training | Original HARL HAPPO runner/trainer, critic and Adam: 64 updates, 4,096 environment steps, 320 optimizer steps per actor; both full actor states changed. |
| Native inference | Strict complete checkpoints reloaded; exact output/recurrent-state equality with direct original actor calls tested. |
| Holdout TaskRunner | 16 episodes on separate seeds; 32 original actor calls and 32 actual dispatches; 16/16 symbolic macro completions. |
| Latest upstream TaskRunner | Loaded `/tmp/hetrocoord-origin-20261028/task/runner.py` from `769e31d8e4a0a72682d8eeaf6acf473a45419e7d`; both actor assignments dispatched. |
| Physical / benchmark | **N/A / not_evaluable**. No Isaac/PhysX execution, Fire Rescue success, or benchmark performance claim. |
| Tests | **24 native adapter tests**, **39 with TaskRunner regression tests**, all passed. |

Source: `/tmp/heterocoord-algorithms-20261028/harl`, commit
`b1af98b0dbab72a2eee9d160751cd09aedbb8ce2`. The parent's shared `verify_source`
checked 616 source/config/doc files, with source code digest
`98783b65ea6cb865fffb05011430d24379d09962a8be6b5127c06ca082fdf359`.
Upstream tracked status remained clean. Original inherited method identity is
recorded for `run`, `collect`, `insert`, `compute`, `train`, `after_update`, and
`save`. Only runner construction binds the custom environment. No upstream
files, shared registries, legacy adapter, or unrelated dirty files were edited.
No commit or push was performed.

The supported condition is **locally_trained_macro_smoke**, with two actor-owned
inspection tasks in a symbolic unit-square environment. Positive reward and
completion are defined by that environment, not physical evidence. Its fixed
actor/task schema rejects arbitrary task graphs and coalitions; it does not
select one robot from native coalition outputs. There is no claim that this
checkpoint is compatible with official SMAC weights or the six-method paper
benchmark. Training seeds 11–14 and holdout seeds 10011/10012 are disjoint, but
this remains a small adapter smoke check rather than a generalization result.

Changed code/document paths (all under `/home/airs/hetrocoord/`):

- `algorithm/system/coordination/harl/native_adapter.py`
- `algorithm/system/coordination/harl/macro_environment.py`
- `algorithm/system/coordination/harl/train_native_smoke.py`
- `algorithm/system/coordination/harl/infer_native_smoke.py`
- `algorithm/system/coordination/harl/requirements-native.txt`
- `algorithm/system/coordination/harl/NATIVE_ADAPTER.md`
- `algorithm/system/coordination/harl/tests/test_native_adapter.py`

Evidence under `/home/airs/hetrocoord/records/algorithm_adapters/harl/`:

- `local_macro_smoke/manifest.json`: exact contract, actor identity, checkpoint and receipt hashes.
- `local_macro_smoke/actor_agent0.pt`, `actor_agent1.pt`: complete locally trained actors.
- `local_macro_smoke/training_receipt.json`: original method identities, configuration, changed parameter hashes, optimizer-step evidence.
- `local_macro_smoke/holdout_taskrunner.json`: all 16 holdout episodes, native action traces, dispatch feedback, runner ledgers and N/A verdicts.
- `inference_cli.json`: independent runnable inference CLI episode using final adapter code.
- `latest_origin_runner.json`: direct integration evidence against the latest origin runner.
- `source_integrity.json`: shared gate receipt.
- `tests.log`, `tests_with_runner.log`, `training.log`: execution logs.
- `environment_and_files.json`: runtime versions and changed-file hashes.

Run the trained checkpoint:

```bash
cd /home/airs/hetrocoord
/home/airs/.cache/heterocoord/envs/harl/bin/python -m algorithm.system.coordination.harl.infer_native_smoke \
  --source-root /tmp/heterocoord-algorithms-20261028/harl \
  --manifest records/algorithm_adapters/harl/local_macro_smoke/manifest.json
```

Full training/setup/test commands and schema details are in
`algorithm/system/coordination/harl/NATIVE_ADAPTER.md`. New method dependencies
are installed in `/home/airs/.cache/heterocoord/envs/harl`; its
`--system-site-packages` venv reuses the host Torch/NumPy installation and is
explicitly not a fully hermetic environment.
