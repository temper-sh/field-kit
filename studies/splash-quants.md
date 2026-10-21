# Splash Q4, Q5 and Q6 on 64 GiB

Question: does a larger Qwen3.8 27B quant produce sufficiently better finished
code to justify its time and memory? Splash is the only engine in this study.
The tester measures and returns work; comparative quality review happens here
on the owner's side.

## Candidates and controls

Carry the balanced Q4 settings from [context/cache tuning](splash-context-cache.md)
forward. Begin with one candidate per size, all from Unsloth revision
`4ca720788d1e01f1bff70c033e0d0028fd02e502`:

| Candidate | File | Published rounded download size |
|---|---|---:|
| Baseline | `Qwen3.8-27B-UD-Q4_K_XL.gguf` | 17.6 GB |
| Q5 | `Qwen3.8-27B-UD-Q5_K_XL.gguf` | 20.9 GB |
| Q6 | `Qwen3.8-27B-UD-Q6_K_XL.gguf` | 25.3 GB |

The [pinned repository listing](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/4ca720788d1e01f1bff70c033e0d0028fd02e502)
was checked 21 October 2026. Resolve exact byte counts and hashes into locks
before preparation. These decimal GB sizes describe downloads, not runtime
RAM. XL is a starting choice to preserve the quantization family, not a claim
that every tensor has the named bit width or that XL is the best Q6 variant.
Add a smaller Q6 variant only if its expected memory/speed tradeoff merits it.

Hold engine, target model revision, template, DFlash2 sidecar, reasoning,
sampling, cache settings, context, fixture and harness constant for the initial
comparison. Verify Splash accepts each exact GGUF. Quant-specific adjustments
are allowed after that control; record their reason and keep the initial result.
Identical draft bytes do not imply identical acceptance rate or speed across quants.

## Screen and tune

Run one-shot coding smoke plus matched short/populated performance work for
each candidate. Capture generated code, native rates, elapsed time, actual
tokens and loaded/peak memory. A tie is enough to continue; lack of an immediate
speed gain does not reject a potential quality candidate. A compile/test defect
is diagnostic evidence, not a universal gate.

Tune promising candidates with a stated hypothesis. Start with the shared
configuration; vary reasoning/sampling or cache precision only when evidence
points there. Re-smoke before expensive work. Do not run completed tasks for
every knob value, and do not compare a carefully tuned Q6 only with an
untuned Q4. Measure context/cache/memory for viable finalists independently
of whether the owner-side quality comparison has returned.

## Completed work and owner review

Before collecting the comparison, the owner freezes a small set of substantive
coding tasks from a real repository, its exact revision, requested behavior
and ordinary project test commands. Use a known historical task only as a
labelled regression; include unused work for a new quality conclusion.
Do not ask the tester's agent to invent a large hidden-test suite or decide
which candidate has better design. The old Flask task machinery is not a
dependency of this study.

Use one existing coding harness and version, with identical instructions,
tools, dependencies and prospective repair/time allowances. Each candidate
starts from a clean copy of the same source. Feed it its own test results and
allow it to finish the task within that scope. Preserve first and final states.
A fixed eight-response stop is not a definition of finished work. The harness
owns tool protocol, stopping, endpoint selection and patch capture; validate
that plumbing cheaply before expensive trials.

Return enough evidence to review without reconstructing the tester's session:

- Original task, source revision, harness/version and effective local
  endpoint/model configuration, with request logs establishing attribution.
- First patch and final patch/tree, including new files; relevant transcript
  and feedback/repair history. Preserve partial work if the task does not finish.
- Original and final project test output, new tests, exact commands/environment
  and any harness errors. Passing tests are evidence, not the quality verdict.
- Whole-task elapsed time, request/token/rate data when available, loaded/peak
  memory, remaining defects and interventions. Never infer missing rates from
  whole-task wall time.

Store artifacts under the command workload's `FIELD_KIT_OUTPUT_DIR`, so the
normal exporter retains them. An idle intermediate export can come back while
other experiments continue. The owner-side review compares requested behavior,
correctness, regressions, design, maintainability, test relevance and remaining
repair across candidates, preferably before looking at their quant labels.
Resolve a consequential disagreement with a focused reproduction or fresh task.

## Decision

Choose one balanced configuration unless another shows a material quality,
speed or memory advantage. More bits alone are not a quality result. If the
code is effectively tied, latency and memory can settle the choice. If the
larger quant is better, show the concrete improvement and its cost.
Keep tester execution, owner quality judgment and catalog publication as
separate facts; pending review does not invalidate resource measurements.
Apply the shared [48 GiB projection method](64gib-studies.md#what-can-transfer-to-48-gib)
to each finalist's own memory observations.
