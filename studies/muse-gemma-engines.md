# Muse and Gemma engine choice on 64 GiB

Question: can MLX improve useful work, populated-input latency or memory on the
64 GiB M5 Pro over the best llama.cpp configurations already found locally?
Run separate studies for Muse Glimmer 30B, Gemma 4 26B-A4B and Gemma 4 31B.
This is engine/composition research, not a Q5/Q6 campaign for these models.

## Evidence to carry forward

The prior machine is an M5 / 32 GiB / macOS 26.6.1. Its observations identify
controls and explain hypotheses; compare finalists again on the M5 Pro before
claiming a speed gain there. Snapshot facts below come from the earlier study
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
provide that budget. In the llama.cpp source pinned by the 22 October Flint
study, the budget limits each thinking block and starts again when the model
opens a new one; it is not a cap on total thinking. Check the stream for
reopened thinking with the version under test. Unrestricted thinking sometimes exhausted the entire
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

Current public assessments:
[Muse](https://temper-sh.github.io/models/muse-glimmer-30b/),
[Gemma 26B](https://temper-sh.github.io/models/gemma-4-26b-a4b/),
[Gemma 31B](https://temper-sh.github.io/models/gemma-4-31b/).

## Prepared inputs

Everything below is in [`muse-gemma-engines/`](muse-gemma-engines). The study
catalog (`catalog.json`) gathers the exact engines, model files and settings of
the earlier runs; every lock in `locks/` is compiled from it with
`temper catalog compile --catalog catalog.json --preset ID --target darwin/arm64
--memory-gib 64`. Author a further variant by editing one setting of a preset in
a copy of the catalog and compiling it the same way.

| Study | Candidate locks (`locks/ID.lock.json`) |
|---|---|
| Muse | `muse-q4-llama-dflash7-131072` (control), `muse-q4-llama-plain-131072`, `muse-4bit-rapid-131072`, `muse-4bit-vllm-131072`, `muse-8bit-rapid-131072` |
| Gemma 26B | `gemma-26b-qat-q4-llama-plain-131072` (control), `gemma-26b-oq4-rapid-131072`, `gemma-26b-oq4-vllm-131072` |
| Gemma 31B | `gemma-31b-qat-q4-llama-mtp3-40960` (control), `gemma-31b-qat-q4-llama-plain-40960`, `gemma-31b-oq4-rapid-40960`, `gemma-31b-oq4-vllm-40960` |

- The llama.cpp locks keep each control's earlier engine build (b11741 for Muse,
  b11756 for Gemma), model files, KV precision, speculation and reasoning, with
  the window raised to the tested point. The Muse control matches the lock Field
  Kit already ran four times, in material and settings.
- Rapid is 0.16.4, the version measured before and the one the duplicate-BOS
  workaround was checked on; the published catalog has 0.16.3. vLLM Metal is
  0.31.0, as published. Both come as exact pinned Python packages.
- The MLX locks use the same windows as their controls and a 4,096-token output
  allowance. Rapid receives no context-window setting from Temper, only the
  output allowance, so its practical limit is memory; vLLM's window is enforced.
- New MLX targets: Gemma 31B oQ4 at revision `d809645df8db9531a57e76a2a55939435ed9a7ce`
  (17.73 GiB) and Muse 8-bit at `679c45e1…` (31.11 GiB). Neither has been run
  through Rapid or vLLM before; start with a smoke.
- `muse-4bit-vllm` has no Muse reasoning or tool parser setting; check that
  reasoning arrives separately before comparing its answers.

Approve each study (Muse, Gemma 26B, Gemma 31B) with its own candidate locks and
`--max-context 262144`, so window extensions remain settings variants.

The labelled controls are ready as workloads in `workloads/`. They carry the
earlier prompts, sampling (temperature 1, top-p 0.95, top-k 64, seed 17) and
per-engine fields exactly:

| Workload | For |
|---|---|
| `muse-controls-llama`, `-rapid`, `-vllm` | Source brief, critical review, a move schedule, a publication review, a scene and its revision; Rapid adds the duplicate-BOS workaround |
| `gemma-controls-llama-thinking` | Notices, a chronology regression, a scene and its revision; thinking on with the 1,024-token budget |
| `gemma-controls-llama-off`, `gemma-controls-mlx-off` | The same with thinking off: the matched llama.cpp/MLX pair |
| `gemma-controls-mlx-thinking` | Thinking on without a budget, which MLX lacks; not matched with the llama.cpp thinking run |

Every earlier reserved case has since been used, so these are controls, not
fresh work. The fresh work for surviving configurations still needs to be
frozen before inference. Context and cache sequences come from Field Kit's
`context` and `cache` generators; the same records count differently on each
engine's tokenizer, so adjust records from the native counts.

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
  control. Temper renders Rapid's MTP speculation but has no route for its
  original DFlash server path, so this arm needs a maintained lifecycle
  integration before it can run. Adding a
  draft artifact field cannot enable it. Report this arm as pending integration
  and continue the supported candidates; do not bypass process ownership.
- Muse Rapid 0.16.4 inserted a duplicate BOS. Sending
  `chat_template_kwargs.bos_token: ""` corrected the token IDs then, and
  `muse-controls-rapid` sends it; verify the token IDs again before relying on it. Unsupported sampling/template settings need diagnosis too.
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

For the 48 GiB projection, the MLX memory setting is a share of the Mac's own GPU
limit, not of its RAM: vLLM sizes its cache from that limit times the share, less
the model, and Rapid's pressure threshold is that limit times the share plus 0.05.
The same share therefore gives a 48 GiB Mac about 30% less. The `-cap48` locks
scale the shares by 36/51.8 (Rapid 0.70 → 0.49, vLLM 0.65 → 0.45), so the
64 GiB machine gives each engine the budget a 48 GiB Mac would. llama.cpp has
no such setting; its projection comes from the measured peak. Muse 8-bit's
31.1 GiB of weights leave almost nothing of a 48 GiB Mac's expected 36 GiB, so
report it as not expected to fit unless a measurement says otherwise.
