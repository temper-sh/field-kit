# Qwen hardware study

Find the maximum usable Qwen presets for this hardware: reviewed quality,
time to completed work, memory fit, cache behavior and measured context scope.
The catalog setting starts the investigation; a first healthy pass is not its
endpoint.
The agent chooses experiments; deterministic commands own effects.

For Q4 engine selection on the tester's M5 Pro / 64 GiB, use the
[focused Q4 campaign](qwen-q4-64.md). It supplies candidate preparation,
engine measurements before coding quality, and a complete-results export.

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

Before screening, research why each candidate should help on this hardware.
Follow the agent's [expectation and diagnosis procedure](../ASSISTANT.md#research-unexpected-results)
when an observation challenges that expectation. Keep some of the approved
budget for extension, diagnosis and confirmation; absence of a gain does not
explain its cause.

## Prepare and approve

Use Temper's [user-preset contract](https://github.com/temper-sh/temper/blob/master/docs/contracts/user-presets.md)
and [engine contracts](https://github.com/temper-sh/temper/blob/master/docs/contracts/execution-lock.md).
Forks support another quant/template or settings; copies support another engine.
Resolve all MLX config/tokenizer files as well as weights. Copies declare
model_name Qwen3.8 27B; independently verify lineage, architecture and target/draft
compatibility. Temper owns software resolution and execution locks.

Prepare a short candidate list and a conditional continuation plan: useful
larger context points, compatible acceleration settings to investigate, and
evidence that would stop or redirect the search. Include needed drafts in the
approved material so settings exploration does not require approval each time.

Choose the context ceiling from supported model/backend capability and the
intended work. Budget long requests for fresh input, early-source replay and
generation using relevant timings or conservative estimates with margin.
Separate an actual user latency requirement from the experiment's deadline.
Leave time for confirmation and cleanup. The example numbers below are neither
recommended preset settings nor universal study limits:

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

Promising candidates get --workload coding --coding-mode quality --purpose screen,
using async-stream. This is a bounded proposal → visible tests → repair workflow.
Run final independent tests and [source review](basic-check.md#quality-review)
immediately. The next experiment is blocked until review exists. Review behavior,
regressions and whether candidate tests exercise the requested behavior.
Preserve first-attempt and final outcomes separately.

## Investigate Q4/Q5 reversals

Apply the shared diagnosis procedure; this section supplies the Qwen-specific
controls for one example of an unexpected result. Quality, speed, memory,
context and functional discrepancies all merit the same scrutiny.

Preserve original attempts. Check evidence validity first: task identity,
submission, grading environment, original/hidden/candidate tests and source
review. Verify the source checkpoint, conversion/calibration provenance where
applicable, quant format and actual loaded files before assuming the artifacts
differ only in precision. Compare exact quant/revision, template bytes/transform,
engine version, thinking/sampling, context/output policy, KV precision,
batching, draft/MTP, cold/warm state, completion time and loaded/peak memory.

- Controlled comparisons match compatible controls and change the quant or
  one explanatory mechanism. Name controls that cannot match across engines.
  Use bounded, order-reversed repeats when needed to separate an effect from variance.
- Deployment comparisons tune each candidate for its own useful configuration.
  Compare quality, completion time and memory together. Do not attribute every
  difference to quant precision.

Treat higher fidelity as an expectation to validate, not a conclusion from a
bit label or a guarantee of a better individual answer. A different reasoning
budget, invalid tool call, broken grader or run variation may explain a reversal;
the expected quality ordering may also fail for this workload. Use --purpose
diagnose with the specific hypothesis and referenced run in --reason. Preserve
unresolved anomalies without declaring a general quant winner.

## Tune speed, cache and context

Use --preset-file trials/variant.json with the approved candidate. Change
one explanatory mechanism at a time, then examine important interactions,
combine useful settings and recheck quality. Separate long-input prefill,
decode throughput and time to done.

Coding performance trials use --workload coding --coding-mode performance
--purpose tune. They make one request and expose no test feedback or repairs.
The study records the qualified quality run used as a reference. Establish a
matched one-shot baseline, compare actual work and costs, and review each
submission for degradation. A one-shot test failure may also exist in the
reference; it is not automatically lost quality or evidence that the full
workflow cannot finish. Investigate new material failures before selection.
Use another full quality run only to assess or reassess quality, including a
finalist whose changes need confirmation. Do not repeat it at each speed point.

For MTP/DFlash, establish that the compatible draft path actually ran. Compare
with plain decoding and examine useful proposal lengths, including smaller
ones when drafting cost overwhelms the benefit. Check short and populated
context where relevant, accepted work per verification cycle, total drafting
and verification cost, quality and loaded/peak memory. Use matched comparisons
to explain effects and independently tuned comparisons to choose a preset.
One disappointing configuration does not reject the technique.

Cache trials include append and rewind: a faster stale answer fails. Report
native cache counters when available, and explicitly mark missing counters.

### Continue toward useful limits

After a correct pass with valid measurements and healthy resources, choose the
next useful larger input/window or faster configuration within the approval.
Use coarse context steps while headroom is clear and finer steps near a
meaningful boundary. Increase --records and read actual input tokens; record
the configured window, output allowance and continuation room separately.
Do not stop at the catalog default, the CLI example or a round token count.

Stop a trial on incorrectness, context rejection or a time/resource limit.
Diagnose its meaning before bracketing: a repeatable allocation boundary may
support refinement, but retrieval errors and heat need not be monotone.
Investigate an unexpectedly low point:
check native attention/state allocation, KV precision, checkpoint/cache policy,
automatic fitting and actual output/continuation reserve. A failed point bounds
the tested configuration and workload; an inherited setting or operational stop
does not establish the machine's physical limit. A corrected configuration starts
a new comparison and preserves the original attempt.

A larger allocation need not slow short work as much as a long cold prompt.
Measure its loaded/peak memory and short-work cost separately from cold
prefill, cached continuation and early-edit replay. A long first response can
be an acceptable disclosed tradeoff unless it violates the intended use.
Do not impose an unrequested latency requirement by reusing a short-test timeout.

Current context/cache probes perform short record lookups with a canonical
answer in the appended history. They do not fill the output allowance or
establish natural conversation or long-context coding quality. Setting
--output-tokens to 4096 does not prove 4096 tokens were generated. For a broader
claim, use applicable evidence or route the missing full-output, actual-history
and representative-work checks to Labs or a new versioned workload. Do not
bypass study gates with standalone runs. Keep the measured scope explicit;
an untested use is not a reason to copy the probe's cap into the product.

An ordinary chat preset should allow output to use remaining context unless a
workload or runtime requires a cap. Fixed output lengths remain useful for
matched diagnostics. Verify the final composition with its actual output
policy, cache and acceleration together; short coding plus long retrieval
cannot establish long-context coding quality.

Continue without repeated permission inside the approved material and limits.
Never raise a running study's approved ceilings or weaken its resource guards.
If they end before the useful boundary is resolved, report the best evidenced
point and prepare the next concrete allocation. Call the search incomplete.
No exhaustive quant × engine × settings matrix is required.

## Confirm and close

Resolve or explicitly defer the useful extension questions, then freeze
finalists before --workload coding --coding-mode quality --purpose confirm. This consumes
template-decorators. Once used, it cannot be unseen evidence for another
tuning iteration of that candidate. If previous work already exposed it,
record that limitation and arrange separate unused work. Review it as carefully
as screening.

Write one concise conclusion and proposed Temper preset/profile change:
selected exact run/lock; chip/memory/OS; completed-work quality; time and native
rates; loaded/peak memory with counter/blind spots; measured context scope;
cache behavior; unresolved anomalies; why exploration stopped and the next
untested boundary. Distinguish reaching a supported endpoint, a diagnosed
resource/quality boundary, an explicit usability tradeoff, no worthwhile next
change and budget exhaustion. The last is not a maximum-context finding.

Propose the strongest evidenced settings. Preserve a smaller or plain option
only for a meaningful tradeoff; a speed preset and a longer-context preset may
both be useful, but two variants are not mandatory. Do not invent a universal
winner or minimum RAM. A confirmed improvement need not be a global optimum.

```sh
./field-kit study status qwen-machine
./field-kit study close qwen-machine
```

Every trial stops and verifies its processes. Close removes owned software,
weight caches, runtime assemblies and reconstructible source copies.
Results, patches, reviews, locks and important failures remain. Existing user
caches/installations stay intact. Uncertain shutdown retains recovery material.
Catalog review and publication are separate actions.
