# Restaurant candidate asset preparation

The frozen v7 candidate is a hash-bound local candidate. The preparation command
copies its four files byte-for-byte and invokes the existing resolver. It also
checks the relative `Restaurant_Only.usda` reference inside the overlay. A
missing source closure is an asset deployment failure and stops before Isaac;
the command does not rewrite the overlay, relax a hash, or set a release flag.

From a clean checkout with the candidate bundle supplied separately:

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  tools/assets/prepare_restaurant_benchmark_assets.py \
  --candidate-root /path/to/products-candidate-v7 \
  --copy-to tmp/restaurant_benchmark/products-candidate-v7
```

For an existing directory, run the same command without `--copy-to`. A successful
report requires both `hash_resolver=passed` and `source_closure_exists=true`.

On a clean checkout whose absolute path differs from the path embedded in the
frozen overlay, materialize the explicitly reviewed source at that reference
before inspection. The command checks existing bytes and refuses a mismatch;
it does not rewrite the overlay or product files:

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  tools/assets/prepare_restaurant_benchmark_assets.py \
  --candidate-root tmp/restaurant_benchmark/products-candidate-v7 \
  --source-usd "$PWD/usd/scene/restaurant/Restaurant_Only.usda"
```

Run this command in both the local and ljy isolated worktrees, using each host's
own checkout source. Preserve the JSON report and the source SHA256 with the
episode preflight record. If destination bytes differ, stop and review the
candidate/source version instead of replacing the file.
Only then may the normal pre-episode command be attempted:

```bash
EAI_USD_ROOT=/path/to/usd \
EAI_CONTROLLER_ROOT=$PWD/source/EAI_assets/EAI_assets/controller \
EAI_RESTAURANT_BENCHMARK_ROOT=$PWD/tmp/restaurant_benchmark/products-candidate-v7 \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.runtime_probe \
  --products-root tmp/restaurant_benchmark/products-candidate-v7 \
  --source-usd /path/to/usd/scene/restaurant/Restaurant_Only.usda \
  --output-root tmp/restaurant_benchmark/native-runtime-preflight --device cpu
```

Run the preparation command independently in the local and ljy isolated
worktrees. The bundle is not source-controlled; transfer it through the approved
asset channel or regenerate it only when a new candidate version is explicitly
reviewed and its hashes are changed together. If the report says
`source_closure_missing`, preserve that evidence and provision the expected
closure layout before retrying. Do not treat a hash-only pass as an episode
startup pass.
