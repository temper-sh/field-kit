# Parked Gemma 26B tuning brief

This file preserves earlier measurements and the broader tuning plan. The
[tester assignment](../studies/README.md) owns the current run.

Owner decision, 5 November 2026: focus on **Gemma 4 26B-A4B only**, with
thinking enabled, current grounded sampling/context practice, and **drafter
and cache tuning** on the M5 Pro / 64 GiB. Seek the best supported configuration
for completed source work, writing, revision and continued interaction,
including useful long context. The first healthy setting is not an optimum.

Keep Unsloth QAT UD-Q4_K_XL and llama.cpp. This supersedes the earlier
sampling-first/thinking-off continuation. Larger target quants and MLX engine
selection remain separate closed/deferred questions. Other models wait;
Kapitsa stays parked. This brief prepares the investigation; it contains no
new inference results or machine-side approval.

## Starting configuration and fixed policy

Copy installed Temper's `gemma-4-26b-a4b-qat-ud-q4-k-xl-llama` into an isolated
study authoring root, inspect its records and compile exact locks. The old
[control lock](../studies/unsloth-q4.lock.json)
is historical thinking-off evidence, not the new baseline.

Current policy, checked 5 November against the installed preset and upstream:

- Target: `unsloth/gemma-4-26B-A4B-it-qat-GGUF` revision
  `7b92b5b28818151e8669af2e45e88d6086f490dd`,
  `gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf`, 14,249,047,104 bytes,
  SHA-256 `a7c5bc715f5ff8e99a3e8901ce7d2b42b402c669bf24f7c5250747633d0f5891`.
- Embedded template; **thinking on**. Verify
  `chat_template_kwargs.enable_thinking=true` and actual thought/final output.
- **Sampling fixed:** temperature 1, top-p 0.95, top-k 64, min-p 0,
  repetition penalty 1, presence penalty 0. Google and Unsloth recommend the
  first three; the others preserve neutral preset controls. Freeze comparison
  seeds without assuming equal seeds produce equal trajectories.
- Begin with the current **1,024-token native reasoning budget**, grounded in
  the earlier Gemma comparison below. Confirm its adequacy before freezing
  runtime comparisons; it is a starting policy, not a capability ceiling.
- Ordinary history retains final answers, not previous thoughts; tool-call
  turns preserve thinking when exercised, following Google's history rule.
- Initial allocation **131,072**, publisher native maximum **262,144**.
  Preserve native positional settings, no context shifting or auto-fit, one
  slot, GPU placement, F16 KV, Flash Attention, batch/microbatch 1,024, four
  generation/prefill threads and mmap initially. Prompt reuse starts on,
  checkpoints at 2 / 8,192 spacing, RAM prompt cache at zero.

The catalog pins llama.cpp **b11756**. Latest stable upstream checked here is
**v0.9.0** (`cd959ba`), not an already-selected study engine. Review relevant
Gemma/drafter/cache changes and supported Temper closures. A newer build is a
candidate when it enables or improves this work: bridge it against the existing
baseline, then freeze the build within tuning comparisons. Do not implicitly
upgrade Temper, patch pinned installed material or edit generated commands.

## Returned evidence and its limits

Source: `gemma26-r2-results.zip`, returned 23 October 2026, SHA256
`c1f019a2610a4c5b36da45b63351b0c8808636516642bc8948aae68fdbcdabac`.
The owner's copy is in Downloads; use the returned archive on another machine.
Paths below are relative to `gemma26-r2/runs/`. Its machine was M5 Pro / 64 GiB /
macOS 26.7.1, Temper 0.3.2 and Field Kit 0.8.0-dev. Keep the closed study intact.

