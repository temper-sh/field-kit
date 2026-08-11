# local-ai-setup

Idempotent installer for a local LLM stack on Apple Silicon. Bash, no runtime
dependencies beyond Homebrew CLI tools. Target: MacBook Air M5, 32GB, macOS.

## Read these first

- **[docs/FINDINGS.md](docs/FINDINGS.md)** — before touching any engine flag.
  Most flags in `models.yaml` are load-bearing for a reason that is not obvious
  and that the upstream docs actively contradict. This file has the measurements.
- **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** — how the scripts fit together
  and the invariants that keep re-runs safe.
- **[docs/PLAN.md](docs/PLAN.md)** — the single planning document: the active
  queue, deferred items with the conditions that would un-defer them, and the
  known rough edges.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — which tools were considered, what
  they were measured against, and *which kind of setup each one suits*. Read it
  before adding a tool or an extension: several obvious ideas have already been
  tested here and rejected, and the reasons are mostly about this machine rather
  than about the tools.
- `README.md` is user-facing. Keep development material out of it.

## Ground rules

1. **`models.yaml` is the only source of truth.** `~/.config/llama-swap/config.yaml`
   and the `local` provider in `~/.pi/agent/models.json` are generated. Never
   hand-edit either, and never make the scripts read `models.example.yaml`.
