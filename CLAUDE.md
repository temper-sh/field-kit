# field-kit

The install probe for [local-ai-setup] — friends run this on their Macs to
produce the foreign-machine data PLAN §9 of that repo describes (bucket
gates vs reality, per-bucket manifest defaults, default-wall numbers). This
repo is deliberately separate and friend-facing: its README assumes the
reader has never seen the stack repo.

The stack repo is the one under `~/work/local-ai/local-ai-setup` (fetched
into `stack/` here at probe time — that clone is disposable, never edited).

## Ground rules (inherited from the stack repo, and they all apply)

1. bash 3.2 — no associative arrays, no `${var,,}`, no `mapfile`.
2. `shellcheck -x -e SC2329 probe.sh tests/run.sh` must be clean.
3. **Never sudo, never launchctl.** The probe serves llama-swap in the
   foreground; a launchd job appears only via the stack's own setup on the
   keep path. Sudo commands are printed for the human.
4. Nothing phones home. The report is a local file the friend pastes.
5. Measurement integrity beats convenience: every measurement records the
   conditions it ran under (wired-limit wall, swap, tune label,
   thermal-throttle state, power source, load — `conditions_line`);
   interventions land in the report's deviations section; agents get
   AGENT.md's guardrails. Chip °C itself is out of reach without sudo —
   `pmset -g therm`'s recorded speed limit is the no-sudo throttle signal.

## Contracts that must not drift

- `machine-report.sh` here is the **distribution copy**; the canonical file
  is the stack repo's `scripts/machine-report.sh` (its check 20). Sync by
  hand when the stack's changes; this repo's tests pin the wired-limit
  formula so drift fails loudly. Preflight runs this copy, deliberately
  before any stack fetch.
- `probe-results/provenance.txt` format (`<kind> <name> <present|absent>`)
  is consumed by the stack's `scripts/uninstall.sh` — change it only in
  both places (stack offline check 21 tests the consumer).
- The `FIELD-KIT RESULT <stage> <ok|fail> <detail>` stdout line is the
  agent-facing API; AGENT.md documents it.
- Stack test harnesses the probe drives: `tests/runtime.sh`
  (`LOCAL_AI_BASE_URL`), `tests/perf.sh` (`LOCAL_AI_PERF_NO_RESTART`),
  `tests/engine-ab.sh` (`LOCAL_AI_AB_MODEL`, `AB_OUT`).
- The probe is the **witness mechanism for per-bucket tuning** (stack PLAN
  §9): buckets are RAM × chip generation × bandwidth; `FIELD_KIT_MANIFEST`
  probes a candidate tuning, the report carries which manifest every number
  belongs to, effective bandwidth is derived from decode (the preflight
  table is only the estimate), and pre-M5 machines are steered toward
  `perf --ab`. The kit tests *mechanical* tuning only — it can rule a
  candidate out, never in; model/quant quality stays an owner decision.
- **In-flight tuning** goes through `probe.sh tune` only (fraction, or a
  whole candidate manifest), re-rendered through the stack's config parse
  gate, labeled `tuneN` in every subsequent conditions line. Editing the
  *clone's* models.yaml is sanctioned because the clone is disposable —
  the stack's ground rule 6 (never mechanically rewrite the manifest)
  protects the owner's hand-edited file, and that boundary must hold.

## Testing

```bash
tests/run.sh   # hermetic, offline, seconds — no network, no service, no launchctl
```

The suite sources `probe.sh` with `FIELD_KIT_LIB_ONLY=1` and exercises the
pure logic (summary parsing, provenance snapshot against a fixture stack and
a brew shim, report assembly, RESULT lines). The full end-to-end can only
happen on a real foreign machine — by design.
