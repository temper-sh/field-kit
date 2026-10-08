# Qwen machine study

Help find out how much text Qwen3.8 27B can handle on a 36 GiB Mac, and whether
other engines or larger model files improve coding on Macs with more memory.
The results will inform Temper's presets and memory guidance.

This study needs **Apple M3 or newer, macOS 26.4+, and either 36 GiB or at least
48 GiB RAM**. Measurements on these machines are still pending. The existing
32 GiB results are a reference, not proof that these configurations fit.

## Choose one configuration

The default run uses **Splash Q4 on two coding tasks**, then saves a report,
removes its installation and private model downloads, and stops. Each task has
its own saved checkpoint. Other configurations are separate, explicit choices.

```sh
./field-kit contribute --preview
./field-kit contribute
```

After that run, choose the next unattempted configuration with `--next`, or name
one with `--configuration`, for example:

```sh
./field-kit contribute --configuration splash-q5 --preview
./field-kit contribute --configuration splash-q5
```

On larger Macs, the suggested order keeps Splash/llama Q5, Splash/llama Q6 and
Rapid/vLLM comparisons adjacent. Q8 is optional and must pass its memory check
before any download. On a 36 GiB Mac, `splash-q4-context` runs the optional
filled-context ladder separately from coding.

## Requirements and cost

