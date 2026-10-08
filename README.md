# Field Kit

Help improve Temper's presets by running a defined experiment on your Mac.
Field Kit handles the setup, runs the tasks and creates a report you can share.

Different Macs can give very different results. Your run helps establish which
models fit, how long useful work takes and which settings help. Failed and
unfinished runs matter too.

## Current experiments

| Experiment | What it helps answer | Requirements | Status |
|---|---|---|---|
| [Qwen machine study](docs/experiments/qwen-machine-study.md) | Run one configuration to help compare coding results, memory use and useful context. | Apple M3 or newer, macOS 26.4+; 36 GiB or at least 48 GiB RAM. Allow about 72 GiB free disk for the default run; see the guide for other choices. | Open for contributions; measurements on these machines pending. |

Read the experiment guide before starting. Runs can take a long time and
download many gigabytes; Field Kit checks your machine and shows the full
plan before asking you to proceed.

## Get started

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh
```

Setup downloads a signed [Temper](https://github.com/temper-sh/temper) release
and a private Python runtime into this folder, then opens the machine check
and confirmation flow. Setup supports Apple Silicon macOS and needs no
Homebrew or administrator access.

To install the tools and preview the plan first:

```sh
./setup.sh --install-only
./field-kit contribute --preview
```

Previewing downloads no models and starts no experiment.

## What to expect

- **One configuration at a time.** The default runs Splash Q4 on two coding
  tasks, saves a report and stops. Each task has a saved checkpoint. You choose
  whether to run another configuration.
- **A separate installation.** Models download into a private study cache.
  After verified shutdown, the run removes its installation and model cache.
  It preserves your existing AI setup and shared caches.
- **A readable report.** `report.md` summarizes the run; `result.json` contains
  the measurements and details needed for review.
- **You choose what to share.** Results stay under `runs/`. Nothing is uploaded.
  Review the JSON before sharing: it contains generated answers, machine details
  and local paths.

Run `./field-kit contribute` to continue an interrupted run or view a completed
result. During measurement, press **Ctrl-C once** and wait for it to save and
stop. The [run guide](docs/START.md) covers recovery and sharing results.

## Development

To check changes without running a model:

```sh
./field-kit verify
python3 -m unittest discover -v
```

Field Kit uses the Python standard library. See
[development and dispatched runs](docs/DEVELOPMENT.md) for its code structure,
checks and command interface.

Licensed under [0BSD](LICENSE).
