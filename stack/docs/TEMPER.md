# temper — client-facing tool spec (DRAFT)

Drafted 2026-08-07 by the working session that ran the field kit's first
live probe; extended 2026-08-08 from the owner discussion (org shape,
modes, home dir, daemon control); for owner edit. This is the product
spec for the tool a stranger gets. It builds on PLAN §10 (catalog /
wizard / lock / generator / profiles / split — all settled there) and
PLAN §9 (the witness mechanism); where this file goes beyond those, the
item is marked **proposed**.

Naming (settled): the binary is `temper`, the org is `temper-sh`. The
release repo is `temper-sh/temper` (extracted clean, takes the name); this
repo becomes `temper-sh/labs` and keeps the history. `temper-sh/field-kit`
already exists and is absorbed as the probe surface (below).

## One paragraph

temper installs, tunes, and verifies a local-LLM stack on a Mac — and
refuses to pretend. Every recommendation it makes traces to a measurement
on real hardware; every number it reports carries the conditions it ran
under; anything unmeasured is labeled unmeasured. It configures a machine
as a *combination* — model set, per-role engine, tunings, residency,
local/cloud routing — not as a tier in a marketing table, and it can
probe any Mac to witness whether a candidate combination actually holds.

## Users and their jobs

1. **The owner-operator** (the stranger installing): wants a working local
   stack matched to their hardware without reading a lab notebook. Runs
   the wizard once, gets a manifest they own, applies updates when they
   choose.
2. **The friend with a different Mac**: runs the probe (today's
   field-kit), sends back one text block, gets to keep or fully remove
   the result. Their machine's witnessed combination can become a catalog
   row.
3. **The AI agent driving either of the above**: first-class. Stable
   RESULT lines, machine-parseable outcomes, an interpretive runbook
   (AGENT.md's evidence model), consent gates that stay human, and a
   `conclude` channel so its analysis lands in the artifact, not a chat
   log.

## Principles (inherited, non-negotiable)

- **Measured beats plausible.** A catalog row ships only with a witnessed
  run behind it (machine, SHAs, date, numbers). The tool can rule a
  combination *out*, never in — quality stays a human decision.
- **Nothing phones home.** Reports are local files a human chooses to
  paste. No telemetry, ever. (The non-local helper group sends *inference
  requests* to a provider the owner configured — that is the owner's data
  boundary decision, stated on the row, and distinct from telemetry.)
- **The user's manifest is theirs.** Written once by the wizard, then
  never mechanically rewritten (ground rule 6). Advisory diffs only.
- **No sudo, ever.** Privileged tweaks are printed for the human.
- **Conditions on every number.** wall / swap / tune label / thermal /
  power / load — measurements without conditions are anecdotes.
- **Second-run-clean.** Idempotence is a release gate, not a nice-to-have.

## The machine model

A machine's configuration is a combination across five dimensions
(PLAN §9, owner decision 2026-08-07):

1. model set · 2. per-role engine · 3. tunings · 4. residency strategy ·
5. local/cloud switch strategy.

The bucket axes — RAM × chip generation × memory bandwidth — select a
*starting* combination (a prior, not a SKU). The wizard proposes it; the
probe witnesses it; the witnessed combination is what the machine runs.
The wall model (fraction × Metal device memory + co-tenants + OS floor ≤
wired limit) is the arithmetic the tool uses to gate proposals before any
measurement, and `gpu-budget` output is always labeled a *prediction*.

## Modes (**proposed** 2026-08-08, owner discussion)

A mode is a complete five-dimension assignment for the *same machine*,
witnessed separately. The wall model is mode-relative: unpin the coder
and 0.85 × device-memory returns to the pool — jina-v3, which can never
co-reside with the coder at 32GB, becomes legal in a helper mode. Modes
are how fixed hardware yields different forced choices per activity: the
same reranker can be GPU-placed in helper mode and CPU-placed in coder
mode.

- **One manifest, per-mode overlays — never N manifests.** Entries carry
  a base plus `modes:` overrides (placement, flags, group, presence).
  The generator renders *per-mode* artifacts — llama-swap configs,
  harness views, extension sets. `models.json` stays a rendered
  projection; nothing mode-shaped originates there.
- **Roles are the stable interface; modes bind roles to models.**
  Harnesses and extensions speak `rerank`; the mode decides what that
  maps to (jina vs qwen, GPU vs CPU), so tool config never changes
  across modes — availability does. The manifest's `kind:` field is
  already this concept. Open: llama-swap alias support (checkable —
  else the generator names entries by role).
- **Switching = swapping the active rendered config.** llama-swap
  already watches its config (2s poll), so the mechanism exists today.
  `temper mode <name>` reports what loads/unloads and the warmup cost
  from the render diff. Needs witnessing: reload behavior under
  in-flight requests, and switch latency itself.
- **Parallel harnesses arbitrate through leases, not a daemon.** A lease
  file in state (harness, mode, expiry — renewed while active).
  `temper mode` honors live leases; `--force` stays human. Idle
  detection lives in the harnesses (temper has no watcher — the
  no-daemon rule holds): the harness that notices the coder idle runs
  `temper mode --request helper`, which succeeds only lease-free. Pi
  switches on coder-model switch the same way.
- **Witness cost multiplies by mode.** Each shipped mode of a bucket
  needs its own soak — helper mode's co-tenant arithmetic is a different
  experiment. v1 ships two named modes (coder, helper); every further
  mode is a witness-queue entry, not a config option. Unwitnessed modes
  do not ship.
- **`off` is a mode.** start/stop/mode form one state machine
  (off ⇄ helper ⇄ coder): every transition is render + kick,
  lease-guarded, recorded. Agents still never run launchctl — they run
  temper, which does, as the stack's own sanctioned tool (the same
  pattern that lets setup.sh do what agents may not), logged and
  condition-stamped.

