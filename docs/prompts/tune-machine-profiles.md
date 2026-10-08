# Tune a preset for this Mac

Use this prompt with an AI coding assistant after a successful smoke test on
the Mac you want to tune. The goal is a usable Temper catalog with a measured
profile for an existing preset: context window, key/value (KV) cache format,
batching, memory cap and other supported engine settings.

This is an optional, AI-guided follow-up. The regular contributor command
continues to run its frozen comparison. A smoke test establishes operation at
its tested workload; tuning measures which complete configuration is useful.

You need the smoke result, its producing Field Kit checkout, the Temper
authoring catalog and a matching Temper executable with profile support.
The current study's pinned alpha.11 host predates that support. Keep it with
its study; use the profile-capable host in a separate tuning workspace.
The assistant should establish the exact commands and bounds before asking
you to run anything. No full engine or quantization matrix is required.

Give the assistant this file and a short request, filling in the paths you
already know:

```text
Use docs/prompts/tune-machine-profiles.md to tune the preset from my successful
smoke test on this Mac and supply a local Temper catalog with the chosen profile.

Smoke result: /absolute/path/to/result.json
Field Kit checkout: /absolute/path/to/field-kit
Temper executable: /absolute/path/to/temper
Temper authoring catalog: /absolute/path/to/catalog.json

Prefer a useful coding context with room for other applications. Start with
one preset and a small bounded comparison. Show me the candidates, costs and
stop conditions before starting model work. Keep results and the catalog local.
```

## Instructions for the assistant

### Establish the starting point

Read the repository instructions, the experiment guide, the returned result
and its source package. Use Field Kit's witness reader from the appropriate
producing checkout to inspect the result without executing returned model
code. Use the report to explain the outcome, and the structured result for
identities, settings and measurements.

Establish that this preset has loaded, completed a valid inference request and
shut down safely on this machine. Record task correctness separately: a valid
response can contain an incorrect solution. A timeout, interrupted request or
unresolved process ownership does not establish a successful smoke. Check
that current machine facts still match the relevant chip, RAM, OS and effective
Metal budget. An OS or runtime change needs a fresh baseline under those
conditions; retain the earlier observation with its original identity.

A successful smoke permits planning this follow-up. Complete or recover an
unfinished study in its original checkout before starting tuning measurements.
Preserve Q4/Q5 comparison settings and first attempts. Each preset, deliberate
repeat and tuning session has its own retained results.

Read the supplied Temper catalog and that executable's current command help
and contracts. The authoring schema is evolving: use the version and field
names Temper actually accepts. Establish that it can compile and select
`profiles` with a complete `engine_config`. If the catalog or executable is
missing or incompatible, finish the read-only review and provide a compact
handoff naming the required source/build and failed command. Never label an
uncompiled JSON sketch as a usable catalog.

Obtain the authoring catalog from Temper's owner. Treat execution locks as
opaque; they are consumed through Temper commands. Do not reconstruct a
catalog by editing lock internals or copying model/software records into
Field Kit. Compile with recorded, exact software. If the catalog's weights,
template, engine software or speculation differ from the smoke, establish a
new baseline for the selected composition before comparing tunings.

### Define a small comparison

Use the owner's stated workload and resource preference. Otherwise propose
one balanced profile for this machine, with a useful coding context and room
for other applications. Extra RAM can be allocated to context, KV precision,
batching or another supported engine control; the longest context is not the
automatic objective. Start with one preset and reuse its weights.

Keep shared preset fields fixed: artifacts, engine and exact software,
template, compute, interface/modalities, model context limit, request defaults
and speculation. A profile contains the complete supported engine settings
and its context window. Candidate settings can include `kv_cache`,
`batch_tokens`, microbatch size, cache allowances and `max_memory_bytes` where
the selected adapter exposes them. Check support against the pinned engine
and Temper adapter; a llama.cpp control is not automatically a Splash control.
Do not introduce raw launch flags that bypass the catalog.

Compare a small, explicit set against the baseline. Change one setting at a
time when diagnosing its effect, then measure the selected combination as a
complete profile. Do not infer that independently successful KV, batch and
context settings will work together. Before each new batch of candidates,
freeze their exact settings, workload, oracle, selection criterion and numeric
bounds. Later candidates can use earlier findings, while preserving those
findings and their original questions.

Use existing completed-work checks for correctness and useful-work latency.
Use the study's filled-input retrieval and follow-up method for sampled
context capacity. Reserve the preset's full output allowance and explicit
follow-up/history space, and verify actual input counts with the engine's
tokenizer. A short prompt at a configured window does not test a filled
window. Retrieval at a sampled size does not establish general long-document
reasoning. Reusing an oracle does not require replaying the entire coding
matrix for every screening candidate; state what each check establishes.

