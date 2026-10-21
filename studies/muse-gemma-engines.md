# Muse and Gemma engine choice on 64 GiB

Question: can MLX improve useful work, populated-input latency or memory on the
64 GiB M5 Pro over the best llama.cpp configurations already found locally?
Run separate studies for Muse Glimmer 30B, Gemma 4 26B-A4B and Gemma 4 31B.
This is engine/composition research, not a Q5/Q6 campaign for these models.

## Evidence to carry forward

The prior machine is an M5 / 32 GiB / macOS 26.6.1. Its observations identify
controls and explain hypotheses; compare finalists again on the M5 Pro before
claiming a speed gain there. Snapshot facts below come from retained Labs
records, including work newer than the public model cards.

| Reference | Useful result | Loaded → peak engine RSS, GiB |
|---|---|---:|
| Muse Q4, llama.cpp b11741, DFlash2 width 7, F16 KV, medium reasoning | Complete context/cache/output sequence at 131,072 allocated and 122,880 cold input; cold 25.71 min, append 29.65 s, early edit 26.81 min | 18.38 → 19.62 |
| Gemma 26B Q4, b11756, plain, F16 KV | Complete sequence at 131,072 allocated and 122,880 cold input; cold 510.48 s, append 5.52 s, edit 523.78 s | 16.37 → 17.35 |
| Gemma 31B Q4, b11756, MTP width 3, Q8 KV | Complete sequence at 40,960 allocated and 32,768 cold input; cold 432.18 s, append 11.38 s, edit 437.48 s | 18.95 → 19.97 |

All observed zero swap growth. Counters cover the named workload, not minimum
machine RAM. Gemma context sequences used thinking off. Both selected Gemma
compositions also completed short thinking-on confirmation with an explicit
1,024-token native reasoning budget. `reasoning_effort=medium` alone does not
provide that budget. Unrestricted thinking sometimes exhausted the entire
output allowance; thinking off introduced factual/arithmetic errors.

Gemma 26B MTP improved the five-task time by only about 2% and added errors;
plain remains the control. Gemma 31B MTP 3 halved that workload's time relative
to plain. MTP 7 was faster on some short work but decoded only 1.93 tokens/s
at 57,481 input, versus 2.98 for width 3 and 3.02 plain. More RAM does not by
itself remove that populated-context cost. Those diagnostic runs observed
19.80/20.86, 19.95/20.81 and 19.59/20.62 GiB loaded/peak RSS respectively;
the width-7 peak covers its longer interrupted context sequence.

The earlier Rapid 0.16.4 Muse 4-bit screen showed useful prefix reuse and a
fresh-input advantage, but slower continuations and no general quality win.
Its source brief used 15.24/15.60 GiB loaded/peak engine physical footprint;
RSS omitted much of the allocation. Gemma 26B Rapid and vLLM Metal also ran.
Their short writing sequences used 14.68/15.68 and 15.73/16.07 GiB engine
footprint; vLLM additionally had a 2.03/2.10 GiB frontend footprint. These
counters overlap other memory views and are not added to RSS or wired growth.
llama.cpp was faster on that short work. These findings motivate larger-input
and larger-machine comparisons, not an automatic engine replacement.

Owner-side source paths in the V3 workspace:

- `labs/workstreams/writing-and-orchestration/method/muse-engines-2026-10-19.md`
- `labs/workstreams/writing-and-orchestration/method/muse-context-capacity-2026-10-20/README.md`
- `labs/workstreams/writing-and-orchestration/method/gemma-engines-2026-10-19.md`
- `labs/workstreams/writing-and-orchestration/method/gemma-improvement-2026-10-20/README.md`

