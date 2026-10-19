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
| --workload coding --case async-stream | Can it complete the exploratory Flask change? Tests and code review required. |
| --workload coding --case template-decorators | Can the frozen finalist complete unused confirmation work? |
| --workload context --records N --output-tokens M | Can it look up multiple input positions and continue correctly at measured lengths? |
| --workload cache --records N --output-tokens M | Do cold, appended-history and changed-prefix requests stay correct, with useful cache reuse? |

Record count is input size, not tokenizer length. Read actual engine counts.
Output allowance is a ceiling, not proof of generated output. Synthetic context
and cache probes cannot qualify long-context coding by themselves.

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

## Counters and limits

Each case starts a fresh engine. Cache/context steps share one engine within
their case. Per-step records retain actual token counts, time and native cached
tokens where reported. Missing prefill/decode/cache counters stay missing.

Loaded/peak memory is sampled system wired growth over the pre-engine baseline,
in bytes, affected by other apps. RSS/footprint may miss mapped GPU weights.
Splash native Metal memory is separate. Counters overlap; never sum them or
equate weight-file size with runtime RAM or minimum machine RAM.

Stops include swap growth, heat, CPU speed reduction, process identity changes
and measurement gaps. Admission is a model-size floor, not a fit prediction.
Interrupted and failed attempts stay recorded; uncommitted attempts block
replacement until shutdown is resolved.

Standalone checks remove owned installations/cache after verified shutdown.
Studies reuse material until close. Revision 2 does not reinterpret historical
basic-check@1 results.