## Surface

Core transforms (PLAN §10's discipline — the CLI transforms artifacts,
it does not sequence):

- `temper apply` — models.yaml + lock → rendered configs. Fills missing
  lock rows, never moves existing pins.
- `temper update [id]` — re-resolves pins, prints old→new, resets the
  entry's witness to unverified, prints (never runs) the targeted gate.
- `temper check` — budget vs allowance, lock drift, advisory wizard diff.
- `temper mode <name>` — switch the active rendered posture; reports
  what loads/unloads and warmup cost (modes, roles, leases, and the
  off-state: their own section above).
- `temper start` / `stop` / `status` — llama-swap daemon control, i.e.
  the off-mode transitions of the same state machine. `status`
  distinguishes the three levels a launchd service can occupy — job
  loaded / process alive / answering with residency — because
  2026-08-08 witnessed them disagreeing (loaded-but-dead after a clean
  SIGTERM, invisible to `launchctl list`). `stop` prints the wired
  memory it freed — on a 32GB machine that number is *why* the user
  stopped.

Lifecycle:

- `temper init` — the wizard (TUI, §10): deterministic checks first
  (machine report + bucket), then profile → harness checkboxes →
  specialists (cascade explained) → allowances. Writes models.yaml once.
- `temper probe [stage]` — the field kit absorbed (**proposed**: same
  stages, RESULT lines, tune/deviation/conclude, AGENT.md; `probe` on
  the owner's own box re-witnesses after an update, and on a friend's
  box does what field-kit does today, including keep-or-restore).
- `temper report` — print the current paste-block (probe results, or a
  status snapshot outside a probe).
- `temper uninstall` — the provenance-guided remover.

**Proposed — routing (dimension 5) becomes a rendered artifact.** Today
the local/cloud switch lives in hand-edited harness config. Under temper,
the manifest's non-local helper group renders per harness (Pi provider
entries; MCP config for the claude subpackage), with the offline fallback
declared in the manifest (`fallback: coder-json-schema` or `none`), so
`apply` owns all five dimensions and the data-boundary note appears in
`check` output. Open: whether temper validates remote credentials
(leaning no — it renders config, the harness owns auth).

## Artifacts (one writer each)

catalog (shipped, labs-compiled) → wizard → models.yaml (user intent) →
lock (pins + witness rows) → generator → configs; plus probe-results/
(report.md, provenance.txt) from probe runs. The catalog row schema
gains: the five-dimension combination, its witness record, the
data-boundary statement when routing is non-local, and the "what this
means for you" blurb the wizard renders.

## Home (**proposed** 2026-08-08): `~/.temper`

Temper's config is machine-witnessed, not portable — a 32GB-witnessed
manifest synced onto a 64GB machine is exactly the lie the witness
system exists to prevent, and people sync `~/.config`. So: one
machine-identity root, `~/.temper` — `models.yaml` (intent), the lock,
`state/` (active mode, leases), provenance, backups. One root also
keeps keep-or-restore and provenance-guided uninstall trivially
auditable (`~/.pi` is precedent next door). Rendered configs stay in
their consumers' homes (`~/.config/llama-swap`, `~/.pi`): temper
renders into other tools' territory, it never relocates it. The real
migration hiding here: the manifest moves out of the repo clone —
today "the install lives in the clone"; under temper the clone is
disposable and `~/.temper` is the machine's identity.

## What ships where (the org, reshaped 2026-08-08 — owner)

The org must carry public value even without the tool. The roster —
each repo independently CI'd, zero-context docs, clean states, high
quality:

