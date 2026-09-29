# Qwen machine study

Find how much context Qwen3.8 27B can use on a 36 GiB Mac with Splash, and which
engines and larger model builds complete useful coding work on larger Macs.

Package: `qwen-machine-study@4`. A compatible development Temper build is
required; see [setup and development](../DEVELOPMENT.md). The software and
workload are frozen. First study runs on 36 GiB and 48 GiB+ Macs
are still pending. Earlier measurements remain attached to their producing
source and are not relabeled as this study.

## What runs on your Mac

| Physical RAM | Splash | Rapid MLX | vLLM Metal | llama.cpp |
|---|---|---|---|---|
| 36 GiB | UD-Q4 coding reference and filled-context ladder; UD-Q5/Q6 fit and coding | — | — | — |
| 48 GiB+ | UD-Q4/Q5/Q6 fit and coding | Shared MLX 4-bit, coding | Shared MLX 4-bit, coding | UD-Q5/Q6 coding; UD-Q8 candidate after memory admission |

32 GiB remains existing reference evidence. There is no intermediate bucket.
Each coding cell runs the same two Flask tasks once. Larger quants retain more
weight precision; this study measures whether that improves completed work and
what it costs. Q8 admission requires its weights plus 4 GiB minimum headroom to
fit the approved engine budget. Admission alone does not establish runtime fit.

## Requirements and cost

Apple M3 or newer, macOS 26.4 or newer, and an effective Metal memory budget of
at least 27 GiB are required. Field Kit checks physical RAM, the chip, OS, budget
and free disk before consent. It does not change system memory settings.

| Limit | 36 GiB route | 48 GiB+ route |
|---|---:|---:|
| Download ceiling | 86.1 GiB | 188.4 GiB |
| Free disk required | 129.3 GiB | 233.7 GiB |
| Retained evidence ceiling | 3 GiB | 3 GiB |
| Coding cells | 3 | 8 |
| Filled-context points | Up to 6 | — |

These conservative bounds do not assume cross-preset download reuse or shared
file storage. A coding cell permits six hours of preparation, two four-hour
requests, two thirty-minute startups and grading/cleanup: at most 920 minutes.
Each context point permits 440 minutes; final cleanup permits 90 minutes.
Initial preparation has its own six-hour limit. The shared measurement ceiling
is 7,450 minutes. These are hard bounds, not expected completion times. A
particular route can finish much earlier, including after failed admission.

Use [the run guide](../START.md) to preview and start. Connect power, prevent
sleep and close demanding applications. One confirmation covers the selected
finite matrix. No failed model request is retried or repaired automatically.

## Frozen compositions

| Component | Exact selection |
|---|---|
| Splash | 1.1.0; GGUF UD-Q4/Q5/Q6, Frog v22.5, external DFlash2 draft |
| Rapid MLX | 0.15.2; native MLX 4-bit weights and template; no draft |
| vLLM Metal | 0.30.0 with vLLM 0.30.0+cpu; the same MLX weights/template; no draft |
| llama.cpp | b11205; GGUF UD-Q5/Q6/Q8, Frog v22.5, embedded MTP |
| Router | llama-swap v260 |
| Coding request | 118,000-token total window; medium reasoning; seed 17; temperature 1; top-p 0.95; top-k 20 |

The [execution locks](../../catalog/packages/qwen-machine-study@4/executions/)
retain artifact revisions, software dependencies and all engine settings.
The output allowance is the total window minus the engine's native prompt
count, matching the completed Splash comparison. Token counts must agree with
the delivered response. A fresh engine starts for each coding task, with no
warm-up generation. Verification may warm filesystem caches; storage-cold
startup is not claimed.

Different formats, templates and draft mechanisms make these comparisons of
usable compositions. They do not isolate engine implementation as the cause of
a performance or quality difference. The previous Q4 Splash/llama decision is
already established; this matrix does not repeat that contest.

## Coding correctness and performance

The [frozen workload](../../catalog/packages/qwen-machine-study@4/workloads.json)
reuses `async-stream` and `template-decorators`, the original Flask source
packet, restricted `submit_patch` tool contract and independent oracles. Each
first submission is retained. Original tests, independent tests and the model's
new tests run separately in a local macOS sandbox using a locked evaluator.
Returned-result review never executes generated Python.

