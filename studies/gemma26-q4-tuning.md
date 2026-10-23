# Gemma 26B Q4 quality and useful-context tuning

Question: can the selected Gemma 4 26B-A4B QAT Q4 configuration complete source,
chronology and revision work more reliably before considering a larger quant?
Target machine: M5 Pro / 64 GiB. Keep llama.cpp; the owner closed the Gemma 26B
Rapid/vLLM comparison after reviewing `gemma26-r2`. This continuation does not
change the separate Muse or Gemma 31B investigations.

Start with sampling and thinking-budget comparisons, then confirm useful work.
Runtime speed/cache experiments are secondary. Q8 is a deferred comparison,
not a selected replacement. This brief prepares the next study; it does not
start inference, download material or change the installed selection.
Upstream references below were checked on 23 October 2026; their claims are
expectations to test, not new measurements on this Mac.

## Control and existing inputs

Reuse the [prepared control lock](muse-gemma-engines/locks/gemma-26b-qat-q4-llama-plain-131072.lock.json)
and [authoring catalog](muse-gemma-engines/catalog.json). The lock matches the
returned baseline, SHA256
`67ab26cf6e9054ee6aa9cfe091f9e94977c163e6b62319c366721bfd66e1b616`:

- `unsloth/gemma-4-26B-A4B-it-qat-GGUF` revision
  `7b92b5b28818151e8669af2e45e88d6086f490dd`, `UD-Q4_K_XL`.
- llama.cpp b11756, GPU placement, F16 KV, Flash Attention on, one slot,
  131,072-token allocation, batch/microbatch 1,024, four threads, plain decoding.
- Thinking off; temperature 1, top-p 0.95, top-k 64, min-p 0,
  repetition penalty 1, presence penalty 0; 4,096 output tokens.
- Prompt reuse on, two context checkpoints, checkpoint spacing 8,192,
  RAM prompt cache disabled. Preserve these unless that control is under test.

Use [thinking-off controls](muse-gemma-engines/workloads/gemma-controls-llama-off.json)
and [1K-thinking controls](muse-gemma-engines/workloads/gemma-controls-llama-thinking.json)
as source inputs. Their scenes, notices and revisions have already been used;
they are diagnostic controls, not held-out quality evidence. Keep originals
unchanged and freeze treatment copies in the new study before inference.

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

## Quality comparisons, in order