| Run | Observation | Service time | Loaded → peak system wired growth, GiB |
|---|---|---|---:|
| `20261023T090304247693Z` | Fresh Ukrainian source/schedule task, thinking off: key facts and 08:58 arrival correct; notices too short | 5.57 s | 17.15 → 17.32 |
| `20261023T090318955514Z` | Same task, 1K thinking: 08:40 headline contradicts 08:58 calculation; reasoning ends mid-sentence, consistent with budget exhaustion | 21.01 s | 16.71 → 16.92 |
| `20261023T095817000382Z` | 121,812-token cold input; dense retrieval wrong despite successful execution and append reuse | Cold 205.82 s; append 2.81 s; early edit 209.11 s | 17.31 → 18.95 |
| `20261023T113120252558Z` | Microbatch 512, same 122K-class greedy workload; retrieval still wrong | Cold 184.74 s; edit 205.34 s | 16.64 → 18.27 |
| `20261023T113801858604Z` | Matched microbatch 1,024 repeat; one pair does not establish a speed winner | Cold 221.55 s; edit 224.11 s | 16.91 → 17.78 |

All listed runs observed zero swap growth. Wired growth includes other apps;
five-second sampling can miss peaks. These values are not isolated model
allocations or minimum machine RAM; do not add overlapping process counters.

Both finalists passed the three approximately 31K dense-retrieval steps.
Greedy decoding at 62K/122K (`20261023T101632018547Z`,
`20261023T102147591855Z`) and bounded thinking at 122K
(`20261023T104855558010Z`) did not repair middle-record errors. The 122K
thinking responses ended below their 1K budget; do not explain those failures
by the short task's apparent cutoff. The earlier successful 122,880-token M5
test used five marked records in repetitive filler, not thousands of dense
records. It does not establish a regression or a smaller hardware ceiling.

All three engine/export compositions failed the constrained scene/revision
control. The isolated Ukrainian result is not a general quality ranking.
There was no 256K trial and no observed memory ceiling at the last tested point.

The later `gemma26-quants-results.zip` completed the larger-quant question:
all four quants passed approximately 31k dense retrieval and failed around
62k; Q6 had limited scheduling gains with remaining defects, not a reason to
replace QAT Q4. Its thinking-off output/continuation pair remains useful:

| Run | Work | Long-output time | Loaded → peak wired growth, GiB |
|---|---|---:|---:|
| `20261023T141321963507Z`, QAT Q4 | 31k input, 4,096 generated tokens, correct continued lookup at 35,387 and early edit | 74.92 s | 17.22 → 17.36 |
| `20261023T141010730096Z`, IT Q6 | Same sequence and correct continued lookup | 95.77 s | 25.51 → 25.96 |

Both observed zero swap growth. Forced output proves generation room, not
completed prose. QAT versus IT also changes lineage. Preserve both archives.

The [earlier Gemma improvement study](../../labs/workstreams/writing-and-orchestration/method/gemma-improvement-2026-10-20/README.md)
grounds the reasoning default: on M5 / 32 GiB, plain 26B with 1K thinking took
143.87 s on five tasks, preserving tested facts, with 14.18 → 14.53 GiB engine
RSS. MTP width 1 took 141.09 s, 14.51 → 15.00 GiB RSS, and added errors. Carry
this contrary evidence forward without rejecting drafting on the M5 Pro.
Unrestricted plain used 4,699 output tokens on one scene; MTP exhausted 8,192
without a final answer. RSS and wired growth have different blind spots.

## Establish a usable thinking baseline

Use the [thinking-on controls](gemma26-q4-tuning/historical-thinking-controls.json)
as labelled regressions. Preserve this historical input; copy its cases into
the new study with the thinking/output allowances established below, rather
than inheriting its old 4,096-token cap. Freeze fresh English/Ukrainian source, scheduling and
revision cases and their oracles before output. Check factual precedence,
chronology, unsupported claims, length and revision regressions. Preserve each
arm's actual first answer and bounded revision.

If the 1K reasoning budget truncates thought or plausibly changes the delivered
answer, make a focused comparison with sufficient/unrestricted thinking (`-1`).
Choose the common policy from those observations before runtime pairs; do not
start a sampling or on/off grid. Retain cutoffs and rerun affected comparisons
if policy changes. The budget is per thought block and can restart; inspect
total reasoning, reopened blocks and final-answer space.

Initial total allowance for natural work: **16,384 tokens**, deliberately
doubling this model's previously exhausted 8,192-token allowance to investigate
completion. This is diagnostic room, not a product limit or an established
sufficient bound. If healthy work reaches it, extend from that evidence within
approved context/time. Match total allowance and reasoning policy across arms.
The current catalog has no separate total-output cap.