Their compact records and frozen catalogs own exact inputs and identities.
Carry over selected inputs/locks needed for reproduction, not their private
Python runners. Current public assessments:
[Muse](https://temper-sh.github.io/models/muse-glimmer-30b/),
[Gemma 26B](https://temper-sh.github.io/models/gemma-4-26b-a4b/),
[Gemma 31B](https://temper-sh.github.io/models/gemma-4-31b/).

## Candidate order

| Model | Control | MLX candidates |
|---|---|---|
| Muse | Q4 llama.cpp + DFlash2 7; plain as a targeted memory/decoder control | Rapid 4-bit; compatible vLLM Metal on the same MLX bytes; then Rapid 8-bit/plain and original DFlash as a distinct larger-memory question. |
| Gemma 26B | QAT Q4 llama.cpp/plain with bounded thinking | Rapid and vLLM Metal using the previously tested `mlx-community/unsloth-gemma-4-26B-A4B-it-qat-oQ4` target. |
| Gemma 31B | QAT Q4 llama.cpp/MTP 3 with bounded thinking; plain where needed | Rapid and vLLM Metal using `mlx-community/unsloth-gemma-4-31B-it-qat-oQ4`, after model-path compatibility checks. |

Prior MLX target identities: Muse 4-bit revision
`3e7677d7a40d348a3daba263a2b1c0aa41910710`; Gemma 26B oQ4 revision
`1d6b563fb127a03f13921d9b80ee68ed8c43ce46`. Resolve Gemma 31B and each current
runtime closure exactly before preparation. The
[31B MLX export](https://huggingface.co/mlx-community/unsloth-gemma-4-31B-it-qat-oQ4)
exists (checked 21 October 2026); that does not establish either server's
compatibility. Reuse the installed supported runtime first; investigate a
different version only for an identified feature/fix, without silently upgrading.

GGUF versus MLX also changes quantization/export. Call this a composition
comparison. Use identical MLX target bytes for Rapid/vLLM wherever possible,
and record their different software closures. Do not claim an isolated kernel
speedup or assume reasoning budgets have the same meaning across engines.

## Smoke, then useful work

Start with text-only loading/tokenization, one source-constrained answer and
matched short/populated input. Verify effective reasoning, template tokens,
actual speculation and full model placement. Preserve near ties. A download,
adapter or unsupported-option error is a repair question, not a poor model score.

Use the existing non-coding briefs as labelled controls: source review,
chronology/scheduling, scene and revision of the actual first answer. Give
surviving configurations fresh work and ordinary bounded feedback so we can
judge the completed deliverable. Return outputs and source packets for central
review. Match controls where supported; otherwise label the difference rather
than sending an ignored `reasoning_budget_tokens` field to MLX and calling it equal.

Tune only promising arms. Look at prefill chunk/batch settings, effective cache
precision and reserve, speculation where implemented, and bounded reasoning.
Separate fixed-output decoder diagnostics from natural task completion.
Confirm cache behavior with cold input, append and early edit; extend actual
occupancy with output/continuation room for finalists. Muse's native limit is
131,072; Gemma's is 262,144. Prior successful points are not physical ceilings.
The [shared brief](64gib-studies.md) defines memory recording, waiting and return.

## Known integration work and repair traps

- Muse Rapid's original DFlash arm is not the GGUF DFlash2 route. The
  [pinned upstream report](https://github.com/raullenchai/Rapid-MLX/blob/ec83784c6662cf19803cffab85787d0bdcc20e27/docs/engineering/performance/muse-glimmer-30b-dflash-qualification.md)
  uses `mlx-community/Muse-Glimmer-30B-8bit` revision
  `679c45e1b331a6514a6e38076a353cc5fed21bf6` and
  `meta-models/Muse-Glimmer-30B-assistant` revision
  `e8192f3a8f617f74be2ce220360c89ef4789f39f`. Keep the same 8-bit target plain
  control. Temper currently renders Rapid with no speculation or MTP, so it
  needs a maintained lifecycle integration before this arm can run. Adding a
  draft artifact field cannot enable it. Report this arm as pending integration
  and continue the supported candidates; do not bypass process ownership.
- Muse Rapid 0.16.4 inserted a duplicate BOS. A study-local template-argument
  workaround corrected token IDs then; verify the actual installed path before
  reusing it. Unsupported sampling/template settings need diagnosis too.
- The earlier Gemma vLLM frontend legitimately reached 2.10 GiB. The engine
  adapter now proposes a 3 GiB frontend allowance in study preview, retaining
  2 GiB for its other helpers and the machine's wired/swap guards. Old approvals
  retain their original limits; review the new preview before dispatch.
  A known artificial guard must not be reported as failure to fit 64 GiB.
- Historical startup helper timing and vLLM first-start ownership needed fixes.
  Reuse the reports, inspect the installed versions, and report any recurrence
  with exact process evidence and repair. Do not repeat a week of blind retries.
- Native Gemma budget support is implemented in Temper's 0.4 work but may not
  be in the installed release. A verified explicit request field on supported
  llama.cpp preserves the experiment without an automatic Temper upgrade.
  Keep this distinct from an installable preset default.

Select one useful composition per model by default; retain alternatives only
for demonstrated quality, latency or memory benefits. An engine winner at
short input may differ from the populated-context winner. Project to 48 GiB
using each composition's own measured envelope, especially the 8-bit Muse arm.
