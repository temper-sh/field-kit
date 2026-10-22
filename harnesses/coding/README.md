# Coding harness

Measure how well a served preset completes a supplied-source repair. The model
gets the task, the relevant source and the visible tests. Its only tools apply a
complete proposal: `test_patch` runs the visible tests and its own new tests on a
fresh copy, and `submit_patch` ends the task. Independent tests and review
checks grade every distinct complete proposal afterwards. The model never sees
them, and the harness never runs or repairs incomplete output.

Field Kit serves the preset and owns its processes, deadlines and memory. This
harness owns the conversation, the proposals and their grading. It does not
decide quality: passing tests, a submission and good source are separate
observations, and comparative source review stays with the owner.

## Tasks

| Task | Source | Default budget | Grading |
|---|---|---|---|
| `pagination-repair` | A small pagination function | 900 s, 6 responses | unittest; Field Kit's own Python |
| `flask-async-stream` | Flask at `49b7e7bc8fb69d605719991d1c0a99fcee689053` | 1800 s, 6 responses | pytest; independent tests and two context-guard review checks |
| `flask-template-decorators` | the same Flask revision | 1800 s, 6 responses | pytest; independent tests and 21 delegation/argument review checks |

These are historical tasks. Earlier studies used them repeatedly, so they serve
as labelled regressions and harness checks. A new quality conclusion needs
unused work as well. The Flask tasks and archive are byte-identical to the
earlier Field Kit coding suite, so new results compare directly with that
evidence.

## Run

Install the Flask test packages once. They are exact, hash-pinned wheels from
PyPI, about 4 MB, installed into `.local/grading` from Field Kit's own Python:

```sh
./field-kit grading
```

Then run a task as an ordinary command workload in an approved study:

```sh
./field-kit study run my-study --candidate my-preset \
  --workload workloads/coding-flask-async-stream.json \
  --reason "Completed coding work on the selected runtime"
```

Add `--model-adapter flint/v1` for Flint. Field Kit passes the endpoint, the
engine's own routes, the preset name, the model format and the context window.
To change the harness's arguments, copy a workload and edit its command; the
attempt records the actual argv:

| Argument | Meaning |
|---|---|
| `--seconds`, `--turns` | The task's wall-clock budget and response allowance |
| `--contract feedback` or `feedback-state` | A labelled prompt treatment added to the system text; the task, tools and tests stay the same |
| `--sampling JSON`, `--seed` | Sampling overrides; the default is the preset's own sampling with seed 421. Earlier Flint, Qwen and Muse comparisons used `{"temperature": 1, "top_p": 0.95, "top_k": 20, "min_p": 0}` |
| `--context` | Overrides the window Field Kit reports |
| `--output-tokens` | A fixed output allowance, for an engine without template and tokenizer routes |

Each request first renders and counts the full history through the engine's
template and tokenizer routes, then allows the rest of the window as output
(less 256 tokens). llama.cpp and Splash provide those routes. The request also
carries llama.cpp's optional timing and progress fields; other engines are
untested.

## What it records

The attempt's `sessions/task/output/` holds `result.json` and one folder per
response: the exact request, the raw stream, the decoded response and the token
budget. Each graded proposal keeps its patch, test invocations, sandbox
profiles, logs and JUnit results. `result.json` names the task, prompt
treatment, sampling overrides, harness and task file hashes, Field Kit runtime
and grading environment. Field Kit adds wall time and loaded/peak memory, and
its relay records every request with usage and the engine's native rates.

Tests run inside the macOS sandbox, without network access and with writes only
in their result folder. This is containment for an experiment, not a defense
against deliberately hostile code.

## Check and change

`{python} harness.py verify` (part of `./field-kit verify`) checks that the
frozen task files match `manifest.json` and that each reference proposal under
`tasks/*/reference/` applies to its original source. The references are
positive controls from earlier runs, not solutions of record. A new task
revision is a deliberate change: add its files, then record it with
`harness.py verify --write-manifest`.

Provenance: the harness and its tasks were extracted on 22 October 2026 from the
Flint, Qwen and Muse comparison
(`labs/workstreams/model-runtime-optimization/.work/flint-qwen-muse-2026-10-22`,
the same bytes as that study's frozen evidence). The prompt treatments are
byte-identical to those of the Flint tuning study that day.