## Drafter tuning

Keep plain decoding as control. Screen model-matched QAT MTP and 26B-specific
DFlash. Include DSpark after verifying its exact runtime/Temper route. Earlier
DFlash/DFlash2/DSpark measurements were **31B**, not 26B: inspection lessons,
not a rejection of these candidates.

Candidate material checked 5 November; sizes are files, not runtime memory:

| Candidate | Pinned material | Initial comparison |
|---|---|---|
| QAT MTP | Target repository/revision above; `mtp-gemma-4-26B-A4B-it.gguf`, smart Q4_0, 251,939,328 bytes | Width 4 from publisher example; compare narrower widths based on acceptance/cost and the prior width-1 result. |
| MTP precision control | Same revision: `MTP/mtp-gemma-4-26B-A4B-it-Q8_0.gguf`, 461,785,600 bytes; BF16 855,247,360 bytes | Use if acceptance or a close result can change selection; no automatic precision grid. |
| Original DFlash | `Anbeeld/gemma-4-26B-A4B-it-DFlash-GGUF@d5262e1db8fc07d80f41ef2d0f333eac7978e97a`; `gemma4-26b-a4b-it-dflash-Q8_0.gguf`, 472,433,632 bytes; BF16 control 875,234,272 bytes | Original-IT training, not QAT. Check extracted layers/tokenizer. Its 16-position block bounds width at 15; start with a partial block to measure overhead, then expand if acceptance pays. |
| DSpark, conditional | `williamliao/dspark_gemma4_26b-a4b-it-GGUF@e35d144b11a585cb63b3ba4020ae747fd912adb8`; `gemma4_26b-a4b-DSpark-Q8_0.gguf`, 1,297,022,720 bytes | Original-IT training. Updated bonus-anchor conversion required; author's starting width 5, maximum 6 actual predictions. Verify support against source because published guidance conflicts. |

Resolve hashes with Temper authoring before preview. Inspect native commands,
draft loading and accepted/proposed token counters. Temper 0.5.0 exposes
MTP/DFlash/DFlash2, not a named DSpark method. Do not relabel DSpark as DFlash
without proving the native path preserves Markov/anchor semantics. A missing
supported route is an integration finding; continue other arms. Do not bypass
supervision to force an unsupported candidate through.

Match target, template, thinking, sampling, placement and cache state. Screen
short and populated natural work, then tune plausible candidates. Record draft
overhead/acceptance, generated length and service time separately. Fixed-output
replays resolve throughput questions; ground their length in preceding natural
work. Greedy replay is diagnostic only; selected sampling stays fixed. Different
bytes need semantic review, not automatic rejection. Confirm finalist/plain
pairs in reverse order when variance could change selection.

## Cache and useful context

Begin with F16 KV. Keep it if there is room and lower precision buys nothing
useful. Test Q8 for a demonstrated capacity/latency tradeoff, matched against
F16 for correctness and memory. Do not lower precision merely to reduce RAM.

Freeze a sequence representing continued work: cold input, natural append,
recent correction, correction beyond the sliding window, early-prefix edit,
then conversation A → B → A in the same process/slot. Assert current facts,
invalidated facts and separation between conversations. Keep canonical replays
for timing distinct from revisions using each arm's actual answers.

Tune checkpoint count/spacing from 2 / 8,192 where saved-state coverage can
change an edit's cost. Gemma's sliding-window attention makes restoration
relevant. Inspect saved/restored positions and state bytes; derive candidates
from measured size and intended edit positions, not Qwen's numbers. More
checkpoints cannot recover a changed prefix before all of them.

Size RAM prompt retention from measured A/B snapshots and headroom; prove
retention, eviction and reuse. A nonzero budget alone proves nothing. Keep
checkpoint restoration separate from whole-prompt RAM retention. Test KV-
shifting cache reuse only when the exact engine supports the relevant Gemma
state and a named edit workload benefits. Zero remains its control.
Process-local cache does not establish persistent SSD reuse.

Measure cached/processed tokens, native prefill, first visible answer, service
time, correctness and loaded/peak memory. Recheck drafter/cache interactions
at populated context. Microbatch 512 versus 1,024 is secondary only when
prefill still affects selection; the old pair did not establish a winner.

