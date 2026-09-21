# Run an experiment

Field Kit runs a defined set of tasks on your machine and produces a report you
can share. See [current experiments](../README.md#current-experiments) for what's
available, then read the linked guide before starting.

## Set up

In Terminal:

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh --install-only
./field-kit contribute --temper /absolute/path/to/temper
```

This development revision requires a matching Temper build supplied by the
maintainer. Keep revision 1 runs in their original checkout. Setup installs the
signed bootstrap Temper and Python under `.local/`; that pinned Temper release
cannot run revision 2. Use the supplied executable with `--temper`.
It downloads about 31 MB, verifies the Temper release's checksum and signature,
and needs no administrator access. Rerunning setup checks and reuses the
installation.

To install the tools and inspect the experiment before deciding to run it:

```sh
./setup.sh --install-only
./field-kit contribute --temper /absolute/path/to/temper --preview
```

The preview checks your machine and shows the limits. It downloads no models
and runs no inference.

## Start a run

Follow the choices in the terminal. Field Kit shows what it will download,
how much disk space it needs, how long it may run and what it will remove.
Answer `yes` to proceed, or `no` to leave without starting the experiment.

Keep the machine connected to power and awake. Close other demanding
applications so their resource use does not distort the measurements. Keep this
checkout unchanged until the run finishes: the code and inputs are part of the
recorded experiment.

## Stop or continue

During measurement, press **Ctrl-C once**. Allow up to two minutes for the
experiment to stop its processes and save partial results.

To continue after an interruption, run this from the same folder:

```sh
./field-kit contribute --temper /absolute/path/to/temper
```

If initial tool installation failed, rerun `./setup.sh`. An interrupted Python
installation may leave a temporary lock; setup can wait up to 15 minutes for it
to expire. If setup reports `.local/setup.lock` and no setup process is running,
run `rmdir .local/setup.lock`, then `./setup.sh` again.

A forced kill or restart can prevent results from being saved. Field Kit may
then retain the experiment installation and refuse to continue. Send the
maintainer the printed error and session path. Keep those files until recovery
is resolved; completed and failed measurements are not silently repeated.

Once a run is complete, repeating the command shows its result again. To deliberately
start another run while keeping earlier results:

```sh
./field-kit contribute --temper /absolute/path/to/temper --new
```

## Return the result

The terminal prints two paths under `runs/`:

- **`report.md`** — a readable summary of what happened.
- **`result.json`** — measurements, generated answers, machine details, local
  paths and the exact experiment configuration.

Read the report and review the JSON, then send **`result.json`** to the person
coordinating the experiment through your chosen channel. Mention relevant
conditions, such as other heavy work running at the same time. Nothing is
uploaded automatically.

Incorrect answers and incomplete runs can still be useful. The coordinator
reviews the evidence before using it in a model card or recommendation. The
experiment's guide explains how to check its results.
