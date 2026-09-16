# Field Kit

Run local-AI experiments on your own machine and share results that others can
compare and review.

Field Kit helps answer practical questions: does a model fit, how well does it
perform, and which settings help? Each experiment defines its tasks and limits.
Field Kit handles the run and records the machine, configuration and results,
including failed or unfinished work.

## Current experiments

Follow an experiment's link for its requirements, run options and method.

| Experiment | Useful result | Requirements | Status |
|---|---|---|---|
| [Qwen machine study](docs/experiments/qwen-machine-study.md) | Comparable performance numbers and tested context lengths and settings for Qwen3.8 27B | Apple Silicon Mac; ≥32 GiB RAM; about 35 GiB free disk | Prepared; live measurements needed |

This build opens the Qwen study directly. Results need review before becoming
recommendations.

## Get started

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh
```

Setup downloads a signed [Temper](https://github.com/temper-sh/temper) release
and a private Python runtime into this folder. You need no Homebrew, compiler
or administrator access. The current setup supports Apple Silicon macOS.

Before an experiment starts, Field Kit checks your machine, shows the download,
disk and time limits, and asks for confirmation. The experiment guide explains
its requirements and choices.

## What to expect

- **A separate installation.** The current study removes its model and engine
  installation after finishing. Your existing AI setup stays unchanged.
- **A readable report.** `report.md` summarizes the observations;
  `result.json` contains the details needed to review them.
- **Control over sharing.** Results stay under `runs/` in this folder. Nothing
  is uploaded. Review the JSON before sending it: it includes generated answers,
  machine details and local paths.

Run `./field-kit` again to continue an interrupted run or view a completed
result. During measurement, press **Ctrl-C once** and wait for it to stop and
save. See the [run guide](docs/START.md) for recovery instructions.

## Learn more

- [Run an experiment and return a result](docs/START.md)

## Development

Field Kit uses the Python standard library. To check changes without running a
model:

```sh
./field-kit verify
python3 -m unittest discover -v
```

CI runs these checks on Python 3.9 and 3.14. Adding an experiment currently
requires code changes as well as a package; the guided flow and result reviewer
are wired to the Qwen study.

Licensed under [0BSD](LICENSE).