2. **A dependency must not borrow a runtime the user manages for other reasons.**
   Not "Homebrew only" — Homebrew is preferred because it satisfies all four of:
   pins its own runtime, one obvious location, one update command, clean
   removal. Judge anything new by those four, not by its package manager.
   Rapid-MLX's venv exception **re-opened hours after it closed**
   (2026-08-06, both same day): the 0.12.4 formula passed FINDINGS #15's
   re-derive, served for an evening, and was then measured running prefill
   at ~1/3 baseline — brew pairs it with mlx 0.32.0 against upstream's
   load-bearing `mlx<0.32` pin (FINDINGS #19's correction). The stack now
   serves from a **uv-managed venv** at `~/.rapid-mlx/venv` (rapid-mlx
   0.12.4 + pip-compliant mlx); `steps/20-engines.sh` blocks the 0.12.4
   bottle and re-adoption of a future formula is manual, gated on the pin
   *plus a context-sized timing probe*. The full history is DECISIONS'
   dependency note — read it before proposing any non-brew install.
   Anything that resolves `node` or `python` from `PATH` at run time fails
   question 1 however it was installed.
3. **Never run `sudo`.** Detect the state, print a ready-to-paste command, count
   it as `[manual]`, move on.
4. **A second run must change nothing.** `./setup.sh && ./setup.sh` — the second
   is `[ok]`/`[skip]`/`[manual]` only.
5. **`--dry-run` must not mutate.** Only ever mutate inside a `do_install` /
   `do_patch` / `do_change` helper, which handle this for you.
6. **Never `yq -i` the manifest.** It reflows comments and strips the quoting
   around the `--speculative-config` JSON. Reading with yq is fine.
7. Target **bash 3.2** — no associative arrays, no `${var,,}`, no `mapfile`.
8. **Output quality outranks throughput.** The unit of value is a finished
   task, not a token: a wrong edit at 100 tok/s costs more wall-clock than a
   right one at 13, because the retry is a whole extra turn including its
   prefill. Never propose a model or engine change on tok/s alone — the 35B
   MoE was retired on quality despite being several times faster. Anything
   that could re-open a quality decision must measure **first-attempt task
   success**, with tok/s secondary.

## Testing

```bash
tests/run-all.sh              # offline, hermetic, ~30s — run this before finishing
tests/run-all.sh --runtime    # needs llama-swap up; ON-DEMAND, not a per-change gate
tests/run-all.sh --perf       # slow; restarts the service, loads 15GB
tests/cache-ab.sh <flags>     # A/B an engine flag before adding it
node --test tests/compact-test-output.test.mjs   # the extension's parsers
node --test tests/project-search.test.mjs        # candidate/rerank/logging boundaries
shellcheck -x -e SC2329 setup.sh steps/*.sh tests/*.sh scripts/*.sh
```

**Tests are plain JavaScript** (`.mjs` — a `.js` file would be CommonJS without
a `package.json`, and there deliberately is none). Keep them that way. The
*extensions* stay `.ts` only because Pi discovers `extensions/*.ts`; node 24
strips their types on import, so neither side needs a toolchain, a
`package.json`, or `node_modules`.

Do not add an engine flag because a doc recommends it. Measure it with
`tests/cache-ab.sh` first — on this stack the published docs have been wrong more
often than right.

**The heavy suites are on-demand** (owner call, 2026-08-06). The offline run is
the only per-change gate. For an engine move, the acceptance gate is check 12's
request as a *single curl* (streamed `tool_calls` delta) plus one plain
completion — not a full `--runtime` pass, which churns ~19GB of weights on a
32GB box. Run the full runtime suite only when the llama-server side or the
generated config topology changes, or when something misbehaves — and announce
it first.

## Current state (2026-08-06)

Built, deployed and running on this machine. All 13 original acceptance checks
pass; `tests/run-all.sh` is **84/84 offline** (2026-08-07, up from 36 — checks
15–21 are new) and **12/12 runtime** (2026-08-04, on the upgraded
engines), with the check-12 streaming-tool-call guard in place. Runtime
check 10 was rewritten 2026-08-07 (manifest-driven residency — the old
assertion encoded the pre-#16 posture and would fail on today's healthy
stack); the fixed suite has not been run end-to-end yet, per the
heavy-suites-on-demand policy. A second
`./setup.sh` is clean: `installed 0 · patched 0 · changed 0 · manual 0`.

**Offline tests must never touch launchd.** `steps/50-service.sh` honours
`LOCALAI_SKIP_LAUNCHCTL=1` and check 16 sets it. The plist label is a constant,
so a `launchctl load` from a test under a scratch `HOME` replaces the *real*
service with a job pointing into a temp directory — it did exactly that once,
and took `:8080` down. A scratch `HOME` isolates files, not launchd labels.

**Engines**: llama-swap **245**, llama.cpp **b10200** (both 2026-08-01),
rapid-mlx **0.12.4 in a uv venv** (`~/.rapid-mlx/venv`, late 2026-08-06 —
the brew bottle served for one evening and was retired the same night on
FINDINGS #19's ~3× prefill regression; the venv's pip-compliant mlx 0.31.2
restored the ~190 tok/s baseline and passed check 12 plus the new
context-sized probe). The full runtime suite passed
12/12 on the 0.11.5 floor on 2026-08-04, closing release gate §0.2; a cutover
to the brew 0.11.9 formula ran later that day and was **reverted on
2026-08-05** — the bottle crashes streaming tool calls (FINDINGS #15). The
venv was then upgraded in place to 0.12.3 (verified-fixed, carries the #1359
hang fix) and re-verified on check 12's shape plus a plain completion —
re-confirmed 2026-08-06 through llama-swap, which closed the owed `--runtime`
pass by owner decision: the streaming fix is confirmed once, and the full
suite is on-demand from here (PLAN §3a). 0.12.3's first
serving day also produced **FINDINGS #16**: two GPU-OOM aborts of the coder
engine under concurrent load (streaming turn + rerank + disk-checkpoint
write); llama-swap recovers, the in-flight turn is lost. Acted on the same
day: the checkpoint writer is **off** (`--kv-disk-checkpoint-interval 0` in
models.yaml; §4.6's confirming A/B owed), and `scripts/gpu-budget.sh`
computes whether the coder's utilization fraction leaves room for the
co-resident models — setup notes when it does not. Two more aborts on
2026-08-06 during plain coding turns (eight total in two days) settled the
posture: **on 32GB only the coder resides on the GPU**. The reranker is
on-demand (`group: heavy`, ttl 120 — 2.4s cold load against
project_search's 180s budget) and the budget gate is bucketed by RAM
(<32GB: below the minimum, the coder does not fit; 32GB: any
always-resident co-tenant fails by existing; 33–36GB: the resident set
gates by arithmetic; >36GB: the full stack gates — README has the table).
0.85 itself is unchanged; if aborts recur the levers left are the fraction
(≤0.74) or a CPU reranker. Read #16 before touching any of this.
**FINDINGS #17** (2026-08-06): a coder idle ~14h decays to SSD speed while
still reporting `ready` (decode 13 → 3–5 tok/s, *declining* under memory
pressure — it does not self-heal); the coder now carries `ttl: 7200` in
models.yaml as the guard. Check residency with a **measured decode** only:
FINDINGS #19 (same day) showed wired pages are compute-coupled — they
collapse to ~3GB seconds after any request, healthy or not — and ps RSS no
longer reflects the weights. #19 also sets the bar for engine-timing runs:
swap near the ~1GB July baseline, since at ~2GB+ a harness's own file churn
evicts the coder between paced runs (that is what invalidated sampling A/B
arm A's first attempt). Note the serving bottle is an upstream-unsupported
dependency set — brew ships mlx 0.32.0 against rapid-mlx's `mlx<0.32` pin,
and the pin is **load-bearing**: mlx 0.32.0 runs prefill at ~1/3 of
baseline on this model (#19's correction has the A/B). Short-context
probes and check 12 cannot see it; any engine gate needs a context-sized
timing probe. Serving posture is an open decision (PLAN §3b).
`scripts/machine-report.sh` is the field kit's stage-0 preflight (one file,
no installs; canonical here, distribution copy hand-synced into the kit);
the probe itself is **built** (2026-08-07) as the separate friend-facing
repo `~/work/local-ai/field-kit`, public at **github.com/temper-sh/field-kit**
— its provenance file feeds `scripts/uninstall.sh` here (offline check 21
tests the pair), and its first foreign-machine run is the open item
(PLAN §9). **The one-engine
A/B is done and the two-engine split stands** — on prefill, not on
generation, which turned out to be a tie (FINDINGS #13).

**There are no `[manual]` items left.** Both sudo tweaks are applied: the GPU
wired limit is `24576` persisted in `/etc/sysctl.conf`, and log rotation is
`/etc/newsyslog.d/llama-swap.conf`, both reported `[ok]` since 2026-07-30. They
live in `scripts/` and compute their targets from the machine — never hardcode
either value back into a step.

Rotation was verified end-to-end on 2026-07-30 with a forced run
(`sudo newsyslog -F -f /etc/newsyslog.d/llama-swap.conf`): both generations came
out owned by the invoking user, and after a restart writes landed in the fresh
file while `.0` stayed frozen. The restart is not optional — launchd holds the
fd, so until
the service cycles llama-swap keeps writing into the *rotated* file.

Outstanding, in priority order:

1. **Memory pressure still starts with `sysctl vm.swapusage`.** During
   acceptance the box was thrashing (10.4GB of 11.2GB swap) and one timing run
   took 435s instead of ~5s. The GPU ceiling no longer explains pressure, so
   look outside the stack too: on 2026-07-29 swap was 17.0GB of 18.4GB and the
   cause was 413 leaked iOS Simulator processes holding 9.8GB.
2. ~~Pi's `defaultModel` still points at `qwen3.6-35b-mlx`~~ — resolved by
   2026-08-06: Pi 0.83.0's `settings.json` carries no `defaultModel` key at
   all, so setup's check is rightly silent. The check stays in
   `steps/40-configs.sh` for the day the key returns.
3. ~~**A hung request is invisible and unbounded**~~ (FINDINGS #9) — **fixed
   upstream in v0.11.9 (PR #1391), deployed since 2026-08-05, and the
   passive ledger CLOSED 2026-08-07**: 24 task-shaped turns (sampling A/B,
   both arms, 240s watchdog armed) with zero unbounded hangs. The successor
   failure is bounded and *visible* — a repetition loop the engine breaks
   in-stream and ends with `finish_reason: length` (FINDINGS #18 outcome +
   addendum) — and the **sampling adoption targets its cause**: models.yaml
   now carries Unsloth's `--default-*` values (A/B 2026-08-07: incumbent
   10/12 with 2 loop events, guide 12/12 with 0; PLAN §4.4). If a true hang
   ever recurs (assistant message left open, keepalives, no termination),
   FINDINGS #9's diagnostic runbook is the history to reach for, and it
   re-opens with fresh captures.
4. **Licence is [0BSD](LICENSE)** (2026-08-02) and the tree redistributes no
   third-party file — keep it that way. The Qwen chat-template fix is
   Apache-2.0 and is *fetched* at setup time via `patches/*/FETCH`, never
   vendored. Anything new that would put someone else's file in this repo needs
   the same treatment or an explicit decision recorded in PLAN §0.1.
5. See [docs/PLAN.md](docs/PLAN.md) for all planned work — **§0 is the release
   gate list** (all four closed 2026-08-04), then pre-publish fixes and the
   measurement queue (sampling DONE 2026-08-07 — arm B adopted, PLAN §4.4;
   extraction consolidation is next). The Rapid-MLX#1359 hang closed
   end-to-end: fixed upstream, verified here, ledger done (PLAN §3a).
   `project_search` landed 2026-08-05; its retained real-world
   queries now gate the deferred retrieval work in PLAN §6. The one-engine A/B
   is **done** (FINDINGS #13). The repo reshape — catalog → wizard →
   models.yaml → lock → generator pipeline, an `apply`/`update` CLI, and a
   release/labs split — is planned in **PLAN §10** (2026-08-06): discussion
   recorded, **nothing started — do not begin it without the user.**

## Debugging a model that will not start

llama-swap only ever says `upstream command exited prematurely`. The real error
is in the upstream log:

```bash
curl -s localhost:8080/logs/stream/upstream | tail -40
```

Then reproduce by hand: copy the `cmd` out of `curl -s localhost:8080/running`
and run it directly with `--port 10099`.
