# Basic check

**Question:** does this preset fit this Mac, and how fast is it on a fixed
coding task?

The check measures a preset exactly as Temper installs it on your Mac. It is
the first step for every preset: later experiments compare engines, model
builds and settings, but they start from these numbers.

Field Kit looks for the best setup each Mac can use: the largest context
window and the fastest speeds that still finish the task. The catalog's
settings are a starting point, not that answer. Each report shows the room
left, and a [tuning trial](#tuning-trials) measures a changed preset with the
same method.

## What runs

1. Temper compiles the preset for this Mac's memory from its signed catalog,
   or, for a tuning trial, from your preset file. Field Kit passes the result
   to Temper unchanged.
2. Temper installs the engine and the model files into the run's own folder.
3. For each of two Flask coding cases, Temper starts a fresh engine. Field Kit
   waits until the model is loaded, then sends the case once.
4. Temper stops the engine. Field Kit removes the installation when Temper
   confirms that its processes stopped.

Each case gives the model a part of the [Flask](https://github.com/pallets/flask)
source and a requested change, and asks for one `submit_patch` tool call with
the edits and new tests. The two cases are about 16,000 and 24,000 tokens long.
The request uses the preset's own sampling and output settings. There is no
second attempt and no feedback.

## What is measured

| Measurement | Meaning |
|---|---|
| Prefill speed | Tokens per second while the engine reads the prompt, from the engine's own counters |
| Generation speed | Tokens per second while it writes the answer, including reasoning, from the engine's own counters |
| Task time | From sending the request to the end of the answer |
| Start time | From starting the engine until the model answers its health check; shorter when the model files are still in the Mac's file cache |
| Engine after load | How much the Mac's wired memory grew from before the engine started, after the model loaded |
| Engine peak | The highest such growth in the samples taken during the case |
| Splash native memory | For Splash presets: the engine's own Metal allocations; they overlap the engine numbers, so do not add them |

Field Kit reports a rate only when the engine reports it. It never estimates
one.

Memory the GPU holds is wired, so the wired growth covers the model, its
cache and its working buffers, for every engine. The process's own footprint
and resident memory miss model weights that the engine maps from files; they
stay in `result.json` but are not used as the engine's memory. Another app that
wires memory during the case adds to the number.

## Results of a case

| Result | Meaning |
|---|---|
| `patch-submitted` | The model returned a patch inside the case's edit scope. Tests run later, at review. |
| `invalid-submission` | The answer had no usable patch, or the patch touched files outside the scope. |
| `context-window-exhausted` | The answer filled the preset's context window before it finished. |
| `exceeds-context` | The case did not fit the preset's context window. |
| `memory-failure`, `stopped-by-watch` | The engine ran out of memory, or the run stopped to protect the Mac. |
| `timeout`, `interrupted`, `runtime-failure` | The case did not finish for another reason. |

A model-side result lets the second case run. A stop on the machine side ends
the run. A case marked **not comparable** finished, but its speed or memory
numbers were disturbed: by heat, a CPU speed limit, sleep or missing samples.

## Room to tune

Every report ends with the distance to this model's and this Mac's limits:

- the context window the run used, and the most the model accepts;
- each case that filled the window, with its input and output tokens;
- the engine's peak memory, and how much of the engine's share of this Mac
  (three quarters of its memory, within the GPU limit) was left.

A case that filled the window, or a lot of memory left, is a reason to try a
tuning trial.

## Which presets

The check covers Temper's **General** chat presets. Specialist presets have
small context windows or do not write text. The preview shows which presets
can run on your Mac and why the others cannot.

Before it downloads anything, Field Kit applies a memory floor: the model files
plus 4 GiB must fit in three quarters of the Mac's memory and in its GPU memory
limit. This is a floor, not a prediction that the preset fits.

## Tuning trials

The catalog's presets are measured on few Macs: today each preset has one set
of settings, sized for 32 GB. A Mac with more memory can often use a larger
window or other settings. A tuning trial measures one changed preset with the
same cases, limits and measurements as the basic check, and records the change.

Write a Temper preset file: a *fork* of a preset of the check, with your
changes. The file name is the trial's ID (lowercase letters, digits, dots and
dashes). For example, `trials/qwen-mtp-128k.json` (Git ignores `trials/`):

```json
{
  "schema": "temper-preset/v1",
  "origin": "qwen3.8-27b-q4xl-mtp",
  "changes": {"context_window_tokens": 131072}
}
```

Check it, then run it:

```sh
./field-kit check --preview --preset-file trials/qwen-mtp-128k.json
caffeinate -i ./field-kit check --preset-file trials/qwen-mtp-128k.json
```

The preview shows Temper's list of changes, or Temper's reason when it refuses
the file. To see every setting you can change, with its current value, run
this after any preview:

```sh
.local/temper preset fields qwen3.8-27b-q4xl-mtp --root .local/preview
```

How to tune:

1. Run the basic check of the preset first. It is the baseline the trials are
   compared with.
2. Change one setting per trial and push it toward its limit, for example a
   window of 131,072 tokens, then 262,144.
3. Stop when a limit is reached: the task no longer finishes, swap grows, the
   run stops for heat, memory runs out, or speed drops so far that the setup is
   no longer worth using. Keep the last setting that passed.

Rules and limits:

- A trial changes settings only: context window, engine settings, speculation
  and request defaults. It cannot change the model or template files, because
  Field Kit uses the original preset's download plan.
- The memory floor counts the model files, not the context cache. A larger
  window needs more memory than the floor shows; the run stops if swap grows.
- A larger window can make a case run longer than the 4-hour request limit.
- The report is titled **Tuning trial**. `result.json` records the original
  preset, Temper's list of changes and your file, so a trial never counts as
  the preset's baseline. Temper makes no quality or speed claim for your own
  presets; the maintainers review trials before a catalog preset changes.

## Limits

- One request per case and one run per preset give a single sample, not an
  average.
- Tests of the submitted patches run later, at review, not on your Mac. A
  submitted patch is not yet a correct one.
- Other apps, the disk cache and heat change the numbers. The report marks
  disturbed cases; it cannot see everything.
- The run stops when swap grows by more than 512 MiB, on a thermal warning or
  on a CPU speed limit.
- Time limits: 6 hours for the installation, 30 minutes for a model to load and
  4 hours per request.

The method and limits are fixed in
[`protocols/basic-check.json`](../../protocols/basic-check.json); the cases and
the Flask source are in [`tasks/flask-coding/`](../../tasks/flask-coding/).
