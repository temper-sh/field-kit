# Qwen hardware study

Produce a defensible hardware-specific Qwen preset: reviewed quality, time
to completed work, memory fit, cache behavior and measured context scope.
The agent chooses experiments; deterministic commands own effects.

## Prioritize missing evidence

Read hardware facts and existing assessments. Use a cheap current baseline,
then prioritize what the reference M5 / 32 GiB cannot establish: admitted MLX
engines and longer actual input/continuation sizes on larger machines.
Temper's plans decide admission; RAM alone does not prove fit.

The initial family is Qwen3.8 27B. Candidate engines include llama.cpp
MTP/plain, Splash DFlash, Rapid MLX and vLLM Metal where compatible exact
material exists. Compare Q4/Q5 to investigate the known reversal; add Q6/Q8
only when their information value justifies resources. This is a candidate
space, not a matrix to exhaust. Do not send known unsuitable models into
coding merely because they share a catalog tab.

## Prepare and approve

Use Temper's [user-preset contract](https://github.com/temper-sh/temper/blob/master/docs/contracts/user-presets.md)
and [engine contracts](https://github.com/temper-sh/temper/blob/master/docs/contracts/execution-lock.md).
Forks support another quant/template or settings; copies support another engine.
Resolve all MLX config/tokenizer files as well as weights. Copies declare
model_name Qwen3.8 27B; independently verify lineage, architecture and target/draft
compatibility. Temper owns software resolution and execution locks.

Prepare a short candidate list. Example command shape (replace the files and
limits with the actual proposed study):

```sh
./field-kit study plan \
  --preset qwen3.8-27b-q4xl-mtp \
  --preset-file trials/qwen-q5.json \
  --preset-file trials/qwen-rapid.json \
  --python .local/grading/bin/python \
  --max-runs 12 --hours 8 --download-gib 100 \
  --max-context 131072 --request-seconds 1800 \
  --out trials/qwen-plan.json
./field-kit study start qwen-machine --plan trials/qwen-plan.json
```

The plan freezes exact engine/software, artifacts and templates. Approved
tuning includes context/output, supported engine configuration, sampling,
reasoning, speculation with existing artifacts and compute selection.
Temper compiles each variant and Field Kit compares its fixed material.

Elapsed time includes pauses after approval. Downloads are conservatively
reserved per candidate on first use; shared files can be counted twice.
This bounds planned material, not network retransmission bytes. Closing
remains available after budgets expire.

Planning also checks the disk estimate for all retained candidates together,
with a 2 GiB working reserve. It is a floor, not an upper bound on output logs.
Existing user caches are reused offline only when every model and template
file is present; otherwise the plan includes a study-owned private cache.

## Smoke and review quality

```sh
./field-kit study run qwen-machine --candidate qwen-rapid \
  --workload smoke --reason "MLX evidence missing on this memory tier" --background
```

Inspect the exact execution, startup, tool interface and memory. A parser or
unsupported engine failure is not a quant quality score. Diagnose cheaply.

Promising candidates get --workload coding --purpose screen, using async-stream.
Run tests and [source review](basic-check.md#quality-review) immediately. The
next experiment is blocked until review exists. Review behavior, regressions
and whether candidate tests actually exercise the requested behavior.

## Investigate Q4/Q5 reversals

Preserve original attempts. Check evidence validity first: task identity,
submission, grading environment, original/hidden/candidate tests and source
review. Compare exact quant/revision, template bytes/transform, engine version,
thinking/sampling, context/output policy, KV precision, batching, draft/MTP,
cold/warm state, completion time and loaded/peak memory.

- Controlled comparisons match compatible controls and change the quant or
  one explanatory mechanism. Name controls that cannot match across engines.
  Use bounded, order-reversed repeats when needed to separate an effect from variance.
- Deployment comparisons tune each candidate for its own useful configuration.
  Compare quality, completion time and memory together. Do not attribute every
  difference to quant precision.

A larger quant need not produce a better individual answer. A different
reasoning budget, invalid tool call or broken grader can explain a reversal.
Use --purpose diagnose with the specific hypothesis and referenced run in
--reason. Retain unresolved anomalies instead of promoting a winner.

## Tune speed, cache and context

Use --preset-file trials/variant.json with the approved candidate. Change
one explanatory factor at a time, then combine useful settings and recheck
quality. Separate long-input prefill, decode throughput and time to done.

Cache trials include append and rewind: a faster stale answer fails. Report
native cache counters when available, and explicitly mark missing counters.

Bracket context with real input: increase --records, read actual input tokens,
reserve an explicit output allowance, and exercise continuation. Stop on
incorrectness, time/resource limits or context rejection; refine between the
last valid pass and first failure. Retain both with their failure meaning.

Distinguish configured window, tested input, generated output and output
allowance. The synthetic probe does not fill the output allowance or establish
long-context coding quality. Qualify only the measured workload/range; a lower
bound stays a lower bound. Short coding plus long retrieval does not support
a long-context coding claim.

## Confirm and close

Freeze finalists before --workload coding --purpose confirm. This consumes
template-decorators. Once used, it cannot be unseen evidence for another
tuning iteration of that candidate. If previous work already exposed it,
record that limitation and arrange separate unused work. Review it as carefully
as screening.

Write one concise conclusion and proposed Temper preset/profile change:
selected exact run/lock; chip/memory/OS; completed-work quality; time and native
rates; loaded/peak memory with counter/blind spots; measured context scope;
cache behavior; unresolved anomalies. A speed preset and a longer-context
preset may both be useful. Do not invent a universal winner or minimum RAM.

```sh
./field-kit study status qwen-machine
./field-kit study close qwen-machine
```

Every trial stops and verifies its processes. Close removes owned software,
weight caches, runtime assemblies and reconstructible source copies.
Results, patches, reviews, locks and important failures remain. Existing user
caches/installations stay intact. Uncertain shutdown retains recovery material.
Catalog review and publication are separate actions.
