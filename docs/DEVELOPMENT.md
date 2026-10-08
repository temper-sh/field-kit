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

The current `qwen-machine-study@6` bundle needs Temper `0.1.0-alpha.11`.
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

The bundle uses compiled execution locks. It derives bounded context, output
and memory settings through `temper execution configure`, then uses the
inspection, preparation, serving and removal commands. Field Kit does not
interpret or rewrite the locks' internal fields.

The [study guide](experiments/qwen-machine-study.md) owns requirements, costs and
method. [Authoring instructions](../scripts/README.md) explain how to prepare a
new frozen package from reviewed inputs. Updating the normal host also requires
verifying the signed release and updating the bootstrap pin.

## Preserve unfinished and older runs

Keep an unfinished run in its original checkout. Changing its code, Python,
Temper binary or package can prevent resumption. Revision 5 uses separate
sessions and does not migrate earlier runs. Keep an unfinished revision 4 run
in its original checkout, including its private installation and shared-cache
references. Use a separate checkout for configuration runs.

To inspect an older report, use a separate checkout of the source that produced
it. These are the known dispatched sources:

| Study | Producer source |
|---|---|
| Revision 1 | `f1fc7e0dcb268858dcbe67910a054b99731e6539` |
| Revision 2, returned M3 Pro report | `9a060f1` |
| Revision 4, before configuration chunks | `aae99f8` |
| Revision 5, process memory only | `29ea9cf` |

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

Revision 6 derives one exact `qwen-chunk-CONFIGURATION@6` package from the
frozen bundle and the contributor's selected configuration. Its origin records
the source bundle hash; the accepted plan binds the selected package, lock,
task actions and costs. The witness reader repeats that pure selection before
validating the result. It accepts the bundle's `package.json` as its input.

Revision 6 preserves revision 5 tasks, settings, costs and locks. Its protocol
adds bounded Splash `/status` observations of `memory_actual.current_bytes`
and `peak_bytes` after readiness, during requests and before shutdown. Evidence
retains load and last native snapshots, sample count, endpoint, instance identity
and observation issues. The native lifetime peak covers intervals between polls;
reset or changed-instance readings never combine. Missing memory stays distinct
from valid task/timing evidence. Process counters remain separate and overlap
these allocations.

Completed revision 5 choices remain visible to `--next`, and `--new` creates a
separate revision 6 attempt. Unfinished older chunks require their producing
checkout. The current witness can inspect revision 5 chunks using its unchanged
bundle, without inventing the missing native memory counters.

Each coding task is one action. The session commits its report and next action
before another task can begin. `--pause-after-task` returns at that boundary;
resuming retains the earlier result. A failed or interrupted action without a
committed result never authorizes replay or cleanup.

Every Temper/protocol invocation for a run receives `HF_HUB_CACHE` pointing
inside the session-owned root. This overrides the caller's model-cache location
for that run while preserving client discovery and authentication settings.
Ordinary verified root cleanup removes these private downloads. It never
prunes a shared cache. Separate completed runs may download common weights
again; the bounded disk lifetime is deliberate. HF/uv support-tool caches
remain owned by those tools.

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
