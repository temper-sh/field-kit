# Run an experiment

Field Kit runs one selected configuration on your machine and produces a report
you can share. See [current experiments](../README.md#current-experiments) for what's
available, then read the linked guide before starting.

## Download client

Model downloads need either `hf` or `uv` on your `PATH`. Check in the same
Terminal window you will use for the experiment:

```sh
command -v hf || command -v uv
```

If neither command is found, install uv with its
[official installer](https://docs.astral.sh/uv/getting-started/installation/):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

Temper uses an existing `hf`, or obtains the official Hugging Face client
through uv when needed. Field Kit setup installs Temper and private Python;
it does not install these download clients.

## Set up

In Terminal:

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh
```

Setup installs the experiment's signed Temper release and Python under `.local/`.
It downloads about 31 MB, verifies the Temper release's checksum and
signature, and needs no administrator access. Rerunning setup checks and reuses
the installation. Keep unfinished earlier runs in their original checkout.

Setup opens the machine check and confirmation flow after verifying the tools.

To install the tools and inspect the experiment before deciding to run it:

```sh
./setup.sh --install-only
./field-kit contribute --preview
```

The preview checks your machine and shows the limits. It downloads no models
and runs no inference.

## Start a run

Follow the choices in the terminal. Field Kit shows what the selected
configuration will download, how much disk space it needs, how long it may run
and what it will remove.
Answer `yes` to proceed, or `no` to leave without starting the experiment.

Keep the machine connected to power and awake. Close other demanding
applications so their resource use does not distort the measurements. Keep this
checkout unchanged until the run finishes: the code and inputs are part of the
recorded experiment.

To start after inspecting the preview:

```sh
./field-kit contribute
```

The first run uses the experiment's default configuration. It completes that
configuration and stops. Model and engine settings are frozen; read the
displayed costs before accepting.

To preview a named configuration, or the next unattempted one:

```sh
./field-kit contribute --configuration splash-q5 --preview
./field-kit contribute --next --preview
```

Omit `--preview` to review and approve the run. Reopening a completed
configuration shows its existing result. Nothing advances to another
configuration automatically. The experiment guide lists available choices.

## Stop or continue

To run only to the next task checkpoint:

```sh
./field-kit contribute --pause-after-task
```

The engine stops and the completed task remains in the session and readable
report. The selected configuration's files stay on disk for resumption. Run
`./field-kit contribute` to continue with the remaining task; it does not repeat
the first attempt. Finish the pending configuration before selecting another.

During measurement, press **Ctrl-C once**. Allow up to two minutes for the
experiment to stop its processes and save partial results.

To continue after an interruption, run this from the same folder:

```sh
./field-kit contribute
```

If initial tool installation failed, rerun `./setup.sh`. An interrupted Python
installation may leave a temporary lock; setup can wait up to 15 minutes for it
to expire. If setup reports `.local/setup.lock` and no setup process is running,
run `rmdir .local/setup.lock`, then `./setup.sh` again.

A forced kill or restart can prevent results from being saved. Field Kit may
then retain the experiment installation and refuse to continue. Send the
maintainer the printed error and session path. Keep those files until recovery
is resolved; completed and failed measurements are not silently repeated.

Once a run is complete, repeating the command shows its result again. To
deliberately repeat that configuration while keeping earlier results:

```sh
./field-kit contribute --new
```

Use `--next` or `--configuration NAME` to choose a different configuration.
Each run has its own report and result file. Completed runs remove their private
model downloads; separate runs can download the same weights again. Existing
shared Hugging Face caches are never pruned.

To repeat Q4 explicitly while preserving earlier Q4 and Q5 attempts:

```sh
./field-kit contribute --configuration splash-q4 --new --preview
caffeinate -i ./field-kit contribute --configuration splash-q4 --new
```

Keep the lid open and the Mac connected to power. Share the new run's
`result.json` and `report.md`; their separate run directory identifies the
repeat. Revision 6 adds native Splash memory measurements with the same model,
tasks and inference settings. Completed revision 5 chunks still count for
`--next`. Finish an unfinished run in its original checkout before updating.

Keep unfinished revision 4 studies in their original checkout. They cannot
be resumed as configuration runs; use a separate checkout for the new study.

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
