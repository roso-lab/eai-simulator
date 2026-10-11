# Experiment analysis infrastructure

This dependency-free package connects frozen experimental designs, sealed
collector attempts and externally evaluated scalar/event outcomes to auditable
descriptive calculations. It does not register tasks, decide milestone success,
certify replay/transfer, generate primary-hypothesis p-values, or approve paper
release. The external benchmark remains the authority for those decisions.

See the [Chinese audit and roadmap](ARCHITECTURE_AUDIT.zh-CN.md) and
[development checklist](DEVELOPMENT_TODO.zh-CN.md). Run from the repository root.
No Isaac, GPU, ROS, network access or optional scientific libraries are needed.

## Run a complete mathematical control

```bash
python -m tools.experiment_analysis synthetic \
  --output tmp/experiment_analysis/control --instances 40 --resamples 1000
python -m tools.experiment_analysis readiness \
  --design tmp/experiment_analysis/control/design.json
python -m tools.experiment_analysis compute \
  --design tmp/experiment_analysis/control/design.json \
  --data tmp/experiment_analysis/control/data.json \
  --output tmp/experiment_analysis/recomputed
```

Output directories must be new. Synthetic raw observations, source design,
analytic expected-answer checks, readiness counts, JSON/CSV/Markdown reports and
a SHA-256 output manifest are written there. Generated data never belong in Git.
Reports also record the Python version and SHA-256 of every analysis source
module, so a changed implementation is distinguishable from an exact replay.
Synthetic records always produce `qualification=synthetic_validation` and
`publishable=false`. `compute` returns zero for a successful calculation even
when a registered estimand is N/A; inspect its status/reasons. `synthetic` returns
1 if any analytic point-answer check fails, without publishing a result directory.
Invalid inputs return 2. Result publication requires the supported Linux host's
`renameat2(RENAME_NOREPLACE)` operation; the mathematics uses standard Python.

The controls use two toy systems, two cells, three repeats, four factor corners,
five event causes including censoring, and three transfer families with unequal
pair counts. These are mathematical fixtures, not new tasks or a robot benchmark.
Their repeated outcomes are deliberately correlated. The seed controls bootstrap
sampling; analytic raw values are deterministic and do not mimic empirical noise.

## Data contract

`schema.py` is the authoritative strict parser. Unknown fields, duplicate JSON
keys/IDs, non-finite numbers, booleans in numeric fields, repeated observation
identities, unregistered repeats and mixed member/base IDs are rejected.
`synthetic` generates a complete working example of both JSON schemas.

| Object | Meaning |
|---|---|
| Design | `eai.analysis.v1.design`; external authority and protocol hash; systems, cells, units, measures, estimands and bootstrap settings |
| Cell | Frozen domain, primary label and difficulty partition; infrastructure count/fraction floors. Condition is supplied by each exact term key |
| Unit | One base instance (`members.main`) or one transfer pair (`members.src/tgt`); cell, registered repeats, support memberships, infrastructure status/reason and optional family |
| Measure | Scalar in a declared natural unit/optional bounds, or event with nonnegative elapsed time and registered competing causes |
| Estimand | Exact member/system/condition/branch/checkpoint/measure terms; required cells, repeat/common-support floors, estimator parameters |
| Dataset | `eai.analysis.v1.dataset`; canonical design digest, `synthetic` or `measured_unverified` provenance and externally evaluated records |
| Record | Unit/base/member/system/condition/branch/checkpoint/repeat identity, status, method outcome, values, artifact and pairing-context digests |

All systems/conditions/repeats for an ordinary instance must refer to the same
registered base ID. A base cannot be split across units. The parser cannot detect
a producer that invents different IDs for physically identical instances; the
external generator audit must establish that identity. Attempts are not repeats:
use a frozen collection schedule for deterministic selection before analysis. The
engine rejects multiple records for a scientific observation key.

`valid` method failures, timeouts, malformed actions, budget overruns and no-plan
outcomes remain observations. The external evaluator supplies their values.
Unavailable records require an explicit exclusion reason and have no numeric
values. Every absent planned term/repeat is materialized in the unit audit.
Infrastructure validity is frozen independently of method outcomes; deleting
records cannot shrink its planned denominator. Endpoint support membership,
oracle eligibility and relation signatures are supplied by the benchmark, never
inferred from endpoint values.

