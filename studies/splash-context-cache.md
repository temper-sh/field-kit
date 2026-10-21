# Finish Splash Q4 on 64 GiB

Question: which context/cache settings make Qwen3.8 27B Q4 useful on the
contributor's 64 GiB M5 Pro, and what smaller-memory profile follows from them?
This continues the returned Splash work. Engine selection and coding
qualification are already outside this study's question.

## Starting point and gap

Use `qwen3.8-27b-q4xl-splash` as the baseline: Unsloth UD-Q4_K_XL,
Frog v22.5 template, Splash 1.3.2, the Inco DFlash2 sidecar, int8 KV,
medium thinking, 131,072-token allocation and 48 GiB engine allowance.
Sampling is temperature 1, top-p .95, top-k 20, neutral penalties.
The installed catalog may precede this locally prepared 64 GiB profile;
inspect and author an isolated equivalent rather than assuming publication.

The returned `qwen-q4-64-results.zip` is the owner-held evidence source
(SHA-256 `a5e3db2381b664516dc1fb584e01e59018dce6d594bab459f1b20df6bd913ec2`).
Run `20261020T170830465306Z` observed 19.22/21.63 GiB loaded/peak system wired
growth, zero swap growth and at most 59,744 occupied tokens at a 131,072
allocation. Run `20261020T164010204583Z` filled a 65,536-token allocation:
63,881 input plus 1,655 output. These establish real populated use, but not
128k occupancy or a dedicated cache study. Coding defects and a missing
submission marker do not erase these measurements.

The [layout assessment](https://temper-sh.github.io/models/qwen3.8-27b/layouts/splash-ud-q4-k-xl/)
owns the maintained conclusion. The 32 GiB observation was short input with a
different memory counter; do not treat it as a controlled memory-scaling pair.

## Investigation

1. Check installed Temper, exact material and effective settings. Run a cheap
   one-shot response and a populated cache sequence to establish the new
   apparatus. Reuse prior task evidence; do not rerun the whole coding campaign.
2. Start near the known useful occupancy. Measure cold input, append using the
   actual preceding answer, and an early source correction. Increase through
   useful points such as roughly 64k, 96k and 120k input in a 128k window, with
   output and continuation room. Native token counts own occupancy. Let the
   next trial follow what the previous one teaches, rather than filling a matrix.
3. At a promising populated point, compare int8 with BF16 KV using identical
   source material, sampling and output limits. Record both memory and quality;
   extra precision needs a useful gain. Explore memory allowance independently
   of context. A smaller cap may trade prefill/cache behavior against headroom.
   Inspect installed supported controls; batch size and draft block width are
   not currently general Splash tuning fields in Temper.
4. Confirm finalists with sustained output, another lookup after that output,
   and continuation. Do not count a short lookup's unused output allowance as
   a completed reserve test. Include at least one realistic growing coding or
   source-review conversation; sparse retrieval is a capacity probe.
5. Extend beyond 128k when the result and time/memory budget justify it, toward
   the model's 262,144-token native limit. Use coarse extensions, then refine
   a boundary that changes the recommendation. A timeout, wrong answer,
   allocation refusal and exhausted study budget mean different things.
6. Repeat only consequential close comparisons. Test a smaller allowance for
   the [48 GiB projection](64gib-studies.md#what-can-transfer-to-48-gib).

Use the selected medium-thinking configuration for catalog memory evidence.
Thinking-off diagnostics can isolate cache/capacity costs, but label them and
confirm the intended profile separately. Keep maximum occupied context,
allocated window and cumulative tokens across requests distinct.

## Cache questions

Measure in-process prefix reuse first: cold → append → early edit. Retain native
cached/processed counts if exposed, correctness and first-visible/service time.
Different output lengths can hide or exaggerate reuse, so use a matched small
answer for the cache timing comparison.

Persistent SSD prefix caching is a separate question. Temper's current
`splash/v2` renderer explicitly disables it and exposes no policy to turn it
on. Complete in-process/cache-precision work now; report the missing control
for repair here. If a reviewed Temper build later exposes it, use a study-owned
cache and compare restart reuse, early-edit invalidation, disk size and warm
latency. Do not silently edit generated launch scripts or the user's cache.
Lossless prefix reuse and quantized KV storage are different mechanisms.

## Return

Recommend a balanced profile and only materially useful alternatives, such as
a lower-memory setting or a larger context window with its latency cost.
For each, return the lock, populated workload, cold/append/edit numbers,
prefill/decode rates or explicit missing values, loaded/peak counter values,
observed output/continuation room and next failed or untested point.
Record missing SSD support and any runtime repairs even when the rest finishes.
The [shared return](64gib-studies.md#shared-execution-and-return) feeds Results
and catalog review; it does not publish or change a live layout.
