# Developing Field Kit and reviewing dispatched runs

The current package is `qwen-machine-study@3`. It requires Temper's
`execution inspect|prepare|render|serve|remove` commands and supervised probe
status, plus software receipt reuse across configuration changes. The package
reserves 0.1.0-alpha.11 as its minimum released host; a compatible signed build
has not been delivered. Setup still uses signed/notarized 0.1.0-alpha.9 to
install private Python and stops before opening the study.

The receipt fix is integrated locally with Temper's Splash work (`3ba7813` plus
the uncommitted receipt changes). The same host reports the effective Metal
budget and its optional raw sysctl override separately; Field Kit retains both.
Build that checkout without loading a model:

```sh
temper_source=/absolute/path/to/temper-checkout
(cd "$temper_source" && go build -o build/temper ./cmd/temper)
./field-kit contribute --temper "$temper_source/build/temper" --preview
```

Use `./setup.sh --install-only` first if the private runtime is not installed.
A preview performs reads only. Omitting `--preview` enters the exact-plan
consent flow before any model download or inference. Pass the same `--temper`
path when resuming. A default development version is accepted for local work;
its exact executable bytes are still bound by consent. Before contributor
delivery, set the actual released host minimum and bootstrap
pin/checksum, and verify setup with that signed release.

Revision 3 pins llama.cpp b11205 and llama-swap v260. The model, template,
workload, oracles, tuning variants and thresholds are unchanged from revision 2.
Software checks establish compatibility with the commands, not model quality.

## Dispatched revision 1

Do not update a checkout with an unfinished run. Its exact runtime, Python and
Temper bytes are part of the approved plan. Revision 3 refuses old sessions and
leaves their contributor pointers and package files untouched.
The preserved local producer source is commit
`f1fc7e0dcb268858dcbe67910a054b99731e6539`.

For read-only review, create a separate checkout from that commit:

```sh
git worktree add --detach ../field-kit-dispatched f1fc7e0dcb268858dcbe67910a054b99731e6539
../field-kit-dispatched/field-kit witness --input /absolute/path/to/result.json \
  --package ../field-kit-dispatched/catalog/packages/qwen-machine-study@1/package.json
```

This preserves current development edits. To resume a dispatched run, use its
unchanged original checkout, interpreter and Temper executable. If that checkout
was updated, retain its `.local/`, session and evidence and arrange recovery with
the maintainer; never migrate the session to revision 3 or replay its first
measurements automatically.

## Dispatched revision 2

The returned September 2026 M3 Pro report was produced by `9a060f1`. Review it
with that source and the unchanged revision 2 package:

```sh
git worktree add --detach ../field-kit-dispatched-2 9a060f1
../field-kit-dispatched-2/field-kit witness --input /absolute/path/to/result.json \
  --package ../field-kit-dispatched-2/catalog/packages/qwen-machine-study@2/package.json
```

Other returned runs use the source that produced them. Revision 3 does not
reinterpret historical exports. Start a separate revision 3 run explicitly
with `--new`; it does not replace old evidence.

## Ownership and verification

Each action has one bounded invocation and one retained result, referenced by
session ID and action attempt. Sessions own those results; the export contains
the plan and structured session. Logs and the Markdown report are useful views,
not hashed identities or required inputs to export. Failed or interrupted invocations remain attempts; a later
action cannot authorize cleanup using an earlier action's shutdown result.

File checksums still freeze package inputs and verify downloaded bytes. One plan
checksum binds consent and rejects changed code, executable or experiment
inputs on resume. Software receipts compare installed units, target and
installation directly; source provenance and model settings do not invalidate
unchanged installed software. Transaction and dependency identities remain
inside Temper where they support recovery and shared installation ownership.

Qwen-specific code lives in `fieldkit_runtime/experiments/qwen/`. Shared code
records consent, budgets, sessions and evidence. Temper consumes execution locks
directly and supplies process identities, listener validation and shutdown
results. Field Kit retains macOS measurements and experiment stop thresholds.

Run `./field-kit verify` and `python3 -m unittest discover -v`. Tests use synthetic
responses and temporary processes; they do not run a model or make public claims.
