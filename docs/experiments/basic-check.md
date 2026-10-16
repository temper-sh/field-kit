# Basic check

**Question:** does this preset fit this Mac, and how fast is it on a fixed
coding task?

The check measures a preset exactly as Temper installs it on your Mac. It is
the first step for every preset: later experiments compare engines, model
builds and settings, but they start from these numbers.

## What runs

1. Temper compiles the preset for this Mac's memory from its signed catalog.
   Field Kit passes the result to Temper unchanged.
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
| Start time | From starting the engine until the model answers its health check |
| Engine after load | Memory of the engine process after the model loaded, before the request |
| Engine peak | The engine process's highest memory during the case |
| Splash native memory | For Splash presets: the engine's own Metal allocations; they overlap the process numbers, so do not add them |

Field Kit reports a rate only when the engine reports it. It never estimates
one.

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

## Which presets

The check covers Temper's **General** chat presets. Specialist presets have
small context windows or do not write text. The preview shows which presets
can run on your Mac and why the others cannot.

Before it downloads anything, Field Kit applies a memory floor: the model files
plus 4 GiB must fit in three quarters of the Mac's memory and in its GPU memory
limit. This is a floor, not a prediction that the preset fits.

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
