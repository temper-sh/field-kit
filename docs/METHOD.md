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

## Completed work

One response is sufficient for smoke. Stronger quality conclusions require
finished-task evaluation using an established harness and ordinary project
tests. Allow bounded feedback/repair; preserve first attempts and final results.
Do not repeat a full workflow at every tuning point.

Compare requested behavior, regressions, source quality, meaningful tests,
remaining defects, repair effort and elapsed time. Submission, zero exit status
and visible tests do not replace review. A missing submission marker does not
erase useful code.

Inspect bounded attempts that do not finish. Keep quality, completion, harness
behavior and measurement integrity separate. Quality guides selection; it is
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
allocations. Do not sum overlapping counters. Disclose sampling gaps/load.

Results owns maintained public comparisons across layouts and machines.
Field Kit may show local comparisons to guide the study. Temper owns the
installable catalog; publication and catalog mutation are separate actions.
