# Measurement commands

The [Qwen study](qwen-hardware.md) chooses questions. The standalone check
executes one bounded measurement:

```sh
./field-kit check --preview
./field-kit check --preset qwen3.8-27b-q4xl-mtp --plan
./field-kit check --preset qwen3.8-27b-q4xl-mtp
```

Smoke is the default. The explicit Qwen roster determines eligibility; a new
General preset never becomes a coding candidate automatically. --preset-file
ID.json accepts a Temper fork or Qwen copy. Copies may use another supported
catalog engine. Their declared model name is checked; the agent must still
verify actual upstream lineage and architecture.

| Workload | Question |
|---|---|
| --workload smoke | Can it load, generate and make the required real tool call? Request limit: 300 seconds. |
| --workload coding --coding-mode quality --case async-stream | Can it finish the Flask change with bounded visible tests and repairs? Final independent tests and code review required. |
| --workload coding --coding-mode performance --case async-stream | What does one realistic coding response cost, and does it show degradation against the reference? |
| --workload coding --coding-mode quality --case template-decorators | Can the frozen finalist complete unused confirmation work? |
| --workload context --records N --output-tokens M | Can it look up multiple input positions and continue correctly at measured lengths? |
| --workload cache --records N --output-tokens M | Do cold, appended-history and changed-prefix requests stay correct, with useful cache reuse? |

Record count is input size, not tokenizer length. Read actual engine counts.
Output allowance is a ceiling, not proof of generated output. Synthetic context
and cache probes cannot qualify long-context coding by themselves.

## Coding modes

Use the same commands for local Labs work and tester machines. Coding defaults
to performance mode, which keeps the original one-response task and submit_patch
tool. It does not execute tests or repair within the timed request. Review the
output afterwards as a degradation check; a one-shot pass is not full quality
qualification, and an imperfect answer is not automatically a regression from
the reference.

Select quality mode explicitly when assessing or reassessing quality:

```sh
./field-kit check --preset qwen3.8-27b-q4xl-mtp \
  --workload coding --coding-mode quality --case async-stream \
  --python /absolute/path/to/grading/python \
  --quality-turns 8 --quality-seconds 14400 --plan
```

Choose budgets for the work and machine; these example limits are not model
capabilities. After approving the concrete plan, omit --plan to execute it.
Quality mode adds test_patch: the model proposes a complete patch, receives
original-test and candidate-test output, and can repair it before submit_patch.
Every proposal applies to the original source. A control checks the unchanged
source before the first inference request. Independent tests are withheld from
both feedback and the visible-test sandbox; they run after final submission.
Source review also follows submission. No reviewer-authored fix is silently
credited to the model.

Study plans approve --quality-turns and --quality-seconds alongside the overall
budget. Select --coding-mode quality with --purpose screen or diagnose;
confirmation requires quality mode. Use --coding-mode performance with
--purpose tune for one-shot timing after a passing quality assessment. The study
records that quality run and its lock as the reference. Establish a matched
one-shot performance baseline as well; full workflow time and first-response
time are different measurements.

Full quality work is not repeated at every performance point. Reassess when a
new candidate, material behavior change, unexplained degradation or final
quality claim needs it. Every coding result still receives tests and source
review, with the mode determining what the result establishes.

Quality runs retain requests, responses, visible test results and proposal
identities under cases/CASE/turn-NNN/, plus quality.json. The first attempt
remains separate from the final submission. Exhausted budgets and interrupted
requests retain the incomplete work; they do not trigger an invisible retry.

## Quality review

```sh
./field-kit review runs/RUN_ID --python /absolute/path/to/grading/python
./field-kit review runs/RUN_ID --assessment assessment.json
```

Tests reconstruct the submission and verify its diff digest. An unchanged-source
control checks the grader; original, independent and candidate tests run in a
sandbox. Grader failures are invalid evidence; failing valid tests are outcomes.

Inspect the task, changed source, candidate tests and test output. Write:

```json
{
  "result_sha256": "<SHA-256 of result.json>",
  "reviewer": "<agent/model or human identity>",
  "cases": [{
    "id": "async-stream",
    "patch_sha256": "<diff_sha256, or null when no patch>",
    "verdict": "fail",
    "behavior": "Explain the control flow and whether it meets the task.",
    "regressions": "Describe compatibility, lifecycle and failure-path findings.",
    "test_quality": "Explain what the candidate tests exercise or miss."
  }]
}
```

Use pass, fail or invalid. Passing requires valid passing tests plus source
review. Every recorded attempt needs an assessment. Reviews bind to exact
result/patch hashes without rewriting inference evidence. Older basic-check@1
runs remain reviewable when their original response files accompany the result.
Review the final submitted patch for quality mode; retain earlier proposal
failures in its turn records. The same pass/fail assessment on a performance
run describes its one-shot output and cannot qualify the quality baseline.

## Counters and limits

Each case starts a fresh engine. Cache/context steps and quality repair turns
share one engine within their case. Per-step records retain actual token counts, time and native cached
tokens where reported. Missing prefill/decode/cache counters stay missing.
Quality time includes visible tests and repairs through submission; final
independent grading and source review are separate. Its token totals sum all
requests, including repeated input. Native rates remain per turn. Peak wired
growth during quality work may include the evaluator; use performance runs
for matched inference-memory comparisons.

Loaded/peak memory is sampled system wired growth over the pre-engine baseline,
in bytes, affected by other apps. RSS/footprint may miss mapped GPU weights.
Splash native Metal memory is separate. Counters overlap; never sum them or
equate weight-file size with runtime RAM or minimum machine RAM.

Stops include swap growth, heat, CPU speed reduction, process identity changes
and measurement gaps. Admission is a model-size floor, not a fit prediction.
Interrupted and failed attempts stay recorded; uncommitted attempts block
replacement until shutdown is resolved.

Standalone checks remove owned installations/cache after verified shutdown.
Studies reuse material until close. Revision 3 adds the explicit quality loop
and performance distinction; historical revisions 1/2 remain one-shot evidence.