- **`temper-sh/temper`** — release: setup + wizard + generator + lock,
  catalog, acceptance suites, machine-report, README, findings
  *summary*, harness subpackages (Pi first), and the probe.
- **`temper-sh/field-kit`** — stays the thin public probe repo
  (friend-facing README + curl-able machine-report), becoming a shim
  over `temper probe` at v1 so the "send one file first, then one
  clone" flow survives the rename.
- **`temper-sh/extensions`** — the Pi extensions with their own test
  suite and docs; useful to any Pi user with any OpenAI-compatible
  backend, temper or not. Their default model ids track catalog ids —
  the repo's docs own that contract. `extract-json` parks here (the
  helper-mode candidate). Open: Pi's `packages` mechanism as the
  consumption channel (checkable) — the entry ↔ extension gate would
  become package install/remove.
- **`temper-sh/edit-formats`** — the pi-edit-formats experiment,
  already split out and nearest to org-ready: the edit-tool-shape
  question measured with success × output tokens, and the
  loud-beats-silent finding (hashline's well-formed-but-silently-wrong
  patches vs string-replace's retryable failures). Note (owner,
  2026-08-08): current focus has shifted to Claude and other frontier
  models, where the anchored format works well; the local-model
  measurement that motivated it stays paused-and-resumable. Its
  token-economy results are bucket-relative — output tokens cost more
  at 8 tok/s than 13 — so the catalog may eventually cite them per
  row, not as one global verdict. When a format wins, the tool ships
  via `extensions`; this repo keeps the evidence.
- **`temper-sh/measurements`** — witnessed numbers as self-evidencing
  paste-blocks (machine, SHAs, conditions, date). The public evidence
  trail, deliberately independent of labs.
- **labs — private** (leaning; owner-undecided). The working repo and
  its journal. Public value flows out through curated exports — the
  measurements repo, the findings summary — not the process log.
  pi-edit-formats is the existing proof the extraction model produces
  publishable repos. Staying private is reversible later; publishing
  is not.

No cross-links from release to labs; one linkless provenance sentence
in release. Extraction order by stability: edit-formats (already
split), measurements, extensions.

## Quality bars

Release-bar: shellcheck-clean bash 3.2 (scripts), hermetic offline suite,
second-run-clean, --dry-run purity, no launchctl/sudo from tests. The Go
TUI/CLI (if §10's port proceeds) is diffed against the bash generator as
oracle — byte-identical configs before any cutover.

## Non-goals

- No model-quality rankings without a task-success corpus behind them.
- No weight mirroring or redistribution; cache pre-seeding stays the
  documented path.
- No daemon beyond llama-swap; no background updaters; no phoning home.
  (`temper start/stop` controls llama-swap's launchd job; temper itself
  stays a CLI, and the idle-watcher lives in harnesses, never here.)
- No Linux/Windows in v1 (the measurements are Apple-Silicon-specific;
  the wall model doubly so).

## Milestones (proposed)

- **M0 — generator extraction** (§10 sequence step 1; unblocks wizard,
  probe, and the Go-oracle diff at once).
- **M1 — lock + apply/update/check** on the extracted generator.
- **M2 — wizard TUI** rendering catalog rows; profiles + mode overlays.
- **M3 — probe absorption** (`temper probe` = field-kit stages) + the
  routing render (dimension 5) with its check output.
- **M4 — the split**: labs/release extraction, findings summary compiled,
  catalog seeded with every witnessed row that exists by then (32GB
  coder-only-GPU is row one if tomorrow's witness run holds).

## Open questions (owner)

1. Distribution: brew formula vs curl-installer vs release-asset binary
   (and §10's Go-scope decision folds in here).
2. Does the helper profile ship in v1? It reshapes the below-32GB story
   and is the claude-subpackage's first customer.
3. Catalog contribution flow: how a friend's probe report becomes a row —
   hand-curated by the owner (current stance) or a structured submission?
4. Remote-provider surface for dimension 5: render-only (leaning) or
   managed?
5. Versioning of witnessed rows when engines move: a row's witness pins
   engine versions — does `update` invalidate the row or fork it?
6. llama-swap mechanics the modes design leans on: role aliases
   (checkable), and config-reload behavior under in-flight requests
   (witnessable — a mode-switch-under-load probe measurement).
7. Lease semantics: is an advisory state file with expiry enough for
   cooperating harnesses (leaning yes — hostile tools are not the
   threat model), and is `--force` human-only (leaning yes)?
8. Do mode witnesses become a first-class probe surface —
   `temper probe --mode <name>` soaking the non-default posture?
9. Pi `packages` as the extensions distribution channel, and what the
   extensions repo's standalone CI looks like without a stack to lean
   on.
