# Field Kit

Measure how a Temper preset runs on your Mac: whether it fits, how fast it
reads and writes, and how long a real coding task takes.

Status: three studies, the basic check, engine tuning and weights comparison,
for Apple Silicon Macs with 32 GB of memory or more. A run can take hours on a slower Mac and can download 15–20 GB
when the model is not on your Mac yet.

## Why this is useful

[Temper](https://github.com/temper-sh/temper) recommends presets, but they are
measured on few machines. Different Macs give very different results. One run
on your Mac adds a measured point for that preset: how fast it reads the
prompt, how fast it writes, how much memory it uses and how long it needs for a
fixed coding task. Failed and unfinished runs are useful too.

The goal is the best setup each Mac can use: the largest context window and
the fastest speeds that still finish the task. The catalog's settings are a
starting point. Tuning trials on your Mac show how far they can go.

## Current experiments

| Experiment | What it helps answer | Requirements | Status |
|---|---|---|---|
| [Basic check](docs/experiments/basic-check.md) | Does a preset fit this Mac, and how fast is it on a fixed coding task? With tuning trials: how far can its settings go on this Mac? | Apple Silicon Mac with 32 GB or more; the Hugging Face tool `hf`; free disk for the model unless it is already downloaded. Splash presets need Apple M3 or newer and macOS 26.4+. | Open for contributions. |
| [Engine tuning](docs/experiments/engine-tuning.md) | What is the best setup this Mac can use for one preset: the largest window and the fastest speeds that still finish the task? An AI agent plans the trials; you approve each run. | As for the basic check, plus an AI coding agent such as Claude Code or pi. | New; first studies welcome. |
| [Weights comparison](docs/experiments/weights-comparison.md) | Which build of a preset's model works best on this Mac? Larger builds (Q5, Q6, Q8) need more memory; their answers are compared centrally. An AI agent plans the runs. | As for engine tuning, plus free disk for one larger build at a time; most useful with 48 GB or more. | New; first studies welcome. |

## Try it

**With an AI agent**, such as Claude Code or pi, send it this one message:

```text
Help me run a Field Kit study on this Mac. Use my Field Kit folder if I have one, or clone https://github.com/temper-sh/field-kit into ~/field-kit, then follow its docs/ASSISTANT.md.
```

It sets up what is missing (it asks before installing Temper or `hf`),
explains the studies, recommends one for your Mac and runs it. Each run starts
only after you agree to its plan, and runs in the background.

**Without an agent**, install Temper 0.3.1 or newer and the Hugging Face tool
`hf`, then:

```sh
brew install temper-sh/temper/temper hf
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
./field-kit check --preview
./field-kit check
```

The first command installs Field Kit's own Python through Temper (about 27 MB,
once). The preview lists the presets that can run on your Mac and downloads no
models. `check` shows the full plan and asks before it downloads anything.

## What to expect

- **One preset per run.** The default is the first preset of the check that
  fits your Mac. Choose another with `--preset ID`.
- **Tune for your Mac.** The report shows the room left: the context window
  against the model's limit, and the memory left. A
  [tuning trial](docs/experiments/basic-check.md#tuning-trials) runs a preset
  with your changed settings, such as a larger window, with
  `--preset-file FILE`. In an [engine-tuning study](docs/experiments/engine-tuning.md),
  your AI agent plans the trials for you.
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