Passing tests is a candidate for source review. The retained async task's
cleanup-error caveat remains outside the frozen oracle; this revision does not
silently strengthen the old task or claim broad coding qualification.

The report separates preparation, model startup, first output, first answer,
request time, test outcomes, engine RAM and swap growth. Prompt processing
(prefill) and generation rates use native counters when available; missing
counters remain unmeasured. Observed streaming output rate is labeled separately
and includes transport overhead and speculative batches. Incorrect answers
retain their timing but do not count as successful completed work.

## Filled context on 36 GiB

Splash UD-Q4 tests total windows of **32,768, 65,536, 98,304, 131,072, 196,608 and
262,144 tokens**. Each point fills the initial prompt to window minus 5,120,
reserving 4,096 output tokens and 1,024 for continuation. The native template and
tokenizer construct the prompt, and the response must confirm the count.

Five ledger facts are distributed across the input. The initial answer must
recover three correctly; a follow-up must recover the other two while retaining
4,096 output tokens of room. Each request has a thirty-minute limit. The first
unsuccessful point stops larger context points; the larger-quant coding cells
can still run if shutdown and machine conditions permit.

The result gives the largest successful input and total window, the next
unsuccessful point, and latency/memory observations. Untested sizes stay unknown.
This is a sampled bracket under declared limits, not a universal context ceiling
or a claim about long-document reasoning.

## Evidence for Temper's preset cards

The study also supplies the evidence needed to improve the wizard's memory
guidance and tested-context choices. Each configured cell retains Temper's
context identity, total window, output allowance and explicit memory limit.
The result includes the Mac's chip, RAM and effective Metal budget, per-role
peak RSS and footprint, swap growth, first-output/answer latency and failures.
Configured limits stay separate from measured peaks; process peaks are not
summed into an alleged physical-memory total.

The wizard currently estimates from weight sizes and declared caps. These runs
can expose missing KV-cache or runtime overhead in that estimate. A reviewed
successful filled-context point can support a catalog `context_findings` entry
for the exact tested composition and machine. A coding pass at 118,000 configured
tokens does not establish filled-context capacity at that size.

Field Kit does not change the catalog or automatically recommend a preset.
Review returned evidence before updating Temper's authored cards and estimates.
Different software, templates, memory caps or machines need an explicit
assessment; a preset's name alone is not a match.

## Failures, stopping and cleanup

Startup failures, out-of-memory errors, incompatible formats, truncated answers
and invalid submissions remain distinct observations. A stopped request keeps
its partial response. A safe engine failure permits the next matrix cell;
resource watcher stops, thermal/CPU throttling, excessive swap, missing counters
or uncertain process ownership end further measurement.

The engine limit is the minimum of 75% of RAM, the effective Metal budget and
96 GiB. The router has a separate 2 GiB limit; Splash's frontend has 2 GiB,
vLLM's frontend 4 GiB and its Python resource tracker 256 MiB. Additional swap
is limited to 512 MiB. Per-process RAM is retained separately, not summed into
a claimed physical-memory total.

Temper verifies exact process identities, loopback listeners and shutdown.
Field Kit removes its installation only after shutdown is confirmed. Failed
cleanup retains the installation for recovery. Reports and evidence stay local.
See [interruption and recovery](../START.md#stop-or-continue).

### Adjust the wired-memory limit

A Mac with enough physical RAM can still fail the GPU memory check:

```text
requires at least 27648 MiB wired limit, found 24576
```

The current Temper host reads Metal's recommended working set directly. Its
`wired_limit_mib` field is the effective budget; `wired_limit_override_mib`
records the raw sysctl setting separately when available. A zero override means
macOS chooses the budget. Earlier hosts estimated a default from physical RAM;
their historical observations retain that label and value.

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
   preserve that raw value rather than Field Kit's estimate. If either `sysctl`
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

   Use the same `--temper /absolute/path/to/temper` argument as in the
   [development instructions](../DEVELOPMENT.md). If the check passes, start
   the study and confirm its plan. Its engine limit is the lower of 75% of RAM,
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
  --package catalog/packages/qwen-machine-study@4/package.json
```

The reader checks frozen inputs, session/attempt references, matrix order,
settings, tool submissions, reported test groups, native timing arithmetic and
context answers. It does not attest the machine or rerun candidate code.
Inspect retained patches, failure details and measurement plausibility before
using a result in a model card. Equal RAM does not imply equal performance.
