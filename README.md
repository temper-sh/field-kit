# Field Kit

Measure how a Temper preset runs on your Mac: whether it fits, how fast it
reads and writes, and how long a real coding task takes.

Status: one experiment, the basic check, for Apple Silicon Macs with 32 GB of
memory or more. A run can take hours on a slower Mac and can download 15–20 GB
when the model is not on your Mac yet.

## Why this is useful

[Temper](https://github.com/temper-sh/temper) recommends presets, but they are
measured on few machines. Different Macs give very different results. One run
on your Mac adds a measured point for that preset: how fast it reads the
prompt, how fast it writes, how much memory it uses and how long it needs for a
fixed coding task. Failed and unfinished runs are useful too.

## Current experiments

| Experiment | What it helps answer | Requirements | Status |
|---|---|---|---|
| [Basic check](docs/experiments/basic-check.md) | Does a preset fit this Mac, and how fast is it on a fixed coding task? | Apple Silicon Mac with 32 GB or more; the Hugging Face tool `hf`; free disk for the model unless it is already downloaded. Splash presets need Apple M3 or newer and macOS 26.4+. | Open for contributions. |

## Try it

```sh
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./setup.sh --install-only
./field-kit check --preview
./field-kit check
```

Setup downloads a signed Temper 0.2.0 release and a private Python into this
folder (about 31 MB) and needs no Homebrew or administrator access. Install
`hf` yourself first, for example with `brew install hf`. The preview lists the
presets that can run on your Mac and downloads no models. `check` shows the
full plan and asks before it downloads anything.

## What to expect

- **One preset per run.** The default is the first preset of the check that
  fits your Mac. Choose another with `--preset ID`.
- **Two coding requests.** Each one asks the model to change a Flask
  repository and starts on a freshly loaded engine. One request can take from
  a few minutes to a few hours.
- **Downloads only when needed.** If the model is already in your Hugging Face
  cache, for example because you use Temper, the run reuses it. Otherwise the
  model goes into a private cache inside the run, removed at the end.
- **Your Mac comes first.** The run stops early when the Mac starts to swap,
  gets hot or slows its CPU.
- **You choose what to share.** Results stay under `runs/`. `report.md` is a
  summary; `result.json` is the file to send. It contains the patches the model
  submitted, details of your Mac and local paths. Nothing is uploaded.

The [run guide](docs/START.md) covers stopping, continuing and sharing.

## Learn more

- [Run the basic check](docs/START.md): steps, stopping and continuing, sharing
  the result
- [What the basic check measures](docs/experiments/basic-check.md): method,
  limits and how to read the report
- [Develop Field Kit](docs/DEVELOPMENT.md): checks, structure and development
  Temper builds

Licensed under [0BSD](LICENSE).
