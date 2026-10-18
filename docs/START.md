# Run the basic check

The basic check installs one Temper preset, sends it two fixed coding requests
and records speed and memory. Read
[what it measures](experiments/basic-check.md) if you want the details first.

## Before you start

- An Apple Silicon Mac with 32 GB of memory or more.
- The Hugging Face tool `hf` on your `PATH`; Temper downloads models with it:

  ```sh
  command -v hf || brew install hf
  ```

- Free disk for the model, unless it is already in your Hugging Face cache.
  The preview shows the exact amount.

## Set up

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh --install-only
```

Setup installs a signed Temper release and a private Python under `.local/`.
It downloads about 31 MB, checks the release's checksum and signature, and
needs no administrator access. Running it again checks and reuses the
installation.

If setup reports `.local/setup.lock` and no setup is running, run
`rmdir .local/setup.lock`, then `./setup.sh` again.

## Look first

```sh
./field-kit check --preview
```

The preview lists the presets of the check, says which ones can run on this
Mac and why the others cannot, and names the one a run would use. It downloads
no models and creates no run.

## Start a run

```sh
caffeinate -i ./field-kit check
```

To check another preset, add `--preset ID` with an ID from the preview.

Field Kit shows the plan: the preset, the downloads, the disk space, the time
limits and what it removes afterwards. Type `yes` to start. Anything else
cancels without downloading.

During the run:

- Keep the Mac on power and awake. `caffeinate -i` keeps it from sleeping.
- Close other demanding apps; they change the measurements.
- Your own Temper can keep running: the run uses its own ports. A request
  that loads one of your models uses memory and the GPU, so it changes the
  measurements like any other demanding app.
- Keep this folder unchanged until the run ends. The code and inputs are part
  of the recorded run.

When the run ends, Field Kit removes the engine, the run's Temper folder and
any private model cache. The terminal prints the report.

## Stop and continue

Press **Ctrl-C once** and wait. Field Kit stops the current request, waits for
Temper to stop its processes and saves what it has. This can take up to two
minutes.

To continue, run the same command again:

```sh
./field-kit check
```

An unfinished run always comes first. It continues with the next case; an
interrupted request is kept as it is and is not repeated. If the installation
was interrupted, the next run continues it.

If Temper could not confirm that its processes stopped, Field Kit keeps the
run's files and refuses to continue. Keep the `runs/` folder and send the
maintainer the run's `report.md` and the printed error.

## Share the result

Each run has its own folder under `runs/`:

- **`report.md`**: a readable summary.
- **`result.json`**: the measurements, the patches the model submitted,
  details of your Mac, local paths and the exact versions used.

Read the report, look over the JSON, then send `result.json` to the person
coordinating the measurements, through a channel you choose. Mention anything
unusual, such as other heavy work at the same time. Nothing is uploaded
automatically. Incorrect answers and stopped runs are still useful.

To measure another preset, start a new run with `--preset ID`. Each run keeps
its own folder.
