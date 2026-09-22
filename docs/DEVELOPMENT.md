# Developing Field Kit and reviewing dispatched runs

The current package is `qwen-machine-study@2`. It requires Temper's
`execution inspect|prepare|render|serve|remove` commands and supervised probe
status. Setup installs signed/notarized Temper 0.1.0-alpha.9, which supplies
those commands and installs the private Python runtime.

To test a development Temper build in the local-AI workspace without loading a model:

```sh
(cd ../../temper && go build -o build/temper ./cmd/temper)
./field-kit contribute --temper ../../temper/build/temper --preview
```

Use `./setup.sh --install-only` first if the private runtime is not installed.
A preview performs reads only. Omitting `--preview` enters the exact-plan
consent flow before any model download or inference.

## Dispatched revision 1

Do not update a checkout with an unfinished revision 1 run. Its exact runtime,
Python and Temper bytes are part of the approved plan. Revision 2 refuses old
sessions and leaves the old contributor pointer and package files untouched.
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
the maintainer; never migrate the session to revision 2 or replay its first
measurements automatically.

## Ownership and verification

Each action has one bounded invocation and one report. There is no intermediate
artifact protocol. Failed or interrupted invocations remain attempts; a later
action cannot authorize cleanup using an earlier action's shutdown result.

Qwen-specific code lives in `fieldkit_runtime/experiments/qwen/`. Shared code
records consent, budgets, sessions and evidence. Temper consumes execution locks
directly and supplies process identities, listener validation and shutdown
results. Field Kit retains macOS measurements and experiment stop thresholds.

Run `./field-kit verify` and `python3 -m unittest discover -v`. Tests use synthetic
responses and temporary processes; they do not run a model or make public claims.