Carry 128k as a starting allocation, not a ceiling. Extend useful occupancy
toward **262,144 total tokens** after healthy passes within scope, reserving
response room derived from thinking-on work. Keep sparse lookup, dense lookup
and realistic source synthesis separate; vary density independently of
occupancy. Check exact identifier wording before explaining wrong lookup as
capacity. Preserve initial errors and label any prompt treatment.

At a finalist point test natural thinking-on completion, sustained actual
output, subsequent lookup carrying the real answer, and correction. The earlier
4,096-token generation is a comparison workload, not an output ceiling; extend
to natural output needs measured here. Use actual occupied input on each step,
excluding discarded thoughts. Reconfirm drafts/cache there and on fresh work.
Wrong retrieval does not erase resource evidence; sparse success does not
establish general comprehension. Stop an arm when its lack of benefit or
failure mechanism is clear, and use remaining time for justified extensions
and confirmation rather than a broad grid or case-count quota.

## Execution and return

Start a new Field Kit study on the **64 GiB M5 Pro**, with installed Temper and
the existing weights cache. This preparation workspace is on a 32 GiB Mac and
cannot supply that result. Follow [the agent guide](../docs/ASSISTANT.md),
[method](../docs/METHOD.md) and [Temper operations](../docs/TEMPER.md).

The agent on that Mac freezes requests/oracles and exact locks, then prepares
`study init --preview` with resolved materials/disk needs, time scope and
layout stop/restore. Fixed: target, sampling, thinking on, GPU placement.
Bounded: context. Tunable: drafts, cache and evidence-driven thinking allowance.
Machine-side approval covers concrete downloads/runs/effects; do not reopen
old studies or implicitly upgrade the host.

Ground watchdogs in this model's retained 122k prefill times (approximately
185–225 s thinking off), plus measured thinking/output and extension/cleanup
room. Begin generously and revise from first on-mode usage; record the source
or calculation beside each limit. Reserve confirmation time. Do not borrow
another model's deadline or interpret budget exhaustion as a hardware limit.
Finish with a retrospective grounding next limits in actual use, cutoffs and
unused allowances. Never retry an unchanged resource failure.

Return one preferred exact lock/request policy, with an alternative only for
a material tradeoff. Include completed-task defects/revisions, short and
populated latency, native/actual token data, draft/cache counters, loaded/peak
memory by named counter, swap/thermal state, useful occupied context and actual
output/continuation. Keep helpers separate and disclose mmap/counter blind
spots; never sum overlapping memory views. Name the next failed or untested
point and why exploration stopped. An unfinished search is not an optimum.
Preserve every first attempt and repair, close owned resources and export.
Publication and live user selection remain separate actions.

## Sources checked 5 November 2026

- [Google sampling, thinking and history](https://gemma.ai/get-started/best-practices/).
- [Unsloth QAT sampling and context](https://unsloth.ai/docs/models/gemma-4/qat#recommended-settings).
- [Matching QAT MTP](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-qat-GGUF/blob/7b92b5b28818151e8669af2e45e88d6086f490dd/MTP/README.md).
- [26B DFlash config](https://huggingface.co/z-lab/gemma-4-26B-A4B-it-DFlash/blob/main/config.json)
  and [pinned GGUF](https://huggingface.co/Anbeeld/gemma-4-26B-A4B-it-DFlash-GGUF/tree/d5262e1db8fc07d80f41ef2d0f333eac7978e97a).
- [DSpark conversion and bounds](https://huggingface.co/williamliao/dspark_gemma4_26b-a4b-it-GGUF/blob/e35d144b11a585cb63b3ba4020ae747fd912adb8/README.md).
- [b11756 cache controls](https://github.com/ggml-org/llama.cpp/blob/b11756/tools/server/README.md)
  and [checkpoint implementation](https://github.com/ggml-org/llama.cpp/blob/b11756/tools/server/server-context.cpp).
- [Current llama.cpp release](https://github.com/ggml-org/llama.cpp/releases/tag/v0.9.0)
  and [speculation reference](https://github.com/ggml-org/llama.cpp/blob/v0.9.0/docs/speculative.md).
