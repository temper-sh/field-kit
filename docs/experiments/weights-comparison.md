# Weights comparison

**Question:** which build of a preset's model works best on this Mac?

Study revision: `weights-comparison@1`.

A model is published in several builds. Smaller builds keep less of the
original model's precision and need less memory; larger ones, such as Q5, Q6
or Q8, keep more, need more memory and are usually slower. Each catalog preset
uses one build, chosen for 32 GB Macs. A Mac with more memory can run larger
builds.

This study runs the preset's own build and the larger builds of the same model
that fit this Mac, with the preset's settings. For each build it records
whether both coding tasks finished, the speeds, the time and the memory.
Whether a larger build writes better code is judged later, centrally, from
the patches it submitted; the study does not decide that on your Mac.

An AI coding agent, such as Claude Code or pi, plans the study. Between runs it
reads the results, chooses the next build, writes the choice and its reason
into the study's log and shows you the run's plan. Each run starts only after
you agree to that plan.

## What you need

- Temper and Field Kit set up as in the [run guide](../START.md). Do not
  upgrade Temper during the study: speeds compare only between runs on the
  same Temper.
- An AI coding agent that can read files and run commands in the Field Kit
  folder. You pay for its use; each session reads a few small files.
- Free disk for one larger build at a time, for example about 20–30 GB for a
  27B model. A build that is not in your Hugging Face cache downloads into the
  run and is removed when the run ends.
- Time: a run takes from one to several hours; a study usually has three or
  four runs.

## Start or continue

Open your agent in the Field Kit folder and send:

```text
Read docs/experiments/weights-comparison.md and compare builds of qwen3.8-27b-q4xl-mtp on this Mac.
```

After each run, send the same request; the agent continues from the study's
log, `trials/<preset>/log.md`.

## What you send

The study's folder `trials/<preset>/` (the log and the trial files) and the
`result.json` of every run the log names. Nothing is uploaded; sharing is your
choice.

## Instructions for the assistant

You plan a weights-comparison study, revision `weights-comparison@1`. Scripts
run and measure; you decide between runs and record every decision with its
reason. Write plainly. This study reuses the session steps, rules and log
template of [engine tuning](engine-tuning.md#instructions-for-the-assistant);
read them first. The differences are below.

### Goal

Measure the preset's build and every larger build of the same model that fits
this Mac, with the preset's settings, so that a central review can compare
their patches. On this Mac, record for each build: both cases' outcomes,
prefill and generation speed, task time and the engine's peak memory. Do not
rank builds by answer quality.

### What is fixed and what changes

Only the weights change. Everything else stays as the preset sets it,
including the context window and the speculation. A build that does not fit at
the preset's window is skipped and named in the log, not run with a smaller
window: two changes in one trial cannot be told apart.

### Choose the builds

- Take the builds from the Hugging Face repository of the preset's weights
  (`runs/<run>/execution.lock.json`, or `temper preset fields <preset> --root
  .local/preview`). List its files read-only, for example
  `https://huggingface.co/api/models/<repository>`, and read its model card for
  the publisher's recommended builds.
- Use the same file format as the preset's weights. Prefer single-file builds.
- Estimate each build's peak as its file size plus the context cache at the
  preset's window plus 2 GiB (the cache estimate is in engine tuning). Keep the
  builds whose estimate stays under the engine's share minus 2 GiB.
- Run them from the smallest to the largest. A build that fails to start or
  fills the window is a result: record it and go on.

### Make a trial file

```sh
temper preset fork <preset> <trial-id> --name "<model> <build> (trial)" --root .local/preview
temper preset weights <trial-id> <repository> <file> --root .local/preview
mv .local/preview/presets.d/<trial-id>.json trials/<preset>/<trial-id>.json
```

Temper fills the file's size and SHA-256 from Hugging Face. `./field-kit check
--plan --preset-file trials/<preset>/<trial-id>.json` must show the change
`artifact <preset's weights> → <new weights>` and the download.

### Stop and result

Stop when the budget is used (suggest four runs, the baseline included) or no
larger build fits. Write the log's result as a table, one row per build: file
size, both cases' outcomes, prefill and generation tok/s, task time and engine
peak. Add that answer quality waits for central review of the patches.
