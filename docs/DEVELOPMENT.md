# Develop Field Kit

Use this guide when changing Field Kit. To take part in a measurement, use the
[run guide](START.md).

## Check a change

Field Kit uses the Python standard library and supports Python 3.9 or newer.
CI checks Python 3.9 and 3.14. From the repository root:

```sh
./field-kit verify
python3 -m unittest discover -v
```

`verify` checks that the runtime loads and that the task files match the
protocol's hashes. The tests use a fake Temper and synthetic responses. They do
not run a model or show that a preset fits a Mac.

## Structure

Field Kit owns the experiment: the fixed method, consent, limits, measurement,
first attempts, reports and cleanup. Temper owns compilation, installation,
serving and process identity. Field Kit reads Temper's `--json` documents and
passes execution locks to Temper unchanged.

| Path | Content |
|---|---|
| `protocols/basic-check.json` | The basic check's fixed method, limits and stop rules |
| `tasks/flask-coding/` | The Flask cases, the Flask source and the hidden tests used at review |
| `fieldkit_runtime/check.py` | The basic check: plan, consent, install, measure, clean up, report |
| `fieldkit_runtime/study.py` | Bounded approval, frozen candidate material, quality gates, reusable resources and close/recovery |
| `fieldkit_runtime/workloads.py` | Cheap smoke and deterministic context/cache questions |
| `fieldkit_runtime/review.py` | Sandboxed grading control, exact patch reconstruction and attributable source-review bindings |
| `fieldkit_runtime/temper.py` | The Temper commands Field Kit uses and the fields it reads |
| `fieldkit_runtime/probe.py`, `watcher.py` | One supervised `temper execution serve` and its process, memory, swap and heat observation |
| `fieldkit_runtime/serving.py`, `stream.py` | Bounded HTTP calls and the streamed answer, kept even when it stops early |
| `fieldkit_runtime/splash_memory.py` | Splash's native Metal memory counters |
| `fieldkit_runtime/coding/` | The Flask task's patch scope and its sandboxed test grader |
| `fieldkit_runtime/coding/quality.py` | Bounded quality-only proposal/test/repair loop; visible feedback, preserved attempts and final submission |

A run folder `runs/<UTC time>/` holds `run.json` (the state, written after each
step), the execution lock, one folder per case (request, raw stream, answer,
patch and process observations), `result.json` and `report.md`. While the run
is active it also holds the run's Temper folder, the unpacked Flask source and,
when needed, a private model cache.

Studies own `studies/ID/study.json`: one approval and budget reservation, frozen
candidate plans and references to immutable runs. Their private runtime/cache
survives individual trials and is removed on close. `--reason` records the
evidence gap driving each trial. Tests cover interrupted work, stale reviews,
scope violations, budget exhaustion and failed cleanup with a fresh controller.
They establish workflow behavior, not real engine performance or hardware fit.
Protocol revision 3 separates one-shot coding performance from quality work.
Quality tests cover feedback-driven repair, withheld oracles, exhausted budgets
and uncertain evaluator shutdown. The same executor serves local and remote
studies; performance calls never enter its repair loop.

The coding evaluator uses the pinned development requirements in
`runtime/grading-requirements.txt` with Python 3.12+. A baseline control must
pass before candidate tests can be interpreted. The runner remains stdlib-only.
Record the evaluator identity and retain failed controls as invalid evidence.

## Use a development Temper build

Field Kit uses the `temper` on `PATH` and needs 0.3.1 or newer
(`MINIMUM_VERSION` in `fieldkit_runtime/temper.py`; raise it when Field Kit
starts to use a newer Temper command). On first use, the `./field-kit`
launcher asks that Temper to install Field Kit's Python from
`runtime/python-macos-arm64.lock.yaml`; `FIELD_KIT_PYTHON` names another
Python 3.9 or newer instead. To try a Temper checkout:

```sh
temper_source=/absolute/path/to/temper-checkout
(cd "$temper_source" && go build -o build/temper ./cmd/temper)
./field-kit check --temper "$temper_source/build/temper" --preview
```

Pass the same `--temper` path when continuing a run: the exact Temper binary
and this checkout are part of the run, and a changed one is refused.

## Change the method

The protocol, the cases and the Flask source are part of every result. Change
them only in a new protocol revision, and record the reason. A new revision
makes earlier results comparable only where the method stayed the same.