Within-system paired terms must share a `pair_context_digest`. Its external
definition must bind checkpoint, untreated history, random-event keys, versions,
permissions and non-target budgets. This package tests equality, not scientific
truth. Different systems may have different checkpoints; explicit system
contrasts align units/repeats but do not claim identical within-system state.
All terms must either specify their system or omit it; omitted systems generate
separate reports for every registered system.

## Estimator behavior

| Kind | Calculation and support |
|---|---|
| `mean` | Repeat mean within instance, equal-instance cell mean, equal-cell panel |
| `ratio_of_means` | Ratio of the two fixed-panel means on common repeats/instances; denominator must be positive and pass its registered floor |
| `mean_of_cell_ratios` | Repeat and instance means first, ratio within each cell, equal-cell panel; appropriate only when the external estimand specifies this ordering |
| `linear` | Registered coefficients applied to common-support term means; e.g. `[1,-1,-1,1]` for a four-corner interaction |
| `cif` | Cell-level Aalen–Johansen from all included trajectories; competing causes remain distinct; base-instance cluster bootstrap |
| `rmst` | Area under the all-cause survival curve, then fixed-cell panel; not cause-specific recovery latency |
| `family_gap` | Aligned repeats → matched-pair means → equal-pair family means → frozen affine orientation/anchor/scale → target minus source → equal-family mean |

All required cells pass infrastructure and endpoint/common-support gates before
a point estimate is emitted. A failed cell does not disappear or change panel
weights. Transfer additionally requires every registered family's matched-pair
count/fraction floor; families never get reweighted by episode count. The current
schema supports affine orientation (`+1`/`-1`), equal cell/family weights and one
registered checkpoint round per term. Nonlinear orientation, arbitrary weights,
cross-round pooling and nonlinear factor effects on ratios require an explicitly
extended schema; do not emulate them by averaging per-repeat ratios.

Bootstrap uses a deterministic RNG per estimand/system, sorted unit IDs, and
resamples base instances within cells while carrying aligned repeats/branches
together. Transfer resamples whole families including all their nested pairs.
Reordering data or inserting another estimand does not change a result's RNG.
With fewer than two independent clusters in a cell (or fewer than two transfer
families), the interval is N/A. An undefined bootstrap draw makes the interval
N/A, retaining the point estimate and undefined-draw count; it is not silently
discarded. Percentile intervals are descriptive approximations, not a proof of
coverage, power or release validity. The report does not enforce a precision
target or authorize outcome-dependent top-up.

Competing events tied with censoring use the pre-time risk set; events are
applied before censor removal. Observations after the horizon are administratively
censored there. A horizon after follow-up has ended with positive survival is
N/A. Once all-cause survival reaches zero, extension contributes zero area.
An all-censored sample observed through the horizon has CIF zero and RMST equal
to that horizon. These are valid estimates, not observed recoveries.

`primitives.py` also exposes fixed-family Holm adjustment of **caller-supplied**
p-values, lower-bound noninferiority gates, joint-bootstrap sup-deviation grid
bands and descriptive adjacent-grid crossing brackets. It does not derive
p-values from percentile intervals or reuse separately sampled pointwise
intervals as simultaneous bands. These primitives are not automatically a
registered confirmatory analysis or a cost-superiority claim.

## Collector and evaluator handoff

```bash
python -m tools.experiment_analysis collector-handoff \
  --run-dir tmp/your_sealed_run --output tmp/experiment_analysis/handoff
python -m tools.experiment_analysis bind-outcomes \
  --design tmp/registered_design.json --run-dir tmp/your_sealed_run \
  --outcomes tmp/evaluated_outcomes.json --output tmp/experiment_analysis/bound
```

The handoff first copies the exact sealed members through the same bounded,
no-follow reader used by portable archives. It verifies the seal and collector
structure only on that private snapshot, **without writing into the run**, exports
all attempts, and rejects duplicate terminal episodes. Every sealed JSON/JSONL
is strictly parsed, including branch mirrors and evaluator artifacts. Source
roots/ancestors cannot be symlinks; aliases, special files, unknown sealed
members and unsealed branch/evaluator evidence are rejected. Changes after the
snapshot cannot change the admitted data; this is not a live-directory lock.
No latest/successful-attempt selection is performed. Raw ledgers remain
at the referenced sealed collector path; the handoff is not a self-contained
copy of every high-volume ledger. Preserve that directory alongside results.

