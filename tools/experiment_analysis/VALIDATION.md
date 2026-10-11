# Validation record — 2026-10-11

## Phase F: consistent ingestion and transactional result publication

Continuation baseline: `fb171c30`. The final four-directory regression command
recorded below passed **482 tests, 1 skipped in 34.41 s**. The skip is still the
unavailable historical scene plugin. AST parsing passed for all 23 analysis
Python files, and `git diff --check` passed.

The 35 new transaction tests include:

- Rename the live collector away before legacy validation and still reconstruct
  the exact handoff from private bytes; verify the temporary copy is cleaned.
- Remove the live collector immediately after the one CLI acquisition and bind,
  serialize and recompute successfully from that same handoff.
- Reject linked roots/ancestors/members, FIFO, checksum aliases, unknown sealed
  files, unsealed mirrors/artifacts and resealed duplicate/non-object JSON before
  legacy validation. Detect source-seal mutation while copying.
- Enforce per-file, total-byte and file-count limits, including a budget shared
  across batch sources. Prevent handoff/bind/assemble output inside collectors.
- Inject failures at data write, rendering, manifest write, verification and
  publication; inject `KeyboardInterrupt` and `SystemExit`. Each leaves no public
  target or staging residue and permits a successful retry at the same path.
- Preserve competing empty/populated directories, files and symlinks at the
  final rename. Reject inconsistent readiness and failed analytic controls
  before a result directory becomes visible.
- Run a real child process that calls `os._exit(23)` before publication. It
  leaves one hidden staging directory and no public target; retry succeeds
  without deleting the abandoned staging. This is process-exit evidence, not
  a power-loss/fsync test.

Fresh end-to-end CLI commands used
`PYTHONDONTWRITEBYTECODE=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python`
in place of `python` below:

```bash
python -m tools.experiment_analysis synthetic-batch \
  --output tmp/experiment_analysis/transaction-batch-verified
python -m tools.experiment_analysis synthetic \
  --output tmp/experiment_analysis/transaction-math-verified --resamples 1000
python -m tools.experiment_analysis verify \
  --bundle tmp/experiment_analysis/transaction-math-verified --recompute
python -m tools.experiment_analysis archive \
  --bundle tmp/experiment_analysis/transaction-batch-verified/result \
  --run-dir tmp/experiment_analysis/transaction-batch-verified/synthetic-batch-a \
  --run-dir tmp/experiment_analysis/transaction-batch-verified/synthetic-batch-b \
  --output tmp/experiment_analysis/transaction-archive-verified --recompute
python -m tools.experiment_analysis verify-archive \
  --archive tmp/experiment_analysis/transaction-archive-verified --recompute
```

The math control produced 3,984 records and 19 matching analytic results, zero
N/A, with 1,000 bootstrap draws and complete-report recomputation. The two-batch
control preserved 14 attempts → 12 observations and 5/12. The portable archive
contains 53 files / 391,412 bytes and revalidates 2 runs / 42 raw files, selection
and full calculation. Generated outputs remain ignored under `tmp/`.

| File under `tmp/experiment_analysis/` | SHA-256 |
|---|---|
| `transaction-batch-verified/result/output_manifest.json` | `a766fa2bdc69b3fde18c02b380eb11e182ff8c788764552d97c35f3236cb44c8` |
| `transaction-math-verified/output_manifest.json` | `fd82d93722999f0f64d3342cd58c631784b994074c242071ce83a183509dd08c` |
| `transaction-archive-verified/evidence_manifest.json` | `7ed5d330dae451980075d27aa8ea63da19097d3bdcf1ff4cf58d13b5d1f0d1bb` |

Independent final review found no unresolved P0/P1/P2. Focused regression passed
111 tests in 19.30 s; the independent analysis/collector/task suite passed 424
tests plus the one historical skip in 34.57 s. These overlapping counts are not
additive. A pre-F result bundle
passed ordinary `verify` under the current code and correctly rejected
`--recompute` with exit 2 for a changed source environment. Existing archive and
recomputation pins remain strict; no silent migration or weakened validation.

