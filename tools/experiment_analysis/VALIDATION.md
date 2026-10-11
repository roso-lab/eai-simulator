# Validation record — 2026-10-11

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
