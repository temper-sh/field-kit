# AGENT.md — runbook for an AI agent driving this probe

You are driving a **measurement instrument**. The humans on both ends want
honest numbers from this machine, not a probe that "went well". Your job is
to keep the probe running and report what actually happened; it is never to
make results look better.

## The one rule that overrides everything

**Heal the environment, never the measurement.**

- A failing check is a **result**. Record it, continue. Do not retry it
  until it passes, do not adjust flags, do not edit any file in `stack/` or
  in this kit to make it pass. The one sanctioned way to change what is
  being measured is `./probe.sh tune` (its own section below) — explicit,
  recorded, and followed by a re-measure.
- An engine crash during the fit soak **is the datapoint the soak exists to
  catch**. Do not restart-and-hope; the stage records it and continues.
- Environment problems are yours to fix: a full disk, a stuck download, a
  port conflict, Homebrew complaining, Wi-Fi dropping. Fix those, then
  record every fix with:

```bash
./probe.sh deviation "what you did and why"
```

An unrecorded intervention silently poisons the dataset this probe feeds.
If you fixed something — even something trivial — log it. No exceptions.

## How to run

```bash
./probe.sh preflight    # needs nothing fetched; stop here on a below-min verdict
FIELD_KIT_STACK_REPO=<url-or-path> ./probe.sh fetch
./probe.sh install      # asks keep-or-restore + consents — see below
./probe.sh verify
./probe.sh fit
./probe.sh perf         # optionally: perf --ab (extra ~16GB — human consent)
./probe.sh report
./probe.sh cleanup
```

Each stage prints exactly one machine-parseable outcome line:

```
FIELD-KIT RESULT <stage> <ok|fail> <detail>
```

A `fail` RESULT is still a completed stage — the report has the evidence.
Continue to the next stage unless the process itself errored out. Exit
codes: 0 = the stage ran to a recorded verdict; non-zero = it could not run
(that is yours to diagnose — environment, usually).

## Decisions that are the human's, not yours

The kit blocks on these by design; in a non-interactive session it refuses
rather than assumes. Ask the human, then pass their answer:

- **keep-or-restore** → `FIELD_KIT_CONTRACT=keep` or `restore`
- **stage costs** (the ~15GB install, the ~16GB `--ab` arm, the soak's
  machine-time) → `FIELD_KIT_YES=1` only after they consented to the run
- **anything `sudo`** — the kit and the stack only ever *print* sudo
  commands. Relay them to the human verbatim. Never execute them yourself,
  even if you have the permission to.

## Tuning in flight

When a stage's result points at a known tuning lever — the classic case is
fit-soak aborts, whose documented ladder is lowering the GPU utilization
fraction — the probe can be tuned mid-visit instead of ending the session
with only a failure:

```bash
./probe.sh tune --fraction 0.74     # the FINDINGS #16 lever
./probe.sh tune candidate.yaml      # a manifest the owner sent
./probe.sh fit                      # ALWAYS re-run the stage that prompted it
```

Rules: **propose it, get the human's yes, run it, re-measure.** Only these
levers — inventing other flag edits stays forbidden. Prior results are
never deleted; every measurement line already records which tune it ran
under, so iterations sit side by side in the report. A tune that the
stack's config gate rejects restores the previous manifest and is itself
recorded. Tuning is not an environment fix — it does not go through
`deviation`, it has its own section in the report.

When comparing two configurations, hold the soak shape constant: a fit
run with `FIELD_KIT_SOAK_EXTRACT=1` and one without are different
experiments (the RESULT line carries `shape=` so they can never be
silently mixed). Change one thing per re-measure.

## Reading the evidence

You are expected to reason about results, not just relay them. The mental
model that makes the numbers interpretable:

**The GPU memory wall.** Everything wired must fit under the machine's
wired limit (the `wall=` in every conditions line):

```
fraction × Metal-device-memory (~81% of RAM)   the coder's allocation
+ whichever helper models are currently wired   transient co-tenants
+ ~1–2 GB the OS itself keeps on the GPU        not in gpu-budget's math
≤ the wall
```

gpu-budget's verdict is the **prediction**; the fit soak is the test. When
they disagree, the disagreement is the datapoint — do not smooth it over.
Known blind spot: its spike allowance understates long-context prefill
spikes, and it omits the OS floor, so its "a fraction ≤ X covers it" line
runs ~0.03 optimistic. A bigger co-tenant lowers the survivable context:
witnessed on a 32GB M5, a 0.6GB co-tenant moved coder aborts to ≥12k-token
prompts, a 3.2GB one to ≥6k. The abort window is each turn's prefill with
the co-tenant still wired.

**Failure signatures — check these before concluding anything:**

- Broken stream + `recovered from upstream disconnection` in llama-swap.log
  + `Insufficient Memory (kIOGPUCommandBufferCallbackErrorOutOfMemory)` in
  `fit-libcabi.txt` or the upstream log = **GPU-OOM abort**. The fraction
  ladder is the lever.