`collector-handoff`, `bind-outcomes` and `assemble` accept `--max-files`,
`--max-file-bytes`, and `--max-total-bytes` (defaults: 100,000 files, 256 MiB
per file, 2 GiB total). These limit copied raw collector bytes, including seal
files; checksum metadata additionally has an 8 MiB cap. `assemble` shares one
budget across all source runs. These are not whole-process memory limits or
limits on design/evaluator-sidecar JSON. Temporary disk must accommodate the
private copy of one source at a time. Existing source contents and reported
`collector_path` are preserved; temporary paths never become source identities.

The evaluator returns `eai.analysis.v1.evaluated_outcomes` with `design_digest`,
`collector_seal_sha256`, `evaluator: {id, version, source_digest}`,
`selection_policy_digest`, and `records`. Each selection contains `branch_id`,
the handoff's `identity_digest`, and an `outcome` record without `artifact_digest`.
Binding requires exact base/system/condition/branch/repeat equality and an
authoritative `checkpoint_round` in branch metadata (`benchmark_run_key` or
`metadata.checkpoint_round`). It hashes seal, identity, episode, evaluator,
selection-policy and raw outcome into the assigned evidence digest. A synthetic
collector cannot be upgraded to measured by evaluator output. A measured
collector stays `measured_unverified`; a hash does not verify the evaluator or
prove that a selection policy was registered before outcomes.

The CLI acquires exactly one handoff for binding and saves that same handoff
next to the evaluator payload and bound dataset. It never rereads the collector
to build a different export snapshot. All three ingestion commands reject
output paths inside their source collector directories.
Missing external evaluators or historical identity mismatches must be repaired
at their owner; this adapter never guesses a checkpoint round, task outcome,
base-ID migration, strict pairing or release qualification.

## Frozen multi-batch collection and retry selection

Run the complete transport control (two real CPU collector sessions, scripted
synthetic outcomes, no Isaac or physical evaluation):

```bash
python -m tools.experiment_analysis synthetic-batch \
  --output tmp/experiment_analysis/batch-control
python -m tools.experiment_analysis verify \
  --bundle tmp/experiment_analysis/batch-control/result --recompute
```

The control writes its design, registration and both exact plans **before**
starting either collector. It splits cells across runs and also registers two
cross-run retry chains. Fourteen attempts yield twelve scientific observations:
one allowed infrastructure retry supplies a value; one method timeout remains
zero despite a later success. The equal-cell result is 5/12. All generated data,
including sealed collector directories, remain below the requested output path.
`synthetic_batch.py` is an executable transport example, not a production task
contract or a physical success evaluator.

For externally owned tasks, prepare the design and schedule, freeze them before
execution, run the exact declared plans, and have the external evaluator write
one outcomes file per sealed run:

```bash
python -m tools.experiment_analysis freeze \
  --design tmp/design.json --schedule tmp/schedule.json \
  --output tmp/frozen-collection
python -m tools.experiment_analysis assemble \
  --design tmp/design.json \
  --registration tmp/frozen-collection/registration.json \
  --sources tmp/batch-sources.json --output tmp/experiment_analysis/assembled
python -m tools.experiment_analysis verify \
  --bundle tmp/experiment_analysis/assembled --recompute
```

`registration.py` validates `eai.analysis.v1.collection_schedule`:

| Field | Contract |
|---|---|
| `design_digest`, `evaluator` | Exact frozen design and external evaluator `{id, version, source_digest}` |
| `execution_scope` | Exactly one of `protocol_synthetic`, `measured`, `measured_diagnostic`; scopes never mix |
| `batches` | Nonempty `{run_id, plan_digest}` list; set a stable run ID before hashing `BranchPlan.row()` |
| `retry_policy` | `{mode, infrastructure_reasons}`; `first_attempt` has no reasons and exactly one attempt per observation; `infrastructure_retry` has a nonempty reason allowlist |
| `observations[].identity` | Exact unit/base/member/system/condition/branch/checkpoint/repeat key; the observation set must equal the design's enumerated dependencies |
| `observations[].execution_identity` | Frozen task/system/source versions, checkpoint/restore/contract/scene/asset/build-order digests, skill version, RNG namespaces, intervention, pair seed, slot, pair group and canonical metadata digest; obtain the exact shape via `execution_identity(job.identity())` |
| `observations[].attempts` | Ordered `{run_id, branch_id, attempt}` array; within a run, one logical branch starting at ordinal zero with no gaps; suffix must be `#attempt-N` |

