# Engine findings

Things that are true about this stack **and that its own documentation gets
wrong**. Every entry was established by measurement, not by reading. Each one
carries the command to re-derive it, because all of it is version-specific and
will rot.

Verified 2026-07-28 against: rapid-mlx 0.11.0, llama-swap v242 (7aa7f52),
llama.cpp build 9810 (2f18fe13c), hf 1.24.0, Pi 0.82.1, macOS 26.5.1 (M5,
32GB — the sweep of 2026-08-05 corrected this from "26.5", which was never
the resident version).

**Engines upgraded 2026-08-01** to rapid-mlx **0.11.5**, llama-swap **245**,
llama.cpp **b10200**. Only what findings #12 and #13 touch was re-verified on
those versions: #3's chain-of-K clamp is still present, #10's repeat discard
still happens (now quantified, #13), and #1's prefill rate has moved from ~140
to ~180 tok/s. The full runtime suite passed 12/12 on these versions on
2026-08-04 (release gate, PLAN §0.2): #7's reranker scores are still
non-degenerate and #8's streaming tool-call parse still binds. That is
functional verification, not re-measurement — **everything else below still
carries its 0.11.0 / b9810 provenance** and should be re-derived before being
leaned on.

**rapid-mlx moved again 2026-08-05**: the cutover to the brew 0.11.9 bottle
was reverted the same day (#15), and the venv was then upgraded in place to
**0.12.3** to carry the #1359 hang fix. On 0.12.3 only #15's request shape
and a plain completion are re-verified. 0.12.x crosses cache-relevant changes
upstream (transient-prefix exclusion, long-context memory-pressure work,
SuffixDecoding), so #1's prefill numbers, #10's discard behaviour and #13's
table carry **pre-0.12 provenance** until re-derived. 0.12.3's first serving
day also produced #16 (GPU-OOM engine aborts under concurrent load), whose
crash reports show the OS has moved too: **macOS 26.6 (25G72)** — the 26.5.1
above is provenance for the 2026-07-28 sweep, not the current floor.

When something here stops matching reality, fix the entry *and* the flag it
justifies. A stale finding is worse than no finding: it will be used to defend a
flag that is no longer doing anything.

---

## 1. `--hybrid-cache-entries` is load-bearing, not tuning

Without it the prefix cache never engages on the coder, every turn re-prefills
the entire conversation, and pinning the model resident buys nothing.

Measured on a conversation that grows by one exchange per turn, reading
rapid-mlx's own scheduler counter (`tokens_to_prefill` is what it actually
computes; `prompt_tokens` is what was sent):

| turn | baseline | `--hybrid-cache-entries 8` |
|---|---|---|
| 1 | 527 / 527 · 3.70s | 527 / 527 · 3.89s |
| 2 | 559 / 559 · 4.08s | 540 / 559 · 4.20s |
| 3 | 591 / 591 · **4.90s** | **53** / 591 · **2.07s** |
| 4 | 623 / 623 · **5.93s** | **53** / 623 · **2.05s** |

Baseline latency climbs every turn because it re-prefills everything. With the
flag, prefill pins at ~53 tokens and latency goes flat. Confirmed through
llama-swap out to turn 5 (`655 → 53`).

**It takes two turns to warm up.** Turn 2 reuses almost nothing (19 tokens); the
retained entry only pays off from turn 3. A two-message test shows nothing and
will make you conclude the flag is useless — it is not, you just did not let the
conversation grow.

**A byte-identical repeated request also shows nothing.** The first attempt at
this measurement sent the same prompt three times and saw zero reuse. Test the
shape you actually care about — prefix extension (stable prefix + new suffix) is
what agent turns do, and it is what this table measures.

The *reason* exact repeats show nothing was originally recorded here as correct
behaviour. It is not: the cache finds them and the scheduler throws the hit away.
See finding #10.

Re-derive: `tests/cache-ab.sh` (see `tests/README.md`).

## 2. The coder is a hybrid; rapid-mlx's static profile says otherwise

`rapid-mlx info froggeric/Qwen3.6-27B-MLX-4bit` prints `Architecture: pure
attention`, and the boot log says `is_hybrid=False`. Both are wrong.

```bash
# The checkpoint itself:
jq -r '.text_config | {layer_types, mamba_ssm_dtype, mtp_num_hidden_layers}' \
  ~/.cache/huggingface/hub/models--froggeric--Qwen3.6-27B-MLX-4bit/snapshots/*/config.json
# -> layer_types: ["full_attention", "linear_attention"], mamba_ssm_dtype set
```

rapid-mlx's *runtime* probe gets it right — `Runtime probe: model has ArraysCache
layers — marking as hybrid` — but that fires after the static profile has already
decided what to enable. This mismatch is the root cause of finding #1: recurrent
state cannot be trimmed the way attention KV can, so ordinary prefix caching
finds nothing reusable and nothing turns the non-trimmable path on for you.

## 3. MTP needs a sidecar head, and `rapid-mlx info` lies about it

`config.json` declares `mtp_num_hidden_layers: 1`, so `rapid-mlx info` reports
`MTP path: native` and `Spec decode: ✓ supported`. But the 4-bit conversion
ships **zero MTP tensors**:

```bash
jq -r '[.weight_map|keys[]|select(test("mtp";"i"))] | length' \
  ~/.cache/huggingface/hub/models--froggeric--Qwen3.6-27B-MLX-4bit/snapshots/*/model.safetensors.index.json
# -> 0
```

rapid-mlx builds the module, finds nothing to load, and **hard-fails at boot**
rather than silently serving without speculative decoding:

```
[mtp.inject] refusing to ship a random-init MTP head
RuntimeError: MTP speculative-config was set but the family MTP injector
rejected the model. Refusing to boot with MTP silently disabled.
```

llama-swap surfaces this only as `upstream command exited prematurely`, and
`/running` shows the model stuck in `starting`. When a model will not start,
**always read the upstream log** (`curl -s localhost:8080/logs/stream/upstream`),
never just llama-swap's own.

Fixed by naming the sidecar in the flag:
`{"method":"mtp","model":"mlx-community/Qwen3.6-27B-MTP-4bit","num_speculative_tokens":3}`
(258MB, `model_type: qwen3_5_mtp`, `hidden_size: 5120` — must match the base
model's MTP module or the injector rejects it too).

**`num_speculative_tokens: 3` is effectively 1.** The boot log says
`[MTP-chain-of-K] SSM cache detected in model_cache — clamping max_k from 3 to 1
(chain-of-K on SSM-hybrid targets needs per-position snapshots not yet wired)`.
Left at 3 in the manifest because that is the value to raise once rapid-mlx
wires up per-position snapshots.

## 4. The published rapid-mlx CLI reference is stale in both directions

`docs/reference/cli.md` in the Rapid-MLX repo omits `--pflash` and
`--kv-cache-dtype` entirely, lists `--kv-cache-quantization` as the current
spelling (it is a deprecated alias), and shows an MTP invocation whose shape does
not match what the binary requires. It also lists `qwen3_coder_xml` as if
`qwen3_coder` were not a valid alias — both work.

Trust `rapid-mlx serve --help` from the installed binary. Nothing else.

Both flags the docs omit turn out to matter, and the manifest's overrides are
correct:

- `--pflash` defaults to **`always`** for the Qwen3.5/3.6 family, with
  `--pflash-threshold 32768` and `--pflash-keep-ratio 0.20` — i.e. it silently
  discards ~80% of any prompt over 32K tokens. `off` is not optional for agent
  work.
- `--kv-cache-dtype` defaults to **`int4`**. `int8` is what rapid-mlx's own
  `--reasoning` profile pins, and int4 is unevaluated on code.

## 5. llama-swap's top-level `groups:` is legacy

Canonical is `routing.router.settings.groups`. Both are accepted by v242, but the
schema says *"Alternative to the legacy top-level 'groups'/'matrix' keys; a
config must not use both styles."* The generator emits the canonical form.

Field semantics (`swap` / `exclusive` / `persistent` / `members`) are identical
in both. Authoritative source:

```bash
curl -sSL https://raw.githubusercontent.com/mostlygeek/llama-swap/refs/heads/main/config-schema.json | jq .
```

## 6. The Rapid-MLX installer breaks on a stale `~/.rapid-mlx`

It decides "a venv already exists" from `[ -d ~/.rapid-mlx ]`, then runs
`~/.rapid-mlx/bin/pip`. But it writes `telemetry-consent.yaml` into that
directory *before* creating the venv. An interrupted or partially-removed run
therefore leaves a directory with no `bin/`, and the installer dies under
`set -e` with the real error swallowed by `2>/dev/null`.

`steps/20-engines.sh` used to move a stale directory aside (preserving the
consent answer) before installing; that mitigation left with the curl
installer itself in the 2026-08-04 formula migration — setup no longer runs
the upstream installer at all. If you ever run it by hand, remove the whole
directory first, not just its contents.

## 7. The reranker pin is a good one

Most community Qwen3-Reranker GGUFs are missing `cls.output.weight` and return
~1e-20 for every document — which still looks like a healthy HTTP 200.
`Voodisss/Qwen3-Reranker-0.6B-GGUF-llama_cpp:Q8_0` is not one of those:

| document | score |
|---|---|
| the relevant one | **0.0152** |
| distractor | 0.000042 |
| distractor | 0.000019 |

~360× discrimination. Absolute values are low, which is normal for this model —
**ranking and separation are what matter, not magnitude**. Anything uniformly
around 1e-20 means the GGUF is broken; swap the repo rather than shipping it.

Re-derive: `tests/runtime.sh` (check 9).

## 8. `--tool-call-parser` alone silently disables tool calling

Passing `--tool-call-parser qwen3_coder` **without** `--enable-auto-tool-choice`
is worse than passing neither. It does not select a parser — it removes one.

The flag's help text said, on 0.11.0, that it "selects the tool call parser
for the model" (0.12.3's help now appends "Required for
--enable-auto-tool-choice" — upstream partially fixed the wording),
which is true of the `python -m vllm_mlx.server` entry point (`server.py:2500`
comments `_enable_auto_tool_choice = True  # Implied by --tool-call-parser`).
`rapid-mlx serve` is a *different* entry point and binds the parser only when
both flags are present:

```python
# vllm_mlx/cli.py:2735
if args.enable_auto_tool_choice and args.tool_call_parser:
    server._enable_auto_tool_choice = True
    server._tool_call_parser = args.tool_call_parser
else:
    server._enable_auto_tool_choice = False
    server._tool_call_parser = None          # <- the parser is thrown away
```

It compounds: auto-detection (`cli.py:2517`) runs only `if not
args.tool_call_parser`, and that is the branch that would have set *both*. So
naming the parser explicitly suppresses the code that would have enabled it.
Omitting the flag entirely works; naming it does not.

**Why this survived acceptance:** the two paths degrade differently.

| | parser bound | no parser |
|---|---|---|
| non-streaming | qwen3_coder | generic fallback (`helpers.py:2784`) — still parses |
| streaming | qwen3_coder | `_create_tool_parser` returns `None` — no fallback |

A `curl` without `"stream": true` returns a clean `tool_calls` array and
`finish_reason: "tool_calls"` either way. **Every agent streams.** Streaming with
no parser emits the raw template text as assistant content:

```
<tool_call><function=read><parameter=path>demo.txt</parameter></function>
```

with `finish_reason: "stop"`. Pi renders that as a chat message and calls no
tool, so the local model could not use *any* tool — this was not specific to one
tool or one extension.

Re-derive (the `stream: true` is the whole test — without it both arms pass):

```bash
curl -s -N localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model":"qwen3.6-27b-mlx","stream":true,"max_tokens":200,
  "messages":[{"role":"user","content":"Read demo.txt and tell me line 7."}],
  "tools":[{"type":"function","function":{"name":"read","description":"Read a file",
    "parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
| grep '^data: ' | sed 's/^data: //' | grep -v '^\[DONE\]' \
| jq -c 'select(.choices[0].delta.tool_calls) | .choices[0].delta.tool_calls'
```

One `tool_calls` delta means it works; empty output means the parser is gone and
the XML came through as `delta.content`. Covered by `tests/runtime.sh` (check 12).

## 9. A hung request has generated thousands of tokens you cannot see

Filed upstream 2026-07-31 as
[raullenchai/Rapid-MLX#1359](https://github.com/raullenchai/Rapid-MLX/issues/1359).
**Closed as completed 2026-08-02** by PR #1391 — "bound tool-call streaming
suppression so it can't wedge" — shipped in **v0.11.9** (verified against the
issue, PR and release notes 2026-08-04). The fix is deployed here since
2026-08-05 (the venv's in-place upgrade to 0.12.3). **The passive ledger
closed 2026-08-07** (PLAN §3a step 7): 24 task-shaped Pi turns on the fixed
floor — sampling A/B armA4 + armB, 12 runs each, watchdog armed at 240s —
with **zero unbounded hangs**. The one loop that occurred terminated
*visibly* (FINDINGS #18's class, and see its outcome line: the engine now
breaks loops in-stream). Read everything below as the diagnostic runbook
and the historical record, not as current behaviour; any true hang — an
assistant message left open with keepalives and no termination — re-opens
this with fresh captures.

A request can burn its entire timeout generating tokens that never reach the
wire. From the client it is indistinguishable from the server never having
answered, and the client-side token counters say zero — which is how it gets
mistaken for an infrastructure stall.

Measured, from one 300s timeout during an edit-format A/B run (that work now
was a separate experiment, see docs/DECISIONS.md):

```
[REQUEST] POST /v1/chat/completions stream=True msgs=2 tools=2 max_tokens=16384
[stream_outputs] f93960c4-f51 first token after 27.6s
[stream_outputs] f93960c4-f51 CancelledError after 2207 tokens, 300.7s
[stream_outputs] f93960c4-f51 ABORTING orphaned request (2207 tokens generated in 300.7s)
[disconnect_guard] CLEANUP done, 1 chunks total, elapsed=300.8s
```

2207 tokens generated; **one** chunk on the wire, and that one was the opening
`{"delta":{"role":"assistant"}}`. A healthy turn on this stack logs 5–39 chunks
and finishes in 3–16s.

The client-side view of the same request:

- Pi emits assistant `message_start` and then **zero `message_update` events**;
- `message_end` never arrives, so `usage` is never reported and every token
  counter reads `0`.

That combination — client says nothing happened, server says 2207 tokens — is
the signature. To confirm one, match the run against the upstream log:

```bash
curl -s -m 8 localhost:8080/logs/stream/upstream \
| grep -E 'CancelledError after|chunks total'
```

**What is actually on the wire, byte for byte** (captured 2026-07-30 from a 518s
hang, via the capture buffer described below):

```
data: {"id":"chatcmpl-57a0fdaa",…,"choices":[{"index":0,"delta":{"role":"assistant"}}]}

: keepalive

: keepalive
…26 of them, one every ~20s, and nothing else, for 518 seconds
```

Those keepalives are `[disconnect_guard]`, which polls every 0.5s and logs every
5s. They are why **no timeout anywhere can fire**: the socket never goes idle.
The captured request carried `X-Stainless-Timeout: 300` and still ran 518s — Pi's
own client timeout did not save it. Only an external kill ends one of these.

They also explain the client-side view above: Pi's `message_start` is emitted
from that opening role delta, so a hung run always ends on a `message_start`
with no matching `message_end`. That unmatched-`message_start` tail is the
cheapest client-side detector there is.

**llama-swap gives you a greppable marker for free.** Every one of these leaves
an adjacent pair in `~/Library/Logs/llama-swap.log` (11 occurrences on
2026-07-30, always adjacent, never apart):

```
[INFO] <qwen3.6-27b-mlx> recovered from upstream disconnection during streaming
[WARN] error processing streaming response: no valid JSON data found in stream, …
```

The `[WARN]` is the **metrics parser**, not the fault: it is llama-swap failing
to find a usage chunk in a stream that contained only SSE comments. Do not chase
it as a cause — it is the most visible symptom and it points at the wrong layer.

**Post-hoc forensics beat the streaming log**, which is a bounded ring buffer and
will have rotated by the time you look. `captureBuffer: 15` in the generated
config keeps the last 15 request/response *bodies*:

```bash
# timestamps (note: `timestamp` is when the request ENDED), status, duration
curl -s localhost:8080/api/metrics/activity \
| jq -r '.data[]|[.id,.timestamp,.resp_status_code,.duration_ms,.tokens.input_tokens]|@tsv'
# the exact bytes of one exchange — both fields are base64
curl -s localhost:8080/api/captures/<id> | jq -r .resp_body | base64 -d
```

That is how the wire dump above was obtained, hours after the fact. Note that
`input_tokens: 0` in the activity table means *the metrics parse failed*, not
that no work was done — short successful requests report `0` too, so it cannot be
used as a hang detector.

**Cause is not established.** The consistent facts are that generation ran
normally and the streaming layer emitted nothing, which fits a block that is
buffered until its closing tag and never closed — both `<think>` and
`<tool_call>` are handled that way. That is a hypothesis; it has not been
reproduced deliberately, and `--no-thinking` plus the R12-T1F
`enable_thinking=False` injection are both already in force on this stack, so
the obvious explanation is also the one with the least support.

**It is not a property of the request.** On 2026-07-30 a 518s hang was replayed
from its own capture, byte for byte, three times: 60s, 73s, 68s. Restarting the
service first changed nothing (weights went 2.18GB → 10.8GB resident; the times
did not move), so "the weights had paged out" is ruled out as well. The same
request, at the same point in the same conversation, is a ~40–70s turn. Whatever
turns it into a 518s hang is state on the box at that moment, and the one
difference on record is memory pressure: swap was 2826MB/4096MB during the hang
and 1.8GB/3.0GB during every replay. Not proven — but it is the next thing to
instrument, and it is consistent with the standing advice in CLAUDE.md to start
every investigation of this kind at `sysctl vm.swapusage`.

The load-bearing consequence is for measurement, not for the engine: **never
classify a zero-token timeout as infrastructure noise and exclude it.** It is a
failed task that happens to be invisible from the client. Any harness that reads
client-side usage will score it as "nothing happened" and, if it excludes such
runs, will silently drop real failures from its comparison. The edit-format
experiment hit exactly that; the fix is to detect the hang from the transcript
rather than the clock (docs/DECISIONS.md).

## 10. A 100% prefix-cache hit is discarded on purpose — the hybrid cannot be trimmed

`--hybrid-cache-entries 8` reuses a *partial* prefix correctly and discards a
*complete* one. The scheduler's own log says so in as many words. Measured
2026-07-30, same 10,468-token request throughout:

| `[cache_fetch]` says | `[schedule]` then does | wall |
|---|---|---|
| `HIT cached=6643 remaining=63` | `tokens_to_prefill=63, 6643 cached` | 2s |
| `HIT cached=6706 remaining=250` | `tokens_to_prefill=250, 6706 cached` | 9s |
| `HIT cached=6956 remaining=3512` | `tokens_to_prefill=3512, 6956 cached` | 39s |
| `HIT cached=10468 remaining=0` | `tokens_to_prefill=10468` | 60s |
| `HIT cached=10468 remaining=0` | `tokens_to_prefill=10468` | 73s |
| `HIT cached=10468 remaining=0` | `tokens_to_prefill=10468` | 68s |

Note the shape of the `[schedule]` line, which is the tell: on a partial hit it
carries a `, N cached` suffix, and on `remaining=0` that suffix is absent
entirely. 3/3.

Re-derive:

```bash
curl -s -m 4 localhost:8080/logs/stream/upstream \
| grep -E 'cache_fetch|\[schedule\] request'
```

**It is deliberate, and it is architectural — not a bug and not our flags.**
`_resolve_exact_hit_tokens` (`vllm_mlx/scheduler.py:4384` on 0.11.x; ~5874 on
0.12.3) has to re-forward the
last prompt token to get logits for the next position, and to stay byte-equal to
a cold prefill it must first trim the saved cache by 1 to un-write that doubled
token. `can_trim_prompt_cache` is `all(c.is_trimmable() for c in cache)`. This
model never satisfies it: the coder is a hybrid (#2), and

```python
# mlx_lm/models/qwen3_5.py:305
return [ArraysCache(size=2) if l.is_linear else KVCache() for l in self.layers]
```

gives every linear/SSM layer an `ArraysCache`, which inherits
`_BaseCache.is_trimmable() → False`. So the scheduler takes its documented
correctness-first fallback: drop the reuse and full-prefill, which is byte-equal
to cold. Its own comment is *"Only an identical re-request of a rotated prompt
loses reuse."* Skipping the trim instead would drift the first generated token —
they verified that on gpt-oss-20b.

**`--kv-cache-dtype int8` is not the cause** — turboquant's `is_trimmable`
returns `True`. Nothing in `models.yaml` changes this. A real fix needs SSM state
rollback, i.e. per-position snapshots, which is the same upstream blocker that
caps `num_speculative_tokens` in #3.

**This corrects finding #1's explanation, and #1 was closer to right than the
first version of this entry.** #1 saw byte-identical repeats get zero reuse and
called it correct behaviour. That is true of the *outcome* and of the intent; it
is only the stated reason ("the flag targets prefix extension, not exact
repeats") that is off. The cache layer does find exact repeats and reports
`cached=10468 remaining=0` — the reuse is then dropped on purpose, for
correctness, because this backbone cannot be trimmed.

Three practical consequences:

- **The blast radius is narrow.** Only byte-identical re-requests hit it. Ordinary
  agent turns append a new suffix, so `remaining >= 1` and they take the
  trim-free path by construction. What is affected is retries — a harness
  resending after a timeout, or Pi's own client retry — and any benchmark that
  replays a prompt, which is exactly what misled #1.
- **Where it does bite, make the prompt differ by one token.** Appending `" x"`
  to the last message took the same request from 68s to 28s and moved it onto
  the reuse path (`tokens_to_prefill=3827, 6643 cached`). Note it recovered 6643
  tokens, not ~10466 — how much comes back depends on what is still resident in
  the 8-entry cache, so this halves the cost rather than eliminating it.
- **The cache is small and easy to evict.** `entries=` in the `cache_fetch` line
  counts them, capped at 8. Five unrelated probe prompts drove it from 8 down to
  `entries=2` and turned the next repeat into an outright `MISS`. Interleaving
  arms, or running anything else against the endpoint mid-measurement, silently
  changes what you are measuring.

## 11. Assorted smaller facts

- The coder's **vision tower is unavailable**: it `auto-downgraded to the
  text-only mlx-lm lane` because the MLLM engine cannot serve a hybrid backbone
  (Rapid-MLX #352). `qwen3.5-4b` is the vision path; that is what it is for.
- `llama-server -hf` **does** auto-fetch mmproj files (confirmed: NuExtract3
  pulled a 675MB `mmproj-NuExtract3-BF16.gguf` unprompted), and it caches into
  the standard HF cache, not a private one.
- The reranker's quant tag resolves despite the unusual filename — `:Q8_0`
  correctly matched `Qwen3-Reranker-0.6B.Q8_0.gguf` (dot-separated, not the usual
  dash).
- llama-swap **parses `cmd` with shell-like quoting**: the single-quoted JSON in
  `--speculative-config` survives intact as one argv entry. Verified by reading
  the launched process line out of `/running`.
- **`yq -i` will destroy `models.yaml`.** It rewrites the whole document —
  stripping the quotes around the `--speculative-config` JSON and reflowing every
  comment. `setup.sh` only ever *reads* with yq. Edit the manifest by hand.
- Pi tolerates the `_generated_by` key in `models.json` (verified: it still lists
  and selects the model).
- `~/.config/llama-swap` was a dotfiles symlink whose target had been deleted,
  which makes `mkdir -p` fail with a bare `ENOENT`. It is now a real directory,
  since `config.yaml` is a generated artifact. `~/.config/zshrc.d` is still
  dangling the same way — unrelated, but you will trip over it eventually.

## 12. llama.cpp reuses the exact repeat that rapid-mlx throws away — and its hybrid checkpoints work

Measured 2026-08-01 on llama.cpp **b10200**, `froggeric/Qwen3.6-27B-MTP-GGUF:Q4_K_M`,
run directly on port 10099 (llama-swap stopped) — the probe that gates the
one-engine A/B in docs/PLAN.md.

**Upstream #24055 does not reproduce.** A conversation grown one exchange per
turn prefills only the new tokens:

| turn | prefilled | cached | prefill wall |
|---|---|---|---|
| 1 | 2615 | 0 | 18848ms |
| 2 | 113 | 2611 | 1289ms |
| 3 | 113 | 2720 | 1265ms |
| 4 | 113 | 2829 | 1267ms |
| 5 | 113 | 2938 | 1259ms |

The wall times corroborate the counters independently (turn 1's 2615 tokens
at ~139 tok/s; the warm turns' ~1.27s is far below the ~19s a full re-prefill
would cost at that rate), so this is reuse, not a mislabelled counter.

**A growing conversation does not test checkpoints, though**, and that is the
trap: appending never needs the recurrent state rewound, so it exercises
nothing #24055 is about. The test that does is replaying an *earlier* turn
after the slot has moved past it:

| probe | prefilled | cached | wall |
|---|---|---|---|
| repeat turn 5 verbatim | 4 | 3047 | 250ms |
| **rewind to turn 3** | **113** | **2720** | 1228ms |
| forward to turn 5 again | 222 | 2829 | 1759ms |

The rewind restored from a checkpoint instead of re-prefilling 2833 tokens.
Checkpoint restore works on b10200.

**The repeat row is the one that matters for engine choice.** It is exactly the
case FINDINGS #10 documents on the MLX side, where a 100% hit is discarded and
the whole prompt re-prefills. llama.cpp reuses it. Same class of operation,
opposite behaviour.

**Re-derive:**

```bash
llama-server --port 10099 -hf froggeric/Qwen3.6-27B-MTP-GGUF:Q4_K_M \
  --no-mmproj --parallel 1 --spec-type draft-mtp --spec-draft-n-max 3 -fa off \
  --cache-ram 12000 --ctx-checkpoints 64 --checkpoint-min-step 512 \
  --ctx-size 16384 -b 512 -ub 512 --chat-template-kwargs '{"enable_thinking":false}'
# then grow a conversation and read .timings.prompt_n / .timings.cache_n
```

**Three flags in that line are load-bearing and none were in the parked
manifest entry.** Without them the model loads, reports `listening`, answers
`/health` with `ok` — and then dies on the first 10-token request with
`kIOGPUCommandBufferCallbackErrorOutOfMemory`:

- **`--parallel 1`.** Current llama.cpp defaults to `-np -1` (auto), which
  resolved to **4 slots** here, so `--ctx-size
  32768` silently means 4 × 32768 of KV.
- **`--no-mmproj`.** It auto-downloads and loads an **f16 mmproj** vision tower
  for this repo. Nothing in this stack asks for vision from the coder.
- **`-b 512 -ub 512`.** With `-fa off` (which the model card requires for this
  architecture) the default `-b 2048` attention buffer does not fit under the
  24576MB wired limit alongside 16GB of weights and a second MTP draft context.

`--checkpoint-min-step` is a fourth trap of a different kind: it defaults to
**8192 tokens**, so at default spacing an ordinary agent conversation never
creates a second checkpoint at all. A benchmark that leaves it alone will
report on a mechanism that was never engaged.

**Preliminary, n=1, not the A/B.** One 300-token generation each: llama.cpp
14 tok/s decode at 98.8% MTP draft acceptance (83 of 84); the incumbent MLX
coder 109 tokens in 8401ms wall through llama-swap ≈ 13 tok/s. That is nearer
parity than "MLX is faster on Apple Silicon" implies, but it is one sample per
side, measured two different ways (engine-reported decode vs end-to-end wall),
under unequal configs — llama.cpp was handicapped to half context and a
quarter batch to fit at all — and **swap was at 3.6GB of 5.1GB during the MLX
sample**, which CLAUDE.md's own rule says invalidates a timing. Treat it as a
reason to run PLAN §4.3 properly, not as its result. **Superseded by #13**,
which ran it properly.

## 13. The one-engine A/B: generation is a tie, prefill is not

Measured 2026-08-01 (docs/PLAN.md §4.3). Both arms served the **same model**
(Qwen3.6-27B, 4-bit) run **directly on a scratch port with llama-swap stopped**
— 16GB of weights per engine means they cannot coexist on 32GB, and it keeps
the live stack out of the numbers, same reasoning as `tests/cache-ab.sh`.

Arms: rapid-mlx 0.11.5 with the production flags from `models.yaml`; llama.cpp
b10200 with the fixed `qwen3.6-27b-mtp-gguf` flags from `models.example.yaml`.

| test | Rapid-MLX | llama.cpp |
|---|---|---|
| decode ×3 (45-tok prompt, wall-derived) | 12.4 / 13.5 / 12.7s → **13.2 tok/s** | 12.3 / 12.3 / 12.8s → **13.3 tok/s** |
| cold prefill, 14,207 tok | 78.9s → **180 tok/s** | 128.5s → **111 tok/s** |
| turn 1, 2,585 ctx | 14.9s | 26.4s |
| turns 2–5, warm | 1.9 / 1.8 / 1.7 / 2.0s | 2.3 / 2.1 / 2.2 / 2.1s |
| byte-identical repeat | **17.2s** | **0.8s** |

**Methodology, and why it is not the n=1 comparison in #12.** Identical client
path for both arms: same prompt bytes, same wall clock, same derived metric.
Engine-reported counters were deliberately *not* used for headline numbers —
the two engines report different quantities, and mixing them is exactly what
made #12's preliminary reading untrustworthy. Swap held at ~2.5GB of 4.1GB
across both arms, so no timing here is a swap artifact.

**Three results worth keeping separately from the decision:**

1. **Prefill on this stack is now ~180 tok/s, not the ~140 of finding #1.**
   That number predates the M5 Metal-4 path and rapid-mlx 0.11.5. Anything that
   budgets prefill time — `tests/cache-ab.sh` expectations, the pi-tool-ab
   watchdog's 150s quiet limit — was calibrated against the old figure.
2. **The repeat penalty of #10 costs 8.8×, quantified.** Same 2,901-token
   content: 1,960ms as a prefix extension, 17,215ms byte-identical (the ratio
   is from those, not from the rounded seconds). llama.cpp serves
   the same repeat in 0.8s. #10 says this is deliberate (the hybrid cannot be
   trimmed, so a complete hit is discarded); this is what deliberate costs.
3. **MLX ties decode with speculation effectively off.** Finding #3's
   `clamping max_k from 3 to 1` is still in the 0.11.5 boot log, while
   llama.cpp ran draft-n 3 at 98.8% acceptance (83 of 84). The tie is between
   MLX-unaccelerated and llama.cpp-accelerated.

**The draft-n 1–3 sweep was deliberately skipped**, not forgotten. Speculative
decoding changes generation only, generation is already a tie, and the verdict
rests on prefill — which `--spec-draft-n-max` cannot affect. Sweeping it could
not have changed the outcome.

**What is and is not new here.** Throughput comparisons of these two engines on
this model already exist —
[stared/benching-local-llms-on-apple-silicon](https://github.com/stared/benching-local-llms-on-apple-silicon)
covers llama.cpp ± MTP vs MLX on an M5 Max/128GB and reaches the opposite
verdict, for reasons that are about RAM (see the DECISIONS entry). What that
benchmark does *not* report is the prefill/decode split — it publishes one
combined "total tok/s" — and this table's whole argument lives in that split.
The repeat-vs-extension asymmetry above, and #12's checkpoint results, are also
not throughput-benchmark material. Those are the parts worth citing elsewhere.

**Re-derive:** `tests/engine-ab.sh` runs one arm and
`tests/engine-ab-summary.sh` tabulates (see `tests/README.md` for the exact
invocation). Start each engine on a scratch port with
llama-swap stopped, then `engine-ab.sh <port> <label> 3`. The client resolves
the model id from `/v1/models` because rapid-mlx rejects an unknown id while
llama-server ignores the field, and the two answer in different shapes
(`{data:[{id}]}` vs `{models:[{name}]}`).

## 14. rapid-mlx parks up to 20 GiB of KV checkpoints on disk, by default

Found 2026-08-02 while cleaning the machine: `~/.cache/rapid-mlx` had grown to
**24GB** — larger than any model on the box.

```bash
du -sh ~/.cache/rapid-mlx/*
# 20G  kv_checkpoints      <- 28 directories
#  3.7G prefix_cache       <- 3 models, two of them retired months ago
```

**This is not a leak, and that matters.** The first assumption — that nothing
prunes it — is wrong, and `rapid-mlx serve --help` says so:

> `--kv-disk-checkpoint-interval` — Token interval at which the scheduler
> snapshots KV state to `~/.cache/rapid-mlx/kv_checkpoints/` for resume /
> shared-prefix reload (R15 #296, default 256). 0 disables. Pairs with the
> `RAPID_MLX_KV_CHECKPOINT_MAX_BYTES` env var (**default 20 GiB**) for the
> oldest-first disk-cap eviction policy.

The cache was sitting precisely at its designed ceiling. Eviction works. **The
default is simply sized for a workstation, not a laptop** — and because it is a
cap rather than a leak, it will climb straight back to 20 GiB and stay there,
silently, forever.

Two consequences worth acting on:

- **Set the cap for the machine.** `RAPID_MLX_KV_CHECKPOINT_MAX_BYTES` belongs
  in the launchd plist's environment, computed from free disk the way
  `scripts/gpu-wired-limit.sh` computes from RAM — never hardcoded. Done since
  2026-08-02: `steps/50-service.sh` computes it (10% of free disk, clamped to
  [1, 20] GiB) and writes it into the plist; check 16 tests three disk sizes.
- **The prefix cache keeps entries for retired models indefinitely.** 2.1GB of
  the 3.7GB here belonged to `gpt-oss-20b` and the 35B MoE retired in July.
  Nothing upstream removes a model's cache when it leaves `models.yaml`;
  since 2026-08-05 the models step reports such orphans as a `[note]` with
  the reclaim command (detection only — repos named in `flags`, like the MTP
  sidecar, count as live).

**Whether any of it earns its keep on this stack is untested.** FINDINGS #1
shows the *in-memory* `--hybrid-cache-entries 8` is what makes multi-turn fast,
and #10 shows a complete hit gets discarded anyway because the hybrid cannot be
trimmed. The disk path is for "resume / shared-prefix reload" — a different
case, and one nothing here has measured. `--kv-disk-checkpoint-interval 0` is a
one-flag A/B; if the counters do not move, this stack buys 20 GiB and a write
every 256 tokens for nothing. **Since 2026-08-05 the interval is 0 in
models.yaml** — not from that A/B (still owed, PLAN §4.6) but from #16: the
writer was mid-write in both GPU-OOM engine aborts. The plist's
`RAPID_MLX_KV_CHECKPOINT_MAX_BYTES` cap stays, for anyone who re-enables.

**Reclaim safely** (it is a cache; stop the service first so nothing holds the
files open):

```bash
launchctl stop com.llamaswap.server
rm -rf ~/.cache/rapid-mlx/kv_checkpoints/*
rm -rf ~/.cache/rapid-mlx/prefix_cache/<retired-model-dir>
launchctl start com.llamaswap.server
```

## 15. The 0.11.9 formula crashes streaming tool calls; the 0.11.5 venv it replaced does not

Found 2026-08-05, the morning after cutting the stack over to the
homebrew-core formula (0.11.9). The runtime suite went 11/12: check 12 — a
*streaming* request with a **named `tool_choice`** — wedged ~450s and then
died inside the grammar-constrained tool-call path:

```
vllm_mlx/api/tool_grammar.py … in __call__
OverflowError: out of range integral type conversion attempted
InferenceAbortedError … CLEANUP done, 1 chunks total, elapsed=451.6s
```

Nothing reaches the client, so **every agent turn that carries tools fails —
while non-tool chat answers normally and health checks look perfect.** The
same trap shape as #8, arrived at from a different direction. The 450s wedge
also drove swap to an 11.9GB peak, which is how it announced itself.

What it is *not*:

- **Not a missing parser.** The bottle ships `qwen3_coder` in both parser
  directories, and the log binds it: "Native tool format enabled for parser:
  qwen3_coder", grammar warmup complete.
- **Not the grammar library.** The working venv (0.11.5) and the crashing
  bottle carry identical `llguidance` 1.7.6 and `mlx_lm` 0.31.3.

The error text is PyO3's — Python handing Rust an out-of-range integer — and
the traceback runs through the streaming path that PR #1391 (the #1359 hang
fix) rewrote, which makes that fix the prime suspect for the trigger.

**Probed same day: 0.12.3 fixes it.** In a throwaway uv venv on python
3.14.6 — the same interpreter version the bottle uses — the identical request
streams a parsed tool call. Same interpreter, so the fix is in the
0.11.10–0.12.3 code delta; `tool_grammar.py` itself is untouched upstream
since 2026-07-24, so the fix is on the trigger side. Unreported upstream as
of 2026-08-05 (five search phrasings, issues and PRs); homebrew-core PR
#297106 (0.12.1) was in flight, **unverified** against this crash — the only
verified-fixed version is 0.12.3.

The stack rolled back to the venv the same day (`brew uninstall rapid-mlx` +
kickstart — the service PATH falls through to `~/.local/bin`), and check 12's
request streams a parsed tool call via llama-swap again.
`steps/20-engines.sh` refuses to reinstall the measured-bad formula version.
The venv was then upgraded in place to 0.12.3 to carry the #1359 hang fix —
the crash fix holds on the serving interpreter (py3.12) too, re-verified on
this finding's request shape through llama-swap.

Re-derive: stop the service (two engines' weights do not fit), serve the
coder from the engine under test with models.yaml's exact flags on a scratch
port, and send check 12's request (tests/runtime.sh — `stream: true` plus
`tool_choice` naming one function). A `tool_calls` delta is a pass; content
deltas, silence, or `InferenceAbortedError` in the engine log is the crash.

**Closed 2026-08-06.** The 0.12.4 bottle passed the gate through llama-swap
on the live service: check 12's request streamed a parsed `tool_calls` delta
(`read`, arguments intact, nothing leaked as content) and a plain completion
answered clean. The formula serves; the venv and its seven symlinks are
removed, and the refuse-by-version guard is deleted from
`steps/20-engines.sh` — if a future bottle regresses, the re-derive above is
the test and the guard is the remedy.

## 16. The GPU pool has a fatal edge: one Metal OOM aborts the whole MLX engine mid-turn

Found 2026-08-05 — the first day `project_search` saw real use. The venv
engine (0.12.3) died twice by SIGABRT: at 18:17:00 local (a process llama-swap
had run since 00:21) and again at 18:20:01, when its replacement was three
minutes old. macOS 26.6 (25G72). Crash reports: incidents `9D4AE31F` and
`51E55E96` in `~/Library/Logs/DiagnosticReports/`; llama-swap captures **28**
and **30** hold the two requests that died (~74KB Pi turns, #9 documents the
capture mechanism).

Both reports show the identical faulting stack — MLX checks every completed
command buffer and **throws on any GPU error inside the Metal completion
callback, where an exception cannot propagate**, so `std::terminate` fires:

```
mlx::core::gpu::check_error(MTL::CommandBuffer*)   <- throws std::runtime_error
… MTL::CommandBuffer::addCompletedHandler block    <- com.Metal.CompletionQueueDispatch
… std::__terminate → abort                         <- throwing here is fatal by design
```

The upstream log buffer names the error (crash 2's dump; crash 1's had already
rotated out by capture time — its error *string* is inferred from the
identical stack, not retained):

```
libc++abi: terminating due to uncaught exception of type std::runtime_error:
[METAL] Command buffer execution failed: Insufficient Memory
(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)
```

A transient allocation failure is therefore not a failed request — it is the
end of the process. llama-swap restarts it and health checks pass; the
streaming turn in flight is lost (the client sees a truncated 200 — llama-swap
logs "no valid JSON data found in stream") and the retry pays a full
re-prefill.

What was sharing the 24 GiB wired limit when it tipped over — all from logs:

- **The coder near its ceiling.** `--gpu-memory-utilization 0.85` set
  `allocation_limit=21.9GB (85% of 25.8GB)` — note 25.8GB is already *above*
  the 24 GiB wired limit, so the real headroom under the hard wall is
  ~2.1GB, not 15%. Telemetry in the pre-crash stretch shows
  `active=19.3–19.8GB, peak=23.1–23.2GB` — peaks routinely above the
  (advisory) limit, with steady `prefix-pressure-evict` lines.
- **The reranker, resident.** llama-server (0.6B Q8_0), `ttl: 0`, up since
  01:16. For crash 1 it was also *working*: the `project_search` query is in
  the mode-0600 log at 18:16:51 local, `POST /v1/rerank` returned 200 after
  3.7s — finishing ~5s before the abort, inside the coder's ~9s-open
  streaming window. Before crash 2 **no rerank ran** (query log empty for
  that window): residency alone sufficed. The burst is a trigger, not a
  requirement.
- **#14's disk-checkpoint writer, mid-write in both.** Crash 1's report has a
  thread inside `mlx::core::save_safetensors` under
  `disk_kv_checkpoint.write_checkpoint`; crash 2's faulthandler dump shows
  the same path in `save_prompt_cache`, seconds after the scheduler logged
  `prompt_tokens=19268 … 18782 cached` and `saved 19261 tokens at message
  boundary`. Serializing a ~19k-token KV cache materializes transient copies
  on top of everything above. The writer is the only actor present in both
  crashes.

Which consumer contributed the marginal gigabyte is **not attributable from
these logs**. That is the finding: `--gpu-memory-utilization` reads like a
safety margin, but it bounds one process's steady state on a pool that also
carries a second engine and the checkpoint writer's spikes — and when the
pool runs out, the failure mode is not backpressure, it is engine death.

Not #15 (an `OverflowError` in the Python tool-grammar path — different layer,
different signal) and not #9 (this dies loudly and recovers; nothing hangs).
First observed on 0.12.3's first serving day, but nothing ties it to 0.12.x:
the throw-on-error completion handler is long-standing MLX design and the
checkpoint writer is #14's. n = 2 crashes in one afternoon; the current log
generation holds 8 `/v1/rerank` calls total.

Levers, deliberately not pulled here (PLAN §4.6 owns the decision):
`--kv-disk-checkpoint-interval 0` removes the one actor present in both
crashes — already queued as an A/B on perf grounds, now carrying a stability
datapoint; and the 0.85 utilization can be shaved so the reranker's share
lives outside the coder's budget instead of inside its margin.

Re-derive: crash reports are `Python-*.ips` files with
`parentProc: llama-swap` and faulting queue `com.Metal.CompletionQueueDispatch`
in `~/Library/Logs/DiagnosticReports/`. The error string goes to stderr — read
it promptly via `curl -s localhost:8080/logs/stream/upstream`; the ring buffer
dropped crash 1's dump within the hour. No deterministic repro: the shape is a
long-context streaming turn plus a `project_search` rerank while the
checkpoint interval sits at its default 256.

**Acted on, same day.** `--kv-disk-checkpoint-interval 0` is in models.yaml —
the writer present in both crashes is gone; verified afterwards on a plain
completion and check 12's streaming tool-call shape (PLAN §4.6 still owes the
cache-ab A/B confirming the perf side). The margin arithmetic became
`scripts/gpu-budget.sh` plus a setup.sh note (offline check 19): fraction ×
device memory vs. wired wall vs. co-resident reserve measured from the hf
cache. On this machine it reports 0.85 **short by ~2.8GB** against the worst
co-resident case (reranker + one heavy 4B); a fraction ≤ 0.74 fits. The
utilization value itself is untouched — lowering it trades coder cache
headroom for pool safety, which is a tuning decision, not a bug fix.

**Tuning call, next day (2026-08-06).** 0.85 stays, on the owner's operating
assumption that extract/vision work does not overlap coding turns. The
budget gate was narrowed to match: `--check` now fails only when the
*always-resident* co-tenants + spike do not fit the margin (here: reranker
610MB + 1024MB spike = 1634MB against 2016MB — fits, 382MB to spare). The
heavy-overlap scenario (+3228MB if NuExtract3 is wired too; ≤ 0.74 would
cover it) is still printed by explain mode as accepted-not-gating. Two
caveats recorded with the decision: the assumption is about *usage*, not
physics — a heavy member stays wired for its full ttl (600s) after its last
request, so one extraction leaves the pool short for the next ten minutes
of coder turns; and both #16 crashes needed no heavy model at all — the
checkpoint writer that caused them is off, which is what the 1024MB spike
allowance now stands in for. Un-defer if a #16-shaped abort recurs or if
extract/vision becomes routine mid-session.

**It recurred within hours, and the arithmetic lost.** The full tally is
eight aborts in two days, all with the identical Metal-completion-handler
stack and (where the ring buffer retained it) the identical
`kIOGPUCommandBufferCallbackErrorOutOfMemory` string: six on 2026-08-05
(18:17–18:58 — the two recorded above plus four more, all before the
interval-0 config went live at 20:37), then two on 2026-08-06 at 00:56 and
00:59 during plain Pi coding turns. Those last two are the clean signal:
checkpoint writer off, **no rerank in flight** (ring buffer), swap healthy
(1.6GB of 3GB), and the restarted engine survived only 3.5 minutes. So the
coder at 0.85 plus the 610MB always-resident reranker plus the coder's own
transients (prefill batches, MTP buffers, a growing KV — and the pool is
machine-global: WindowServer and the browser share the same wired wall,
which the budget never modeled) overflow the 24576MB limit on their own.
The previous paragraph's operating assumption is dead: the budget said
"fits, 382MB to spare" and the machine said otherwise, twice.

**Resolution (owner call, 2026-08-06): on 32GB, only the coder resides on
the GPU.** The reranker moved from `utilities`/ttl 0 to `group: heavy` with
`ttl: 120` — it now loads on demand (measured: 2.4s cold load + answer,
against project_search's 180s budget) and gives the GPU back two minutes
after a search burst. `scripts/gpu-budget.sh` gained RAM buckets to keep
the policy portable (README has the table): on 32GB-class machines any
always-resident llama-server entry fails the check *by existing* — no more
arithmetic about whether a resident fits, because the arithmetic was tried
and it lost; below 32GB the pinned coder does not fit at all and the check
stops there; 33–36GB gates the resident set by arithmetic; >36GB gates the
full co-resident stack. Residual risk, stated plainly: n=0 on the new
posture, a rerank still wires ~610MB transiently during searches, and if
aborts continue the remaining levers are the fraction itself (the ≤0.74
arithmetic above) or moving the reranker off the GPU entirely
(`-ngl 0`, unmeasured).

## 17. "ready" is not "resident": an idle coder silently decays to SSD speed

Found 2026-08-06, after a night of idle. The pinned coder (rapid-mlx 0.12.3,
`ttl: 0` at the time) had been serving for ~14.5 hours when a Pi session
resumed against it. llama-swap and the engine both reported `ready`; the
process RSS was **0.17GB** against a healthy ~9.5GB. macOS had reclaimed the
model's mmap'd weight pages during the idle hours — file-backed clean pages
are the first thing the kernel drops under pressure, and it does not care
that a GPU uses them.

The measured cost, from a 15-minute upstream-log capture of the live session:

- **Prefill ~85 tok/s** (three full re-prefills of a ~28k-token
  conversation at 305–343s each — the re-prefills themselves were cache
  divergence, #1/#10's non-trimmable backbone, after interrupted turns).
- **Decode 4.7–5.3 tok/s** against the ~13 tok/s baseline — and it
  **declined during use**, to 2.7–3.6 tok/s by the end of the capture.

The decline is the finding. Warming pages by working should converge to
fast; it converged to slow, because faulting a page in evicts another and
the machine's total working set (15GB weights + 2.4GB KV + the user's
actual day) exceeded 32GB: eviction kept pace with fault-in and the steady
state was decode at SSD-random-read speed. A degraded process **does not
self-heal under pressure.**

A restart is not the same I/O in a worse order — it is the same I/O in a
*better* order: one sequential 15GB pass (~55s to `ready` here), landing in
**wired** Metal buffers (`vm_stat` showed ~21.5GB wired after reload),
which the kernel cannot reclaim the way it reclaimed the decayed pages.
How wired memory demotes to reclaimable over long idle is the open
mechanism question; that it does is the observation.

Two practical rules fall out:

1. **Do not read health from endpoints or RSS.** `state=ready` says the
   process answers; it says nothing about residency. And on 0.12.3, ps RSS
   no longer reflects the weights at all (1.5GB RSS while 21.5GB sat
   wired) — check `vm_stat` wired pages, or just measure a decode.
2. **Cap how long the decayed state can exist.** The coder now carries
   `ttl: 7200`: llama-swap kills an idle process after 2h instead of
   letting it rot overnight. The cost when it fires is a predictable
   ~1-min reload plus one full re-prefill at healthy speed — strictly
   cheaper than the 5-minute crawling turns it replaces. Any break shorter
   than the ttl keeps the radix cache. The 2h value is a judgment call:
   the decay *curve* is unmeasured (14h → 0.17GB is the one data point).

Owed: verify at the first 2h+ idle gap that the ttl unload actually
returns the ~18GB of wired memory (`vm_stat` before/after). The explicit
successor to this automatic guard is the mode switch planned in PLAN §10.

**Corrected 2026-08-06 evening (measurements in #19):** the wired-pages half
of rule 1 is wrong, and the open mechanism question above is answered. Wired
is a **compute-coupled** signal, not a residency signal: on every
configuration probed the same evening — rapid-mlx 0.12.3 and 0.12.4, mlx
0.31.2 and 0.32.0, python 3.12 and 3.14 — the weight pages unwire within
**~8–15 seconds** of the last request, on a perfectly healthy engine, and an
idle box reads ~2.9GB wired regardless of engine health. The "~21.5GB wired
after reload" above was a during-compute reading. What actually varies is
whether the unwired, file-backed pages survive in the *page cache* — a
memory-pressure bet, not an engine state. The 14h decay was therefore not a
slow demotion of wired memory: unwiring happened seconds after the last
request, as it always does, and the overnight hours merely gave eviction
time to win. Rule 1 reduces to: **measure a decode.** The restart cure
stands, for a corrected reason: one sequential 15GB pass repopulates the
page cache faster than random re-faults under load — not because wiring
persists (it does not). The owed `vm_stat` before/after check is moot: wired
returns to baseline seconds after any activity, unload or no unload.

## 18. The hang's successor: a repetition loop, bounded by the engine, 27 minutes later

Found 2026-08-06, ~16:54 local, on the fresh 0.12.3 engine (minutes after the
FINDINGS #17 restart — wired weights, not the decayed state). llama-swap
capture 51 holds the exchange; the payload contains client code, so the
capture is preserved off-repo and this entry carries only its numbers.

A 148-message, ~183KB streaming Pi request produced **10 content deltas**,
then ~80 keepalives across **27 minutes**, then an in-band error and a clean
close:

```
data: {"error": {"message": "Internal error during streaming", "type": "internal_error"}}
data: [DONE]
```

The engine side names the cause and survived it — no crash report, orderly
cleanup:

```
WARNING:rapid_mlx.scheduler:Stopping agent request … after exact token loop
  (period_tokens=28 repeats=3 completion_tokens=3848)
vllm_mlx.request.InferenceAbortedError: Model generation aborted: exact
  repetition loop detected (period_tokens=28, repeats=3), 11 chunks, elapsed=1609.8s
```

Read the asymmetry: **3,848 tokens generated, 11 chunks delivered.** The
model wedged into a 28-token cycle and the tool-call buffering path
suppressed nearly all of it while SSE keepalives defeated every client
timeout on the way (the request carried Pi's usual
`X-Stainless-Timeout: 300` and ran 1,610s). On 0.11.5 this exact shape *was*
FINDINGS #9 — the unbounded, invisible hang. On 0.12.3 it is **bounded and
visible**: the engine's loop detector aborts the generation and tells the
client. The failure family is not extinct; its failure mode improved. While
it ran, co-scheduled requests crawled (a measured 2-token completion beside
it took 18.4s), so the 27-minute bound is also 27 minutes of a shared engine.

What was being sampled when it looped — verified from the capture, not
assumed: **Pi sends no sampling at all** (`temperature`, `top_p`, `top_k`,
`min_p`, `presence_penalty` all null), so the model's `generation_config`
applied: `temp 1.0, top_k 20, top_p 0.95`, and **no presence or repetition
penalty of any kind**. The loop happened *with* temperature sampling on —
this is not a greedy-decoding artifact, and the missing lever is exactly the
`presence_penalty 1.5` in Unsloth's guide values for this family (PLAN
§4.4), which exists because Qwen3.6 repetition-loops in non-thinking mode.

Levers, in order of preference: the loop *detector* has no tuning flags
(only `--default-repetition-penalty` exists as a serve flag, and penalizing
repetition is notorious for degrading code, which is legitimately
repetitive) — so prevention is sampling territory, and the §4.4 A/B decides
it with first-attempt success primary and loop/hang rate secondary. This
entry is the first local incident that item predicted.

**Outcome (2026-08-07): the §4.4 A/B ran, and prevention won on the primary
metric.** Incumbent arm: 10/12 with 2 loop-guard stops (one fatal — see the
addendum below); guide arm (`presence_penalty 1.5` et al.): **12/12 with
zero loop events across its full-arm upstream log**. Adopted same day —
models.yaml's `--default-*` flags carry the numbers, and
`pi-tool-ab/results/sampling-ab/` holds the raw runs.

**Addendum — 0.12.4 improved this failure's shape again.** The 27-minute
in-band error above is 0.12.3 behaviour. On 0.12.4, armA4's two loop events
show the guard now breaks the exact-token loop *in-stream*
(`Broke exact token loop … interventions=N`) and, when the loop persists,
ends the turn gracefully with `finish_reason: length` instead of an error —
the turn completes truncated rather than dying at minute 27. Better shape,
same disease: one of the two still cost the task (a guard-truncated turn is
not an answer), which is what made prevention worth its +18% wall. The
task-level signature to sweep transcripts for is `stopReason == "length"`;
the log signature is `Stopping agent request … after exact token loop` —
and NOT `disconnect_guard`, which on 0.12.4 is routine per-request
lifecycle logging (counting it reads ~367 on a clean arm).

## 19. Weights unwire seconds after compute stops — "resident" is a page-cache bet, and the wired counter measures activity

Found 2026-08-06 evening, because sampling A/B arm A (PLAN §4.4) collapsed
on the fresh 0.12.4 floor. The driver logged 19.7GB wired at run 1 and
2.8GB at every later run start; four of the first six runs tripped the
harness watchdog — 150+ seconds of output silence with an assistant message
open — and the arm was aborted at 6/12 with one task success. The runs are
marked INVALID in `pi-tool-ab/results/sampling-ab/armA/META.txt`; nothing
from them may be scored.

**The weights never left the process.** Through the whole episode the
engine's own telemetry held `[Metal memory] active=17.8–18.4GB` steady.
What collapsed is the *wired* state of the pages backing those buffers —
and probing it isolated a mechanism that has nothing to do with the
cutover:

| serving config (same box, same evening) | wired after last request |
|---|---|
| brew 0.12.4 + mlx 0.32.0 (stock bottle) | 20.8GB during compute → 2.9GB within ~90s |
| brew 0.12.4 + mlx 0.31.2 (shadow-patched Cellar) | 18.8GB → 2.9GB in ~8s |
| scratch uv venv: 0.12.3 + mlx 0.31.2 + py3.12, models.yaml's exact flags on :10099 — the previous day's serving stack, reconstructed | 18.6GB at load → 2.9GB in ≤15s, flat 2.8–3.0GB for 2.25 min |

Engine version, mlx version and interpreter are all exonerated (n=1 each;
the effect is binary and instant, not a rate to power). The variable that
*did* move is baseline memory pressure: the July tool A/B's native arm
recorded `swap_mb` 1044–1076 per run (n=4); tonight the arm ran at
1.9–2.9GB used — roughly double.

Mechanism, synthesized from the above: the weights are file-backed mmap
pages wrapped in Metal buffers. Metal wires them for command execution and
macOS unwires them seconds after the queue drains; whether the *next*
request is fast is whether the page cache still holds those clean,
first-to-evict pages. "Resident" is not a state the engine holds — it is a
bet against everything else the box is doing. Measured re-fault cost on a
then-calm box: **first decode after idle 6.9 tok/s, the next 11.8 tok/s**
(engine log concurs with both). Under arm A the bet lost harder: the
harness's own churn between runs — sandbox `git clone`, node, tool output —
evicted the pages, re-faults went to SSD *mid-prefill*, and prefills that
normally run ~40–70s went silent past the watchdog's 150s. #17's overnight
decay is this same mechanism given fourteen hours instead of sixty seconds
(#17 carries the correction).

Practical rules:

1. **Health is a measured decode.** Wired pages only say whether the GPU
   is executing right now; `ready` says less than that (#17).
2. **Measurement runs need a quiet box, quantified:** at the ~1GB swap
   baseline the July harness runs were fine; at ~2GB+ the harness's own
   file churn evicts the coder between paced runs. Check `sysctl
   vm.swapusage` against ~1GB — not just "is it thrashing" — before an
   engine-timing run, and treat the paced-idle re-fault penalty as part of
   what is being measured if swap is elevated.
3. Not measured: whether wired persists through idle on a pressure-free
   box (all probes ran under the elevated baseline), and the 0.12.3 scratch
   venv's decode rate (its probe rejected the llama-swap model alias —
   only its wired trace is valid).

**Side finding, independent and reportable upstream:** the homebrew-core
`rapid-mlx` formula depends on the separate `mlx` formula (0.32.0), while
rapid-mlx upstream — 0.12.3 and 0.12.4 both — pins `mlx<0.32,>=0.31.2`.
pip refuses this combination; brew ships it silently. It is *not* the cause
of anything in this entry (the 0.31.2 shadow behaved identically, and the
check-12 gate passes on stock), but the serving stack is an
upstream-unsupported dependency set until homebrew-core pins a compliant
mlx or upstream lifts the cap. The Cellar was restored to stock after the
probes and the gate re-passed on it.

**Corrected later the same night: the pin violation is NOT benign — mlx
0.32.0 is a ~3× prefill regression on this model.** Found because arm A's
*rerun* on a post-restart, pressure-free box (swap 80MB, wired weights,
radix cache HITting, AC power, no thermal event, no co-tenant) still
backstopped: the engine ran prefill at ~48–61 tok/s and long-context decode
at ~3–4.5 tok/s, while *short*-context decode measured a healthy 12.9 tok/s
seconds after the abort. A/B with identical `temperature: 0` requests, same
box, minutes apart, only core mlx swapped inside the same bottle:

| probe | mlx 0.32.0 (stock) | mlx 0.31.2 (compliant) |
|---|---|---|
| 128-tok decode, 30-tok prompt | 10.0s (12.9 tok/s) | 9.3s (13.8 tok/s) |
| 3,653-tok full prefill + 44 tok | 64.0s | 22.9s |
| 3,659-tok full prefill + 20 tok | 69.6s | 22.4s |

That is ~55–61 vs ~190 tok/s prefill (n=2 per arm; both full prefills —
the near-identical extension request re-prefilled, the #10 non-trimmable
shape, which is what makes them clean prefill benchmarks). ~190 matches
FINDINGS #13's 180 tok/s venv baseline, so upstream's `<0.32` cap is
load-bearing and the check-12 gate cannot see the breakage: prefill-bound
degradation needs a context-sized request. Task-level corroboration: on the
stock bottle, July's 146–222s corpus tasks ran 251–402s+ all day. The
sampling A/B is blocked on the serving posture (stock = 3× slower agent
turns; the uv-shadowed Cellar = silently reverted by any brew operation;
a compliant uv venv = the DECISIONS-sanctioned exception path). Lesson for
the acceptance gate itself: an engine move needs one *context-sized* timing
probe next to check 12 — a tiny-prompt gate passes a 3×-slower engine.

Re-derive: warm the coder with one small completion, then
`vm_stat | grep "Pages wired down"` every 15s — collapse to ~3GB within a
minute of idle is this finding. Then time a 128-token `temperature: 0`
completion twice back-to-back; a first/second ratio well below 1 is the
re-fault cost, and the engine log's `tok/s` lines give the same numbers.
