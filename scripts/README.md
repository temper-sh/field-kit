# Author the Qwen study

Contributors run the frozen package; they do not run these scripts. Authoring
requires the adjacent Temper source and the retained Labs comparison. Runtime
code remains compatible with Python 3.9; authoring uses Python 3.11 or newer.

`catalog/packages/qwen-machine-study@4/` owns the tasks, fixtures and experiment
protocol. Temper's `catalog/experiments/qwen-study.json` owns exact software and
model compositions in `temper-catalog/v3` presets. Execution locks are opaque,
compiled snapshots for the dispatched study. No dependency resolver runs on a
participant's machine.

## Reproduce the package from reviewed inputs

From this repository, substitute the actual source paths:

```sh
python3 scripts/prepare_qwen_coding.py \
  --result ../labs/workstreams/model-runtime-optimization/results/qwen27-splash-frog-m5.json \
  --flask-source /absolute/path/to/coding-quality-fixture/base \
  --out catalog/packages/qwen-machine-study@4
python3 scripts/prepare_qwen_study.py \
  --temper /absolute/path/to/temper/build/temper \
  --catalog /absolute/path/to/temper/catalog/experiments/qwen-study.json
./field-kit verify
python3 -m unittest discover -v
```

The source packet's sibling `manifest.json` must match the retained comparison.
The first command validates that source and extracts both original requests and
independent oracles. It removes transport hashes from the runner rather than
creating a second receipt chain. The second compiles all eight compositions,
freezes package checksums, costs and action bounds, and updates the question
index. It uses `catalog compile --preset`, with no legacy Selection file or
revision 3 seed. The experiment derives bounded context/output/memory settings
through `execution configure`; it never reads or rewrites the lock's internals.
Neither command downloads weights or runs inference.

The package requires Temper `0.1.0-alpha.11`; setup pins the verified signed
release. See the [development guide](../docs/DEVELOPMENT.md) for the host
interface and reviewing older runs.

Changes to a dispatched package require a new revision. Regeneration is for
preparing a new revision, or checking that the exact inputs reproduce. Use the
original producer checkout to review older reports, as described in the
development guide.

## Validation boundary

Synthetic runs exercise both routes, OOM continuation, context stopping,
stream fragments, native token accounting, edit scope, uncertain shutdown,
consent/export/review and completed-run replay. They do not qualify model fit.
The frozen original and independent Flask tests are reused without adding an
oracle after observing a candidate. Generated tests remain a separate group;
passing all groups still needs source review.
