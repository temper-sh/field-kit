# Plan

The single planning document: the active queue, the deferred items with the
conditions that would un-defer them, and the known rough edges. Absorbed
BACKLOG.md on 2026-07-31 — deliberate deferrals live in §6 now. What is
*settled* (measured and decided) lives in [DECISIONS.md](DECISIONS.md), not
here; delete sections from this file as they land.

Provenance: full-repo review 2026-07-31 (code, docs, engine/model ecosystem),
revised 2026-08-02 under CLAUDE.md ground rule 8 (output quality outranks
throughput) — §0 is new, §4's run order changed, §4.5 was demoted. Every
upstream claim below carries the PR/issue number it rests on; re-verify before
acting on one — they rot like findings do. A second architecture review on
2026-08-05 is preserved in
[ARCHITECTURE-REVIEW-2026-08-05.md](ARCHITECTURE-REVIEW-2026-08-05.md); its
recommendations are not silently inserted into this file's active run order.

---

## 0. Release gates

§1–2 are *work*. These four are **gates**: publishing without them is either
legally broken or actively harmful to the first stranger who runs the thing.

1. ~~**LICENSE**~~ — **done 2026-08-02: [0BSD](../LICENSE)**, chosen on "fewest
   restrictions, attribution not wanted, made for public good". 0BSD is MIT
   minus the notice-retention clause, and unlike CC0 or the Unlicense it is
   OSI-approved and SPDX-clean, so corporate scanners pass it without review —
   which matters more than the wording for something meant to be freely reused.

   Apache-2.0 was the earlier recommendation *because* the tree vendored an
   Apache-2.0 chat template. That input is gone: the template is now fetched at
   setup time (`patches/*/FETCH`) rather than redistributed, so the tree is
   single-licensed under 0BSD with no third-party file in it and no NOTICE
   question to answer. `CONTRIBUTING.md` and an issue template remain polish,
   not gates.
2. ~~**The owed `tests/run-all.sh --runtime` pass**~~ — **done 2026-08-04:
   12/12 against the upgraded floor** (rapid-mlx 0.11.5, llama-swap 245,
   llama.cpp b10200), with the 55 offline checks passing on the way in.
   Check 9 reported non-degenerate reranker scores (relevant document first,
   max 0.015) and check 12 streamed a parsed tool call, so FINDINGS #7 and #8
   hold on the new engines. CLAUDE.md's runtime line and FINDINGS' header were
   updated in the same pass. The runbook that lived here is condensed away:
   its durable content is in tests/README.md (§runtime.sh — first-run pulls,
   check 9/12 semantics, swap-before-timings) and CLAUDE.md (swap first,
   one-curl debugging, the hang).
3. ~~**§1.1 — the parse gate**~~ — done 2026-08-02.
4. ~~**§1.2 — fresh-machine `--dry-run` must exit 0**~~ — done 2026-08-02,
   verified against a PATH with Homebrew's prefix removed so yq is genuinely
   absent rather than mocked.