Field Kit checks your chip, macOS version, memory and free disk before asking
you to run. It also requires at least **27 GiB available to Metal**, Apple's GPU
interface. Enough physical RAM does not always mean macOS allows that much GPU
memory; see [the memory check](#adjust-the-wired-memory-limit) if it fails.

| Configuration | Machine RAM | Download allowance | Free disk allowance |
|---|---|---:|---:|
| `splash-q4` (default) | 36 GiB or 48 GiB+ | 35.9 GiB | 71.9 GiB |
| `splash-q4-context` | 36 GiB | 35.9 GiB | 71.9 GiB |
| `splash-q5` | 36 GiB or 48 GiB+ | 39.0 GiB | 78.1 GiB |
| `llama-q5` | 48 GiB+ | 35.4 GiB | 70.9 GiB |
| `splash-q6` | 36 GiB or 48 GiB+ | 43.1 GiB | 86.3 GiB |
| `llama-q6` | 48 GiB+ | 39.6 GiB | 79.1 GiB |
| `rapid-mlx` | 48 GiB+ | 31.0 GiB | 62.0 GiB |
| `vllm-metal` | 48 GiB+ | 31.0 GiB | 62.0 GiB |
| `llama-q8` (conditional) | 48 GiB+ | 45.3 GiB | 90.6 GiB |

These are conservative allowances for one selected configuration, not measured
disk peaks. Download allowance includes its exact weights plus 16 GiB for
support software. Disk allowance includes two weight-sized sets plus 32 GiB
for installed software, preparation files and evidence. Retained evidence is
limited to 1 GiB per run. Existing shared caches are preserved and excluded
from cleanup; new study models download into the run's private cache. Separate
runs may download the same weights again.

**Requests can still be long.** A coding run permits two four-hour requests.
Its hard limit is 12 hours 10 minutes, plus up to six hours of initial setup.
Each task allows thirty minutes for preparation verification, thirty minutes
for startup, four hours for its request and twenty minutes for grading and
shutdown. Final cleanup allows ninety minutes. These are ceilings, not expected
durations, and chunking does not make the same inference work faster.

The optional context ladder allows up to 15 hours 30 minutes plus initial
setup, stopping at its first unsuccessful point. Its six points each allow
two thirty-minute requests, preparation verification, startup and shutdown.

Follow the [run guide](../START.md) to pause at a task boundary or resume.
Keep the Mac on power and awake, and close demanding applications. One
confirmation covers only the displayed configuration. Failed requests remain
results and are not retried or repaired automatically.

## What runs on your Mac

| Memory | Tests |
|---|---|
| 36 GiB | Choose Splash Q4, Q5 or Q6 coding, or the separate Q4 context ladder. |
| 48 GiB or more | Choose Splash Q4, Q5 or Q6; Rapid MLX or vLLM Metal with the same 4-bit MLX weights; or llama.cpp Q5, Q6 or memory-admitted Q8. |

Q4, Q5, Q6 and Q8 are compressed model builds. Larger numbers generally retain
more precision and need more memory. The study asks whether that extra memory
helps with completed work. The Q8 test is admitted only if its weights leave
at least 4 GiB within the engine's memory budget; actual runtime fit still needs
measurement. Other RAM sizes below 48 GiB are outside this study.

Each coding configuration attempts the same two Flask tasks once. These are
comparisons of complete setups: the model format, template and acceleration
method can differ along with the engine.

## How the results are checked

The two tasks ask for an asynchronous streaming change and a template-decorator
change. Each model receives the same frozen Flask source and submits a patch.
The original tests, independent tests and tests written by the model run
separately in a local macOS sandbox. Review of a returned result does not execute
the generated Python again.

Passing tests still needs code review. One task's tests do not catch a known
cleanup-error weakness; passing them does not establish that the patch is
ready to use or that the model is reliable at coding generally.

The report separates preparation, startup, first output, first answer, total
request time, test results, memory use and swap growth. It uses engine-reported
token counts and timing where available. Streaming rate is labeled separately
because it includes transport overhead and batches of generated tokens.
Incorrect answers keep their timings but do not count as successful work.

### Long inputs on 36 GiB

Splash with Q4 weights tests total windows of **32,768, 65,536, 98,304, 131,072,
196,608 and 262,144 tokens**. A token is a piece of text; the window must hold
the input, conversation and answer together.

Each initial input uses the window minus 5,120 tokens, leaving 4,096 for the
answer and 1,024 for continuation. The engine's own template and tokenizer
prepare the input, and its reported count must agree.

Five facts are spread through the input. The first answer must recover three;
a follow-up must recover the other two with 4,096 tokens of output room still
available. Each request has a thirty-minute limit. The first unsuccessful size
stops larger sizes; the other coding tests may continue if the machine and
shutdown checks permit.

The result identifies the largest successful size, the next unsuccessful one,
and their waiting times and memory use. It tests retrieval at sampled sizes,
not general reasoning over long documents or a universal context limit.

### How this helps Temper

These measurements can improve the wizard's memory estimates and tested
context choices. They keep configured limits separate from measured memory
use. A short coding prompt in a large configured window does not establish
that the model can use the whole window.

Results are reviewed before changing a preset. Different software, templates,
memory limits or machines may change what a measurement supports.

### Tune a profile after the smoke test

After this preset completes a valid request on your Mac, you can use the
[AI tuning prompt](../prompts/tune-machine-profiles.md) to prepare a separate,
bounded comparison and a local Temper catalog. A profile can vary context,
KV cache precision, batch settings and memory limits supported by its engine.
The choice uses correctness, latency and measured memory together.

This follow-up needs Temper's authoring catalog and a matching executable with
profile support; the study's pinned alpha.11 host predates that schema. The
prompt guides an AI assistant through the required inputs and exact run plan.
Finish or recover an unfinished study before tuning, keeping its first attempts
and settings intact. You choose the additional work and its limits separately.
The regular contributor command does not start tuning or export profiles.

### Memory measurements

Splash runs record native Metal allocations after model readiness, every five
seconds during requests, and before shutdown. The report shows the loaded
allocation and the engine's lifetime peak, which includes loading and both
requests at a context point. The native peak captures transients between polls.

These counters overlap process RSS and physical footprint; keep them separate.
The report also retains per-process observations and swap growth. Configured
memory limits are allowances, and none of these measurements alone establishes
minimum machine RAM. Other engines currently retain process counters only.

Unavailable native observations stay unmeasured; partial peaks are lower
bounds. An instance change or counter reset invalidates the native memory
comparison. A failed native status read does not erase a completed task or its
timing; the ordinary process, swap, thermal and ownership guards still apply.
Revision 5 reports did not capture native Splash allocations and cannot recover
them after shutdown.

## Failures, stopping and cleanup

Startup failures, memory errors, incompatible formats, truncated answers and
invalid patches remain separate results. Stopped requests retain partial
answers. A safe engine failure permits the next test; resource-limit stops,
thermal or CPU throttling, excessive swap, missing process counters or uncertain
process ownership end further measurement.

The engine's memory cap is the lowest of 75% of RAM, the effective Metal budget
and 96 GiB. Separate limits are 2 GiB for the router, 2 GiB for Splash's
frontend, 4 GiB for vLLM's frontend and 256 MiB for its Python resource tracker.
Additional swap is limited to 512 MiB. Process peaks are reported separately;
adding them would not establish total physical memory use.

Field Kit removes this run's model cache and engine installation after Temper
confirms shutdown. Pausing between tasks keeps only that configuration's files
for resumption. Failed cleanup leaves the installation for recovery and blocks
another configuration. Reports stay local. See
[interruption and recovery](../START.md#stop-or-continue).

## Exact test configuration

This guide describes `qwen-machine-study@6`, using signed Temper
`0.1.0-alpha.11`. The software and tasks are fixed so returned runs can be
compared.

| Component | Selection |
|---|---|
| Splash | 1.1.0; GGUF UD-Q4/Q5/Q6, Frog v22.5 template, external DFlash2 draft |
| Rapid MLX | 0.15.2; native MLX 4-bit weights and template; no draft |
| vLLM Metal | 0.30.0 with vLLM 0.30.0+cpu; the same MLX weights and template; no draft |
| llama.cpp | b11205; GGUF UD-Q5/Q6/Q8, Frog v22.5, embedded MTP |
| Router | llama-swap v260 |
| Coding request | 118,000-token total window; medium reasoning; seed 17; temperature 1; top-p 0.95; top-k 20 |

The [execution locks](../../catalog/packages/qwen-machine-study@6/executions/)
record exact files, software and settings; the
[workload](../../catalog/packages/qwen-machine-study@6/workloads.json) defines
the tasks and checks.

For coding, the output allowance is the total window minus the engine's prompt
count. Counts must agree with the delivered response. A fresh engine starts
for each task without warm-up generation. File verification may warm disk
caches, so these runs do not measure startup from a cold disk cache.

## Adjust the wired-memory limit

A Mac with enough physical RAM can still fail the GPU memory check:

```text
requires at least 27648 MiB wired limit, found 24576
```

Temper reads the memory budget reported by Metal. A system setting can override
macOS's default; an override of zero leaves the choice to macOS. Field Kit
reports both values and does not change them.

For this study, use **75% of physical RAM, capped at 96 GiB**. That is the
highest engine memory budget the study permits; a higher wired limit does not
increase that budget. On a **36 GiB Mac, use 27 GiB (27,648 MiB)**, leaving
9 GiB outside the GPU wired-memory cap for macOS, the router and other work.
The admission minimum is 27 GiB.

The [macOS GPU memory override](https://ml-explore.github.io/mlx/build/html/python/_autosummary/mlx.core.set_wired_limit.html)
requires an administrator password and applies system-wide. Wired memory stays
in RAM. There is no universally safe maximum determined by RAM alone; other
processes also need memory. The value below follows this study's existing
budget, and actual fit is still measured during the run. Close other
memory-heavy applications before starting.

These steps are for a Mac with 36 GiB or at least 48 GiB physical RAM. Make the change
before starting a new study; keep the limit unchanged during an unfinished run.

1. Run this once in Terminal, **before changing the limit**. It reads the
   machine's RAM, calculates the study ceiling and prints both commands:

   ```sh
   if field_kit_wired_before=$(sysctl -n iogpu.wired_limit_mb) &&
      field_kit_ram_bytes=$(sysctl -n hw.memsize); then
     field_kit_wired_max_mib=$((field_kit_ram_bytes / 1048576 * 3 / 4))
     if [ "$field_kit_wired_max_mib" -gt 98304 ]; then
       field_kit_wired_max_mib=98304
     fi
     printf 'Current override: %s MiB\n' "$field_kit_wired_before"
     printf 'Study ceiling: %s MiB\n' "$field_kit_wired_max_mib"
     printf 'Apply: sudo sysctl iogpu.wired_limit_mb=%s\n' "$field_kit_wired_max_mib"
     printf 'Rollback: sudo sysctl iogpu.wired_limit_mb=%s\n' "$field_kit_wired_before"
   fi
   ```

   **Save the printed rollback command** so it remains available if you close
   Terminal. A current value of `0` means macOS chooses the limit automatically;
   preserve that raw value rather than Metal's reported budget. If either `sysctl`
   read fails, stop and send the error to the maintainer.

2. Only if the effective Metal budget fails the study's 27 GiB requirement,
   consider the printed **Apply** command. Do not lower an existing positive
   override that already exceeds the proposed value. For a 36 GiB Mac, this is:

   ```sh
   sudo sysctl iogpu.wired_limit_mb=27648
   sysctl -n iogpu.wired_limit_mb
   ```

   The sysctl readback should match the requested override: `27648` on a 36 GiB
   Mac. This alone does not prove that Metal's effective budget changed; the
   preview in the next step reads that budget again. If the change is refused,
   send that error to the maintainer before continuing.

3. From the Field Kit folder, check the plan again:

   ```sh
   ./field-kit contribute --preview
   ```

   If the check passes, start the study and confirm its plan. If you explicitly
   chose a development host, keep the same `--temper` argument. The engine
   limit is the lower of 75% of RAM,
   the observed Metal budget and 96 GiB. A 36 GiB Mac has a 27 GiB study ceiling;
   its observed budget may be lower.

4. **Roll back after the study finishes and cleanup completes.** Run the exact
   rollback command saved in step 1, then read the setting again. If the
   original value was `0`, use:

   ```sh
   sudo sysctl iogpu.wired_limit_mb=0
   sysctl -n iogpu.wired_limit_mb
   ```

   The readback should match the original value. If it was a nonzero number,
   restore that exact number instead of `0`. If you decide not to start the
   study, roll back immediately. For an interrupted study, follow the
   [recovery instructions](../START.md#stop-or-continue) before changing its
   recorded conditions.

   Field Kit does not restore this manual change. These commands do not persist
   the override across reboots; a reboot discards this temporary override, but
   an existing startup configuration may apply its own value. The saved command
   restores the exact prior setting without rebooting.

## Review a result

Use the source that produced it:

```sh
./field-kit witness --input /absolute/path/to/result.json \
  --package catalog/packages/qwen-machine-study@6/package.json
```

The reader checks frozen inputs, session/attempt references, matrix order,
settings, tool submissions, reported test groups, native timing arithmetic and
context answers. It does not attest the machine or rerun candidate code.
Inspect retained patches, failure details and measurement plausibility before
using a result in a model card. Equal RAM does not imply equal performance.
