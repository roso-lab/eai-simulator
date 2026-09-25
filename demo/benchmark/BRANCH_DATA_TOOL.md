# Branch Data Tool

`demo/benchmark/branch_data.py` is the simulator-independent ledger and
branch lifecycle layer. A scene runner owns Isaac creation, actions, stepping,
task interpretation, and checkpoint capture/restore. It passes those values to
the common batch API.

## API

```python
from demo.benchmark.branch_data import (
    open_batch, resume_batch, validate_run,
)

batch = open_batch(
    output,
    scene_key="warehouse_nvd",
    task_id="W-DAG-01",
    requested=32,
    execution_scope="measured",
    task_version="warehouse.task.v1",
    formal=True,
)
```

The batch methods are `register_branch`, `record_step` (or the barrier-aware
`record_step_batch`), `record_event`, `record_progress`, `record_network`,
`record_cost`, `record_checkpoint`, `finish_branch`, and `close_batch`.
`resume_batch` reconstructs branch identities and row counters from the
append-only JSONL files. `validate_run` writes `validation.json` and refuses a
formal result unless all 32 admission, completion, verification, active-step,
and ledger gates are present.

## Adapter boundary

Adapters implement the `BranchSceneAdapter` protocol in `branch_data.py`:
`create_scene`, `read_branch_state`, `apply_participant_actions`,
`step_simulation`, `capture_checkpoint`, `restore_checkpoint`, `task_status`,
and `scene_payload`. Current scene integrations are:

- `demo/warehouse_benchmark/branch_data_adapter.py`
- `demo/city_benchmark/branch_data_adapter.py`
- `demo/factory_benchmark/branch_data_adapter.py`
- `demo/modern_house_benchmark/branch_data_adapter.py`
- `demo/restaurant_benchmark/branch_data_adapter.py` (scene-lifecycle probe;
  Restaurant business task remains authoring-only)

The adapters do not replace scene task control. They only project scene-owned
state into the common ledger.

The scene-owned runners are available as modules:

```text
demo.warehouse_benchmark.checkpoint_branches
demo.modern_house_benchmark.checkpoint_branches
demo.factory_benchmark.checkpoint_branches
demo.city_benchmark.checkpoint_branches
demo.restaurant_benchmark.checkpoint_branches
```

Write each run below the corresponding `demo/<scene>_benchmark/runs/`
directory. Warehouse, Modern House, Factory, and City require the locally
imported benchmark asset pack described in
`source/EAI_assets/docs/benchmark-scenes.md`; those multi-gigabyte USD assets
are runtime data and are not carried by this Git branch.

`demo.benchmark.protocol_runner` and
`demo.warehouse_benchmark.protocol_completion` are protocol-synthetic
companions. Their outputs remain `execution_scope=protocol_synthetic` and do
not become measured Isaac evidence merely because a 32-slot infrastructure
report is supplied.

## Output and execution scope

Each batch writes `manifest.json`, `branch_manifest.jsonl`, `steps.jsonl`,
`episodes.jsonl`, `events.jsonl`, `progress.jsonl`, `network.jsonl`,
`costs.jsonl`, `checkpoints.jsonl`, `run_state.json`, and `validation.json`.
Every ledger row carries the immutable branch identity fields, including
`scene_key`, `task_id`, `base_instance_id`, `condition_id`, `pair_seed`,
`pair_group_id`, `branch_id`, `branch_type`, `slot`, `checkpoint_digest`,
`task_version`, `source_revision`, and `execution_scope`.

`execution_scope` is one of:

- `measured`: values collected from a live scene run;
- `protocol_synthetic`: protocol-only or kinematic pilot values;
- `pending`: declared but not executed;
- `failed`: an attempted run that failed before acceptance.

Checkpoint rows include both `sim_step` (the concise protocol field) and the
cross-ledger `simulator_step` alias. They use `restore_error=null` with `pending` or `unavailable` when
there is no real restore measurement. A missing measurement is never encoded as
zero.