**All four gates are closed** (the last on 2026-08-04), and the small
pre-publish items (§2.4, §2.5, §1.7's note) all landed 2026-08-05. The
engine bridge resolved on 2026-08-06 — §3a's cleanup runbook ran, the 0.12.4
formula serves, and the README's install story is true again. What remains
before publishing is the verification sweep of the inherited text below.

Not a gate, but nothing else covers it: **a verification sweep of the inherited
text.** Two fabricated claims were caught in the 2026-08-01 docs pass, and a
pre-existing error surfaced alongside them (FINDINGS' header had labelled the
Darwin version as the macOS version). That was one spot-check, not a survey.
For a repo that publishes numbers as its product, every version, path, URL and
measurement claim in the older text is worth checking against the tree or the
source before strangers start citing them.

**Run 2026-08-05**: four parallel fact-check agents over README, FINDINGS,
DECISIONS, DEVELOPMENT, tests/README and both manifests — ~325 discrete
claims, each flagged item re-verified by hand before fixing. ~30 corrections
landed, in three classes: *stale-after-fix* text (a README caveat still
claiming the output parsers had no tests; "the extension does not yet
distinguish truncation"; DEVELOPMENT's shellcheck command missing two globs),
*never-true* claims (the header's macOS version — 26.5 was never resident,
the box ran 26.5.1; "both generated files carry the GENERATED header"), and
*example-manifest flag sets contradicting the repo's own findings* (parked
MLX entries shipping `--tool-call-parser` without `--enable-auto-tool-choice`
— FINDINGS #8's exact trap — and the pinned unsloth entry missing
`--hybrid-cache-entries`). Every upstream reference, license, PR state and
cross-doc number checked out; the full agent reports are in the session
transcript, not the tree.

## 1. Before publishing — code

None of these touch an engine flag, so none needs the measurement machinery.

**All of §1 landed 2026-08-02** except the orphaned-`prefix_cache` note in
§1.7. New regression cover: check 15 (bad manifests never reach a live config),
check 16 (the plist is derived, not hardcoded), check 17 (the extension's
parsers); check 2 gained the fresh-machine run. Suite went 36 → 55.

1. ~~**Parse gate on generated configs.**~~ Done. `install_generated` now takes
   a `yaml`/`json` argument and parses the rendered file before touching
   anything; on failure it refuses, keeps the bad render at
   `.state/invalid-<name>`, and leaves the live config alone. In front of it,
   `validate_manifest_strings` and `validate_manifest_booleans` name the
   offending field instead of the symptom. Entries are read **by index**, never
   through `mval()` — that interpolates its argument into the yq query, so
   asking it about a model whose id contains a quote builds a broken query and
   returns nothing, which was the case most worth catching. Quotes inside
   `flags` stay legal: `--chat-template-kwargs` needs them and flags are
   emitted bare inside `cmd: >`.
2. ~~**Fresh-machine `--dry-run` must exit 0.**~~ Done — skips with the reason
   in dry-run, still fails loudly for a real run.
3. ~~**Validate manifest booleans.**~~ Done, as part of §1.1's front end. Every
   entry is checked rather than only the enabled ones, precisely because
   `enabled: no` is the bug in question.
4. ~~**compact-test-output.ts fixes**, then the unit harness~~ — done
   2026-08-02. All four known bugs fixed, and **the harness found two more on
   its first run** — which is the entry's own point about pure functions that
   have never fired in production:
   - junit: a bare `<testsuites>` header (pytest) has no counts, so failing runs
     summarized as `passed`. Now sums per-`<testsuite>`, *and* treats any parsed
     `<failure>` as failing regardless of counts.
   - runner detection matched a filename anywhere on the line. Now anchored
     against the *program* of each segment, after stripping env assignments and
     wrappers (`CI=1`, `time -p`, `timeout 300`, `nice -n 10`).
   - `usesRtk` missed `FOO=1 rtk …` / `time rtk …`; it uses the same segment
     machinery now.
   - the >100-*lines* gate never fired on one-line JSON. Gates on bytes OR
     lines, and the size is reported in whichever dimension is meaningful —
     `truncateMiddle` also produced a *negative* "lines omitted" count on a
     single enormous line, and now slices characters for that shape.

   **Found by the new tests, both shipping silently until now:**
   - `/name="/` matches inside `classname="`, so every junit finding was named
     after its class twice (`tests.test_api.tests.test_api`). All attribute
     reads are `\b`-anchored now.
   - a self-closing `<testcase/>` — how passing tests are normally emitted —
     was treated as an opening tag, so `[\s\S]*?` ran on to the *next*
     `</testcase>` and the passing test inherited the following test's failure.
     It was reporting the wrong test, pointing at innocent code.

   Harness is `tests/compact-test-output.test.mjs` (offline check 17, 23
   assertions): `node --test`, **plain JS**, no package.json and no
   node_modules. The extension stays `.ts` only because Pi discovers
   `extensions/*.ts`; node 24 strips its types on import. It ships as one
   self-contained file, so the test copies the source with its one unresolvable
   import removed rather than splitting the parsers into a module that could
   not be deployed.
5. ~~**Batch tool install.**~~ Done — behind one `do_install`, so a dead
   formula reports and the run continues instead of aborting under `set -e`.
6. ~~**Portability for strangers.**~~ All four done:
   - the plist template and `SERVICE_PATH` now take `$(brew --prefix)`, as does
     `tests/cache-ab.sh`. (`steps/20-engines.sh` still names both prefixes
     literally, which is correct — it is a search list, not an assumption.)
   - the plist carries `HF_HUB_CACHE`, resolved by a new `hf_hub_root()` in
     `setup.sh` that the models step and the service step now share. launchd
     gives the service a bare environment, so without it weights downloaded to
     a custom `HF_HOME` were invisible to the engine that had to load them.
   - README's "what size machine" paragraph landed 2026-08-01: the >16GB floor,
     the 8192MB wired ceiling on 16GB, and no drop-in option because every chat
     entry in `models.example.yaml` is ≥20B (both numbers re-derived).
   - python-yq is rejected in preflight — two different programs are called
     `yq` and the wrong one fails confusingly deep inside a step.
7. ~~**Cap the KV checkpoint cache in the plist.**~~ Done.
   `RAPID_MLX_KV_CHECKPOINT_MAX_BYTES` is written into the launchd environment,
   computed from free disk: 10% clamped to [1, 20] GiB, never above upstream's
   own 20 GiB default. Stated in the code as a policy rather than a
   measurement. On this machine (238GB free) it lands on 20 GiB, so nothing
   changes here — the point is the 256GB stranger who was silently losing 8%.
   `LOCALAI_FREE_KB` overrides the probe, the way `LOCALAI_MEMSIZE_BYTES` does
   for the RAM formula, and check 16 tests three disk sizes through it.

   ~~Still open: orphaned `prefix_cache/` entries~~ — closed 2026-08-05. The
   models step now reports any prefix-cache directory no enabled model uses
   as a `[note]` with the reclaim command — detection only, and repos named
   inside `flags` (the MTP sidecar) count as live so they are never flagged.
8. ~~**Smaller confirmed items.**~~ All done: `extract-json.ts` now checks
   `finish_reason == "length"` and reports a truncation instead of blaming the
   server's grammar support — the two are indistinguishable from the parse
   error alone, and the wrong message sends people a long way off;
   `tests/cache-ab.sh` kills its recorded pid rather than
   `pkill -f "port $PORT"`, which matched on a command line rather than on
   ownership; its baseline arm gained `--enable-auto-tool-choice` (FINDINGS #8
   — harmless for prefill, but the file gets read as an example); `backup_file`
   prunes to the newest 3; the dead `_plist_changed` and `__RAPIDMLX_BIN__` are
   gone from `steps/50-service.sh`.

## 2. Before publishing — docs

The repo's value is evidence; these are places the text lags the measurements.

1. ~~**README §compact-test-output** "a safety net behind rtk"~~ — done; the
   README already reads "the only thing shrinking command output in this
   stack… nothing sits upstream of it".
2. ~~**DECISIONS.md §"Two engines, and why not one"**~~ — done 2026-07-31,
   then **replaced 2026-08-01 with the measurement** (§4.3, FINDINGS #13). The
   section no longer rests on "MLX generates faster" — generation is a tie; it
   rests on prefill, with the numbers and the conditions that would overturn
   them stated inline.
3. ~~**tests/README.md §perf.sh** teaches FINDINGS #1's superseded
   explanation~~ — done. The section now records that #10 retracted it and
   cites 17.2s against 2.0s. `tests/cache-ab.sh` carried the same wrong
   explanation in its header comment and was corrected 2026-08-02.
4. ~~**`--pi-hints`**~~ — removed 2026-08-05 (the flag, the parse, the whole
   hints block in `steps/10-tools.sh`; a comment records why). README's usage
   block and layout row updated.
5. ~~**Promote the agent-safety incident to README.**~~ — was already there
   (README §"Two failure modes", second bullet) but unstruck here; completed
   2026-08-05 with the answered-over-`isError=true` detail and the
   "failure is quiet, diff the tree" framing. The hang bullet beside it was
   stale ("unfixed") and now records the v0.11.9 fix plus the FINDINGS #15
   caveat.
6. ~~**README's "## Layout" omits `tests/`**~~ — done 2026-08-02. The block now
   lists `tests/` and follows it with the five entry-point commands, so the
   measurement machinery is visible from the one document a stranger judges the
   repo by. A `## Licence` section landed in the same pass.
7. ~~**models.example.yaml `qwen3.6-27b-mtp-gguf`**~~ — done 2026-08-01.
   `--chat-template-kwargs '{"enable_thinking":false}'` added (as parked, the
   A/B arm would have run thinking-on against the MLX side's thinking-off),
   along with the four memory/checkpoint flags the probe showed it cannot run
   without (FINDINGS #12). The draft-n sweep stays a §4.3 concern.

## 3. Upstream — the hang is filed, watch four things

### 3a. FINDINGS #9 — Rapid-MLX#1359: fixed upstream, unverified here

Filed 2026-07-31; **closed as completed 2026-08-02** by PR #1391 —
"fix(stream): bound tool-call streaming suppression so it can't wedge" — which
confirms the suspected mechanism: the tool-call buffering path could suppress
flushes indefinitely while keepalives kept every timeout alive. The fix ships
in **v0.11.9**; at the time of the revert this machine ran 0.11.5 — the
bridge to 0.12.3 the same evening deployed the fix (see below).
(User-reported 2026-08-04, then verified against the issue, PR and releases.)

Per the rule this section carried, the fix slots into §4's queue ahead of the
sampling A/B: **upgrade rapid-mlx, then re-measure the hang rate on the
agentic corpus against the ~3-in-7 baseline** (§4.3, DECISIONS). Notes for
that run:

- **The cutover to brew 0.11.9 ran 2026-08-04 and was reverted the next
  morning.** The flag check passed and the engine served, but the runtime
  pass went 11/12: 0.11.9 crashes streaming named-`tool_choice` requests in
  its tool-grammar path (FINDINGS #15) — and the traceback runs through the
  code PR #1391 rewrote, so the prime suspect is the hang fix this upgrade
  was *for*. A same-interpreter probe verified 0.12.3 fixes it. The stack is
  back on the venv 0.11.5 (verified streaming parsed tool calls via
  llama-swap), and `steps/20-engines.sh` refuses the 0.11.9 bottle by
  version. **Later the same day the venv was upgraded in place to 0.12.3**
  (`~/.rapid-mlx/bin/pip install rapid-mlx==0.12.3`; rollback within the venv
  is `==0.11.5`) so the verified-fixed version — and with it the #1359 hang
  fix — serves while the formula catches up. Re-verified after the kickstart
  on check 12's shape and a plain completion, this time on the serving
  interpreter (py3.12) — and **again on 2026-08-06 through llama-swap**
  (streamed `tool_calls` delta, function parsed, nothing leaked as content;
  plain completion clean). That second confirmation closed the owed
  `--runtime` pass by owner decision (2026-08-06): the streaming fix is
  confirmed once, and **the full suite is on-demand from here** — a full
  pass is not owed after an engine move; the per-move gate is the targeted
  pair above. (The other runtime checks ride llama-server, which did not
  move.) The hang re-measure (below) is now unblocked.
**Cleanup runbook — executed 2026-08-06 on the 0.12.4 bottle. Steps 1–6
done; step 4 skipped by design (only the engine moved); step 7 stays a
passive ledger.** The gate (step 3) passed on the live service: check 12's
request streamed a parsed `tool_calls` delta and a plain completion answered
clean — FINDINGS #15 carries the closing line. The venv is gone, the version
guard is deleted, and §7.7 is closed. Kept for the record; ordered; do not
skip ahead:

1. `sysctl vm.swapusage` — the standing precheck; stop on a thrashing box.
2. `./setup.sh` — the version guard permits any non-0.11.9 bottle; expect
   `[install] rapid-mlx` and the bin-dir record flipping to brew's bin. Then
   `launchctl kickstart -k gui/$UID/com.llamaswap.server` — the service PATH
   tries brew's bin first, so the formula's engine takes over on restart.
3. **The acceptance gate**: check 12's request through llama-swap must
   stream a `tool_calls` delta (FINDINGS #15 has the exact curl), plus one
   plain completion. **If it fails**: add the bottle's version to
   `RAPIDMLX_FORMULA_BAD` in `steps/20-engines.sh`, kickstart back onto the
   venv, update FINDINGS #15, and tell upstream.
4. *Optional* (owner call, 2026-08-06 — the full suite is on-demand):
   `tests/run-all.sh --runtime`, only if more than the engine moved. Step 3
   is the gate.
5. Only after 3 passes (and 4, if run): remove the venv and its seven symlinks —
   `rm -rf ~/.rapid-mlx && find ~/.local/bin -maxdepth 1 -type l -lname "$HOME/.rapid-mlx/*" -delete`
   — and delete the 0.11.9 guard from `steps/20-engines.sh` (FINDINGS #15
   says to). This closes §7.7.
6. Docs sweep: CLAUDE.md ground rule 2 + current state (formula serves, venv
   gone), README's venv paragraphs collapse to the simple story, DECISIONS'
   exception paragraph closes, FINDINGS #15 gets its closing line, §3a and
   the §3b formula bullet strike.
7. ~~The hang re-measure (~3-in-7 baseline, agentic corpus) stays queued~~
   — **converted to passive observation, 2026-08-06**, and **CLOSED
   2026-08-07**: the sampling A/B supplied the ledger in one night —
   armA4 + armB, 24 task-shaped Pi turns with a 240s output-silence
   watchdog armed, **zero unbounded hangs**. Bounded loop events: 2 in
   armA4 (visible, in-stream — the fix working; FINDINGS #18's outcome
   line has their shape), 0 in armB. FINDINGS #9's head now carries the
   closure and keeps its diagnostic runbook as history. Any true hang
   (assistant message left open, keepalives with no termination, ever)
   re-opens this with fresh captures.
- The sampling A/B (§4.4) keeps hang rate as a secondary metric but is no
  longer the only local lead on the hang.
- If the corpus still hangs, re-open with *fresh* captures — the filed
  buffer's bodies rotated long ago.

### 3b. Watch, with numbers

- ~~**Rapid-MLX #1359**~~ — fixed upstream (PR #1391, ships in v0.11.9) —
  but that release ships the FINDINGS #15 tool-grammar regression, quite
  possibly *caused by* #1391, so the fix is unadoptable until a ≥0.12.x
  formula verifies clean; §3a.
- **Qwen 3.8 27B** — reported to ship the week of 2026-08-10 (unverified;
  nothing to check until it exists). A candidate coder replacement, which
  makes it a *quality* decision under ground rule 8: first-attempt task
  success on the pi-tool-ab corpus against the 3.6, tok/s secondary. Before
  any swap, check whether the fetched chat-template fix (`patches/*/FETCH`,
  written for the 3.6) is still needed or still applies, and treat the swap as
  re-opening the acceptance gate — check 12's streaming tool calls above all,
  as the targeted curl (the full runtime suite stays on-demand, §3a).

- **Rapid-MLX PR #1216** — per-position SSM snapshots; removes the chain-of-K
  clamp (FINDINGS #3). Source-measured 1.36× at K=3 on a Qwen3.6 hybrid. When
  it ships, the manifest's `num_speculative_tokens: 3` starts being honoured;
  sweep 1–3 (acceptance reportedly collapses at 4). Checked 2026-08-06:
  still an open draft awaiting core-team review, functionally dependent on
  PR #1214 — nothing to adopt yet.
- ~~**llama.cpp #24055**~~ — **probed 2026-08-01 on b10200: does not
  reproduce.** Checkpoint restore works, including the rewind case the issue is
  actually about; the exact-repeat case that FINDINGS #10 loses on the MLX side
  is reused here. Full numbers and the re-derive command are FINDINGS #12. The
  gate on §4.3 is cleared. The issue is still open upstream, so if the A/B is
  run on a build older than b10200, re-probe rather than assume.
- ~~**`rapid-mlx` in homebrew-core**~~ — **adopted 2026-08-06 at 0.12.4**,
  which passed FINDINGS #15's gate (check 12's streaming tool call plus a
  plain completion) on the live service. The 0.11.9 bottle was
  measured-broken for agents (FINDINGS #15): the 2026-08-04 cutover to it
  was reverted the next morning when check 12 caught streaming tool calls
  crashing. §3a's runbook ran to completion; §7.7 is closed; the venv and
  the version guard are gone.
- **The bottle's mlx violates upstream's pin — and it costs ~3× prefill**
  (FINDINGS #19 + same-night correction): the formula depends on brew's
  `mlx` 0.32.0 while rapid-mlx pins `mlx<0.32,>=0.31.2` — pip refuses the
  combination, brew ships it. A/B on identical requests: 3.65k-token
  prefill 64–70s on 0.32.0 vs 22–23s on 0.31.2 (~55–61 vs ~190 tok/s);
  short decode healthy on both, so check 12 cannot catch it. **Report to
  homebrew-core with the A/B numbers** and watch for a compliant pin or an
  upstream cap lift. **Posture decided late 2026-08-06 (owner):** a
  uv-managed venv at `~/.rapid-mlx/venv` serves rapid-mlx 0.12.4 with
  pip-compliant mlx 0.31.2 — gated clean (check 12; 3.65k prefill 21.8s ≈
  190 tok/s) — and `steps/20-engines.sh` blocks the 0.12.4 bottle, prints
  the manual re-adoption path when the formula moves, and prefers the venv
  on the service PATH while it exists. Re-adoption gate: upstream pin
  satisfied + check 12 + a context-sized timing probe.

## 4. Measurements

**Run order, revised 2026-08-02 under ground rule 8** — the numbering below is
kept as-is so existing cross-references keep resolving, but it is no longer the
order. Quality-affecting work goes first; pure-speed work goes last:

> **§4.4 sampling** → **§4.7 extraction** → **§4.8 tool-schema trim** →
> **§4.6 disk-checkpoint** → §4.2 `--pin-system-prompt` → §4.1
> `--enable-prefix-cache` → §4.5 MoE (or never — see the entry).

Sampling leads because it is the only queued item that touches output quality
*and* the hang (§3a), and because nothing in the stack pins sampling today.
§4.6 is placed ahead of the two cache flags for a different reason: it is the
one item that *frees* resources rather than spending them, and it can retire
§1.7 outright.

**Deferred as a block, 2026-08-05:** the three cache-ab flag A/Bs (§4.6, §4.2,
§4.1) wait until the coder question settles — Qwen 3.8 27B is reported for
the week of 2026-08-10 (§3b), and flag verdicts are coupled to the coder
model, so measuring against the 3.6 days before a possible swap buys numbers
with a shelf life. `project_search` (§5) proceeded instead and landed on
2026-08-05: it rides the reranker, which no pending swap touches.

**Update, later the same day:** FINDINGS #16 (two GPU-OOM engine aborts on
`project_search`'s first day of real use, both with the disk-checkpoint
writer mid-write) gives §4.6 a *stability* reason that is independent of the
coder choice — the coupling-to-the-coder rationale above does not cover a
crash. Resolved the same day: §4.6's flag was flipped off on stability
grounds (the confirming A/B is still owed — see the entry), and the
utilization margin became `scripts/gpu-budget.sh` + offline check 19. The
remaining tuning question was whether to lower the manifest's 0.85 — the
arithmetic says ≤ 0.74 fits this machine's worst co-resident case. A first
2026-08-06 decision (keep 0.85, gate the resident set by arithmetic) was
superseded within hours by two more aborts during plain coding turns: the
resolution is that on 32GB **only the coder resides on the GPU** — the
reranker is on-demand (heavy, ttl 120) and the budget gate is bucketed by
RAM (FINDINGS #16, README bucket table). If aborts continue on the new
posture, the remaining levers are the fraction (≤ 0.74) or a CPU reranker
(`-ngl 0`, unmeasured).

0. ~~**Upgrade the floor.**~~ Done 2026-08-01: llama.cpp 9810 → **b10200**,
   llama-swap 242 → **245**, rapid-mlx 0.11.0 → **0.11.5** (`~/.rapid-mlx/bin/pip
   install --upgrade rapid-mlx`; roll back with `==0.11.0`). Coder verified
   answering afterwards. FINDINGS' header now records what was and was not
   re-verified on the new floor. The owed `tests/run-all.sh --runtime` pass
   **moved to §0.2 on 2026-08-02** — it is a release gate, not a measurement.
1. **`--enable-prefix-cache` check** (cache-ab.sh, one run). Upstream PR #1163
   claims hybrid prefix-cache fixes; expectation here is **no change** on the
   exact-repeat case — FINDINGS #10 shows storing already works and the drop
   happens at the trim step, which upstream still can't do. Document either
   outcome; correct FINDINGS #10 if it moves.
2. **`--pin-system-prompt` A/B** (cache-ab.sh takes arbitrary flags for
   exactly this). Pins the system prompt against cache eviction under memory
   pressure — plausibly valuable since the coder is pinned specifically to
   protect its cache, and no longer confounded now the GPU wired limit is in.
3. ~~**The one-engine A/B**~~ — **run 2026-08-01. The split stands.**
   Full table and methodology in FINDINGS #13; the verdict is now the opening
   of DECISIONS' "Two engines" section. Headline: decode is a **tie** (13.2 vs
   13.3 tok/s), so the rationale the split was defended with is wrong; MLX
   keeps its place on **prefill**, 180 vs 111 tok/s, which is the metric agent
   work is actually made of. llama.cpp wins the byte-identical repeat 21×, a
   shape agent turns essentially never produce.

   Left undone on purpose, in case this is ever revisited:

   - **Task success on the pi-tool-ab corpus was not run.** The throughput
     result was one-sided on the dominant metric and the corpus run costs ~1h
     at a 3-in-7 hang rate. If the engines ever land this close again, run it.
   - **The `--spec-draft-n-max` 1–3 sweep was skipped**, because speculation
     changes generation only, generation is already tied, and the verdict rests
     on prefill.
   - **Re-measure on a >32GB machine before reusing this table.** llama.cpp's
     prefill number is depressed by the quarter batch it is forced into
     (FINDINGS #12), and that constraint is specific to 32GB.
   - **Recheck if Rapid-MLX PR #1216 lands.** MLX tied decode with chain-of-K
     clamped to 1; unclamping should move it, in the direction that favours
     keeping the split.
4. **Sampling A/B — DONE 2026-08-07, arm B adopted** (runbook + results
   header: `pi-tool-ab/docs/SAMPLING-AB.md`; raw runs:
   `pi-tool-ab/results/sampling-ab/`). Valid arms after two invalid
   attempts (below): armA4 (incumbent) **10/12**, 2 loop events, 1 fatal
   guard-truncated turn; armB (guide values) **12/12**, 0 loop events in
   its full-arm upstream log, at ~+18% mean wall (206s vs 174s). The
   declared ceiling worry did not materialize — the corpus discriminated —
   and the declared rejection trigger (any B success regression) did not
   fire, so B was adopted on success, not just prevention (owner call,
   2026-08-07). models.yaml carries the flags with the numbers; FINDINGS
   #18 has the outcome + the 0.12.4 loop-shape addendum. n=12/arm; a rerun
   where B drops a task A held re-opens this. **First arm A attempt aborted the same
   evening at 6/12 runs — data INVALID** (marked in its META.txt): the box
   sat at ~2× the July swap baseline and the harness's own churn evicted the
   unwired weights between paced runs, so four runs died on SSD re-faults,
   not on sampling (FINDINGS #19 has the full mechanism). **Attempt 2, post
   restart on a pristine box, also aborted (2/12, INVALID, marked in
   armA2/META.txt)** — this time on the brew bottle's mlx 0.32.0 prefill
   regression (#19's correction), not on the box: prefill ran at a third of
   baseline and tasks blew the 400s backstop while working correctly.
   ~~The rerun is blocked on the serving-posture decision~~ — **unblocked
   late 2026-08-06**: the compliant uv venv serves at the measured baseline
   (§3b). Rerun conditions: swap near ~1GB, no concurrent heavy work,
   `QUIET_LIMIT=240` as watchdog insurance (tolerance only — pacing and
   arms unchanged); both arms clone the same HEAD. The open questions this
   entry carried are answered with data:
   - *What Pi sends*: **nothing** — every sampling field null (verified
     from llama-swap capture 51), so the incumbent arm is the model's
     `generation_config`: `temp 1.0, top_k 20, top_p 0.95`, no penalties.
   - *Deployability*: all five guide values have matching `--default-*`
     serve flags on 0.12.3, so arm B is a temporary models.yaml flags edit
     plus `./setup.sh`.
   - *Motivation upgraded from theory to incident*: FINDINGS #18 — a
     27-minute repetition-loop abort under the incumbent settings, exactly
     the failure `presence_penalty 1.5` exists to prevent in this family's
     non-thinking mode.
   Arms = incumbent vs Unsloth guide
   (`temp 0.7, top_p 0.8, top_k 20, min_p 0, presence_penalty 1.5`); corpus
   = pi-tool-ab daily (plus extremes if the daily ceilings); success and
   wall primary, **loop/hang rate secondary**. Known limitation, declared
   up front: the daily corpus previously scored 4/4 on all four tool-A/B
   arms, so success may not discriminate — if both arms ceiling, the
   decision falls to loop rate and wall, and "no measured win" keeps the
   incumbent. Vendor tuning guides are the exact document class FINDINGS
   exists to distrust — adopt only what the A/B keeps.
5. **MoE probe ladder — demoted 2026-08-02, and the framing was wrong.**
   This entry asked "does 3B-active convert to wall-clock wins on this
   bandwidth-bound Air?" — a speed question. But `qwen3.6-35b-a3b` was retired
   in July on **memory *and output quality***, and quality is the priority
   here. A speed-only probe cannot re-open a decision that was made on quality;
   it would just re-confirm the MoE is faster, which nobody disputes (an
   external M5 Max benchmark has it at ~100 tok/s, see DECISIONS).

   If this is ever revisited, the probe must be **first-attempt task success on
   the pi-tool-ab corpus**, with tok/s recorded as a secondary. Same for
   GLM-4.7-Flash (31B-A3B, ~17GB, MXFP4_MOE only, would need its own
   check-12-style streaming-tool-call verification) — do not evaluate it on
   throughput either. Zero adoption cost still holds: both entries stay parked
   in `models.example.yaml`.

6. **Does the disk KV checkpoint path earn its keep?** One flag:
   `--kv-disk-checkpoint-interval 0` through `tests/cache-ab.sh`. FINDINGS #1
   shows the *in-memory* `--hybrid-cache-entries 8` is what makes multi-turn
   fast, and #10 shows a complete hit is discarded anyway; the disk path serves
   "resume / shared-prefix reload", which nothing here has measured. If the
   counters do not move, this stack is paying 20 GiB of disk and a write every
   256 tokens for nothing — and turning it off is strictly better than tuning
   the cap (§1.7). Cheap, and the only item here that frees resources rather
   than spending them.

   **Stability datapoint, 2026-08-05 (FINDINGS #16):** both GPU-OOM engine
   aborts on `project_search`'s first day of real use happened with this
   path's writer mid-write — it is the only actor present in both crashes.
   That is a reason to decide (or run) this ahead of the deferred block
   above: the deferral's rationale is that flag *verdicts* couple to the
   coder model, and a crash does not. **Decided later the same day: flipped
   off** — `--kv-disk-checkpoint-interval 0` is in models.yaml, verified on a
   plain completion plus check 12's streaming shape. The cache-ab A/B is
   still owed with the arms reversed: off is now the incumbent, so the
   measurement is whether *re-enabling* buys anything on the resume /
   shared-prefix cases before it ever comes back.
7. **Extraction consolidation A/B.** NuExtract3 vs its own base
   (Qwen3.5-4B, already deployed for vision) + the same json_schema grammar,
   on real documents. Grammar already eliminates the schema-echo failures that
   make general models look bad; if content accuracy holds, two heavy 4B
   specialists collapse into one.
8. **Tool-schema trim.** Pi's builtin tool schema costs ~4.2k tokens on every
   turn (measured: turn-1 context 6,690 with the builtins, 2,451 without) —
   the only wall-clock win in the compression A/B, and it needs no compressor,
   just a shorter tool list. The experiment is "which builtins does this model
   actually use, and what breaks if the rest are hidden": over 16 runs the
   agent used `bash` heavily, `read`/`edit` regularly, and `grep`/`find`/`ls`
   barely at all — those three are the cheap candidates. Nuance: with the
   prefix cache working, the 4.2k mostly costs turn-1 latency and permanent
   context occupancy; short sessions are where it pays.

   **Re-metricked 2026-08-02:** hiding a tool the model *would* have reached
   for is a quality regression that a wall-clock number cannot see, so the
   primary metric is **first-attempt task success** on the pi-tool-ab corpus,
   with the token saving secondary. "Used barely at all over 16 runs" is a
   usage frequency, not evidence that removal is free.

   **Re-scoped 2026-08-08 — upstream did the tool half.** Pi 0.83.0's
   default active set is `read`/`bash`/`edit`/`write`: grep/find/ls are
   already gone (the July −4.2k hypa-replace number predates this), and
   Pi's own conditional guideline "Use bash for file operations like ls,
   rg, find" ships active — the hint risk this entry worried about is
   retired upstream. What remains measurable is the **prompt side**: the
   builtin "Pi documentation" routing block (self-labeled "read only when
   the user asks about pi itself", appended unconditionally) is 1,419
   chars ≈ **405 tokens of every turn** (witnessed: turn-1 context 6,657
   native → 6,252 trimmed, same task, same sandbox). The mechanism is a
   ~60-line extension (`pi-tool-ab/extensions/context-trim.ts`, the `trim`
   arm of the harness): `before_agent_start` excises the block;
   `setActiveTools` covers tool trims on older Pi or other tools. The
   right upstream home for the docs block is a builtin *skill* (skills
   load on demand — the block's own "read only when asked" is the skill
   contract); worth filing on Pi. Success gate: the daily corpus,
   native vs trim.

   **A/B run 2026-08-08/09 (Pi 0.83.0, `trim` arm): gate not passed
   clean — unadopted, with the failure honestly unattributed.** Round 1
   (4 tasks × both arms): native 4/4 (one backstop-retry); trim 1/4,
   but the three failures decompose as two runs where the engine
   streamed an EMPTY completion after full prefill (one turn, zero tool
   calls, ~641-byte SSE stream, one of them 49.5s) and one matcher
   artifact (the answer said "SHA-256", the `match` verifier greps
   literal lowercase `sha256`). Attribution round (the two
   empty-completion tasks × 2 reps, arms interleaved): zero empties in
   8 runs; trim passed review-changes outright; debug-test straddled
   the 400s backstop for BOTH arms (native double-backstopped it once
   too); and a post-backstop-kill error state invalidated the last
   cycle for both arms equally (llama-swap error-state signature —
   20s runs, peak=0, no tools). Net: no reproducible harm attributable
   to the trim, but two unattributed empty completions landed in the
   trim arm and nowhere else — the trim stays unadopted until a longer
   rep run comes back empty-free or the empties get a mechanism.
   Harness findings from the run: the `match` verifier is
   case-sensitive, and a backstop kill can poison the following cycle
   through the service's error state. Artifacts:
   `pi-tool-ab/extensions/context-trim.ts`, `results/trim-20260808*`.

## 5. ~~Next feature: `project_search`~~ — built 2026-08-05

`pi-extensions/project-search.ts` implements the index-free design: `rg`
content hits plus `fd` filename hits, lexically capped at 40 files, then
`rerank-qwen3-0.6b` ordering and five line-numbered results. It follows the
existing extension contract (`LOCAL_AI_BASE_URL`, specialist model hidden from
Pi's picker) and fails closed on the degenerate ~1e-20 score signature from
FINDINGS #7. Offline check 18 covers candidate merging, response completeness,
score magnitude, the top-five cap, query logging and argv safety; Pi's loader
and the live six-document `/v1/rerank` response shape were also verified. The
first end-to-end call found an undocumented serving constraint: a 550-token
document exceeds this reranker's physical batch of 512 and returns HTTP 500.
Excerpts are now capped at 800 characters with narrow 400/200-character retries
for that exact error. The accepted rerun ranked `steps/45-extensions.sh:1`
first at 0.9995 from 34 candidates.

The query log lives at `~/.pi/agent/project-search/queries.jsonl`, follows
`PI_CODING_AGENT_DIR`, and can be overridden with
`LOCAL_AI_PROJECT_SEARCH_LOG`. It has mode 0600 and records the full candidate
path set plus ranked top five. That makes §6.1's future quality A/B replayable
without turning the log into a copy of project contents. Deliberately still not
a vector index — real top-five misses, not feature appetite, are what can open
§6.1–2.

First-day real use also surfaced **FINDINGS #16**: a mid-turn rerank was the
trigger (not the cause) of a GPU-OOM abort of the coder engine. The
two-resident-engine memory arithmetic is at fault, not the extension. Acted
on the same day: the checkpoint writer is off (§4.6) and the margin is now
computed per machine by `scripts/gpu-budget.sh` (setup note + offline
check 19). Two more aborts on 2026-08-06 during plain coding turns settled
the rest: on 32GB only the coder resides on the GPU — the reranker is
on-demand (heavy, ttl 120, 2.4s cold load against the extension's 180s
budget) and the budget gate is bucketed by RAM (FINDINGS #16, README
bucket table).

## 6. Deferred, with conditions

### 6.1 `jina-reranker-v3` — conditional evaluation path

A 0.6B listwise reranker: query and all candidates share one context, scores
read off each document's last token — a shape that plausibly suits
rerank-over-grep *better* than the pointwise incumbent, one pass over 20–50
candidates instead of N. Blocked from *serving*: llama.cpp's `--reranking` is
pointwise only and upstream closed listwise support as **not planned**
(llama.cpp #17189), rapid-mlx exposes embeddings only, and the official GGUF
is incomplete anyway — a `projector.safetensors` MLP applied outside the
model, plus a reference script that subprocesses `llama-embedding` with
tempfiles. The blocker is indefinite, not temporary.

How the world actually runs it (surveyed 2026-07-31): mostly it doesn't —
every major RAG framework wires jina rerank through the paid API. Self-hosting
is vLLM (merged genuinely-listwise support 2026-04, PR #38800 — the right
answer on CUDA, a CPU-only source build on a Mac) or **Jina's official MLX
ports** (`jina-reranker-v3-mlx`, `jina-reranker-v3.5-mlx` — a Python
`MLXReranker` class with the projector handled, ~1.2GB BF16, score-parity
claimed; a library, not a server). The GGUF path is still fork-only: upstream
PR #22576 (projector-in-GGUF) stalled since May; PR #26286 (v3.5
sliding-window) active as of 2026-07-29 — and even merged, mainline
llama-server still would not speak listwise. No published Apple Silicon
latency numbers exist anywhere; a measurement here would be the first.

Update 2026-08-08: a third self-host path exists and lowers the stage-2 cost.
oMLX (jundot/omlx — see its DECISIONS entry) implements v3 *and* v3.5
listwise in-process on MLX: `omlx/models/reranker.py`, special-token hidden
states → projector → cosine, with a Qwen3 sliding-window patch covering
v3.5's `layer_types`. Read, not run. When the trigger fires it serves twice:
a second implementation to cross-check the official port's scores against in
stage 1, and reference plumbing to crib for the stage-2 wrapper — the wrapper
itself stays ours; a whole oMLX install is not the dependency to take for one
model.

Quality can be evaluated without building anything permanent:

**Trigger — all three, or don't start:**
1. ~~`project_search` is deployed and logging queries (the consumer exists).~~
   Met 2026-08-05; the installed extension's end-to-end acceptance query is in
   the mode-0600 JSONL log.
2. The incumbent measurably falls short on that log: if the right file is in
   the top-5 on ≥90% of judged queries, stop — there is nothing for jina to
   win, and the entry moves to DECISIONS as settled.
3. A judged set exists: ≥30 logged queries labelled with the correct file
   (cheap — the file that actually answered the question).

**Stage 1 — offline quality A/B, disposable plumbing.** Throwaway venv on a
uv-managed interpreter, never touched by setup.sh: the **official MLX port**
(`jina-reranker-v3.5-mlx` — evaluate v3.5, not v3; its official MLX/GGUF repos
are maintained as of 2026-07-30 and it claims 1.2–1.56× faster listwise at the
same size). No llama.cpp fork, no torch, and MPS-native — so the latency
measured is also the latency that would ship. Same `rg`/`fd` candidates to
both rerankers; metrics: top-5 hit rate and MRR, plus wall-clock for the
listwise pass over 20–50 candidates (unpublished territory — record it).
Decision rule declared before running: jina proceeds only if it fixes at least
half of the incumbent's top-5 misses (or ≥10pp hit-rate gain). Below that:
record in DECISIONS, close, delete this section.

**Stage 2 — only after a stage-1 win: implement this cutover checklist.** Jina
does **not** inherit the incumbent llama.cpp server's 512-token physical-batch
failure: the official MLX implementation tokenizes, truncates and blocks its
own inputs. It has different limits, not no limits (current defaults: 2,048
query tokens, 8,192 tokens per document and 131,072 tokens per listwise block),
and its memory and latency on this 32GB machine still need measurement.

1. **Service:** build a small loopback-only HTTP wrapper over the official
   `jina-reranker-v3.5-mlx` class, exposing the existing POST `/v1/rerank`
   response shape. Install it with a uv-managed interpreter at
   `~/.local/share/local-ai-setup/rerank/`, not below `.pi` (the daemon belongs
   to this stack; only Pi's query history belongs to Pi). Keep model weights in
   the Hugging Face cache. Add setup dry-run/idempotency, launchd lifecycle,
   logs, a health check and an uninstall path. Crib the serving internals from
   oMLX's `models/reranker.py` (the 2026-08-08 update above) rather than
   re-deriving them from Jina's reference script.
2. **Transport:** add a reranker-specific endpoint such as
   `LOCAL_AI_RERANK_BASE_URL`; Jina is a separate service and must not silently
   inherit llama-swap's `LOCAL_AI_BASE_URL`. Change the default model/backend
   metadata while retaining explicit overrides for an A/B rollback.
3. **Inputs:** keep the exact same `rg`/`fd` candidate set during comparison.
   Replace the Qwen-specific 800/400/200-character retry ladder with a
   token-aware Jina excerpt policy. Choose the shipped budget from measured
   quality, memory and latency rather than simply consuming the 131k maximum.
4. **Validation:** retain generic response checks (finite scores, one unique
   score for every candidate), but remove or condition the `0.001`
   `cls.output.weight` sentinel: that absolute-score guard diagnoses the broken
   Qwen GGUF, not Jina. Give Jina its own known-ranking health fixture instead.
5. **Observability and gates:** version the query-log records with backend,
   model and excerpt-policy fields before mixed A/B data is written. Add a live
   known-ranking test, a long-input test proving the old 512-token failure is
   gone, and 20–50-candidate latency/peak-memory measurements on this machine.
6. **Cutover:** only after those gates and Stage 1's quality rule pass, point
   `project_search` at Jina, update setup/runtime checks and documentation, and
   remove or park `rerank-qwen3-0.6b` so both rerankers are not resident by
   accident. Record the `cc-by-nc-4.0` restriction anywhere the resulting
   system might be distributed or used commercially.

Alternative worth one re-check at the time: llama.cpp PRs #22576/#26286 —
though even both merged leave mainline without listwise. One listwise pass may
also beat N pointwise calls on latency outright, which is part of the case for
paying the plumbing cost.

License note: `cc-by-nc-4.0` against the incumbent's apache-2.0. Irrelevant on
a personal laptop; not irrelevant if anything built on it ships.

### 6.2 Embedding / vector index

Only if rerank-over-grep proves insufficient on real `project_search` queries.
`embed-qwen3-0.6b` is parked in models.example.yaml ready to deploy; if the
corpus is mostly code, `jinaai/jina-code-embeddings-0.5b-GGUF` is the stronger
candidate (MTEB-Code 78.7 self-reported vs the incumbent's 75.4 — decide on
own queries, not leaderboards). Would need a store — sqlite-vec via a pinned
dylib was the sketch, since there is no brew formula.

### 6.3 `firish/webfetch` — local web search for agents

Attractive capability. The original "pip-only" objection is dissolved by the
uv pattern (DECISIONS §dependencies): `uv tool install` gives it a dedicated
venv on a uv-managed interpreter, passing all four questions. What stands is
maturity (~51 stars as of 2026-07) and that Pi's MCP support is unverified.
Plan unchanged: trial it as a Claude Code plugin *outside* this project;
adopt only if it proves out and Pi can consume it.

### 6.4 Editor FIM completion

`qwen2.5-coder-1.5b-fim` is parked in models.example.yaml; point
llama.vim/llama.vscode at `/upstream/<id>/infill`. The repo caveat is
resolved: `ggml-org/Qwen2.5-Coder-1.5B-Q8_0-GGUF` is exactly what llama.cpp's
own `--fim-qwen-1.5b-default` preset downloads (verified 2026-07-31 against
`common/arg.cpp`). Keep base-not-instruct; steal the preset's
`--cache-reuse 256`. Upgrade path: the 3B (3.29GB). Decision reconfirmed
2026-08-05: this stays a separate editor endpoint and outside the installed
stack until inline completion is actively wanted; it is not another Pi model.

### 6.5 OCR

Also deliberately separate until needed. Occasional screenshot and document
work already has Qwen3.5-4B and NuExtract3; availability of a smaller OCR model
does not create a consumer. If recurring OCR exposes accuracy or wake-up
latency problems, the first current A/B candidate is the MIT-licensed
`zai-org/GLM-OCR` 0.9B through `ggml-org/GLM-OCR-GGUF`, which llama.cpp can
serve. Compare exact text, tables/formulas, cold/warm latency and peak memory
against both existing paths, then expose any winner as a typed `ocr_document`
capability rather than a chat choice.

### 6.6 Declined for now

Dictation/STT, PII gate, translation — `qwen3guard-0.6b` and `hy-mt-1.8b` sit
in models.example.yaml if that changes. Router/classifier in front of the
coder: explicitly not wanted, see DECISIONS.

## 7. Known rough edges

Live hazards not yet scheduled as fixes. (The backup pruning, parser bugs and
`--pi-hints` items that used to sit here are now scheduled work in §1–2.)

1. **`permission-gate.ts` only guards `bash`.** First line:
   `if (event.toolName !== "bash") return undefined` — `write`/`edit` bypass
   it entirely, and its patterns are only `rm -rf`, `sudo`, `chmod 777`. Every
   destructive action observed during the A/Bs came through `write`, the one
   door not watched. Widening it means hooking `write`/`edit` and treating
   "overwrites a file that already exists" as the dangerous case — a different
   shape of check from a command regex.
2. **The coder will overwrite a script it was only asked to run**, then report
   results from its own invention (2026-07-30: fabricated a Docker/Ollama test
   suite over `tests/run-all.sh`, ran it, answered `1`; reproduced 2 of 4;
   answered over an `isError=true` result). Never point a write-capable agent
   at the real tree; the failure is *quiet*. §2.5 promotes this to README.
3. **Memory pressure still starts with `sysctl vm.swapusage`** — and look
   outside the stack too: on 2026-07-29 the cause was 413 leaked iOS Simulator
   processes holding 9.8GB (`xcrun simctl shutdown all` cleared it).
4. **Pi's `defaultModel` still points at `qwen3.6-35b-mlx`**, not in the
   manifest. setup.sh reports it and deliberately does not rewrite
   settings.json; fix with Pi's `/model` picker. (§4.5's MoE probe would
   temporarily un-strand it.)
5. **`extract_json`'s validation paths are unexercised** — the HTTP layer and
   registration are verified, but verbatim-span checking and the
   referenced-file warnings have never met a real hallucination.
6. **`--verify` is untested** — `hf cache verify` exists and the wiring looks
   right, but checksumming 16GB was never actually run.
7. ~~**`~/.rapid-mlx` borrows Homebrew's Python.**~~ **Closed 2026-08-06**:
   the 0.12.4 formula passed §3a's gate, the venv and its seven symlinks are
   removed, and the formula's Python dep is brew's to rebuild on interpreter
   migrations. The doctrine survives in DECISIONS' dependency note — a venv
   is only as pinned as its interpreter, and any *new* venv exception uses a
   uv-managed interpreter. **The edge itself stays closed, but a venv is
   back** (same night): the formula's mlx pairing failed FINDINGS #19's
   measurements and the engine now serves from `~/.rapid-mlx/venv` on a
   **uv-managed** py3.12 — the stricter standard this entry prescribed, so
   the original hazard (an interpreter another manager can delete) does not
   apply to it. §3b's mlx bullet tracks re-adoption.

## 8. Architecture-review recommendations — recorded, not scheduled

The 2026-08-05 review found two trust improvements more valuable than another
model: lock exact model/patch artifacts, and put write/edit behind a tested,
recoverable mutation boundary. It also recommends structured evaluation
records and a Rapid-MLX 0.12.3 re-baseline before new tuning. The evidence,
proposed shapes, optimization candidates, non-candidates and conditional model
watchlist are preserved in
[ARCHITECTURE-REVIEW-2026-08-05.md](ARCHITECTURE-REVIEW-2026-08-05.md).

The coding-loop recommendations that followed are preserved separately in
[CODING-OPTIMIZATION-PLAN.md](CODING-OPTIMIZATION-PLAN.md): selective thinking,
manual tool profiles, verification debt, fresh-context review, conditional
related-code retrieval and a last-resort weight-precision A/B.

Neither companion document changes §4's run order or authorizes a model
install. Promote an item into the active queue only when chosen for
implementation.

## 9. Field kit — the install probe for other machines

**Built 2026-08-07, as its own repo: `~/work/local-ai/field-kit`** (owner
call — a clean, friend-facing repo whose README assumes the reader has never
seen this one; the kit fetches this repo itself, so a friend clones exactly
one thing). The bucket table in the README is currently one machine plus
arithmetic, and FINDINGS #16 showed twice what arithmetic is worth without a
machine to refute it. It is an **install probe**, not a survey: it installs
the stack on a foreign machine, verifies it actually serves, measures what
that hardware can hold, and reports back the numbers that decide the
per-bucket defaults.

**Shape as built** (`probe.sh` — stages are subcommands, `run` walks them
with a cost consent before each; one `FIELD-KIT RESULT <stage> <ok|fail>`
line per stage for agent drivers):

- **Stage 0** stays `scripts/machine-report.sh` here (canonical, check 20);
  the kit runs the clone's copy in preflight, plus swap/disk/port/brew
  checks. Below-minimum machines stop unless explicitly overridden.
- **Install trial**: provenance snapshot first (pre-existing formulas, dirs,
  weights — the uninstaller's input), then `--dry-run` / real / second run
  with all three summaries parsed into the report; the second must be
  `installed 0 · patched 0 · changed 0`. This is the off-box idempotence
  evidence offline check 3 says needs a real machine.
- **Serve trial**: foreground llama-swap (env read back from the rendered
  plist; no launchd job, the check-16 lesson in reverse), time-to-resident
  as the preload number, the runtime suite via `LOCAL_AI_BASE_URL`, then a
  **context-sized timing probe** (~3.6k tokens, twice) — FINDINGS #19's
  gate, which check 12 cannot provide.
- **Fit trial**: `gpu-budget.sh` prediction recorded, then a soak in the
  FINDINGS #16 shape (default 20 min, growing to ~19k tokens with mid-turn
  reranks) watching all three abort channels. An abort is the datapoint,
  not a bug.
- **Perf trial**: installed engine only by default (`engine-ab.sh` through
  the foreground proxy via `LOCAL_AI_AB_MODEL`, `perf.sh` check 13 via
  `LOCAL_AI_PERF_NO_RESTART`); the two-engine FINDINGS #13 A/B is opt-in
  (`perf --ab`, ~16GB extra, consent required). Every measurement records
  the wall and swap it ran under; the raised-wall re-run stays a printed
  sudo command plus re-invoking fit/perf.
- **Cleanup**: the keep-or-restore contract is asked before stage 1 and
  recorded. Keep = the stack's own setup loads launchd; restore =
  `scripts/uninstall.sh --provenance` (lives here, offline check 21).
- **Agent mode**: the kit's `AGENT.md` is a runbook for a friend's AI agent
  — heal the environment never the measurement, mandatory
  `probe.sh deviation` logging, idle during timing windows, run-only,
  sudo and consent gates stay human. Non-interactive consents via
  `FIELD_KIT_CONTRACT`/`FIELD_KIT_YES`.
- **No model mirror** (considered, rejected 2026-08-07): a CDN copy would
  bypass the real installer the trial exists to test, the speed win is
  unmeasured, and it opens a redistribution channel this project avoids.
  Instead the kit README documents **cache pre-seeding** from a drive
  (`has_weights` makes setup skip the download) and the report records
  seeded-vs-pulled.

**Per-bucket tuning, tested (goal stated 2026-08-07, owner).** The end
state is a tuned configuration per bucket, where the bucket key is
**RAM × chip generation (pre-M5 / M5+) × memory bandwidth** — not RAM
alone. Each axis decides different parameters through different kit
measurements: RAM → residency/fraction/ttl (the fit soak is its test);
chip generation → the engine split (FINDINGS #13 rests on prefill, which
is where M5's accelerators live — only `perf --ab` answers it, so the kit
steers pre-M5 machines toward it); bandwidth → decode, hence which
model-size/quant candidates a bucket can usably offer (the kit derives
**effective bandwidth from decode × weight bytes**; a public-spec chip
table gives only the preflight estimate). **"Tested" means witnessed**: a
catalog row (bucket → tuned manifest) ships only with a kit run on a
machine in that bucket that executed the candidate through serve + fit +
perf — `FIELD_KIT_MANIFEST` exists for exactly this — recorded with chip,
SHAs, date, numbers. Tuning can also iterate **in flight** (owner ask,
2026-08-07): `probe.sh tune --fraction 0.NN | tune <candidate.yaml>`
edits the disposable clone's manifest, re-renders through the config
parse gate, and labels every subsequent measurement `tuneN` — so one
visit can walk the FINDINGS #16 fraction ladder instead of shipping a
bare failure home. The clone-manifest edit is sanctioned there precisely
because ground rule 6 protects the owner's hand-edited file, not the
probe's copy. The §10 catalog inherits this bucket key — and the
five-dimension configuration space the first-live-run decisions below
define: buckets select a starting combination, they do not ship as
fixed SKUs. Boundary
unchanged (ground rule 8): the kit tests mechanical tuning and can rule a
candidate *out*; model and quant choices are quality decisions and stay
with the owner and the task-success corpus.

**Honest limit:** the end-to-end (foreign install, foreground serve, soak)
cannot be verified on this machine — the stack is installed here and :8080
is live. The kit's hermetic suite covers its decision logic; **the first
friend run is the real test** and is the open item this section now waits
on.

**What the data decides** — the point of the exercise:

- Per-bucket manifests in `models.example.yaml`: which residents each bucket
  can actually hold, and whether a >36GB bucket upgrades the reranker
  (§6.1's jina-v3 would live there, not on this machine).
- Whether the 32GB posture hardens further. If the on-demand specialists
  still trip aborts here, the endgame for this machine is a single pinned
  model — which also means either a CPU reranker (`-ngl 0`, unmeasured) or
  retiring project_search's rerank stage, so it is not a free decision.
- Whether a below-minimum manifest is worth building: a smaller coder for
  16–24GB machines. The README currently says there is no drop-in option,
  and that stays true until someone measures a candidate.

**Boundary.** The probe decides *usability* (fits, serves, streams tool
calls) and *parameters* (utilization fraction, residents, ttl) — all
mechanical. It never decides model quality: first-attempt task success is
not probe-measurable, and ground rule 8 stands — no model or backend
recommendation on tok/s alone. A probe result can rule a model *out*
(does not fit, does not serve); ruling one *in* stays a quality decision.

**Open questions — all four settled 2026-08-07, in the build:** delivery is
"clone the kit repo, it fetches the stack" (a drive copy works for both repos
plus weights); the service runs in the foreground from the plist's own env;
the cleanup contract is asked before stage 1 and executed by the
provenance-guided uninstaller; comparability is enforced by per-stage cost
consents, the idle-during-timing rule in AGENT.md, and swap labeling on
every measurement (numbers above the ~1GB baseline are marked polluted, not
scored).

**Stack-side pieces that landed here for the kit (2026-08-07):**
`scripts/uninstall.sh` (+ offline check 21) making the README removal table
executable; runtime check 10 rewritten manifest-driven (it asserted the
pre-#16 residency posture and failed on today's healthy stack);
`machine-report.sh`'s wired-limit line fixed above 32GB (reserve rule alone
overstated exactly the machines friends have; check 20 now pins the 75%
rule); `steps/50-service.sh` made second-run-clean under
`LOCALAI_SKIP_LAUNCHCTL`; `tests/perf.sh` gained `LOCAL_AI_PERF_NO_RESTART`;
`tests/engine-ab.sh` gained `LOCAL_AI_AB_MODEL`.

**First live run (2026-08-07 evening, this machine as its own foreign
machine).** The stack was provenance-uninstalled, the box rebooted, and the
kit ran the full sequence against a clean state with seeded weights. It
found and fixed **nine kit bugs** on the way (provenance comment-mangling
×2, brew-list SIGPIPE mis-read, seeded-detection, preload measured before
ready-state, streaming usage absent so the soak's 19k reset never fired,
decode window folding prefill in, A/B arm flag quoting, preload
never-retried stall — kit `e3df3fc` and back), and two stack bugs landed
here from it: **`brew` now refuses untrusted taps** (every clean machine
would have failed install; engines step trusts the llama-swap tap,
`ecaa018`) and the uninstaller's own brew SIGPIPE skip (`d85bc12`,
check 21's shim now reproduces the race). Run archive:
`field-kit/probe-results-run1-20260807/` (local only).

**What the run witnessed (M5 32GB, wall 24576MB, archived report):**

- **The wall model.** `fraction × Metal-device-memory (~81% RAM) +
  co-resident helpers + ~1–2GB OS GPU floor ≤ the wall`. Witnessed:
  0.85 solo holds a 14.2k prefill; 0.85 + the 0.6GB reranker aborts at
  ≥12k-token prompts (3/66 soak turns, Metal OOM); 0.75 + the 3.2GB
  extractor aborts from ~6k (13/43 turns) and cannot even warm up fully
  wired (11-token warmup OOM, reproduced outside llama-swap). The
  co-tenant's size sets the coder's survivable context. `gpu-budget.sh`'s
  "≤0.77 covers it" is ~0.03 optimistic — its reserve omits the OS floor;
  FINDINGS #16's ≤0.74 was right.
- **Engine A/B (both arms, identical method).** MLX prefill **185 tok/s**
  vs GGUF 122 (the M5 accelerator split, again); decode MLX **8.2 flat**
  = the dense-read ceiling — its configured MTP is silently not engaging
  (rapid-mlx 0.12.4 auto-downgrades this hybrid-attention checkpoint to
  the mlx-lm lane, its GitHub #352) — vs GGUF **13.0–13.4 with MTP
  working**. The #19 acceptance gate is prefill-only and cannot see this
  class of regression; the kit's perf now prints a dense-ceiling verdict
  that can.
- **llama-swap failure modes.** A model whose load fails sits in error
  state answering instant 500s until ttl expiry or the next real request
  (58 straight failed reranks in one soak); preloads are **never
  retried**; a fresh instance can crash its one preload into the previous
  instance's unreclaimed GPU memory. The kit now nudges; the stack should
  know these in FINDINGS.

**Decisions (owner, 2026-08-07 evening):**

- **The bucketing strategy is the point of all of this, and its shape is
  a configuration space, not a set of SKUs.** A machine's configuration
  is a *combination* across five dimensions, and each laptop may land on
  a unique one:
    1. **model set** — which models exist on this machine at all (which
       coder, which helpers, or none);
    2. **per-role engine** — MLX vs llama.cpp vs remote, chosen per role,
       not globally;
    3. **tunings** — fraction, speculation config, ttl, flags;
    4. **residency strategy** — pinned / on-demand / group-swap / CPU;
    5. **local/cloud switch strategy** — per-role routing policy, not a
       static placement: remote-preferred with a local fallback,
       local-always for latency-critical roles, and the degradation path
       when offline.
  The three bucket axes (RAM × chip generation × bandwidth) select a
  *starting combination* — a prior, not a verdict: chip generation picks
  the prefill engine, bandwidth decides how much speculation is worth (at
  153GB/s it is ~60% of decode — more than the engine choice), RAM picks
  the residency strategy. The witnessed combination on the actual machine
  is what ships for it. The kit witnesses the local-mechanical dimensions
  (a candidate manifest carries model set + engines + tunings + residency
  wholesale via `FIELD_KIT_MANIFEST`); the routing policy lives in the
  provider config outside models.yaml, is recorded on the row, and its
  quality stays an owner decision — the kit can only witness what the
  local side does when the remote side is absent.
- **32GB bucket candidate manifest**: coder on GPU at 0.85 (solo-safe,
  witnessed), reranker local on **CPU** (`-ngl 0` — mid-turn latency
  needs local, 0.6B is CPU-cheap), extractor to the **non-local helper
  group** (remote; offline fallback = the coder's own `json_schema` path;
  the catalog row must state the data boundary this moves). This
  dissolves the fraction ladder from daily operation. Needs: runtime
  check 11 skip-with-reason for extractor-less manifests, then a kit
  witness run. **Both done — witnessed clean 2026-08-08, see the witness
  run below.**
- **The extraction A/B (NuExtract vs Qwen3.5-4B) rescopes** to 64GB+ rows
  and the CPU-fallback quality check; on 32GB the placement decision
  makes the footprint question moot.
- **Agents driving the probe get the interpretive context** (kit
  `e3df3fc`): AGENT.md's "Reading the evidence" (wall model, failure
  signatures, spread/ceiling reading), `probe.sh conclude` so analysis
  lands in the paste-back report, and the perf dense-ceiling verdict.

**Witness run (2026-08-08 evening, this machine, kit `61791d2` / stack
`7470324`).** The 32GB candidate (`candidates/32gb-coder-only-gpu.yaml`:
coder-only GPU at 0.85, reranker on CPU via `-ngl 0`, extraction off-box)
ran the full kit sequence and hit every expected outcome:

- **Fit soak: 65 turns / 1200s, rerank-only shape, zero aborts —
  gpu-budget's prediction held.** Against run 1's 0.85 + GPU-reranker
  (3/66 turns aborted at ≥12k prompts) and 0.75 + extractor (13/43 from
  ~6k), the coder-only-GPU strategy dissolves the fraction ladder from
  daily operation, exactly as the wall model predicts.
- Runtime suite 7 passed / 0 failed: check 11 skipped with the
  extractor-less reason (the `69cf1bb` accommodation, witnessed working),
  check 12 streamed a parsed tool call, CPU reranker scores
  non-degenerate with residency per manifest.
- Perf: decode 8.1 tok/s (reps 8.0–8.2) at the dense-read ceiling,
  effective bandwidth ~121GB/s (preflight estimate 153), cold prefill
  173 tok/s, preload 11s. Configured MTP still not engaging (#352) —
  inherited engine finding, not a manifest defect.
- Conditions clean throughout (therm=ok, power=ac, swap≤91MB); one
  pre-preflight deviation (killed an orphaned run-1 A/B llama-server
  holding 4.3GB and 3GB of swap). Run archive:
  `field-kit/probe-results-run2-20260808/` (local only).

The candidate is marked **witnessed** in its header — catalog-row ready.
The owner manifest adopted the strategy the same day (reranker `-ngl 0`;
extractor and vision retired to `models.example.yaml`). The remote
extraction route was then considered and **declined** (owner,
2026-08-08): the 32GB row ships with no extraction route for now — no
data-boundary move — and `extract-json.ts` stays repo-side for a future
helper mode. That decision landed the §10 entry ↔ extension rule early:
`steps/45-extensions.sh` now enforces the declared edges from the
manifest (install when the backing entry is present and enabled, remove
the installed tool when it is not), and offline check 5 derives its
specialist specimen from the manifest instead of a hardcoded id. New stack
finding from the keep path: **`steps/50-service.sh` treats "loaded" as
"running"** — a loaded-but-dead job (clean SIGTERM exit; the plist's
`KeepAlive SuccessfulExit=false` never restarts a clean exit; `launchctl
load` is a no-op on a loaded job) is never kicked and the health gate
fails 30s later; the step needs a `launchctl kickstart` when the job is
loaded but not running.

**Queue out of the runs:** three FINDINGS entries (wall model + boundaries;
llama-swap error-state/preload behavior; the MLX decode regression and the
gate gap — add a decode gate to the #19 acceptance gate); gpu-budget
reserve gains the measured OS floor; venv reproducibility (rapid-mlx pin
is exact but transitives float — no lockfile; `--exclude-newer` or
constraints); chase the MTP lane downgrade upstream (#352);
`steps/50-service.sh` kickstarts a loaded-but-dead job (2026-08-08
finding); and the original open item stands — a genuinely foreign
machine. Done from the 2026-08-07 queue: check 11 accommodation
(`69cf1bb`, witnessed 2026-08-08) and the end-to-end re-run of the
updated kit (the witness run above). Settled without code: the remote
extraction route — declined 2026-08-08; the extension gate covers the
removal, and a helper mode for `extract-json.ts` is a parked idea.

## 10. The reshape — catalog, wizard, lock, and the release/labs split

**Client-facing tool spec drafted: `docs/TEMPER.md` (2026-08-07, draft for
owner edit)** — the product surface built on this section plus §9's witness
mechanism and the five-dimension configuration space.

Planned 2026-08-06 from a shapes discussion; **nothing here is started**, and
none of it changes §4's run order. The end state: `models.yaml` stops being a
file the user inherits and becomes one they author through a guided first
run — and the project splits into a clean end-user **release** repo and a
public **labs** repo that produces its evidence.

**The pipeline.** Every arrow is a distinct artifact with exactly one writer:

```
catalog (shipped) → wizard → models.yaml (user intent) → lock → generator → configs
     deterministic checks ↑
```

- **Catalog** — what `models.yaml` + `models.example.yaml` become: every
  *measured* entry, tagged with the bucket it suits and a short "what this
  choice means for you" blurb. The wizard renders its explanations from
  here, not from prose hardcoded in the wizard — one home for the advice,
  shared with README/DECISIONS instead of triplicated.
- **Wizard** — runs the deterministic checks first (`machine-report.sh` +
  the `gpu-budget.sh` bucket), then asks only what the machine cannot
  answer, in this order (settled 2026-08-06): **(a) profile** (see the
  profiles bullet), **(b) harness checkboxes** (see the harness bullet —
  orthogonal to profile), **(c) specialists**, recommended set
  pre-selected, extension cascade explained at choice time, **(d)
  allowances** — not "how much RAM do you have" (detected) but "how much
  may the stack take", defaulting to the bucket recommendation. Disk
  allowance controls the HF cache budget and which quants are even
  offered. If allowance lands, `gpu-budget.sh` and the wired-limit
  formula gate against the allowance, not the machine max.
- **`models.yaml`** — written by the wizard *once*, when absent; after that
  it is the user's hand-edited file and nothing rewrites it (ground rule
  6's reasoning: no mechanical rewrite survives the comment layer, and a
  merge is strictly harder than the thing already banned). Re-running the
  wizard with the file present is **advisory only**: it prints how the
  manifest differs from the current recommendation for the bucket (retired
  entries carried, new measured options, fraction above recommendation)
  and stops. Start-over = move the file aside and re-run; the hand-port
  from the backup *is* the merge, done by the only party who knows which
  edits mattered. This preserves rules 4 and 5 for free: the wizard never
  fires on a second run and has no interactive stage for `--dry-run` to
  worry about.
- **Lock** (`models.lock.yaml` — review §1, adopted into this shape) —
  sits between intent and render. Two kinds of rows: *pins* (HF revision
  SHAs, exact filenames, hashes — enforced by `apply`, moved only by
  `update`) and *witness* (engine versions, hardware, acceptance date —
  recorded and warned on drift, never enforced, because brew and the venv
  own the engines; pretending to pin them would be a lie in the file).
  Locking never forces downloads: heavy entries keep their lazy pull via a
  generated launcher that fetches the locked revision on first start
  instead of letting `-hf` follow a remote branch.
- **Generator** — `steps/40-configs.sh` extracted into a standalone
  executable. `setup.sh` calls the *same executable*, never a copy — two
  render implementations is the two-sources-of-truth disease. Three
  consumers appear for free: "I edited models.yaml, re-render", the
  wizard, and the field kit's install trial (§9).
- **Specialist choice cascades to extensions.** The wizard presents the
  specialists with the bucket's recommended set pre-selected; unchecking
  one also drops the tools it powers, stated at choice time ("no reranker
  → no `project_search`: the tool has no backend without one"). The edges
  are hard today — `project-search.ts` requires the `rerank-qwen3-0.6b`
  entry, `extract-json.ts` requires `extract-nuextract3`,
  `compact-test-output.ts` requires no model (verified in the sources,
  2026-08-06). The dependency is *declared* in the catalog (entry ↔
  consumer extensions) but *enforced from the manifest*, not from wizard
  state: an extension installs only when its backing entry is present and
  enabled, so a day-two hand-edit — deleting the reranker entry — produces
  the same cascade on the next run. That is the round-trip discipline
  `enabled: false` already has in the configs (check 4), extended to
  extensions. `steps/45-extensions.sh` enforces this since 2026-08-08 for
  the declared edges (install when the backing entry is present and
  enabled, remove the installed tool when it is not — witnessed removing
  `extract-json.ts` on the 32GB machine); the catalog-declared dependency
  data, replacing the hardcoded edge list, is still §10 work.
- **Harness subpackages, Pi first.** The stack serves any OpenAI-compatible
  agent client; what is Pi-specific is the client view (the `local`
  provider in `models.json`), the extensions, and their checks. The
  release repo namespaces that per harness from day one — core renders
  manifest → llama-swap; a harness subpackage renders its client config,
  installs its extensions under the dependency rule above, and contributes
  its targeted checks. **Only the Pi subpackage is produced now**: the
  seam exists so a second harness lands as a new subpackage against the
  same small interface, with no core changes. Harnesses are wizard
  **checkboxes, orthogonal to the profile** (settled 2026-08-06):
  selecting Pi does not imply local development — Pi running a cloud
  default model still consumes the specialist extensions, which call
  llama-swap on :8080 directly and never care what chat model the client
  runs (verified in the extension sources). The constraint runs the other
  way: the local *coder* needs a consuming harness, and today only Pi
  speaks its API — so the full-local profile without the Pi checkbox
  earns a warning ("a 15GB coder no selected harness can drive").
- **Install profiles, and a runtime mode switch** (added 2026-08-06). The
  wizard's first question becomes *what this machine is for*:
  - **Full local stack** — the current shape: pinned coder plus on-demand
    specialists, bucket rules as measured.
  - **Helper** — no main coder at all: just the specialists (reranker,
    extract, vision) serving another harness's agent as local, private,
    token-free tools. This profile rewrites the bucket math: with the
    15GB budget holder gone, specialists can be always-resident again,
    machines below the 32GB coding minimum become viable (a 16GB Air
    holds the reranker and extractor comfortably), and even this 32GB
    box could afford a *better* reranker (§6.1's jina-v3) in helper
    shape. "Below the minimum" is a verdict about the *coding profile*,
    not the machine — which partially answers §9's below-minimum
    question. The helper profile is the `claude/` subpackage's natural
    first customer (MCP rendering instead of models.json) and needs no
    Pi at all — the harness question stops being hypothetical the day
    this profile exists.
  - **Both, with a mode switch** — one manifest, two rendered *postures*.
    A mode is a residency overlay (group/preload/ttl per entry), never a
    second manifest: **helper mode** keeps the coder unloaded and lets
    chosen specialists sit resident; **offline-coding mode** is the
    current posture — coder pinned and preloaded, specialists on-demand,
    the FINDINGS #16 rule applied. The "only the coder resides at 32GB"
    policy thus lives in the coding overlay where it belongs, instead of
    posing as a universal truth. Switching renders the target posture's
    config and lets llama-swap's `--watch-config` pick it up; entering
    coding mode warms the coder (~2 min to resident weights — the switch
    is honest about not being instant).

**The CLI.** Three verbs at most; setup.sh sequences, the CLI transforms
artifacts:

- `apply` — models.yaml + lock → configs. Consumes pins, never moves them.
  A manifest entry with no lock row (hand-added yesterday) gets resolved
  and locked: fill gaps, never move existing pins. First `apply` after the
  wizard is the all-gaps case and creates the whole lock.
- `update [id]` — re-resolves against upstream and rewrites pins. Per-entry
  is the normal move (one boundary at a time); bare `update` exists but
  bundles unrelated risk. Prints the old→new diff per entry, resets the
  touched entries' acceptance witness to **unverified**, and ends by
  *printing* the targeted gate — coder: check 12's curl plus a plain
  completion; reranker: check 9's magnitude probe — never running it. The
  human decides when the box is ready (the heavy-suites-on-demand policy
  and the announce-first rule both apply). Drift stops being something you
  remember and becomes state the file shows.
- `check` (later) — budget + lock drift + the wizard's advisory compare,
  once the first two exist.
- `mode <name>` (with the profiles bullet below) — switch the rendered
  residency posture at runtime; a state change, not a transform, so it
  reports what it loaded/unloaded and how long warmup will take.

**The TUI** (direction, 2026-08-06): the wizard's surface — profile,
harness checkboxes, specialist cascades, allowances, each with its
consequences explained — has outgrown `read` menus, so the likely shape
is a **Go TUI shipped as a single static darwin/arm64 binary**. A static
binary passes the dependency test more cleanly than anything else in the
stack (it borrows no runtime at all), and the bash 3.2 rule is untouched
— it governs the scripts. llama-swap is Go already; the bubbletea/huh
family is the obvious toolkit. Two decisions before committing: (1)
distribution — prebuilt release-asset binary versus building at setup
time (the latter adds the Go toolchain as a brew dependency); the tree
stays 0BSD-clean either way, since a release *asset* carries the embedded
third-party notices and the repo never vendors them. (2) Scope — a
binary that owns only the TUI while bash owns `apply`/`update` is a
split brain, so if Go enters, the whole CLI likely follows. The bash
generator extraction (sequence step 1) goes first regardless: it defines
the interface and becomes the oracle the Go port is diffed against —
render both, byte-compare the configs.

**The split.** Boundary criterion: *does a stranger installing the stack
need it?* And the inversion that follows from "labs keeps the history":
this repo — its git history, the numbered FINDINGS log, the measurement
queue, the A/B harnesses — **becomes labs**; the release repo is extracted
clean and takes the good name. Both public; **no cross-links**. Release
mentions labs in exactly one linkless sentence that doubles as the
provenance line ("compiled from measurements through <date>").

- Release owns: setup + wizard + generator + lock, the catalog, the
  acceptance suites, `machine-report.sh`, README,
  DECISIONS-as-applicability, the **findings summary**, the harness
  subpackages (Pi first), and the field kit (§9) as the distributed thing.
- Labs owns: the FINDINGS log (append-only, keeps its numbering forever,
  retractions as new entries), the historical narrative, the corpora,
  `cache-ab.sh`/`engine-ab.sh`, §4's measurement queue,
  CODING-OPTIMIZATION-PLAN — and this file, which at split time simply
  becomes the labs queue while release gets a short roadmap of its own.
- The **findings summary** is compiled at split time, not before:
  topic-anchored ("streaming-tool-calls", "gpu-pool"), current-truth only,
  rewritten freely as truth changes, every claim standalone — method
  one-liner, n, date, and the condition that would flip it — because a
  release reader has no citation to follow. Derivation is one-way:
  measurements land in the log first; a claim that exists only in the
  summary is the two-sources disease in prose. Release drops the
  "FINDINGS #N" citation style entirely — inline references in models.yaml
  comments, steps and tests move release-side and must resolve to release
  anchors; the compile step maps log numbers to anchors once. A "current
  through <log entry>, <date>" watermark makes staleness visible.
- Two quality bars, deliberately: labs code may be scrappy, hardcoded to
  one machine, ceremony-free; release code carries the ground rules
  (bash 3.2, second-run-clean, `--dry-run` purity, offline coverage,
  machine-derived values only). Graduation is a **rewrite against the
  release bar, not a file move** — precedent: `project_search`. The
  harness that measured a thing and the artifact that shipped because of
  it are different code with different owners; no shared source, no drift.
- One loop crosses the boundary on purpose: the field kit ships with
  release, and its output is labs data that feeds the next catalog. That
  loop is the distribution story for the bucket work; both repos' docs
  name it, linklessly.

**Sequence** — 1–3 happen in *this* repo, before any split:

1. Generator extraction (no behavior change; unblocks wizard, lock, and
   the field kit alike).
2. `project_search` query-log schema v2 (review §3's first record — the
   "while only four live records exist" window expires on its own).
3. Lockfile + `apply`/`update`.
4. Wizard + catalog + bucket menus.
5. The split, with the findings summary written at split time — not
   before, since until then the single FINDINGS.md serves both roles.

Review §2's mutation boundary runs on its own track: it queues behind none
of this, and it is arguably the highest-value single item on the board.

**Open questions before any of this becomes active work:**

- Does publishing wait for the reshape, or does this repo publish first
  and reshape after? §0's gates are closed; the reshape is not a gate.
- ~~The release repo's name~~ — **settled 2026-08-07: the tool and release
  repo are `temper`, under the GitHub org `temper-sh`** (temper-sh/temper,
  temper-sh/labs at split time, temper-sh/field-kit unchanged). Verified
  free at decision time: the org, the brew formula namespace, and PATH; the
  binary name has no CLI collision. Rejected on collisions: `local-ai-*`
  (mudler/LocalAI), `hearth` (an active local-AI agent), `idem`
  (SaltStack's idempotent-infra tool), and the org `temper-ai` (taken, and
  the temper.ai/Tempered-AI brand fog around "-ai"). **The org was
  registered the same day and `field-kit` is pushed** —
  github.com/temper-sh/field-kit is the first public artifact, carrying a
  distribution copy of `machine-report.sh` at its top level (canonical
  stays here; hand-sync, the kit's tests pin the formula). Naming
  rationale (annealing/tempering family, the runner-up candidates and
  their kill reasons) is in the 2026-08-07 session; nothing else renames
  until the split itself.
- What disk allowance controls beyond the HF cache budget.
- Catalog format: one file with bucket tags, or per-bucket manifests. §9's
  "what the data decides" feeds this — the kit's numbers pick the
  per-bucket residents, so the format decision can wait for the first
  foreign-machine data.
- Mode-switch mechanics: whether llama-swap unloads an already-resident
  coder when `--watch-config` swaps the rendered file, or the switch
  needs an explicit unload call — measure, don't assume.
- Pi in helper mode: `defaultModel` naming the unloaded coder means one
  accidental Pi turn pulls 15GB. The helper posture needs a guard — a
  distinct helper-mode client view, or at least a stated warning.
- Mode names ("helper"/"offline"? "assist"/"local"?) — settle before the
  wizard hardcodes strings users will type.
- The two Go decisions in the TUI paragraph: distribution (release asset
  vs built at setup) and scope (TUI-only vs the whole CLI in Go).
