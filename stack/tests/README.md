# Tests

```bash
tests/run-all.sh              # offline only — hermetic, ~30s, safe anywhere
tests/run-all.sh --runtime    # + live-stack checks (llama-swap must be up)
tests/run-all.sh --perf       # + preload and prompt cache (slow, restarts the service)
tests/run-all.sh --all
```

The offline run is the per-change gate; **`--runtime` and `--perf` are
on-demand**, not a ritual. A full runtime pass loads and churns ~19GB of
weights, which on a 32GB machine is itself a stress event. When only an engine
version moves, the acceptance gate is targeted: check 12's request as a single
curl (it must stream a `tool_calls` delta — `docs/FINDINGS.md` #15 has the
exact command) plus one plain completion. Save the full suite for changes on
the llama-server side or in the generated config topology, or for when
something misbehaves.

These grew out of the acceptance list in the original build spec. The mapping:

| # | check | where |
|---|---|---|
| 1 | `bash -n` + shellcheck | `offline.sh` |
| 2 | `--dry-run` touches nothing | `offline.sh` |
| 3 | two runs → second is `[ok]`/`[skip]` only | **manual**, see below |
| 4 | `enabled: false` round-trips through both configs; empty groups/hooks omitted | `offline.sh` |
| 5 | generated config shape (`useModelName`, kind-flags, ttl) | `offline.sh` |
| 6 | `models.json` merge preserves foreign providers | `offline.sh` |
| 7 | `/v1/models` matches the manifest; coder answers | `runtime.sh` |
| 8 | patch self-heal | `offline.sh` |
| 9 | rerank scores are real, not ~1e-20 | `runtime.sh` |
| 10 | residency after a heavy load matches the manifest's groups | `runtime.sh` |
| 11 | `extract_json` returns schema-valid JSON | `runtime.sh` |
| 12p | preload warms the coder after a restart | `perf.sh` |
| 13 | prefix cache reused across a growing conversation | `perf.sh` |

Added since, not from the original list:

| # | check | where |
|---|---|---|
| 12 | streaming tool calls are parsed, not leaked as text | `runtime.sh` |
| 14 | the two sudo scripts derive their values from the machine | `offline.sh` |
| 15 | invalid manifests are refused before anything is written | `offline.sh` |
| 16 | the launchd plist is derived from the machine, not hardcoded | `offline.sh` |
| 17 | compact-test-output's deterministic parsers behave | `compact-test-output.test.mjs` |
| 18 | project_search candidate, rerank and logging boundaries behave | `project-search.test.mjs` |
| 19 | the GPU budget advisory derives from machine and manifest | `offline.sh` |
| 20 | the machine report travels alone and buckets correctly | `offline.sh` |
| 21 | uninstall.sh removes what is ours, keeps what is theirs | `offline.sh` |

Check 15 exists because every case in it is *legal YAML going in* — `enabled:
no`, a quote in `display_name`, a `flags: |-` block, a `ttl` that renders an
unclosed flow sequence. What made them dangerous was that they were wrong on
the way out, silently: setup printed `[install]`, exited 0, and llama-swap's
`--watch-config` picked the broken file up on a service that had been working a
second earlier. The assertion that matters most — that the live config was
never touched by any of them — sits near the end, before the exit-code and
not-over-rejecting cases.

Checks 17 and 18 run the two extension harnesses. Both are
**plain JavaScript** — there is no `package.json`, no `node_modules` and no
build step. `.mjs` rather than `.js` because without a `package.json` a `.js`
file is CommonJS unless node infers otherwise from the syntax; `.mjs` says ES
module outright. Run it directly while iterating:

```bash
node --test tests/compact-test-output.test.mjs
node --test tests/project-search.test.mjs
```

The module under test stays TypeScript, which is not a preference: Pi discovers
`~/.pi/agent/extensions/*.ts` and `steps/45-extensions.sh` installs by that
glob. Node 24 strips its types on import, so that costs no toolchain either.

Each extension is deployed as a single self-contained file copied flat into
`~/.pi/agent/extensions/`, so testable helpers cannot be split into repo-local
modules. Each harness copies its source to a temp file: check 17 removes the Pi
import that is only used inside the event handler, while check 18 also supplies
the tiny TypeBox schema surface evaluated at module load.

Check 18 covers the whole `project_search` boundary with injected command and
HTTP adapters: query-term extraction, `rg`/`fd` candidate merging, complete
reranker-response validation, the broken-GGUF magnitude gate, physical-batch
fallback, the fixed top-five result cap, private replayable JSONL logging, and
literal argv handling for shell metacharacters. It never contacts the service
or the network.

It earned its place immediately: the first run found two bugs that had been
shipping silently. `/name="/` matched inside `classname="`, so every junit
finding was named after its class twice; and a self-closing `<testcase/>` —
how passing tests are normally emitted — was read as an opening tag, so the
passing test inherited the *next* test's `<failure>` and got reported as the
failure. Both were pointing agents at innocent code.

**Check 16 must never speak to launchd**, and passes `LOCALAI_SKIP_LAUNCHCTL=1`
to make sure it cannot. The plist label is a constant, so `launchctl load` from
a test running under a scratch `HOME` does not create a second service — it
*replaces the real one* with a job pointing into a temp directory that is
deleted moments later. That is not hypothetical: it happened while this check
was being written, and `:8080` went down with it. A scratch `HOME` isolates
files; it does not isolate anything launchd keys by label.

Check 3 is not automated because it must run against the real machine to mean
anything:

```bash
./setup.sh && ./setup.sh
# second run: ok N · installed 0 · patched 0 · changed 0
```

The two `[manual]` sudo items stay `[manual]` forever until you actually apply
them. That is not a failure.