Resolve the declared checkpoint and execution context before freezing; do not
fill unknown fields from successful attempts afterward. Retry context includes
all branch metadata, so changing metadata or versions requires a separately
registered experiment, not a retry hidden in the old observation. Cross-run
attempt order is explicitly declared; the tool does not authenticate wall-clock
chronology. The envelope's `registration_digest` covers the exact schedule.
**Content freezing is not trusted preregistration time or source authenticity.**

The sources manifest has schema `eai.analysis.v1.batch_sources` and a `sources`
array of `{run_dir, outcomes}`; paths resolve relative to that manifest's parent.
Each evaluator payload uses `selection_policy_digest=registration_digest`.
Missing sources are permitted and explicitly recorded; duplicate/unregistered
runs, duplicate seals, changed plans, contexts, evaluator versions or scopes
are rejected. Additional observed attempts for any scheduled observation cannot
be hidden from its registered chain. Unrelated collector branches are disclosed
in `unregistered_collector_attempts` and cannot contribute evaluated records.

Only an evaluator `infrastructure_invalid` row whose reason matches the sealed
episode's explicit infrastructure reason can advance a retry chain, and only
when that reason was allowlisted. The collector seals `preflight_rejected` and
`missing_runtime_receipt` for its own failures; adapters may supply explicit
codes. Existing episodes without a machine-readable reason do not acquire one
by inference. A method failure, non-evaluable result or disallowed reason ends
selection; missing preceding source/attempt/episode/evaluation blocks all later
attempts. The audit retains every declared attempt, including unused successes.
Exhausted retries and missing observations remain missing in the dataset, with
explicit audit reasons; no artificial values or evidence digests are inserted.

`assemble` returns zero when assembly/calculation succeeds, including incomplete
collections. Inspect `batch_audit.json.status`, missing measures/sources and each
report result. A passed integrity check does not mean collection is complete.
The earlier `bind-outcomes` interface remains compatible for single-run inputs;
its arbitrary policy digest is caller-managed and does **not** execute the new
registered retry rules. Use `assemble` for that guarantee.

## Offline result-bundle verification

Bundles include the design/data, report, CSV/Markdown views and a manifest with
an exact `bundle_type` and required file set. Batch bundles additionally preserve
registration, handoff/outcomes snapshots, readiness and the complete selection
audit. `compute`, `synthetic`, `bind-outcomes` and `assemble` build the complete
result in private staging beside the destination, write the manifest last, run
`verify_bundle` there, and only then publish the directory in one atomic
no-replace rename. A failed write, render or verification exposes no partial
destination; an existing or racing destination is preserved. Python exceptions,
`KeyboardInterrupt` and `SystemExit` clean up staging, allowing a retry at the
same output path. This verifies content consistency before publication, not an
additional numerical recomputation. `freeze` and `collector-handoff` likewise
publish their already validated single-document exports atomically.

Output parents must remain under caller control and cannot have symlink
ancestors. A hard process kill can leave a hidden `.eai-analysis-build-*`
directory, but the public target is either absent or complete. A retry does not
delete another process's staging. This is atomic visibility, not a power-loss
durability guarantee. The `synthetic-batch` outer directory is a collector work
area and deliberately retains plans/logs on failure; only its `result/` bundle
uses transactional publication.

`verify` reads each member's bytes once, rejects symlinks/non-regular files,
missing/extra members, path escapes, duplicate JSON fields and checksum changes.
It checks identities and qualification flags, regenerates derived views, and
replays batch selection or the single-run evidence join from saved snapshots.
`--recompute` additionally requires identical Python version and analysis source
hashes, then recomputes and compares the **complete** report. Without this flag,
consistent but numerically wrong results can pass content checks; no calculation
verification is claimed. Archived Python is never executed.

Snapshots permit detached selection replay, but do not contain all raw ledgers.
Verification therefore returns `raw_collector_seal_reverified=false` and
`registration_time_verified=false`. Preserve original sealed run directories
for a fresh live `collector-handoff`/`assemble` audit. Hashes cannot certify an
honest evaluator, physical success or chronological preregistration. Every
bundle and result remains `publishable=false`. Older outputs without a
`bundle_type` need a new `compute` export; the verifier does not rewrite them.

## Portable raw-evidence archives

