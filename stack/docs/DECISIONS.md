# Decisions

Why the stack is shaped this way: what was considered, what was measured, and
what was rejected. **The point of this file is so you don't spend an afternoon
installing something we already tested.**

Most local-LLM advice is written for a different machine than yours. A tool that
is obviously correct behind a metered API can be worthless on a laptop, and the
reverse. So every entry below says *which setup it applies to*, not just whether
we kept it.

If you disagree with a row, re-measure it. Every claim here has a date and a
method, and all of them rot.

---

## What this setup is, and why that changes the answers

Four properties decide almost every call below. Check yours against them before
taking any of this as advice.

| | here | if yours differs |
|---|---|---|
| **Do input tokens cost money?** | No. One user, own hardware, no bill. | Behind a metered API, *anything* that shrinks context pays for itself immediately. Most of the "rejected" section flips. |
| **How many requests at once?** | One. A human is waiting. | Serving several users makes throughput and batching matter; here correctness comes first and latency second, and the engine serialises anyway. |
| **How much memory?** | 32GB unified, shared with the GPU. | More RAM means several models resident and the swapping machinery stops earning its keep. |
| **Is a human reading the output?** | Yes, every turn — which is exactly why a *quiet* wrong answer is the expensive failure here: it gets acted on. | Unattended pipelines cannot catch a confident wrong answer at all, so weight silent-wrong-answer risk even higher than this file does. |

The single most consequential one is the first. **On a local box the unit of
value is a finished task — not a token, and not a second.** Input tokens are
free, so only wall-clock and correctness remain, and correctness dominates: a
wrong edit costs a whole extra turn, prefill included. Seconds are the *second*
test, and they convert at roughly 180 tokens/second of
prefill (measured 2026-08-01; it was ~140 before the M5 Metal-4 path and
rapid-mlx 0.11.5), which is slow enough to matter and fast enough that a few
thousand tokens is a few seconds. That exchange rate is why several popular
tools do nothing here.

---

## At a glance

| considered | verdict here | where it *does* fit |
|---|---|---|
| **llama-swap** as the only endpoint | **adopted** | any machine running more models than fit in RAM |
| **Rapid-MLX** for the coder | **adopted, measured** | Apple Silicon **at 32GB**; on 64GB+ llama.cpp likely wins |
| **27B dense** over a 3B-active MoE | **adopted, not measured** — judgement on output quality | anyone who would trade output quality for tokens/s |
| **llama-server** for everything else | **adopted** | universal; GGUF has the widest model selection |
| **Rerank over grep** | **adopted and built** — the `project_search` tool is the consumer | small corpora; a vector index is overkill under ~100k files |
| **`compact-test-output`** extension | **adopted** | anywhere a test runner can dump 1000 lines |
| Shell-output compressors (`rtk` hint, `hypa`) | **rejected, measured** | metered APIs, or agents that dump whole files |
| Line-anchored edit format ("hashline") | **rejected, measured** | models that reliably count lines — not this one |
| **Ollama** | **rejected, not measured** | people who want one command and no flags |
| **oMLX** (one-server MLX stack) | **rejected, not measured** — four ideas taken | all-MLX model sets; a menubar app instead of a manifest |
| Router / classifier model | **rejected on design** | multi-tenant routing across many backends |
| `jina-reranker-v3` | **deferred, A/B planned** — PLAN §6.1 | anyone whose engine can serve listwise rerank |
| Vector index / embeddings | **deferred** | large corpora, or semantic queries grep can't express |
| OCR and editor FIM specialists | **deferred by use case; kept separate** | recurring document OCR or active inline completion |

---

## Adopted

### llama-swap as the only front door

Everything speaks OpenAI-API to one port. llama-swap starts model servers on
demand, unloads idle ones, and keeps a "pinned" group resident.

**Why it earns its place here specifically:** 32GB cannot hold the coder plus the
specialists. Without swapping you pick one model and live with it. The pinned
group also protects the coder's prefix cache from eviction, which matters more
than it sounds — see the prefill economics above.

### Two engines, and why not one

| | Rapid-MLX | llama-server (llama.cpp) |
|---|---|---|
| serves | the 27B coder (MLX 4-bit) | reranker, extractor, vision |
| why | **prefill: 1.6× faster on this box, measured** | widest model selection; GGUF for everything that doesn't need MLX |