Present a concrete plan before model work: candidates and order, request and
session time ceilings, memory/swap/thermal stops, disk/download allowances,
evidence location, reuse and cleanup policy, and acceptance criteria. Derive
costs from this allocation rather than quoting the whole study's ceiling.
Carry forward already authorized limits when applicable; otherwise obtain
the machine owner's consent to this plan. A new batch outside those bounds
needs its own decision. A prompt request alone does not authorize arbitrary
downloads, long inference runs or changes to an installed service.

### Execute and retain exact observations

Use Temper's public commands to compile each candidate from a local copy of
the authoring catalog and inspect its resolved execution. Record which
profile Temper selected on the actual machine. Context/output/memory-only
lock configuration is insufficient for changing KV or batch settings:
compile the candidate profile containing those settings. Retain Temper's
execution identity and the exact catalog used, without inventing another hash
definition. Verify that the compiled settings are the candidate being tested.

Use Field Kit's bounded measurement, process supervision, session and cleanup
primitives for execution. The dispatched contributor command accepts only its
frozen configurations; it is not an arbitrary-profile runner. Prepare the
smallest separate question/driver needed to execute these compiled candidates,
reusing those primitives and the existing workload/oracle. Check candidate
selection, limits, interruption, first-attempt retention and owned shutdown
with synthetic responses before live work. Keep this driver separate from the
dispatched package; do not repurpose an old tuning runner with retired lock
formats. If a required adapter is unavailable, identify that concrete gap
before requesting a live run.

Run one engine residency at a time, with the Mac on power and awake. Reuse
verified model/software material within the tuning session when Temper allows
it. Completed contributor runs remove their private downloads; disclose any
necessary re-download. Never borrow an unfinished session's installation or
prune a shared cache. Stop larger context points after the first unsuccessful
point in that candidate's ladder. Stop the session on unsafe ownership,
observation loss or its resource/time limits; recover before further work.

For every attempted candidate, retain exact resolved settings and machine
facts alongside:

- Workload, actual input/context, output and continuation allowances, task
  checks, submitted answer and correctness. Keep model-authored tests distinct
  from independent checks and retain material code-review findings.
- Preparation/startup, first output, first answer and request completion times;
  prefill/generation rates when available. Incorrect answers retain timings
  but do not count as successfully completed work.
- Native loaded and peak engine allocations in bytes, displayed in GiB, with
  counter source and covered workload/lifetime. Record process RSS/physical
  footprint separately, plus swap growth, configured caps and effective Metal
  budget. These overlapping counters must not be added or presented as minimum
  machine RAM. Missing counters stay missing; partial peaks are lower bounds,
  and changed-instance/reset observations invalidate that memory comparison.
- First failures, interruptions and stop causes. Distinguish a wrong answer,
  unsupported setting, native OOM, resource-guard stop, timeout and invalid
  measurement. A deliberate retry is a separate attempt.

Reuse the existing session/report as the home for these facts. Keep the
smallest evidence needed to reproduce or assess the choice; do not create a
parallel progress registry. An old result without native memory can establish
operation but needs a measured baseline for memory tuning.

### Supply the catalog

Select a passing complete profile using the agreed quality, latency and
memory criteria. Reuse its exact applicable measurement; run a confirmation
only if something changed or a necessary check remains missing. Keep the
baseline when candidates provide no useful improvement. If no profile meets
the criteria, report that outcome and preserve the original catalog.

Produce a local copy of the supplied Temper catalog containing the selected
profile under the existing preset. Preserve other profiles and shared preset
fields. Follow Temper's current ordering/selection rules and avoid duplicate
thresholds. Treat `min_memory_gib` as a selection threshold: use the observed
RAM tier for a newly measured profile unless the owner has specified a
different authored policy. A run on a 64 GiB Mac does not establish a 32 GiB
profile or a physical-RAM minimum; chip and available Metal memory still bound
the evidence. Do not invent profiles for untested machines.

Attach or reference evidence at the resolved-profile grain through the
catalog's supported mechanism. Include exact machine and execution identity,
tested input/window, output/follow-up reserves and observed resource limits.
The largest successful point is a tested point, with the next failed or
untested point identified. Changing KV, batching, software or another consumed
setting invalidates applicability to that changed execution. Shared preset
changes affect every dependent profile; historical observations remain intact.
Use the existing report for details the catalog does not own. Never fabricate
a public evidence URL or add fields its schema rejects.

Compile the final exported catalog with the same Temper executable and
explicit selection inputs. Inspect the selected profile and verify its
execution identity matches the measured candidate. Check the diff for shared
preset or unrelated-profile changes. A successful compile verifies catalog
mechanics; the retained run establishes observed behavior.

Finish with the local catalog path, the precise preset/profile selected, a
short quality/latency/memory comparison, applicability limits, and the exact
validated Temper command to use that file. Link the supporting report and
preserved failures. Publication, upstream merge and activation are separate
owner actions; deliver the usable local catalog first.