Scope limits: result directories have atomic visibility on the supported Linux
host, not guaranteed power-loss durability. The caller controls output parents;
unrecoverable exits can leave hidden staging. The outer `synthetic-batch` work
area preserves runtime evidence and is not a disposable transaction. Raw-copy
budgets do not cap whole-process memory or separate design/evaluator inputs.
No Isaac/GPU run occurred; task, evaluator, numerical and qualification semantics
were not changed. Every synthetic report remains non-publishable.

## Phase E: portable sealed collector archives

Continuation baseline: `7008cd0a`. Final expanded regression used the same
four-directory pytest command recorded below: **447 passed, 1 skipped in
32.60 s**. The skip remains the unavailable historical scene plugin. AST parsing
passed for all 21 analysis Python files; `git diff --check` passed.

New archive and streaming-snapshot tests cover canonical paths, symlink parents
and members, FIFOs, missing/foreign/duplicate sources, extra files/directories,
manifest and raw-seal tampering, consistently rehashed but false handoffs,
file/total/count budgets including the manifest, source growth/change during
copy, and a destination publication race. Scripted measured-format controls
also preserve and revalidate three evaluator JSON artifacts; they are not
physical measurements.

Executed CLI acceptance from the isolated worktree, with
`PYTHONDONTWRITEBYTECODE=1` and the `env_isaaclab` Python executable:

```bash
python -m tools.experiment_analysis synthetic-batch \
  --output tmp/experiment_analysis/portable-control-verified
python -m tools.experiment_analysis archive \
  --bundle tmp/experiment_analysis/portable-control-verified/result \
  --run-dir tmp/experiment_analysis/portable-control-verified/synthetic-batch-a \
  --run-dir tmp/experiment_analysis/portable-control-verified/synthetic-batch-b \
  --output tmp/experiment_analysis/portable-evidence-verified --recompute
mv -T --no-clobber tmp/experiment_analysis/portable-control-verified \
  tmp/experiment_analysis/portable-control-retained
mv -T --no-clobber tmp/experiment_analysis/portable-evidence-verified \
  tmp/experiment_analysis/portable-evidence-relocated
python -m tools.experiment_analysis verify-archive \
  --archive tmp/experiment_analysis/portable-evidence-relocated --recompute
diff -rq tmp/experiment_analysis/portable-control-retained/result \
  tmp/experiment_analysis/portable-evidence-relocated/analysis
```

Both old paths were checked absent before accepting relocation. The two-run
archive contains 53 files / 391,199 bytes, including 42 raw collector files.
Original analysis bytes are unchanged (`diff -rq` exit 0). Raw seal, structural
validation, selection replay and complete-report recomputation all passed
without the original locations. Synthetic selection remains 14 attempts → 12
observations and the analytic 5/12 estimate. Sources were retained at their new
path, and all generated outputs remain ignored under `tmp/`.

| File under `tmp/experiment_analysis/` | SHA-256 |
|---|---|
| `portable-evidence-relocated/evidence_manifest.json` | `e9b210be2c2a67304a50793772c3830f548f4ce28bf6abf4602930f327db1b37` |
| `portable-evidence-relocated/analysis/output_manifest.json` | `7898a031a930eeaad15929582d43ba34c81f04ca2a77816371b605e00556bd04` |
| `portable-control-retained/registration.json` | `08de6d21813f3da585f83d111c419144dfc17adef843f4b2f656746c0539216d` |

Independent final review found no unresolved P0/P1/P2 in this scope. It ran
40 focused tests (3.13 s), 97 cross-module tests (17.79 s), and 389 tests plus
the one known skip across analysis/collector/task (31.12 s); these overlapping
suites are not additive. Its separate relocated CLI probe also passed exact
byte comparison and recomputation with original paths absent. Review closed
earlier findings concerning safe seal enumeration, bounded metadata rereads,
manifest budget accounting and atomic no-replace publication.

