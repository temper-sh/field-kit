# Research method

Seek a small set of useful configurations for this machine and job. Consider
quality, time to completion and memory together. Native rates explain
performance; they do not establish quality.

## Smoke, shortlist, improve

Start with cheap representative one-shot work. Tool smoke answers an interface
question; plain chat smoke fits models without tools. Engine screening compares
the same answer at short and populated input. Include a small coding/domain task
when relevant.

Record output, native prefill/generation rates when available, elapsed time,
actual input/output tokens and loaded/peak memory. Missing timings remain
missing; first-output latency is not prefill time.

Advance promising candidates, including near ties. One shot supports screening.
Repeat when uncertainty could change a decision. Diagnose load/parser/template
failures cheaply; unsupported interfaces are not quality scores.

Research relevant controls, state the expected mechanism, tune, and smoke again.
Examine useful interactions. Match workloads and disclose reasoning, output
allowance, sampling, cache state and effective acceleration.

Select provisional winners by use case. Usually one is enough. Keep another
speed, quality or memory option only for a meaningful practical gain beyond
plausible measurement variation. There is no universal percentage threshold.

## Tune runtime settings

Write the tuning plan before the first run. Sort the settings into three kinds:
fixed, which must keep their value ("context stays 65,536"); bounded, which may
vary up to a limit ("context up to 65,536"); and tunable. Declare fixed ones
with `study init --fixed`; `--max-context` and the other approval limits are
bounds, not fixed values. Add the variants in order, the budget and the stop
rule.

Give each trial a decision to inform. Before it runs, write the hypothesis, the
observation that would support or contradict it, and what you would do
differently afterwards. A trial that cannot change a choice is not worth its
time.

Isolate effects first: change one setting at a time and send the same request
bytes. Then test plausible interactions, such as threads with batch size, as
planned comparisons of their own. Freeze each comparison before it runs, and
let the next one follow the evidence. Run low-risk settings first. Try a
riskier memory placement only after a smaller safe variant has run, and never
repeat a resource failure unchanged.

Reserve budget at the start for confirmation: a matched baseline and finalist
pair, and completed work, unused where a quality conclusion is intended. Before
committing to a long coding task, check cheaply that the thinking budget and the
move to a tool call behave as expected.

Find where the time goes before choosing a setting: startup, prefill, the
number of generated tokens and the decode rate. Report them separately.
Startup depends on the file cache (2 s warm versus 78 s on a cold SSD read in
one Flint study). A reused prompt prefix saves only part of the prefill:
measure what remains (newly read tokens and their time) before ruling out
prefill tuning. A prompt change that shortens wall time may only have changed
the token count or met a faster decode phase; do not credit it with better
reasoning.

A short screen can hide a sustained effect. If real work runs at another rate
than the screen, freeze a timing-only replay of that request: the same
messages, no prompt cache, a forced long output. Compare native time checkpoints
across variants, not only whole-response averages; `study compare` shows the
decode rate in each quarter of a response. In the Flint study a
512-token screen showed no thread difference, but over 4,096 tokens eight
threads averaged 21 tokens/s, falling from about 39 to 11, while four averaged
39. With MoE expert layers on the CPU of an Apple chip, start decode threads at
the performance-core count; llama.cpp sets no CPU affinity there.

With greedy sampling, compare output bytes as well. Identical text supports a
controlled timing comparison for that sample; it does not show that quality is
the same in general. Different text means the setting also changed the computed
numbers, as larger batches did.

Explain a surprise before tuning around it. Take a one-second profile (macOS
`sample`) during the slow phase, and keep profiled requests out of speed
comparisons. Read the engine's warnings, and the pinned source of a control
whose meaning matters. Confirm in the stream that a control works: llama.cpp's
reasoning budget limits each thinking block and starts again on a new one, so
it does not cap total thinking. Native counters show whether a setting took
effect, such as zero reasoning tokens with thinking off.

Repeat the finalist and the baseline as a matched pair, then confirm on short
completed work before a large task. When the results are close or drift is
suspected, run the pair again in reverse order: a warm file or prompt cache, or
a changing thermal state, must not become a tuning recommendation. The same
patch in half the time is a runtime result. Stop a control early once its mechanism is clear and record
the early stop; it is neither a failure nor a timeout. Spend remaining budget
on held-out work, not on another grid.

Keep prompt treatments, such as a system-prompt contract, apart from runtime
settings: freeze their text before any output, label them and compare them at
an unchanged runtime. Record power source and low-power mode with speed
comparisons.

## Completed work

One response is sufficient for smoke. Stronger quality conclusions require
finished-task evaluation using an established harness and ordinary project
tests. Allow bounded feedback/repair; preserve first attempts and final results.
Do not repeat a full workflow at every tuning point.

Freeze each configuration before the quality attempts that judge it. Compare
requested behavior, regressions, source quality, meaningful tests,
remaining defects, repair effort and elapsed time. Submission, zero exit status
and visible tests do not replace review. A missing submission marker does not
erase useful code.

Inspect bounded attempts that do not finish. Keep quality, completion, harness
behavior and measurement integrity separate. When a strict checker rejects the
format, inspect the content separately and report both verdicts. Quality guides selection; it is
not a universal gate for resource/cache investigation. Use suitable unused work
when selection needs independent evidence, without locking a candidate after
one confirmation case.

## Deep tuning and catalog evidence

Investigate selected candidates' useful limits:
- Populated input, output reserve and continued interaction.
- Cold prefix, append reuse and early edits that invalidate cached content.
- Relevant cache precision/reserve, batch, speculation and engine settings.
- Correctness, latency, native rates, loaded/peak memory, swap and heat.

Configured window, occupied context and tested continuation room are distinct.
Allocation alone does not prove populated capacity. Wrong retrieval does not
prove an allocation boundary.

After a healthy pass, choose the next useful extension within approval. Use
coarser steps with headroom and closer steps near a meaningful boundary. Stop
when answered, further work has little value, a real constraint is reached or
budget ends. Name which occurred. Report the best supported point and remaining
uncertainty; diagnostic caps/deadlines are not product limits.

## Evidence consumers

Field Kit retains exact measurements, configurations, outputs, reviews and
important failures. Name memory counter, units and workload with loaded/peak
values. Wired growth includes other apps; process footprint can omit GPU
allocations. Loading mode moves weights between counters: with mmap the
footprint omits mapped weights; direct loading counts them there while wired
growth stays the same. Do not sum overlapping counters. Disclose sampling
gaps/load.

Local comparisons may guide the study. Publishing comparisons across machines
and changing Temper's catalog are separate actions, outside the study.
