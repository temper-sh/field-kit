# Author the Qwen study

Contributors run the frozen package; they do not run these scripts. Runtime
code remains compatible with Python 3.9. The current scheduling revision is
derived offline from the dispatched revision 4 inputs.

`catalog/packages/qwen-machine-study@5/` owns the current tasks, fixtures and
configuration choices. Temper's `catalog/experiments/qwen-study.json` owns exact software and
model compositions in `temper-catalog/v3` presets. Execution locks are opaque,
compiled snapshots for the dispatched study. No dependency resolver runs on a
participant's machine.

## Reproduce the package from reviewed inputs

From this repository:

```sh
python3 -B scripts/prepare_qwen_chunks.py
./field-kit verify
python3 -m unittest discover -v
```

The script validates revision 4, copies its exact execution locks and workload
bytes, and writes the revision 5 configuration choices, cost allowances and task
actions. It does not recompile against a moving catalog, download weights or
run inference. Revision 4 remains unchanged. Cost authoring reads the frozen
lock's file sizes; participant code treats execution locks as opaque.

The experiment derives bounded context/output/memory settings through
`execution configure`. Configuration selection is a pure derivation from the
frozen bundle, used both before consent and during returned-result review.
It narrows the lock, task actions and costs without changing tasks or oracles.

The earlier `prepare_qwen_coding.py` and `prepare_qwen_study.py` scripts retain
the original Labs extraction and Temper compilation method. Use their producing
checkout for historical reproduction. New model, engine, workload or protocol
inputs require a separately reviewed new package revision.

The package requires Temper `0.1.0-alpha.11`; setup pins the verified signed
release. See the [development guide](../docs/DEVELOPMENT.md) for the host
interface and reviewing older runs.

Changes to a dispatched package require a new revision. Regeneration is for
preparing a new revision, or checking that the exact inputs reproduce. Use the
original producer checkout to review older reports, as described in the
development guide.

## Validation boundary

Synthetic runs exercise one-configuration consent, per-task checkpoints,
resumption without replay, private-cache cleanup, retained shared files,
configuration selection, context stopping and witness review. Historical
matrix tests continue to cover the earlier protocol. These checks do not
qualify model fit.
The frozen original and independent Flask tests are reused without adding an
oracle after observing a candidate. Generated tests remain a separate group;
passing all groups still needs source review.
