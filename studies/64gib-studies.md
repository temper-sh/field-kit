# Studies for the 64 GiB M5 Pro

Prepared 21 October 2026. These briefs carry the owner's research decisions;
they are not new measurements or an authorization to start model runs.
Use installed Temper and the [shared method](../docs/METHOD.md).

## Order and questions

| Order | Study | Decision to produce |
|---|---|---|
| 1 | [Splash context and cache](splash-context-cache.md) | Finish the Q4 layout: practical context, cache behavior, memory allowance and useful alternatives. |
| 2 | [Splash Q5/Q6](splash-quants.md) | Decide whether better completed code justifies a larger quant's latency and memory. Owner-side comparative review. |
| 3 | [Muse and Gemma engines](muse-gemma-engines.md) | Compare promising MLX compositions with the best existing llama.cpp choices on this machine. |

Keep Muse, Gemma 26B-A4B and Gemma 31B as separate studies, with separate
winners. The earlier M5 / 32 GiB work supplies controls and known traps. It
does not supply M5 Pro timing measurements. Reuse any exact, applicable
measurements from the contributor's machine too; identify them in the brief.
Do not make an unresolved optional engine arm block the others.

The tester runs, diagnoses, tunes and preserves outputs. We compare completed
work centrally, using the returned source and test evidence. Preliminary
tester judgments help diagnosis; they do not settle the quality ranking.
Results owns maintained comparisons across machines; these files own the
research questions and the evidence needed to answer them.

## Shared execution and return

Prepare exact locks, workload inputs and a concrete material/time allowance
using [Temper](../docs/TEMPER.md) and [Field Kit](../docs/TOOLS.md). Resolve
candidate identities before downloading weights. Include software, target,
draft, template, helper processes, caches and existing-harness dependencies.
Use one resident model at a time. Do not upgrade Temper implicitly.

Allow enough time for healthy populated requests: Muse has already taken
25.71 minutes for a cold 122,880-token input, and Gemma 31B's complete 40k
context sequence took about 30 minutes. A universal ten-minute deadline would
cut off useful work. Choose request/trial/session allowances from the actual
question and observed timings, reserve room for diagnosis, and return partial
evidence when the allocation ends. Time limits are not context-capacity claims.

Use generated `engine`, `context` and `cache` workloads and the existing
command-harness interface. Add ordinary JSON requests for task-specific work;
do not recreate the old study runners or oracle codebase. `--records` controls
records, not native tokens. Adjust from observed usage and retain the final
input. A `max_tokens` allowance does not prove that much output was generated.
Measure a real long generation and a subsequent lookup when claiming output
room. Review semantic answers separately from strict JSON formatting.

For each decision retain the exact settings, actual input/output counts,
native prefill/generation rates when available, cold/append/edit service time,
loaded/peak memory with counter and units, swap/thermal state, outputs and
failure meaning. Missing native timing stays missing. Include frontend/draft
memory where available without summing overlapping counters.

Use background jobs with blocking `study wait`; internal monitoring handles
sampling. At an idle boundary export an intermediate result for owner review,
then continue independent context/cache work within scope. Include every
defect and local workaround through [repair reporting](../docs/REPAIR.md).
Return selected locks, concise conclusions, decisive runs and unresolved
questions. A quality review pending here does not stop resource investigation.

## What can transfer to 48 GiB?

Yes: propose fit and settings from the same execution and its measured memory,
with OS/application headroom. Do not scale context by 48/64 or transfer speed
numbers across chips. A different cache precision, draft, engine, quant or
context allocation needs its own memory basis. Use three clear descriptions:
measured on 48 GiB, projected to fit 48 GiB, or not yet established.

Starting evidence, not a new cross-model benchmark:

| Configuration and workload | Machine | Loaded → peak, GiB | Current 48 GiB inference |
|---|---|---:|---|
| Splash Q4, 131,072 allocated, thinking-on coding, maximum occupied context 59,744 | M5 Pro / 64 GiB | 19.22 → 21.63 system wired growth | Strong fit expectation at this observed occupancy; full 128k remains to be measured. |
| Muse Q4 + DFlash2, 131,072 allocated, 122,880 cold input plus output/continuation | M5 / 32 GiB | 18.38 → 19.62 engine RSS | Strong fit expectation for this already-tested workload. |
| Gemma 26B Q4 plain, 131,072 allocated, 122,880 cold input plus output/continuation | M5 / 32 GiB | 16.37 → 17.35 engine RSS | Strong fit expectation for this already-tested workload. |
| Gemma 31B Q4 + MTP 3, 40,960 allocated, 32,768 cold input plus output/continuation | M5 / 32 GiB | 18.95 → 19.97 engine RSS | Strong fit expectation at 40k; larger windows remain open. |
| Splash Q5/Q6; new MLX compositions | Not yet measured in these studies | Missing | Estimate after measuring each composition; do not inherit the Q4 memory observation. |

The Gemma context sequences used thinking off; short confirmation used thinking
on with a 1,024-token reasoning budget. Muse used its selected medium-reasoning
configuration. All listed runs observed no swap growth. RSS and wired growth
have different blind spots; these are not minimum-RAM figures or interchangeable
memory rankings. See the individual briefs for source identities.

For each finalist, also measure a useful point under a deliberately smaller
engine/cache allowance on the 64 GiB machine. Derive the allowance from the
prospective 48 GiB machine's GPU budget and OS/application reserve; record the
assumption if its facts are unavailable. Retain transient peaks, helpers and
cache growth. Do not set Splash's 64 GiB profile's 48 GiB engine allowance on
a machine with only 48 GiB total RAM.

Call this a constrained run on 64 GiB. It tests the engine's allowance, not
48 GiB OS pressure, paging, Metal limits or hardware speed. Do not simulate
less RAM by allocating dummy memory or changing system wired limits. A future
48 GiB machine needs only focused confirmation at the proposed useful point,
including continuation and pressure, rather than the entire selection campaign.

Muse's proposed Rapid 8-bit/DFlash arm deserves particular caution: upstream
reports 36.9 GiB process RSS after generation/cancellation/recovery on an M3
Ultra / 256 GB; loaded and peak memory are absent. That is a reason to measure
48 GiB headroom, not to assert either fit or failure. The
[pinned upstream report](https://github.com/raullenchai/Rapid-MLX/blob/ec83784c6662cf19803cffab85787d0bdcc20e27/docs/engineering/performance/muse-glimmer-30b-dflash-qualification.md)
is an expectation, not local evidence.

## Preparation still owned here

These are study briefs. Before dispatch, resolve candidate locks and material
sizes, bind the chosen existing coding harness to the local endpoint, and
freeze the common completed-task inputs. There is no new per-study runner.
The briefs identify missing Splash SSD-cache control, Rapid's separate DFlash
lifecycle and the vLLM helper allowance, so they can be addressed here instead
of rediscovered by the tester. No study has been launched.