Rapid-MLX was chosen for speed, and llama.cpp's inability to serve this model's
MTP sidecar reinforced it. That blocker fell in May 2026 (llama.cpp merged MTP
at b9180), so in August 2026 the collapse was measured head to head — same
model, same prompts, same wall-clock method, both engines direct on scratch
ports. **The split survives, but not for the reason it was defended with.**

| | Rapid-MLX 0.11.5 | llama.cpp b10200 | |
|---|---|---|---|
| decode, mean of 3 | 13.2 tok/s | 13.3 tok/s | **tie** |
| cold prefill, 14,207 tok | 78.9s (180 tok/s) | 128.5s (111 tok/s) | MLX **1.63×** |
| first turn, 2,585 ctx | 14.9s | 26.4s | MLX **1.77×** |
| turns 2–5, warm | 1.7–2.0s | 2.1–2.3s | MLX ~1.15× |
| byte-identical repeat | 17.2s | 0.8s | llama.cpp **21.5×** |

**Generation speed is a tie, so "MLX generates faster" is not why to keep it.**
The decision rests on prefill, and prefill is what agent work is made of —
large contexts, tool output, a conversation that grows every turn.

Two things make the tie more interesting than it looks. MLX reaches it with
speculative decoding effectively *off*: rapid-mlx still clamps chain-of-K to 1
on SSM hybrids (FINDINGS #3, re-checked on 0.11.5), while llama.cpp was running
draft-n 3 at 98.8% acceptance. llama.cpp needs working speculation to draw
level. If Rapid-MLX PR #1216 lands, MLX should pull ahead on decode too.

And llama.cpp's prefill deficit is partly self-inflicted — it runs at a quarter
batch because the full one will not fit (FINDINGS #12) — but **that handicap is
not removable on 32GB**, so 111 tok/s is its real number on this class of
machine, not a tuning artifact.

**On a bigger Mac this verdict flips, and there is published evidence for it.**
[stared/benching-local-llms-on-apple-silicon](https://github.com/stared/benching-local-llms-on-apple-silicon)
(June 2026) ran the same model on the same two engine *families* on an
**M5 Max / 128GB** at 8-bit — their MLX arm is plain mlx-lm, ours is
Rapid-MLX on the same mlx-lm kernels — and concluded the opposite — llama.cpp
ahead by 10–24%:

| | M5 Max, 128GB, 8-bit (theirs) | M5 Air, 32GB, 4-bit (ours) |
|---|---|---|
| llama.cpp + MTP | 32 tok/s | 13.3 tok/s |
| llama.cpp, no MTP | 18 tok/s | — |
| MLX (no speculation either side) | 17 tok/s | 13.2 tok/s |

The two results are *consistent with* RAM being the variable — an inference
from two n=1 datasets on different silicon at different quantizations, not a
controlled comparison. Our MLX is 1.3× slower than theirs; our llama.cpp is
**2.4×** slower. llama.cpp is the arm that degrades on
a constrained box. Their no-MTP row is the tell: without MTP, 18 vs 17 is a tie
*on their hardware too*, so MTP is the whole of llama.cpp's advantage — and on
32GB we lose essentially all of it to the memory ceiling despite 98.8% draft
acceptance.

**So the engine choice here is decided by RAM, not by the engine.** On 32GB,
keep the split and serve the coder from MLX. With 64GB+, re-measure before
believing this table — the honest expectation is that llama.cpp wins and the
collapse to one engine becomes the right call. Caveats on the comparison: their
figure is 8-bit and bundles reasoning tokens into one "total tok/s", so the
ratios carry the argument, not the absolute numbers. They also do not separate
prefill from decode at all, which is the split our own verdict turns on.

The one case llama.cpp wins, it wins overwhelmingly: the byte-identical repeat
that rapid-mlx throws away (FINDINGS #10). It is also the case agent turns
almost never produce — turns are prefix *extensions*, which is precisely what
FINDINGS #1 measures. A 21× win on a rare shape does not outweigh a 1.6× loss
on the common one.

**Not measured:** task success on a real agent corpus. The throughput result
was one-sided enough on the dominant metric that the ~1h corpus run (at a
3-in-7 hang rate) could not plausibly overturn it. If the engines are ever this
close again, run it. Re-derive both arms with the harness described in
FINDINGS #13.

### A 27B dense coder, not a faster 3B-active MoE

`qwen3.6-35b-a3b` (35B total, 3B active) was deployed here and **retired in
July 2026 on two counts: memory and output quality.** The 27B dense model
replaced it and is what the pinned coder serves today.

This is the decision most likely to be second-guessed, because the speed
argument for the MoE is real and public. The same M5 Max benchmark cited above
recommends exactly this model on llama.cpp with MTP at **~100 tok/s** as its
daily driver, against the ~13 tok/s the 27B dense manages here. That is not a
close call on throughput.

**Retired on judgement, not on a scored corpus.** The memory half is
arithmetic; the quality half was a working conclusion from daily use, and it is
recorded here as such so it is not silently read as measured. The reframed
probe in [PLAN](PLAN.md) §4 states what would settle it: first-attempt task
success on a real agent corpus.

**It is still the wrong trade for this stack, because the currency here is a
finished task, not a token.** A model that emits a wrong edit at 100 tok/s
costs more wall-clock than one that emits a right edit at 13 — the retry is a
whole extra turn, including its prefill. Speed only converts to value once
quality clears the bar, and 3B active parameters did not clear it here on
agentic coding work.

Both parked entries survive in `models.example.yaml` (`qwen3.6-35b-mlx` and a
GGUF fallback) at zero adoption cost, so anyone who values tokens/s over
first-attempt correctness — or has the RAM to stop worrying about the memory
half — can swap them in by editing one manifest.

**What would reopen this:** a measured quality comparison, not a faster
number. The bar is first-attempt task success on a real agent corpus, not
perplexity and not tok/s.

### Rerank over grep, not embeddings

The implemented design: `rg` content hits and `fd` filename hits produce at
most 40 file candidates, a 0.6B reranker scores them, and the top 5 come back
with line-numbered excerpts. No index to build, nothing to keep in sync. The
reranker is deployed and verified (FINDINGS #7), and `project_search` refuses
the known degenerate-score shape rather than ranking it. Queries and candidate
paths are retained at `~/.pi/agent/project-search/queries.jsonl` (following
`PI_CODING_AGENT_DIR`); that real-use log, not a synthetic benchmark, decides
whether the deferred alternatives get evaluated (PLAN §6.1–2).

**Caution, and this one bites:** most community Qwen3-Reranker GGUFs are broken
with llama.cpp. They are missing the `cls.output.weight` tensor and return
degenerate scores (~1e-20) for *every* document — while returning a healthy HTTP
200. If reranking returns uniformly tiny scores, the file is wrong, not your
code.

### `compact-test-output` extension

Summarises test/lint/build output over ~100 lines or ~8KB (the byte half
exists so one-line JSON counts), writes the full text to a file and includes
the path. Narrow on purpose: a large `cat` or `git log` is not test
output and silently summarising it would destroy something the agent asked for.

This is the **only** output-shrinking thing in the stack that survived
measurement, and the reason it works while the general-purpose compressors did
not is that it is conditional and targeted.

### Specialists are capabilities, not inventory

Reconfirmed in the 2026-08-05 architecture review: a specialist enters the
installed manifest only with a live consumer, an acceptance set and a reason
the existing models fail that task. Small weights and an attractive benchmark
do not qualify by themselves. When adopted, the specialist sits behind a typed
tool or its native client; it does not become another general chat choice.

OCR and editor FIM had already been considered and remain deliberately
separate until needed. The FIM candidate stays parked in `models.example.yaml`
for an editor to call through `/infill`. If recurring OCR becomes a real
workflow, `GLM-OCR` 0.9B is the first current A/B candidate against the existing
Qwen3.5-4B and NuExtract3 paths, not a pre-approved addition. Retrieval follows
the same rule: the query log must demonstrate misses before embeddings or Jina
reranking earn an evaluation. The full dated review and its recommendations
are in
[ARCHITECTURE-REVIEW-2026-08-05.md](ARCHITECTURE-REVIEW-2026-08-05.md).

---

## Measured and rejected

### Shell-output compressors — `rtk` via hints, and `hypa`

**The idea:** wrap the agent's command output in a compressor so less of it
reaches the context.

**Measured 2026-07-31.** Four arms — no compressor, `rtk` suggested in the system
prompt, `hypa` additive, `hypa` replace mode — over 16 runs on two multi-turn
tasks.

**One arm produced wrong answers, and that settles it on its own.** Task
scores were 4/4 across all four arms, but a score is not the whole of
correctness: `hypa` additive silently discarded 10 of 13 lines of
`git diff --stat` **3 times out of 3** while labelling the result
`reducer=passthrough`, and the agent answered `3` where the truth was `13`
(detail below). A compressor that drops data under a passthrough label is
disqualified here regardless of what it saves. The throughput case that
follows explains why the *other* arms are not worth their overhead either.

| arm | fixed overhead per turn | median context growth |
|---|---|---|
| none (control) | baseline | 3,588 |
| `rtk` hinted | **+261** | 3,416 |
| `hypa` additive | **+1,007** | 3,671 |
| `hypa` replace | **−4,239** | 3,444 |

**None of them compressed anything.** Context growth — the only thing a
compressor can act on — landed within 7% across all four. Every difference was
fixed tool-schema overhead, decided before any work happened.

Three findings worth carrying:

- **`rtk` was never invoked** — 0 times in 4 runs with the hint in its prompt.
  The failure is *delivery*: suggesting a command to a 27B model does not make it
  type one. `rtk` itself is a good CLI and stays installed for humans.
- **`hypa` additive is strictly negative here.** It registers its tools without
  removing the builtins, so it starts every turn 1,007 tokens behind. It also
  **drops data silently**: on `git diff --stat` it returned the first 3 of 13
  file lines and discarded the summary, 3 times out of 3, while labelling the
  result `reducer=passthrough`. The agent answered `3` where the truth was `13`.
- **`hypa` replace mode was fastest, but not by compressing.** Its win is that it
  *deletes* the builtin tool definitions from the prompt — its own tool output
  was the largest of any arm. See "the useful leftover" below.

**Why compression cannot win on this box.** Correlating wall-clock against every
candidate driver over those 16 runs:

```
output tokens (what the model WRITES)      +0.87
peak context                               +0.73
turn count                                 +0.63
tool output bytes (what a compressor CUTS) +0.43   <- weakest
```

Three reasons, all local:

1. **The agent already defends itself.** Asked how many files are in a tree, it
   runs `ls | wc -l` and reads one number. On a 96KB listing it put ~100 bytes
   into context where `rtk` on the same command returns 16,880. Compressing beats
   nothing only if the bytes were going to exist.
2. **The prefix cache already solved accumulation.** A turn only prefills its
   *new* suffix — 63 new tokens cost 2s where a full 10,500 costs 60s. Shrinking
   a context that is being served from cache buys very little.
3. **Most of the time is writing, not reading.** ~625 output tokens per run,
   roughly a third of wall-clock, untouchable from the input side.

All told, every byte of command output across 16 runs came to about 19 seconds of
prefill against a 173-second average run. **A perfect, free, lossless compressor
saves ~11% — that is the ceiling**, before the tool's own overhead.

**When to ignore all of the above:** behind a metered API where input tokens are
billed, with a model that reads whole files instead of scoping, or on an engine
with no prefix cache. Then compression is straightforwardly worth it.

**The useful leftover:** the builtin tool schema costs **~4.2k tokens on every
turn** (turn-1 context 6,690 with the builtins, 2,451 without). That was the whole
of replace mode's advantage and it needs no compressor — just a shorter tool list.
Tracked in [docs/PLAN.md](PLAN.md).

### Line-anchored edit format ("hashline")

**The idea:** give the model an `edit` tool that addresses lines by a short hash
instead of by quoting surrounding text, which upstream reported cutting output
tokens by 61%.

**Measured 2026-07-28, and rejected.** Baseline string-replace scored 30/32 strict
passes; hashline scored **24/32**. Output tokens fell only 20.8%, against the
published 61%.

**Where it fits:** plausibly on models that track line numbers reliably. This one
does not, and a format that saves tokens while losing 6 edits is a bad trade at
any token price.

### Wall-clock timeouts as a hang detector

**The idea:** cap each run; if it exceeds the cap, call it hung.

**Rejected — it cannot work here.** The engine sends SSE keepalives while it
works, so a wedged request never trips *any* timeout; one ran 518 seconds through
a client whose own timeout was 300. And the clock cannot separate a hang from a
slow success: passing runs took 146–222s, so the "obvious" 180s cap would have
failed three of four real passes.

**Instead:** detect it from the transcript — an assistant message left open with
no matching close and no new output. Cheap, exact, and it works regardless of how
long a legitimate turn takes.

---

## Rejected without measuring, and why that is defensible

Not everything needs a benchmark. These were turned down on properties that a
measurement would not change.

### Ollama

Pulls models into a proprietary blob store and abstracts away the engine flags.
This stack exists *because* the flags matter — see `FINDINGS.md`, where most of
the load-bearing settings are ones the upstream docs get wrong. Everything here
comes from HuggingFace into the standard cache, and every flag is visible in one
manifest.

**Where Ollama fits:** you want it working in one command and never want to think
about `--kv-cache-dtype` again. That is a completely reasonable thing to want.

### oMLX (looked at 2026-08-08 — read, not run)

[jundot/omlx](https://github.com/jundot/omlx): one Python process serving
LLMs, VLMs, embedders and rerankers from a single memory pool — LRU eviction,
per-model TTL, pinning, a RAM-minus-8GB envelope enforced at runtime — behind
OpenAI *and* Anthropic APIs, with a native menubar app and one-click client
integrations, Pi included. 18.5k stars, Apache-2.0, active. The Ollama
critique above mostly does not land on it: models come from HuggingFace,
per-model settings stay visible and editable, and its pins are serious —
`mlx==0.32.0` exact and mlx-lm by commit, with a comment that the bundled
kernels are ABI-coupled to the pin. That is FINDINGS #19's lesson, learned
independently.

Not adopted, and unlike the rest of this section the reason is the prize, not
the principle. Swapping to it re-opens every measured engine decision at
once: it serves through mlx-lm's `BatchGenerator`, unmeasured here on
context-sized prefill (rapid-mlx's ~190 tok/s baseline), on the coder's
MTP/SSM architecture, on the check-12 streaming-tool-call shape, and on
first-attempt task success — the full engine-gate ladder. And the prize
shrinks on contact: the extractor and vision picks are GGUF, so llama-server
survives the collapse anyway, and MLX means GPU — the witnessed 32GB posture
(coder alone on the GPU, reranker on CPU) is not expressible in it. Its
flagship SSD KV-cache tier is the class of disk churn FINDINGS #16/#19
measured as harmful at this RAM size.

Four things taken from reading it:

1. **Jina listwise rerank is servable on MLX in-process.** Its
   `models/reranker.py` implements v3 and v3.5 (special-token hidden states →
   projector → cosine). That un-blocks the plumbing half of the entry below;
   PLAN §6.1 names it as reference plumbing for the stage-2 wrapper.
2. **Single-pool admission control** — one process, one envelope, evict
   instead of abort — is the structural fix for FINDINGS #16's failure class.
   `scripts/gpu-budget.sh` is a setup-time approximation of what it does at
   runtime. If the swap layer is ever rebuilt, copy this shape.
3. **Model profiles** — per-model setting bundles exposed as
   `<model>:<profile>`, overlaid per request with no reload — are a better
   grammar for A/B arms than manifest edits, and feed the PLAN §10 wizard
   design.
4. **SSE keep-alives during long prefill**, plus scaling reported token
   counts so a small-context model triggers a client's auto-compact on time —
   worth stealing the day any client times out inside this stack's ~79s cold
   prefill.

**Where it fits:** an all-MLX model set, on a machine with enough RAM that
eviction is a rarity rather than a lifestyle — and anyone who wants a menubar
app and runtime memory management instead of a manifest and a proxy. The
README names it as the alternative for exactly that reader.

### A router / classifier model in front

The main model already routes by choosing tools. A classifier in front is a
second thing to keep correct, and it fails in a way that is hard to see.

**Where it fits:** dispatching across genuinely different backends, or
multi-tenant setups where the cost of picking wrong is asymmetric.

### `jina-reranker-v3` — blocked on plumbing, not quality

A listwise reranker: query and all candidates share one context. Neither engine
here can serve that shape. llama.cpp's `--reranking` is a *pointwise*
cross-encoder — one pair in, one score out. The official GGUF does not stand
alone either: it ships a projector MLP applied outside the model, plus a script
that subprocesses `llama-embedding` once per call with tempfiles. Adopting it
means writing a persistent rerank server.

Also `cc-by-nc-4.0`, against apache-2.0 for the incumbent. Irrelevant on a
personal laptop, not irrelevant if anything ships.

**Revisit when** there are real queries to measure on — the listwise shape
plausibly suits rerank-over-grep better, but swapping a component before its
consumer exists is blind tuning.

2026-08-08: the plumbing half un-blocked — two existence proofs of the
persistent server now exist, Jina's official MLX ports (a library) and oMLX's
in-process implementation (its entry above). The A/B is planned with
triggers and a decision rule in PLAN §6.1; on a win, the wrapper gets built
here, modelled on oMLX's `models/reranker.py`.

### Vector index / embeddings

Deferred until rerank-over-grep demonstrably falls short. It needs a store to
build, keep in sync, and invalidate, and none of that is free.

---

## A note on dependencies

The rule here is **not** "Homebrew only". It is that a dependency must not
borrow a runtime you manage for other reasons. Four questions:

1. Does it pin its own runtime, or float on whatever is active?
2. Is it in one obvious place?
3. Can you update it with one command?
4. Does it uninstall cleanly?

Homebrew is preferred because it satisfies all four, not for its own sake.
Rapid-MLX installed into a dedicated virtualenv at `~/.rapid-mlx`, which is why
it earned an exception — with a caveat found later, below, and a resolution
after that (the exception closed 2026-08-06). A tool that resolves `node` or
`python` from `PATH` at run time fails question 1 however it was installed —
and that, not the package manager, is the thing to check.

**A venv is only as pinned as its interpreter** (learned 2026-07-31).
`~/.rapid-mlx/pyvenv.cfg` pointed at `/opt/homebrew/opt/python@3.12/…` — the
"dedicated" venv borrowed Homebrew's Python, so it pinned a version, not a
runtime. Patch bumps survive; `brew autoremove` after the formula world
migrates off 3.12 deletes the interpreter from under it. So the exception
passed question 1 only mostly, and the standard for any
*future* venv exception is stricter: create it on a **uv-managed interpreter**
(`uv python install` / `uv venv` / `uv tool install`) — a standalone CPython
in uv's own directory that no other manager touches. uv itself is one brew
binary, already installed. The JS analog, if a standalone JS service ever
appears, is `bun` via brew — one binary, no PATH `node`. Currently a solution
with no tenant, so it is not installed.

**The exception was retired for one day (2026-08-04), reinstated the next,
and closed for good on 2026-08-06.** Rapid-MLX gained a homebrew-core formula
(0.11.9 by 2026-08-04 — the version carrying the #1359 streaming-hang fix),
`steps/20-engines.sh` moved to it, and the cutover ran clean: flags verified,
service spawning the formula's engine. Then the runtime pass caught the
bottle crashing streaming tool calls (FINDINGS #15 — likely introduced by the
hang fix itself), and the stack rolled back to the venv the morning after.
The rollback design is why this cost one morning and not a rebuild: the venv
was kept precisely because "verified against the manifest's flags" had not
happened yet, and the crash is invisible to everything except a streaming
tool call. The venv was then upgraded in place to 0.12.3 (verified-fixed) and
served until the **0.12.4** bottle passed FINDINGS #15's re-derive — check
12's streaming tool call plus a plain completion — on 2026-08-06. The venv
and its seven `~/.local/bin` symlinks were removed that day.

**Re-opened the same night, on measurements** (FINDINGS #19's correction):
the formula pairs rapid-mlx with brew's mlx 0.32.0 against upstream's
`mlx<0.32` pin — a combination pip refuses — and the pin turned out to be
load-bearing: ~3× slower prefill at agent-shaped context, invisible to
check 12 and to short decode probes. The engine now serves from a venv
again, but built to the stricter standard this note prescribes:
`~/.rapid-mlx/venv` on a **uv-managed** py3.12 that no other package
manager touches, `pip`-resolved mlx (compliant 0.31.2), one symlink in
`~/.local/bin`. `steps/20-engines.sh` blocks the measured-bad bottle and
prints the manual re-adoption path when the formula moves; re-adoption is
gated on the upstream pin *plus a context-sized timing probe* — the lesson
of the day being that a formula can pass every functional gate while
violating the dependency contract that makes it fast.

Pi-owned extension state belongs below `PI_CODING_AGENT_DIR` when set and
`~/.pi/agent/<extension>/` otherwise; `project_search` is the first example. A
future repo-owned service has a different lifecycle: PLAN §6.1's rerank daemon
would live at
`~/.local/share/local-ai-setup/<service>/` — uv venv, wrapper and service logs
in one place — rather than making Pi responsible for a daemon. Neither parent
is created in advance: a container earns its place with its first occupant.
Full self-containment beyond that — vendoring the engines into a dot-folder —
was considered and declined: llama.cpp shipped 370 builds in the month this
machine was on 9810, and `brew upgrade` is doing real work that a vendored copy
would transfer to us.

`README.md` lists exactly what lands on your machine and how to remove it.
