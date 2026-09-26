# Developing Field Kit and reviewing dispatched runs

The current package is `qwen-machine-study@4`. It requires the Temper host with
exact Python supplies, `execution paths`, and supervised Rapid/vLLM processes,
in addition to Splash and software receipt reuse (`98b99ce`). The package
reserves alpha.11 as its minimum released host; a matching signed release is
still pending. Setup retains signed/notarized alpha.9 for private Python and
stops before opening the study.

Build the prepared Temper checkout without loading a model:

```sh
temper_source=/absolute/path/to/temper-checkout
(cd "$temper_source" && go build -o build/temper ./cmd/temper)
./setup.sh --install-only
./field-kit contribute --temper "$temper_source/build/temper" --preview
```

A preview is read-only. Omit `--preview` to review and consent to the exact
machine route before any model download or inference. Pass the same `--temper`
path when resuming. Development binaries are accepted locally; their exact
bytes remain bound by consent. Before dispatch, deliver the matching signed
host, set its actual release minimum and bootstrap pin, and verify setup with it.

The [study guide](experiments/qwen-machine-study.md) owns the matrix, costs and
method. [Authoring instructions](../scripts/README.md) describe regeneration.
All eight configurations compile and render offline. Both exact Python engine
environments installed on the development Mac, passed `pip check`, and accepted
the selected CLI options. The extracted evaluator reproduced the retained
Splash patches' original/independent test outcomes and rejected unchanged Flask
baselines. These checks establish preparation and grading behavior. No new
model inference or weight download was used for this preparation; eligible
36 GiB and 48 GiB+ hardware qualification remains pending.

Revision 3 at `3400df0` is the receipt-cleanup checkpoint, retaining its
llama-only workload. Revisions 1–3 keep their original frozen packages. Revision
4 uses a separate contributor pointer and never resumes or rewrites an older
study. Use `--new` deliberately for a separate run after keeping old evidence.

## Dispatched revision 1

Do not update a checkout with an unfinished run. Its exact runtime, Python and
Temper bytes are part of the approved plan. Revision 4 refuses old sessions and
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
the maintainer; never migrate the session to revision 4 or replay its first
measurements automatically.

## Dispatched revision 2

The returned September 2026 M3 Pro report was produced by `9a060f1`. Review it
with that source and the unchanged revision 2 package:

```sh
git worktree add --detach ../field-kit-dispatched-2 9a060f1
../field-kit-dispatched-2/field-kit witness --input /absolute/path/to/result.json \
  --package ../field-kit-dispatched-2/catalog/packages/qwen-machine-study@2/package.json
```

Other returned runs use the source that produced them. Revision 4 does not
reinterpret historical exports. Start a separate revision 4 run explicitly
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
