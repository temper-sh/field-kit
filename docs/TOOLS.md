# Field Kit tools

Commands emit compact JSON; errors go to stderr. The runtime uses installed
Python and Temper. Help, doctor, workload generation and preview start no model.

## Start a study

Prepare a brief and exact locks with [Temper](TEMPER.md).
The values below illustrate a bounded study; choose them for the actual work.

```sh
./field-kit doctor
./field-kit study init my-study --brief trials/brief.md \
  --lock trials/splash.lock.json --lock trials/llama.lock.json \
  --authoring-root trials/authoring \
  --hours 8 --download-gib 100 --max-context 262144 \
  --request-seconds 1800 --trial-seconds 14400 --preview
```

After the owner approves that concrete scope, repeat without --preview and
add --yes. Approval covers supported settings variants of the exact engines,
weights, templates and software. It does not silently cover new material.

A study stores its brief, approval, exact candidate locks and attempts under
.work/studies/ID/. Time is wall time from approval. The download allowance bounds
planned known material: weights not yet in the cache plus engine software. It
is not a network byte meter; review unlisted dependency costs before approval.
Requests and whole trials have separate deadlines.
Cleanup and evidence return remain available after the budget ends.

Resource monitoring samples every five seconds, stops on observed thermal/CPU
throttling or 512 MiB swap growth, and limits wired growth to the smaller of
87.5% of RAM and the machine's reported wired limit. These are disclosed
experiment stop policies, not measured preset limits. A sampled peak can miss
short transients. No coding evaluator is required to initialize a study.
Preview also lists each engine's helper-process limits. These are frozen with
the approval; changing an adapter cannot enlarge an existing study's limits.

## The machine, the study root and weights

Preview reads `temper layout status` for the user's Temper root (`--user-root`,
default `~/.temper`). A running layout is named in the approval. Field Kit stops
it after approval, stops it again before an attempt if the tester restarted it,
and starts it again at close. It never starts a layout that was not running,
never stops an unapproved one and never signals other processes. Preview lists
large-memory processes read-only. Tell the tester: while Field Kit runs, do not
use Temper, and close other AI apps. The study's `restore.md` holds the steps
back to normal work.

All candidates share one Temper root inside the study, which must be on APFS.
Candidates with the same engine release and router share one installation, so
their software installs once. Weights go to the user's Hugging Face cache, or to
`--weights-cache PATH`, for example a research cache on an external SSD. Only
the hub cache moves; `HF_HOME` and the user's token stay. Files already in the
cache are reused and kept. With `--weights-cleanup added` (default), close
removes only the exact files this study added, never a whole repository; `keep`
leaves them. An exFAT cache can hold full copies instead of links, so preview
requires up to twice the download there, plus 10 GiB free on the study root's
disk. Each attempt records its root and cache volumes, including file system
and internal or USB. A disconnected cache refuses the attempt before Temper
starts: reconnect it and start a new attempt.

With `--authoring-root`, preview also runs Temper's `preset check` and, when
installed, `preset plan` for candidates that are user presets there. Temper's
refusals stop the plan. Otherwise preview shows known file sizes only.

## Smoke and cache measurements

```sh
./field-kit workload smoke --out trials/smoke.json
./field-kit study run my-study --candidate qwen3.8-27b-q4xl-splash \
  --workload trials/smoke.json --reason "Check this candidate before deeper work"
./field-kit workload cache --records 4096 --output-tokens 1024 --out trials/cache.json
./field-kit study run my-study --candidate qwen3.8-27b-q4xl-splash \
  --workload trials/cache.json --reason "Test append reuse and early-edit invalidation" --background
./field-kit study wait my-study --timeout 3600
```

Use foreground completion when the host can wait on a command. For background
work, wait blocks inside the tool and returns on completion, lost worker or
timeout. The host may continue waiting on that process. Avoid repeated agent
turns calling status. Sampling and process checks are internal.