1. **Sampling on completed tasks.** Compare temperature 1 against 0 and 0.3
   with thinking off and all other request/runtime settings unchanged. The
   hypothesis is improved source/chronology consistency, not restoration of
   quantized information. Temperature 1 is the
   [published recommendation](https://unsloth.ai/docs/models/gemma-4/qat#recommended-settings),
   not a demonstrated optimum for these tasks. Repeat stochastic treatments
   with a small seed set frozen before inference; the same seed is not an
   identical token trajectory across settings. Review prose quality as well as
   facts, so deterministic but poor writing does not win by consistency alone.

2. **Thinking with room to finish.** At fixed sampling, compare off, 1,024 and
   4,096 reasoning-budget tokens with the same 8,192-token total allowance for
   every arm. Keep the original 4K-output runs as historical controls, not a
   matched budget comparison. Preserve `enable_thinking`,
   `reasoning_budget_tokens` and the actual stream; `reasoning_effort=medium`
   alone does not set a budget. The native budget is per thinking block and may
   restart if a block reopens, so record total reasoning, cutoffs, reopened
   blocks and remaining answer space. An allowance increase is useful only if
   the completed answer improves enough to justify its time. A truncated run
   does not establish the quality of naturally completed thinking.

3. **Independent confirmation.** Freeze fresh English/Ukrainian source,
   scheduling and revision cases and their rubrics before any candidate sees
   them. Compare the provisional finalist with the original plain control.
   Require correct source precedence, unsupported-claim handling, feasible
   chronology, requested lengths, viewpoint and consistency between calculation
   and final prose. Preserve first answers and revisions of each arm's actual
   answer. Report fact errors, style/length misses and repair effort separately;
   no claim of general quality from a single sample or aggregate score alone.

An explicit extract/calculate → draft → verify workflow is a separate optional
prompt treatment at an unchanged runtime. Freeze its instructions and allowed
repair before running it, retain intermediate outputs, and count total service
time/tokens across passes. It may improve the delivered work without improving
the bare model. Do not mix it into the sampling or budget comparisons.

Record quality alongside elapsed/first-answer time, native prefill/decode rates,
actual input/output/reasoning counts, and loaded/peak memory with the counter.
Longer thinking leaves weight storage unchanged but can increase KV use and
latency; memory and speed for the proposed treatments remain unmeasured.

## Useful context and secondary runtime work

Keep F16 KV and the native positional settings. Current evidence gives no
reason to trade cache precision for memory or alter RoPE. A larger allocation
does not repair retrieval. Evaluate sparse lookup, dense lookup and realistic
source work separately, varying occupancy and record density independently.
Use the existing context/cache generators, retaining native token counts,
semantic correctness and cold/append/early-edit results. Keep output plus input
inside the configured window; expand toward 262,144 only for a useful question
within the approved scope. Preserve successful execution with wrong answers.

For a speed or interaction question that still changes the user's choice:

- **Microbatch 512:** repeat the matched 512/1,024 pair in reverse order with
  the same material, prompts and cache state. Confirm prefill separately from
  decode and correctness. The existing pair showed no memory advantage.
- **Cache checkpoints/RAM cache:** change one control at a time for a realistic
  edit or conversation-switch workload. Measure reused tokens and latency as
  well as extra resident memory. Ordinary append reuse already works; more
  checkpoints cannot recover a prefix changed before the earliest checkpoint.
- **MTP:** optional M5 Pro recheck, not an untried quality improvement. The
  [earlier M5 study](muse-gemma-engines.md#evidence-to-carry-forward) found only
  about 2% time improvement on five tasks and additional errors for Gemma 26B;
  exact memory for that five-task pair is not included in this brief. No MTP
  trial was made in `gemma26-r2`. A
  [matching QAT drafter](https://huggingface.co/unsloth/gemma-4-26B-A4B-it-qat-GGUF/blob/7b92b5b28818151e8669af2e45e88d6086f490dd/MTP/README.md)
  exists. Prepare its exact artifact and supported Temper lock before including
  it in material scope; report the full draft/runtime memory increase and
  short/populated-context acceptance, quality and time. Retain plain unless a
  repeatable benefit survives completed-work review.

Do not run a broad settings grid. Stop an arm when its mechanism or lack of
useful benefit is clear. Use remaining time for confirmation and unresolved
questions; it is not a requirement to exhaust the budget.

## Preparation and return

Follow the [shared execution/return instructions](64gib-studies.md#shared-execution-and-return).
Start a new study from the existing exact control, with installed Temper and
the tester's existing weights cache. Freeze the initial requests, evaluation
rubrics, matched comparisons, material/time limits and confirmation allowance
before inference. Declare fixed material/KV/placement and which settings may
vary. Native runs still use Field Kit's normal scope approval; preparing this
brief does not reopen the closed study or approve new downloads.

Return the selected exact requests/locks, meaningful quality failures and
repairs, latency/memory costs, useful occupied context, and rejected settings
with their reasons. Keep MLX out of this Gemma 26B continuation.

If Q4 tuning does not answer the quality question, propose a separate original
non-QAT `UD-Q8_K_XL` comparison in llama.cpp. It changes checkpoint as well as
precision; its runtime memory and latency remain unmeasured here. Do not
requantize the existing QAT artifact and assume more bits improve it:
[Unsloth's QAT analysis](https://unsloth.ai/docs/models/gemma-4/qat#qat-analysis)
documents conversion sensitivity. No Q8 material or run is included by default.
