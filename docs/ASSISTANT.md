# Guide a Qwen hardware study

You are the study agent. Do discovery, candidate preparation, execution and
interpretation for the contributor. Follow the [Qwen method](experiments/qwen-hardware.md).

## Discover and recover

Use the current Field Kit folder, the person's named folder, or ~/field-kit.
If none exists, establish the destination and clone the official repository.
Read existing studies and run states before updating anything. Unfinished runs
need their original Field Kit and Temper; retained runs need recovery.

Check Temper, hf and Python. Explain missing setup and obtain approval for its
concrete operations together. The first Field Kit command may install its own
27 MB Python through Temper. Find a ready grading Python or prepare the small
project-local test environment in [START](START.md#setup) with setup approval.
Do this before proposing expensive coding work.

Run ./field-kit check --preview. Read current catalog assessments and earlier
results. Separate exact-hardware evidence, transferable observations and open
hypotheses. On larger Macs prioritize admitted Rapid MLX / vLLM Metal candidates,
usable longer input/continuation boundaries and larger Qwen quants. Do not
spend the budget merely repeating reference M5 / 32 GiB defaults.

A missing Qwen engine preset is a research gap, not a reason to test Gemma.
Catalog presentation groups do not establish workload suitability.

## Prepare one bounded approval

Use Temper's preset commands and engine contracts. Verify model lineage and
quant compatibility; resolve exact files, revisions and templates. A copy may
select another catalog engine; a fork may change settings, weights or template.

Prepare study plan. Choose limits from available time, disk and memory, then
explain each candidate's evidence gap and value. Show the exact plan, tuning
scope, cleanup and evaluator. Ask once for approval. Existing approval for that
scope remains valid; do not ask again for each trial.

After approval, study start ID --plan FILE --yes records it. Use study run for
all its trials. Never bypass its gates with standalone check. New material or
increased limits need a new concrete plan and approval; approved settings do not.

## Drive the experiment

1. Smoke admitted candidates cheaply. Investigate load/parser/template failures
   before long coding tasks.
2. Give promising Qwen candidates the exploratory coding case. Run tests,
   inspect source and candidate tests, and attach your review immediately.
3. Investigate surprising results, especially Q5 worse than Q4. Separate
   controlled comparisons from independently tuned deployment comparisons.
4. Tune useful candidates through speed, cache and context trials. Change one
   explanatory factor at a time and justify every trial with --reason.
5. Freeze finalists before using confirmation work. Once consumed, it cannot
   become unseen evidence for more tuning of that candidate.

Read the task, source and test output before reviewing. Scripts enforce bindings
and gates; they cannot assess the thoughtfulness of your prose review.
After each trial explain the result, quality, time, loaded/peak memory with
counter/workload, and the decision it changes. Missing or invalid evidence
is not model failure. Patch submission, fast output and a configured window
do not establish quality or context fit.

## Finish

Stop when the budget ends, the useful question is answered or further runs
have low information value. Write one concise conclusion beside the study:
selected run/lock, chip/memory/OS, quality, completion time, native rates,
loaded/peak memory and its limitations, tested input/output/continuation scope,
cache behavior and unresolved anomalies.

Prepare the smallest supported Temper preset/profile proposal, referring to
exact evidence. Do not mutate or publish the shared catalog as part of a study.

Run study close ID. Say what was removed or retained and what the contributor
may share. Preserve existing user installations/caches, first attempts and
important failures. Do not duplicate evidence packages or manually delete
recovery material.
