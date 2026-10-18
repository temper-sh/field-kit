# Engine tuning

**Question:** what is the best setup this Mac can use for one preset — the
largest context window and the fastest speeds that still finish the coding
task?

Study revision: `engine-tuning@1`.

An AI coding agent, such as Claude Code or pi, plans the study. Between runs it
reads the results, chooses the next [tuning trial](basic-check.md#tuning-trials),
writes the choice and its reason into the study's log and gives you the
command. You start each run yourself; Field Kit measures it with the basic
check's method. The agent never starts a run.

## What you need

- Field Kit set up as in the [run guide](../START.md), with `./field-kit check
  --preview` working.
- An AI coding agent that can read files and run commands in this folder. You
  pay for its use; each session reads a few small files.
- Time. A run takes from one to several hours on most Macs; a study usually
  has three to six runs. Your Mac is busy during each run, not between them.

## Start or continue

Open your agent in the Field Kit folder and send:

```text
Read docs/experiments/engine-tuning.md and tune qwen3.8-27b-q4xl-mtp for this Mac.
```

Use any preset ID from `./field-kit check --preview`. After each run, open the
agent again and send the same request; it continues from the study's log,
`trials/<preset>/log.md`.

The agent asks how many runs you want to spend in total, the baseline
included. Before each run it shows the trial's change and the command, for
example:

```sh
caffeinate -i ./field-kit check --preset-file trials/qwen3.8-27b-q4xl-mtp/qwen-mtp-t1.json
```

Run it in a terminal that stays open. Field Kit shows the full plan and asks
you to type `yes`, as in the basic check.

## What you send

The study's folder `trials/<preset>/` (the log and the trial files) and the
`result.json` of every run the log names. For a case that filled the context
window, also send its `cases/<case>/response.json`: it holds the model's
reasoning. Nothing is uploaded; sharing is your choice.

## Instructions for the assistant

You plan an engine-tuning study, revision `engine-tuning@1`. Scripts run and
measure; you decide between runs and record every decision with its reason.
Write plainly: the contributor may not know these terms.

### Goal

Find the maximum usable setup of one catalog preset on this Mac. In order of
priority:

1. **Completed work:** both Flask cases end `patch-submitted`. A case that
   fills the window (`context-window-exhausted`), times out or fails is a
   missing outcome.
2. **Context:** the largest context window that stays usable, up to the
   preset's limit.
3. **Speed:** higher prefill and generation rates and a shorter task time,
   with the same outcomes.

**Usable** means: no swap-growth, heat or CPU-speed stop, no memory failure,
and an engine peak (`memory.engine_peak_wired_bytes` in `result.json`) within
the engine's share of this Mac: three quarters of
`machine.physical_memory_bytes`, but no more than `machine.wired_limit_mib`.
The report's **Room to tune** section prints both numbers when the run
measured a peak. The catalog's settings are where you start, not the answer:
keep pushing while there is room.

### What is fixed and what you may change

Fixed: the basic check's cases, limits, measurements and cleanup; the preset's
weights and template; its request defaults (sampling, reasoning, output cap).
Also fixed, although Temper lists them under `engine_config`:
`controls.reasoning_effort`, `controls.preserve_reasoning` and
`controls.context_shift`, which change what the model writes or sees, and
`controls.fit`, which lets llama.cpp change settings by itself. Field Kit
refuses changes to model and template files.

You may change, as a fork of the preset:

- `context_window_tokens`, up to the preset's `context_limit_tokens` (in
  `runs/<run>/execution.lock.json`, or the report's **Room to tune**);
- the other `engine_config` settings;
- `speculation.max_draft_tokens`, and `speculation.method` between the
  preset's own method and `none`. Other methods need draft weights, which a
  trial cannot add.

`.local/temper preset fields <preset> --root .local/preview` lists every
setting with its type, choices and current value; run `./field-kit check
--preview` first so that folder has the catalog.

Never start a run, answer Field Kit's consent for the contributor, or change
`runs/`, `protocols/`, `tasks/` or Field Kit's code. Read-only work is fine:
`./field-kit check --preview` with `--preset` or `--preset-file`, `./field-kit
version`, `./field-kit verify` (it prints Field Kit's code as `runtime=`),
`.local/temper preset fields`, and reading a model's public card or
`config.json` on Hugging Face.

### Each session

1. **Orient.** Read `AGENTS.md`, then the study log `trials/<preset>/log.md`
   if it exists. Read `runs/*/result.json` and `runs/*/report.md`. If a
   `runs/*/run.json` has state `preparing`, `measuring` or `cleaning`, a run
   is unfinished: tell the contributor to continue it with `./field-kit check`
   and stop. If one has state `retained`, tell them to send that run's
   `report.md` to the maintainer and stop.
2. **First session.** Run `./field-kit check --preview`, `./field-kit
   version` and `./field-kit verify`, and write the log's brief (template
   below). Ask the contributor
   how many runs the study may use in total, the baseline and any repeats
   included; runs made before the study do not count. Suggest six. Record the
   answer with the date.
3. **Compare like with like.** Speeds and memory compare only between runs
   with the same Temper version, catalog sequence and Field Kit code
   (`field_kit.sha256`, which `./field-kit verify` prints as `runtime=`). When
   the current versions differ from the log's header, add a note to the log
   that names the change and the runs that no longer compare.
4. **Baseline.** The baseline is a completed basic check of the preset on
   this Mac (`trial` is null or missing, `preset.id` is the preset). When
   there is none with the current versions:
   - if an older basic check of the preset on this Mac shows a missing
     outcome, the first trial addresses it; the old run already shows where
     the limit is;
   - otherwise the next run is the basic check:
     `caffeinate -i ./field-kit check --preset <preset>`.

   A speed trial compares with the best earlier run on the same versions, a
   basic check or a trial that finished both cases; if there is none, run the
   basic check first.
5. **Check each new run** before you use it: state `complete`, each case's
   status, failure and `measurement_valid`, and the stop reason. A
   measurement marked not comparable says nothing about the setting; record
   why, and decide whether to repeat the same trial. A repeat is a new run;
   the first attempt stays. A run without `memory.engine_peak_wired_bytes`
   comes from an older Field Kit that did not measure the engine's memory:
   use its outcomes and rates, not its memory numbers.
6. **Decide the next step** with the rules below, and append a decision to
   the log. Earlier entries stay as written; a correction is a new entry.
7. **Write the trial file** `trials/<preset>/<trial-id>.json`:

   ```json
   {
     "schema": "temper-preset/v1",
     "origin": "<preset>",
     "changes": {"context_window_tokens": 131072}
   }
   ```

   A trial is always a fork of the catalog preset, so its file holds every
   change the trial keeps, not only the new one. The file name is the trial
   ID: lowercase letters, digits, dots and dashes, new for every trial. Never
   edit a file that a run has used. A baseline needs no file.
8. **Validate** with `./field-kit check --preview --preset-file <file>`, or
   `--preset <preset>` for a baseline. It must show exactly the intended
   changes. Then give the contributor the run command and stop.

### Rules for the next step

- **A case filled the window.** Read the end of its reasoning first: the last
  3,000 characters of `message.reasoning` in
  `runs/<run>/cases/<case>/response.json`. Record whether it repeats itself
  or still works toward a fix. If the file is missing, record that the ending
  is unknown and read it in the next run that fills the window. If memory and
  time allow (below), the next trial doubles the window, within the preset's
  limit. If the reasoning repeats itself, a larger window may only make a
  longer loop: say so; one larger-window trial is still worth it, because the
  window is a capacity question too.
- **Memory.** The context cache grows with the window. Estimate it per token
  as cache layers × 2 × KV heads × head size × bytes per value (`f16` 2,
  `q8` about 1.06, `q4` about 0.56). Take the structure from the model's
  `config.json` on Hugging Face: the weights' repository is in
  `execution.lock.json`, and a GGUF repository's card names its base model.
  Cache layers are the full-attention layers (`layer_types` entries
  `full_attention`, or every `full_attention_interval`-th layer, else
  `num_hidden_layers`) plus any MTP layer; KV heads is `num_key_value_heads`
  and head size `head_dim`. The new peak is the last measured engine peak
  plus the cache for the added tokens; without a measured peak, the model
  files plus the cache plus 2 GiB for working buffers. Skip a window whose
  estimated peak would exceed the engine's share minus 2 GiB. Write the
  estimate and its source in the log; the measured peak replaces it.
- **Time.** Each request stops after 4 hours. A case can write about
  generation rate × 14,400 output tokens in that time, so a window larger
  than its input plus that number helps only if generation gets faster. Use
  the rate measured on this Mac, and write the estimate in the log.
- **Both cases finished and there is room:** try the next larger window, or
  the speed setting with the clearest mechanism, such as the speculation draft
  length for generation or batch sizes for prefill.
- **One setting per trial.** Settings can interact, for example the window
  and the KV cache precision; name the interaction in the log. A lower KV
  precision saves memory but can change answers; mark such a trial for
  central review of its patches.
- **One run is one sample.** Treat speed differences under 10% as noise.
  Repeat a run only when the difference decides the result.
- **Limits and lower bounds.** A window that passed with no larger window
  failing is a lower bound, not the limit. Say which one you have.
- **Stop** when the budget is used; or the window is at the preset's limit
  and speed settings gave no gain beyond noise; or every next step would
  break a usable rule. Then write the result.

### Log template

```markdown
# Tuning study: <preset> on <chip>, <memory>

Study engine-tuning@1 · started <date> · Field Kit <version> (code <first 8 characters of runtime=>) · Temper <version> · catalog sequence <n>

## Brief

- Preset: <preset>, <window> tokens of its <limit>
- Baseline: runs/<id> (<date>, Temper <version>, catalog <n>), or none yet
- Objective: 1. both cases patch-submitted; 2. largest usable window; 3. faster prefill, generation and task time
- Usable: no swap-growth, memory, heat or CPU-speed stop; engine peak within <share> GiB
- Budget: <n> runs in total, agreed with the contributor on <date>
- Memory: model files <size>; context cache about <size> per 1,000 tokens (<source>)

## Decision <n> — <date>

- Read: runs/<id>, …
- Observed: <facts with numbers>
- Decision: <run trial <id>: <changes> | run the baseline | repeat <id> | stop>
- Reason: <mechanism and evidence; memory and time estimates>
- Expect: <the result that would confirm or refute it>
- Command: `caffeinate -i ./field-kit check --preset-file trials/<preset>/<id>.json`
- Runs used: <n> of <budget>, this one included

## Note <n> — <date>

- <what changed, for example new Temper or catalog versions, and which runs no longer compare>

## Result

- Best usable setup: <trial id and its changes>, from runs/<id>
- Against the baseline: <window, case outcomes, prefill and generation tok/s, task time, engine peak>
- Limits found: <established limits; lower bounds>
- Open questions: <…>
- Send: trials/<preset>/ and the result.json of runs/<id>, …
```

Do not claim answer quality: the patches' tests run later, at central review.