No Isaac/GPU run was required or performed. This archive proves content linkage
under pinned Python/validator sources, not authenticity, preregistration time,
evaluator semantics, physical truth or release eligibility. Those verification
flags and `publishable` remain false. Assets, videos, models, unsealed state and
external paths embedded in JSON are not implicitly archived. Ordinary `verify`
still cannot revalidate absent raw ledgers; only `verify-archive` with the
included originals provides the new raw-content check.

## Phase D: frozen batches and detached result bundles

Continuation baseline: `125bb9d5`. The earlier A–C evidence below is preserved
as historical validation, not substituted for the new test run.

The full regression command shown below was rerun after the batch, bundle and
collector fixes: **407 passed, 1 skipped in 29.66 s**. The skip remains the
uninstalled historical demo plugin. AST parsing passed for 21 analysis and
changed collector Python files; `git diff --check` passed.

Executed end-to-end controls (all outputs ignored under `tmp/`):

```bash
PYTHONDONTWRITEBYTECODE=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis synthetic-batch \
  --output tmp/experiment_analysis/verified-batch-controls
PYTHONDONTWRITEBYTECODE=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis verify \
  --bundle tmp/experiment_analysis/verified-batch-controls/result --recompute
PYTHONDONTWRITEBYTECODE=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis synthetic \
  --output tmp/experiment_analysis/verified-bundle-controls --resamples 1000
PYTHONDONTWRITEBYTECODE=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis verify \
  --bundle tmp/experiment_analysis/verified-bundle-controls --recompute
```

- Two actual `BranchExecutor`/`DummyAdapter` collector runs with a scripted CPU
  session backend: 14 attempts, 12 selected observations, two cells and three
  repeats. Design, registration and plans are written before either run starts.
- Allowed infrastructure retry selects the later attempt; a valid method timeout
  remains zero despite a later success. Estimate `0.41666666666666663` matches
  5/12 within floating-point tolerance. All nine batch bundle members pass
  integrity, selection replay and complete-report recomputation.
- Mathematical bundle: 3,984 records, 19 analytic results, zero N/A; 1,000
  bootstrap draws. All seven bundle members and complete-report recomputation
  pass. This rechecks mathematics after adding new analysis source modules.
- Neither control runs Isaac or measures physical success. Every result remains
  synthetic and `publishable=false`; verifier output explicitly denies trusted
  registration-time certification and offline raw-ledger seal reverification.

Local output fingerprints (file-byte SHA-256, not scientific certificates):

| File under `tmp/experiment_analysis/` | SHA-256 |
|---|---|
| `verified-batch-controls/registration.json` | `08de6d21813f3da585f83d111c419144dfc17adef843f4b2f656746c0539216d` |
| `verified-batch-controls/result/output_manifest.json` | `94762cde0d3d78302c697d056e1c002397aca1b4e5d866ea5930f7472afb808a` |
| `verified-bundle-controls/output_manifest.json` | `aa78c07fb3a0a2bf804cbfcee0a9365475bf392d23c80a1794eb2cfae31cc6be` |

Adversarial regressions cover missing predecessors, evaluator relabeling,
disallowed retry reasons, version/asset/RNG/slot/metadata drift, duplicate runs
or seals, truncated schedules, unregistered attempts, resealed altered audits,
resealed qualification escalation, changed derived views, wrong numerical
results, code/Python mismatch, symlinks, extra files and strict JSON boundaries.

Independent review led to additional fixes: preserve infrastructure reason and
receipt diagnostics even when a backend has nested `outcome`; freeze pair-group,
slot and metadata alongside versions; validate every result's qualification;
link plan/manifest/branch run identities. The new bound-bundle test exposed
explicit jobs expanded before a stable run ID was assigned. Store creation now
rejects that inconsistency before writing instead of silently rewriting jobs;
normal generated-run plans freeze their selected execution scope.

