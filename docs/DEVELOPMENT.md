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
| `fieldkit_runtime/temper.py` | The Temper commands Field Kit uses and the fields it reads |
| `fieldkit_runtime/probe.py`, `watcher.py` | One supervised `temper execution serve` and its process, memory, swap and heat observation |
| `fieldkit_runtime/serving.py`, `stream.py` | Bounded HTTP calls and the streamed answer, kept even when it stops early |
| `fieldkit_runtime/splash_memory.py` | Splash's native Metal memory counters |
| `fieldkit_runtime/coding/` | The Flask task's patch scope and its sandboxed test grader |

A run folder `runs/<UTC time>/` holds `run.json` (the state, written after each
step), the execution lock, one folder per case (request, raw stream, answer,
patch and process observations), `result.json` and `report.md`. While the run
is active it also holds the run's Temper folder, the unpacked Flask source and,
when needed, a private model cache.

## Use a development Temper build

Field Kit needs Temper 0.2.0 or newer. Setup installs the signed release that
`setup.sh` pins. To try a Temper checkout instead:

```sh
temper_source=/absolute/path/to/temper-checkout
(cd "$temper_source" && go build -o build/temper ./cmd/temper)
./setup.sh --install-only
./field-kit check --temper "$temper_source/build/temper" --preview
```

Pass the same `--temper` path when continuing a run: the exact Temper binary
and this checkout are part of the run, and a changed one is refused.

Moving the pin to a new Temper release is a Field Kit release: update the
archive name and checksum in `setup.sh`, then run setup and a preview.

## Change the method

The protocol, the cases and the Flask source are part of every result. Change
them only in a new protocol revision, and record the reason. A new revision
makes earlier results comparable only where the method stayed the same.