- **Zero crash reports does not mean zero crashes** — macOS coalesces
  repeated identical crashes. That is exactly why the kit watches four
  channels (broken turns, .ips reports, swap-log recoveries, libc++abi).
- A check's own FAIL text can misdiagnose: "no tool_calls delta — parser
  not bound" also appears when the engine **died mid-stream**. Read
  llama-swap.log before blaming the parser.
- A helper answering **instant 500s** (~3ms) after a coder death is
  llama-swap's error state, not a broken model — it clears on ttl expiry
  or the next real request.
- llama-swap **never retries a failed preload** (and a fresh instance can
  crash its one attempt into the previous instance's unreclaimed GPU
  memory). The probe nudges automatically; if residency still never comes,
  one small chat request is the lever.

**Reading the perf numbers.** Decode on a dense model is memory-bound:
`tok/s × weight-bytes ≈ bandwidth` is the ceiling, and the report says so
("dense-read ceiling"). At-ceiling with speculation configured means the
speculation is silently not engaging — an engine finding worth a
conclusion, invisible to any prefill-based gate. Decode spread: flat-but-low
is systematic (engine lane, config); declining with `therm=ok` is the
stack's idle-decay signature (#17); declining with `therm=cpulimitNN%` is
thermal, and battery power (`power=batt`) throttles fanless machines first.

**Write conclusions into the report.** Analysis that lives only in your
chat never reaches the owner. After a failed-then-tuned-then-re-measured
sequence, or any comparison, record what the numbers showed:

```bash
./probe.sh conclude "0.85 aborts at >=12k prompts with the reranker wired (3/66 turns); 0.75+extractor aborts from 6k (13/43). Consistent with the wall model; extractor-safe fraction is <=0.74."
```

Conclusions are labeled as interpretation and stamped with conditions.
State the numbers that support each claim; a conclusion the report's own
data cannot back does not belong there.

## Stay idle during timing windows

While `verify`, `fit`, or `perf` is measuring, run **nothing else** — no
searches, no file indexing, no builds, no chatty tool loops. This stack's
own history includes a measurement arm invalidated purely by the harness's
background file churn evicting model weights (its FINDINGS #19). Start the
stage, then wait for its RESULT line.

## Run-only

Treat every file in this kit and in `stack/` as read-only. You run scripts;
you do not edit, "improve", or regenerate them. (The stack's history also
includes an agent overwriting the very test suite it was asked to run, then
reporting results from its own invention. The failure mode is quiet. Don't
be that agent.)

## Known failure modes, and what each one means

| symptom | meaning | your move |
|---|---|---|
| preflight fails on swap/disk/port | environment | fix (quit apps, free disk, free the port), deviation-log, re-run preflight |
| `brew install` failures during install | environment | fix per brew's message, deviation-log, re-run install |
| download stalls mid-pull | network | wait/retry once network is back, deviation-log |
| verify: check 9 "DEGENERATE SCORES" | a broken reranker GGUF conversion | record; this is a finding about the model repo, not your machine |
| verify: check 12 no `tool_calls` delta | the engine's streaming tool-call path is broken — the single worst failure for agent use | record; do NOT try other flags |
| fit: broken turns / new crash reports | GPU memory arithmetic failed on this hardware — **the probe's most valuable possible result** | let the stage finish; make sure the RESULT and report capture it |
| perf numbers with a swap warning | measurements polluted by memory pressure | note it; if the human can quiet the machine, re-run `perf` and deviation-log the retake |
| decode declining across fit turns with `therm=cpulimitNN%` | thermal throttling (fanless Mac, or on battery) — not a stack problem | note it; if the human can plug in / cool down, re-run `fit` and deviation-log the retake |
| decode declining across fit turns with `therm=ok` | possibly the stack's idle-decay signature (its FINDINGS #17) | record it — this is a valuable result, not something to fix |
| fit: broken turns whose prompt sizes cluster above a threshold | GPU-OOM at that context depth with the current co-tenants — the wall model above | propose the fraction tune (or the no-extract shape) to the human, re-run, `conclude` the comparison |
| perf headline says decode sits at the dense-read ceiling, but the manifest configures speculation | the engine is silently not speculating — a real engine finding | record a `conclude` with the numbers; suggest `perf --ab` if not yet run (the other engine's speculation may work) |
| coder never becomes resident though llama-swap answers | failed preload stuck in error state (llama-swap does not retry) | the probe nudges automatically; if it still stalls, send one small chat request, then deviation-log it |
| llama-swap won't start / port taken | environment | `./probe.sh serve-stop`, free the port, retry |

When something matches nothing here and blocks a stage from *running* at
all: stop, summarize for the human, include `probe-results/llama-swap.log`
tail — do not improvise fixes inside the stack.

## Finishing

`./probe.sh report` prints the paste-block. Show it to the human; they
decide to send it. Then `./probe.sh cleanup` executes their keep-or-restore
choice — confirm with them before running it, and if the choice was
`restore`, the printed final `rm -rf` of this kit directory is theirs to
run, not yours.
