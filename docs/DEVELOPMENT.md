# Develop Field Kit and review returned runs

Use this guide when changing Field Kit or reviewing a report from an older
study. To contribute a measurement, use the [run guide](START.md).

## Check a change

Field Kit uses the Python standard library and supports Python 3.9 or newer.
CI checks Python 3.9 and 3.14. From the repository root:

```sh
./field-kit verify
python3 -m unittest discover -v
```

These checks use synthetic responses and temporary processes. They do not run
a model or establish that a configuration fits a contributor's Mac.

Qwen-specific code lives in `fieldkit_runtime/experiments/qwen/`. Shared code
handles confirmation plans, limits, sessions, measurements and reports. Temper
handles model and software installation, rendering, process identity and
shutdown. Keep those responsibilities separate.

## Use a development Temper build

The current `qwen-machine-study@4` package needs Temper `0.1.0-alpha.11`.
Normal setup installs the signed release and verifies its checksum.
Contributors need no compiler or Temper checkout.

For host development, build an existing Temper checkout and preview the study:

```sh
temper_source=/absolute/path/to/temper-checkout
(cd "$temper_source" && go build -o build/temper ./cmd/temper)
./setup.sh --install-only
./field-kit contribute --temper "$temper_source/build/temper" --preview
```

Preview checks the machine and required host commands without downloading
models or creating a study session. Omit `--preview` only when ready to review
and accept the displayed run. Pass the same `--temper` path when resuming;
the exact executable, runtime and experiment inputs are part of the accepted
plan.

The package uses compiled execution locks. It derives bounded context, output
and memory settings through `temper execution configure`, then uses the
inspection, preparation, serving and removal commands. Field Kit does not
interpret or rewrite the locks' internal fields.

The [study guide](experiments/qwen-machine-study.md) owns requirements, costs and
method. [Authoring instructions](../scripts/README.md) explain how to prepare a
new frozen package from reviewed inputs. Updating the normal host also requires
verifying the signed release and updating the bootstrap pin.

## Preserve unfinished and older runs

Keep an unfinished run in its original checkout. Changing its code, Python,
Temper binary or package can prevent resumption. Revision 4 uses separate
sessions and does not migrate earlier runs.

To inspect an older report, use a separate checkout of the source that produced
it. These are the known dispatched sources:

| Study | Producer source |
|---|---|
| Revision 1 | `f1fc7e0dcb268858dcbe67910a054b99731e6539` |
| Revision 2, returned M3 Pro report | `9a060f1` |

For example, review the revision 2 report without changing this checkout:

```sh
git worktree add --detach ../field-kit-dispatched-2 9a060f1
../field-kit-dispatched-2/field-kit witness --input /absolute/path/to/result.json \
  --package ../field-kit-dispatched-2/catalog/packages/qwen-machine-study@2/package.json
```

For other reports, use their recorded producer source and package revision.
The witness command checks the record; it does not rerun generated code or
attest the machine.

Resume using the unchanged original checkout, interpreter and Temper
executable. If that checkout was updated, retain its `.local/`, session and
results and arrange recovery with the maintainer. Do not replay first attempts
automatically. Use `--new` explicitly to start a separate current study.

## Keep measurements and cleanup attributable

Each action has a bounded invocation and a result tied to its session and
attempt. Failed or interrupted invocations remain attempts. Cleanup must use
the shutdown result for the processes it is removing.

Package checksums freeze inputs and verify downloads. The accepted plan binds
the code, executable and inputs used by a run. Logs and the Markdown report are
readable views; the export carries the structured plan and session.

Temper owns installation receipts and shared dependency accounting. Field Kit
records machine observations, resource limits and stop reasons. Keep configured
memory limits distinct from measured peaks, and leave missing or invalid
measurements explicit.
