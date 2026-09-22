# Qwen machine study

Measure Qwen3.8 27B performance across Macs and find useful context lengths and
runtime settings for each machine. The results can inform the Qwen model card
and recommendations for machines with similar chips and memory.

Package: `qwen-machine-study@2`. Setup installs its required Temper host.
Model, software and workload inputs are unchanged from revision 1;
process supervision and session/action records have changed. Live measurements
are still needed. Keep revision 1 evidence with its producing source; see
[development and dispatched runs](../DEVELOPMENT.md). Follow the [run guide](../START.md)
for revision 2 setup.

## Requirements and cost

| Requirement | This study |
|---|---|
| Machine | Apple Silicon Mac with at least 32 GiB RAM |
| Free disk space | About 35 GiB |
| Model download | About 17.6 GB; reused across configurations within the run |
| Baseline measurement | Up to 45 minutes |
| Optional tuning | Up to four additional hours |
| Model and engine installation | Separate six-hour limit |

The script also checks the Mac's wired-memory limit for GPU work: it must be
at least 24 GiB. The study leaves that setting unchanged. Time limits are
maximums, not duration estimates.

Choose **performance only** for the common baseline, or **performance and
tuning** to include configuration comparisons and context tests. You can also
request one tuning group before confirming the run:

```sh
./field-kit contribute --tuning flags
./field-kit contribute --tuning context
```

Both include the baseline. The study uses a separate installation, removes it
after finishing, and retains the results.

## What the result tells you

- **Performance:** prompt-processing and generation speed, time to first output
  and first answer, total request time, correctness, memory use and swap growth.
- **Settings:** whether one tested change gave a repeatable improvement over
  the baseline on this machine.
- **Context:** how much input the model handled correctly, and which tested
  sizes also met the study's response-time budget.

Baseline and tuning results stay separate. Comparisons across machines must use
this same workload and configuration. Equal RAM does not imply equal speed;
chip variants and operating conditions matter too.

## Baseline and measurements

The [execution lock](../../catalog/packages/qwen-machine-study@2/execution.lock.json)
fixes the exact model and software. The baseline is:

| Component or setting | Value |
|---|---|
| Model | Qwen3.8 27B UD-Q4_K_XL |
| Chat template | Frog v22.5 |
| Engine and router | llama.cpp b10964 / v0.4.1; llama-swap v255 |
| Context window / output allowance | 32,768 / 4,096 tokens |
| Thinking / draft prediction | Medium / MTP, up to 3 tokens |
| Context working memory (KV cache) | Q8 precision |
| Batch / microbatch | 512 / 512 tokens |
| Prompt-cache settings | 14 checkpoints; minimum spacing 8,192 tokens; 2,048 MiB extra RAM |

The [workload](../../catalog/packages/qwen-machine-study@2/workloads.json) has eight
tasks: create and amend a notice, look up a registry, continue and rewind that
conversation, switch to another registry, return to the first, and transform a
roster. Conversation history uses the delivered answers and omits prior reasoning.

Each first answer is checked against facts in the supplied documents. JSON key
order and whitespace may vary. Wrong values, extra fields, duplicate keys and
truncated responses fail the task. Incorrect answers remain recorded alongside
their timings.

Tokens are the pieces of text processed by the model. Speed figures are medians
of per-request engine measurements:

- **Prompt processing (prefill)** counts newly processed input; cached input is
  recorded separately.
- **Generation** includes reasoning as well as the delivered answer.
- **First output** is the first nonempty reasoning or answer chunk. **First
  answer** is the first answer chunk. Headers and empty events count as neither.
- **Total request time** ends when the response finishes. The first baseline
  request includes model loading.

File caches may already be warm from verifying the downloaded model. Missing
or invalid counters leave performance unresolved while preserving the answer.
Per-request measurements remain available so fresh prompts and cached
conversations can be examined separately.

## Settings compared

Each configuration uses the same model, template and software, starts a fresh
model process, and records its exact settings. The study tests these changes
independently:

| Variant | Change from baseline |
|---|---|
| `batch-1024` | Increase batch size to 1,024; keep microbatch at 512 |
| `mtp-off` | Disable the built-in draft predictor, or multi-token prediction (MTP) |
| `cache-reference` | Use 16 checkpoints and no extra prompt-cache RAM |
| `kv-q4` | Use Q4 precision for the KV cache; model weights stay unchanged |

A correct, valid trial at least **10% faster** in total request time can nominate
one candidate. Confirmation uses four fresh runs: baseline, candidate,
candidate, baseline. The candidate must improve both pairs by at least 10%,
answer every task correctly, and make no individual task more than **20% slower**.

Missing or invalid confirmation leaves the choice unresolved. A valid comparison
that misses those thresholds retains the baseline. Successful changes are kept
separate; their combination has not been tested.

## Context tests

The context window must hold both the input and generated output. The study
checks windows of **16,384, 32,768, 65,536, 98,304 and 131,072 tokens**, alternating
Q8 and Q4 cache precision at each size. Other settings stay at the baseline.

Each test reserves 4,096 output tokens and 1,024 tokens for a follow-up, so the
initial input is the window size minus 5,120 tokens. The running server's chat
template and tokenizer construct that input; the response's token count must
confirm its length. This preparation loads the model before context timing starts.

A generated ledger places five facts across the input. The first request asks
for three; a follow-up asks for the other two and must still leave room for
4,096 output tokens. An incorrect answer stops larger tests for that cache
precision. An invalid measurement or safety stop ends tuning. Untested sizes
remain unknown.

The report gives two separate limits for each cache precision:

- the largest tested input answered correctly;
- the largest tested window that also completed the initial answer within
  **300 seconds** and the follow-up within **60 seconds**.

These are sampled results under a declared latency budget. They do not establish
a universal maximum, performance at untested sizes, or broad long-document
quality. Context tests use baseline flags, so a faster flag setting and a larger
context are not a tested combination.

## Run limits

The four-hour tuning budget covers configuration trials, confirmation and context
tests together. A final one-minute step consolidates the observations. Failed
and interrupted attempts remain in the result.

The engine memory limit is the smallest of 75% of physical RAM, the existing
wired-memory limit, and 96 GiB. The router has a separate 2 GiB limit. The study
stops on 512 MiB additional swap, thermal or CPU throttling, missing resource
observations, or loss of confidence about which processes it owns.

The server listens only on this machine. Temper supplies process identities and
verifies listener ownership and shutdown. Field Kit measures those identities,
chooses when to stop and requires Temper's shutdown confirmation before removing
the experiment installation. Reports and evidence remain.
See the [run guide](../START.md#stop-or-continue) for interruption recovery.

## Review a result

Use the same Field Kit source revision that produced the result:

```sh
./field-kit witness --input /absolute/path/to/result.json \
  --package catalog/packages/qwen-machine-study@2/package.json
```

This checks file consistency, grades delivered answers again, and recomputes
configuration choices and summaries. Machine provenance and timing plausibility
still need human review. Results describe the measured machine and workload;
model-card updates and recommendations across machine groups require that review.