Intermediate failures (including an undersized mathematical control test and
the explicit-job mismatch) were fixed before the final passing regression.
Independent final review found no unresolved P0/P1/P2 within this scope and
separately ran 93 focused tests (18.26 s), the full 407-pass/1-skip suite
(28.47 s), and a fresh two-batch synthetic run with exact bundle recomputation.

## Earlier A–C milestone

Scope: descriptive mathematical infrastructure, collector identity/handoff and
historical plan compatibility. No robot/controller changes; no Isaac/GPU run
was required or performed in this phase. Formal evaluator semantics and release
certification remain external dependencies.

The latest reviewed online manuscript was Overleaf v773, checked before and
after retrieval. Key source SHA-256 values:

| Source | SHA-256 |
|---|---|
| `sec/5_evaluation_protocol.tex` | `0662e524efc074c519281a5b9d605ad952e394aa73abea68c3385d5ffbfc181c` |
| `sec/X_suppl.tex` | `a7787b88829ae14d91fa6120bd8478e046f892c9e423f40e9fb5a13c1b658c0d` |
| `sec/supp_coverage.tex` | `a55b8df4b82fd430ea6a67462340ef36ea5c5d3360fab5112fbbc0429f3449b8` |

These identify the local read-only text snapshots, not a published paper result.
The CLI ZIP download did not finish and was terminated; the review used complete
live per-file reads instead. No online paper content was edited.

## Executed checks

Commands ran from the isolated worktree with Python in `env_isaaclab`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest \
  --rootdir="$PWD" -q -p no:cacheprovider \
  tools/experiment_analysis/tests tools/benchmark_branch/tests \
  task/tests algorithm/skills/tests
```

Result: **341 passed, 1 skipped**, 25.52 s. The skipped module requires the
uninstalled historical `demo.benchmark.branch_plugins` package. AST parsing of
13 changed/new Python source and test files passed; `git diff --check` passed.
The earlier independent reviewer also ran 75 focused tests successfully.

```bash
PYTHONDONTWRITEBYTECODE=1 \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis synthetic \
  --output tmp/experiment_analysis/verified-controls --resamples 1000

PYTHONDONTWRITEBYTECODE=1 \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.experiment_analysis compute \
  --design tmp/experiment_analysis/verified-controls/design.json \
  --data tmp/experiment_analysis/verified-controls/data.json \
  --output tmp/experiment_analysis/recomputed-controls
```

Synthetic fixture: 3,984 raw records, 212 registered units, 344 member instance
IDs, two systems, three repeats; ordinary estimands use 80 base clusters and
transfer uses **three family clusters**, with 40/44/48 pairs respectively.
All 19 point results matched the independently specified analytic answers.
The suite includes byte-identical report recalculation and output-file checksum
verification. Numerical intervals remain descriptive bootstrap calculations;
these controls do not estimate Type-I error, statistical power or real robot
performance. Generated source data, paper snapshots and report files stay in
ignored `tmp/`.

## Failures found and closed

- Integration initially revealed that generated collector base IDs included
  repeat/system/condition. V2 now preserves base identity across those axes;
  a resealed pre-version plan regression verifies unchanged v1 IDs on resume.
- Independent review reproduced a transfer panel accepting an empty required
  cell. Both cell and family gates now apply, with a regression.
- Malformed nested collector metadata now raises a structured error.
- Planning counts no longer describe paired source/target members as
  independent samples. Per-estimand cluster counts are explicit.
- Decreasing stress-grid crossings now use the paper's strict below-threshold
  rule, including equality and left/right grid-censor cases.

The initial integration-test failures and intermediate outputs were not counted
as passing evidence; the final expanded regression above includes the fixes.
Independent review found no remaining P0/P1 within this descriptive scope.

## Qualification boundaries

No p-values, rankings, paper release, physical task verdict, or six-primary
confirmatory result was generated. Every report is `publishable=false`.
The external benchmark's own identity/reducer/release problems were documented
for its owner; this phase did not modify that dirty concurrent project. See
[architecture audit](ARCHITECTURE_AUDIT.zh-CN.md) for required next inputs.