## offline.sh

Hermetic. Copies the project to a temp dir and runs everything against a scratch
`HOME` and `HF_HOME`, including a fake HF snapshot for the patch test. Touches
nothing real, needs no network and no service — safe to run on a configured
machine, in CI, or on a box that has never seen this stack.

## runtime.sh

Needs llama-swap answering on `:8080`. **First run is slow**: llama-server pulls
the reranker (~0.6GB) and NuExtract3 (~2.7GB plus a 675MB mmproj) on first
request. Subsequent runs are quick.

Check 9 is the one worth reading carefully. A broken Qwen3-Reranker GGUF — one
missing `cls.output.weight`, which is most of the community conversions —
returns a perfectly healthy HTTP 200 with every score at ~1e-20. So the test
asserts both ordering *and* magnitude. If it reports degenerate scores, the
answer is to swap the repo, not to loosen the test.

Check 12 is the same shape of trap for tool calling, and it **must** stream. A
non-streaming request falls back to a generic parser and passes even when the
real parser is unbound, so `curl` looks healthy while every agent is broken —
see `docs/FINDINGS.md` #8. It pins `tool_choice` to the function on purpose: the
check is about whether a tool call gets *parsed*, not about whether the model
feels like making one. Left to its own judgement on a bare prompt the coder
sometimes answers "I cannot access local files" instead, which is a real
question but a different one.

Check 14 asserts the parts of `scripts/` that would otherwise silently bake in
*this* laptop — the memory target and the log owner. It needs no root: the RAM
probe, the account and the destination path are all overridable, which is the
only reason a 64GB machine's target can be checked from a 32GB one. It pins
32GB → 24576 MB specifically, because that is the configuration `FINDINGS.md`
was measured on and a formula change that moved it would invalidate those
numbers silently.

Check 12 runs after check 10 deliberately. Check 10 asserts what is still
resident straight after the heavy load in check 11, so slipping an unrelated
coder request in between changes what it measures.

## perf.sh

Slow and disruptive: restarts the service and loads ~15GB. Not in the default
run. `LOCAL_AI_PERF_NO_RESTART=1` skips the launchd restart (and with it
check 12p) so check 13 can run against a llama-swap that launchd does not
own — the field kit's foreground serve is the consumer.

Check 13 measures a conversation that **grows by one exchange per turn**. This
matters more than it sounds — see `docs/FINDINGS.md` #1:

- a byte-identical repeated request shows zero reuse *even when the cache is
  working perfectly*. The original explanation here — "the flag targets prefix
  extension, not repeats" — was wrong and finding #10 retracted it: the cache
  *does* find the repeat and reports `cached=10468 remaining=0`, then discards
  the hit on purpose, because this backbone cannot be trimmed. It is not free;
  #13 measured it at 17.2s against 2.0s for the same content as an extension;
- the retained entry takes two turns to establish, so anything shorter than four
  turns will tell you the cache is broken when it is not.

It reads rapid-mlx's `tokens_to_prefill` scheduler counter rather than trusting
wall clock, which is noisy when the box is swapping. Check `sysctl
vm.swapusage` before believing a timing regression.

## cache-ab.sh

Not an acceptance check — a **tuning harness**. It drives rapid-mlx directly on a
scratch port (bypassing llama-swap, so it neither disturbs nor is disturbed by
the live stack), runs a growing conversation with and without the flags you
give it, and prints both sets of prefill counters.

```bash
tests/cache-ab.sh --hybrid-cache-entries 8      # the run that produced FINDINGS #1
tests/cache-ab.sh --kv-disk-checkpoint-interval 0   # queued A/Bs: docs/PLAN.md §4
```

Reach for this whenever you are tempted to add an engine flag "because the docs
say it helps". On this stack the docs have been wrong more often than right.

## edit-ab.sh — moved

The edit-format A/B harness, its 16-task corpus and the extensions it measures
now live in a separate experiment repo. Nothing here depends on them.

## engine-ab.sh — the one-engine A/B harness

Not an acceptance check. It answers "could this stack drop an engine?", and it
produced FINDINGS #13.

```bash
# stop llama-swap first — 16GB of weights per engine will not fit twice
launchctl stop com.llamaswap.server
<start one engine on :10098 with its production flags>
AB_OUT=/tmp/ab tests/engine-ab.sh 10098 mlx 3
<stop it, start the other on :10099>
AB_OUT=/tmp/ab tests/engine-ab.sh 10099 gguf 3
AB_OUT=/tmp/ab tests/engine-ab-summary.sh mlx gguf
```

`LOCAL_AI_AB_MODEL` names the model id explicitly. Unset, the script asks the
server — right for a bare engine on a scratch port, ambiguous through
llama-swap, whose `/v1/models` lists every manifest entry.

Both arms get the identical client path — same prompt bytes, same wall clock,
same derived metric. That is the whole point: the engines report *different*
quantities in their own counters, and an earlier comparison that mixed
engine-reported decode against end-to-end wall produced a near-tie that could
not be trusted either way (the later like-for-like measurement, FINDINGS #13,
found decode genuinely tied). Wall clock is the only metric both sides can be
held to.

Four measurements per arm, and the last two are the interesting ones:

| test | what it isolates |
|---|---|
| `decode-rN` | short prompt, 300 tokens — wall is decode-dominated |
| `prefill-cold` | ~14k unique tokens, 8 out — wall is prefill-dominated |
| `turn-1..5` | conversation growing one exchange per turn: the agent shape |
| `repeat-turn5` | byte-identical resend — where the two engines diverge 21× |

Read `sysctl vm.swapusage` before and after. A run under memory pressure is not
a measurement, and the numbers in FINDINGS #13 were taken at ~2.5GB of 4.1GB.