Use an evidence archive when the result and its sealed collector logs must move
together. This is a directory format; no tar extraction, downloaded code or
original absolute source path is used by verification. On the supported Ubuntu
host, publication uses Linux `renameat2(RENAME_NOREPLACE)` to atomically publish
the complete validated directory, including when another writer races to create
the destination. Existing outputs are never overwritten.

```bash
python -m tools.experiment_analysis synthetic-batch \
  --output tmp/experiment_analysis/portable-control
python -m tools.experiment_analysis archive \
  --bundle tmp/experiment_analysis/portable-control/result \
  --run-dir tmp/experiment_analysis/portable-control/synthetic-batch-a \
  --run-dir tmp/experiment_analysis/portable-control/synthetic-batch-b \
  --output tmp/experiment_analysis/portable-archive --recompute
python -m tools.experiment_analysis verify-archive \
  --archive tmp/experiment_analysis/portable-archive --recompute
```

Repeat `--run-dir` for every observed source in a `batch` or `bound` result
bundle. The explicit source list must match the saved handoffs one-to-one by
run ID and original checksum-file hash; the command never follows saved
`collector_path` strings to locate or copy data. Missing, foreign or duplicate
sources are rejected. A batch with missing planned observations can still be
archived: archive integrity does not mean the experiment is complete. Pure
`compute`/`synthetic` bundles have no bound raw collector sources and continue
to use ordinary `verify`.

The archive contains:

```text
evidence_manifest.json           exact files, byte lengths, hashes, source runs,
                                 bundle-manifest identity and validator versions
analysis/                       original result bundle, byte-for-byte
runs/<original-seal-sha256>/     original checksums.sha256 and its sealed members
```

The supported sealed members are `plan.json`, `manifest.json`, all collector
JSONL ledgers, `branches/<branch>/ledger.jsonl`, and content-addressed
`evidence/<sha256>.json` evaluator artifacts. Unsealed state, worker receipts,
validation reports and other convenience files are omitted. An unsealed branch
mirror or evaluator JSON is an error. Asset files, videos, simulator binaries,
external observation paths inside JSON, and unsealed checkpoint state are not
implicitly followed or packaged. Preserve those separately when the registered
experiment requires them; this archive is an evidence boundary, not a complete
physical rerun environment.

Archive creation and verification use bounded streaming copies through directory
descriptors. Source roots, parent components and files cannot be symlinks;
noncanonical/traversal paths, special files, checksum aliases and unknown sealed
member types are rejected. Default limits are 100,000 copied files, 256 MiB per
file, and 2 GiB aggregate, including the archive manifest. Override with
`--max-files`, `--max-file-bytes`, and `--max-total-bytes`; metadata additionally
has an 8 MiB cap. Verification needs temporary disk space for a private snapshot
of the archive and never modifies the input directory. Failed builds clean up
their private staging directory and leave no partly published destination.

`verify-archive` can run after moving the archive and making the original bundle
and collector directories unavailable. It requires the pinned Python version,
analysis code and collector validation modules; their hashes are stored in
`environment`. It checks the exact file/directory set and copied bytes, validates
strict JSON in every ledger and evidence artifact, reruns collector seal and
structural checks, and rebuilds each handoff from the archived logs. Rebuilt
handoffs must equal the saved originals in every field except `collector_path`.
The original analysis files are preserved, not rewritten to new paths. Optional
`--recompute` then also requires the report's own analysis environment and
compares the complete recalculated report, as with ordinary `verify`.

Successful archive verification explicitly reports
`raw_collector_seal_reverified=true` and `raw_ledger_structure_revalidated=true`.
It still reports `collector_authenticity_verified=false`,
`physical_success_verified=false`, `evaluator_semantics_verified=false`,
`registration_time_verified=false`, and `publishable=false`. A party able to
replace the entire archive can also rebuild all its hashes. These checks prove
content linkage and reproducibility of calculations, not honest collection,
trusted timestamps, evaluator correctness, physical success or scientific
release. Measured-format envelope tests use scripted fixtures, not Isaac data.

## Validation

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/experiment_analysis/tests tools/benchmark_branch/tests task/tests
```

Tests cover analytic counterexamples, ties/censoring, family versus pair
weighting, paired support, deterministic resampling, missingness, adversarial
identity/provenance, collector seal tampering, CLI exports and byte-identical
recalculation. They do not replace physical evaluator/Isaac validation or the
formal task-design thread's release audit.