Generators: smoke (plain chat), tools (a real tool call), engine (same answer
at short/populated input, fresh process each), context (cold then append) and
cache (adds an early edit). Record counts are not token counts. Actual usage is
retained from engine responses. Generated checks establish only their narrow
question, not coding quality or a universal context limit.

## Write a one-shot workload

```json
{
  "question": "Does this candidate preserve the requested behavior?",
  "request": {
    "messages": [{"role": "user", "content": "Your frozen task and source here"}],
    "max_tokens": 2048
  }
}
```

Optional expect accepts one semantic check: {"json": VALUE},
{"text": "exact requested text"}, or {"tool": {"name": NAME, "arguments": OBJECT}}.
Omit it for source/deliverable review. Field Kit supplies model and streaming
controls. A workload can contain steps in one process, or named sessions with
their own steps and fresh processes. Generated files are examples of those forms.

Formats default to `openai/v1`. For Flint, add `"model_adapter": "flint/v1"`
at the workload's top level or pass `--model-adapter flint/v1` to `study run`.
The choice is retained with the exact attempt. `append_previous` then preserves
Flint reasoning and tool-call envelopes. Parse issues remain separate from
quality, rate and memory observations. See [adapters](ADAPTERS.md) for supported
formats and adding another one.

## Finished tasks with an existing harness

A command workload runs the installed harness under the same engine/resource
supervision. Replace the following paths/arguments with the harness's real,
documented command; this is a format example, not a bundled harness:

```json
{
  "question": "Complete and test the requested repository change",
  "command": ["/absolute/path/to/task-harness", "--task", "/absolute/path/to/task.md"],
  "cwd": "/absolute/path/to/isolated-task-workspace"
}
```

Relative cwd resolves beside the workload file. The command is an argv list,
executed without a shell. It receives FIELD_KIT_BASE_URL, FIELD_KIT_MODEL,
FIELD_KIT_OUTPUT_DIR and FIELD_KIT_MODEL_ADAPTER. Configure the harness to use
that local endpoint/model and its selected format. The harness owns its own
parsing and conversation replay; the adapter variable alone does not change it.
A small adapter for an actual harness can translate these inputs; reuse it
across studies. Keep candidate execution confined using that harness's controls.

Save patches, original/final test output, acceptance notes and required task
artifacts under FIELD_KIT_OUTPUT_DIR. Record harness/version, task/source
revision, repair budget and evaluation commands in the workload/brief.
Field Kit retains stdout/stderr, wall time and loaded/peak memory. Per-request
rates are missing unless the harness supplies them; do not fabricate aggregates.
Exit status is recorded independently from the agent's quality review.

Retain the harness's effective endpoint/model configuration and relevant request
log. Command supervision alone does not prove that the harness used the intended
local model. Verify that attribution before drawing a model-quality conclusion.

Use ordinary project tests and comparative source review. The agent writes
assessment.md beside the run or uses study evidence/ for a shared comparison.
No submission marker or source-review verdict is required for another trial.

## Tune and return

Compile each new variant with Temper, then pass --lock trials/variant.lock.json
alongside the approved --candidate. Field Kit checks fixed material and context
scope; it does not require another coding pass.

```sh
./field-kit study status my-study
./field-kit study stop my-study
./field-kit study recover my-study
./field-kit repair my-study --report repair.md --patch repair.diff
./field-kit study close my-study
./field-kit study export my-study --out my-study-results.zip
```

Stop sends a signal only to the bound worker. Recover reconciles a dead attempt
and preserves it; retries create new attempts. Review [repair instructions](REPAIR.md)
when a process is still alive or ownership is uncertain.

Write conclusions.md with selected settings, quality comparisons, limitations
and open questions. Attach repaired defects too. After shutdown is verified,
close removes the study's Temper root, removes study-added weights as approved
and restarts a layout the study stopped. Export also works for an idle open
or blocked study, retaining its true state. It includes all evidence and repairs,
including candidate test logs, while excluding the study's private runtime.
The manifest validates archive membership and file bytes. Nothing uploads.

Earlier study schemas use their original checkout/runtime. This branch does
not silently rewrite their evidence or operate their processes.
