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

Research the expected benefit before selecting candidates. Use current primary
sources: model cards, engine documentation, source, release notes and issue
reproductions. In the study brief, name the expectation, source/revision or access
date, claimed workload/metric and conditions that could prevent transfer to this
Mac. Distinguish an upstream claim, a local observation and an assumption. An
engine name or advertised context window is not evidence that its intended path
works here.

## Prepare one bounded approval

Use Temper's preset commands and engine contracts. Verify model lineage and
quant compatibility; resolve exact files, revisions and templates. A copy may
select another catalog engine; a fork may change settings, weights or template.

Prepare study plan. Choose limits from available time, disk and memory, then
explain each candidate's evidence gap and value. Show the exact plan, tuning
scope, cleanup and evaluator. Ask once for approval. Existing approval for that
scope remains valid; do not ask again for each trial.

Plan conditional extension toward the model/backend's supported limits or a
specific usability constraint. Name the next useful steps after a pass and
why exploration would stop. Size request deadlines for long prefill, early-edit
replay and output generation; the CLI defaults are planning conveniences, not
capacity or latency requirements.
Check the engine's request timeout as well as Field Kit's limit. A longer
runner deadline does not override a shorter server deadline.
Budget full quality work separately with --quality-turns and --quality-seconds;
those limits cover test feedback and repairs inside the total study budget.

Reserve time and runs for extension, unexpected results and final confirmation.
Prefer a promising shortlist explored properly to many shallow first passes.

After approval, study start ID --plan FILE --yes records it. Use study run for
all its trials. Never bypass its gates with standalone check. New material or
increased limits need a new concrete plan and approval; approved settings do not.

## Drive the experiment

1. Smoke admitted candidates cheaply. Investigate load/parser/template failures
   before long coding tasks.
2. Assess promising Qwen candidates with --workload coding --coding-mode quality.
   The model can test and repair proposals within the approved budget. Grade
   its final submission with independent tests, inspect source and candidate
   tests, and attach your review immediately. Preserve first-attempt outcomes.
3. [Research unexpected results](#research-unexpected-results) throughout the
   study: quality, speed, memory, context and functional behavior. Q5 worse than
   Q4, absent acceleration and surprising gains are examples, not a fixed list.
   Separate controlled comparisons from independently tuned deployment comparisons.
4. [Continue toward useful limits](experiments/qwen-hardware.md#continue-toward-useful-limits)
   through speed, cache and context trials. A healthy pass calls for the next
   useful extension within approval. Change one explanatory mechanism at a time,
   examine relevant interactions, and justify every trial with --reason.
   Coding performance uses --coding-mode performance: one response, no repair
   feedback. Review it for degradation against a matched one-shot reference
   associated with the qualified quality run. Do not demand a full quality run
   at each tuning point or reinterpret every imperfect first answer as regression.
5. Resolve or explicitly defer useful extension questions before freezing
   finalists for confirmation. Once consumed, that work cannot become unseen
   evidence for more tuning of the candidate.
   Reassess quality with a full run when a material change, unexplained degradation
   or the final selection needs new quality evidence. Confirmation uses quality
   mode and unused work; one-shot performance results never qualify by themselves.

Read the task, source and test output before reviewing. Scripts enforce bindings
and gates; they cannot assess the thoughtfulness of your prose review.
After each trial explain the mode, result, quality, time, loaded/peak memory with
counter/workload, and the decision it changes. Missing or invalid evidence
is not model failure. Patch submission, fast output and a configured window
do not establish quality or context fit.
Keep first-submission performance separate from quality-workflow time, which
includes visible tests and repairs. Final independent grading and source review
are outside the recorded inference workflow; name any unmeasured human cost.

## Research unexpected results

A material mismatch with a researched expectation needs diagnosis before a
candidate is selected or rejected, or a context boundary is generalized. Apply
this to quality, latency, memory, context and functional behavior, including
unexpected gains. A supposedly higher-fidelity quant giving worse answers is
one such question. Investigate the expectation itself as well as the test setup.
These are agent decisions; the runner's gates do not establish a causal explanation.

1. Preserve the attempt and check measurement validity: task/oracle, actual
   input/output including reasoning, cold/warm state, counters and competing
   machine load. Compare the claim's exact baseline and conditions with ours.
2. Inspect effective execution through rendered requests and native observations:
   template, sampling, output reserve, KV/cache allocation, automatic fitting,
   placement and the intended draft/kernel path. Requested flags do not prove
   activation. An unsupported adapter path is untested, not a failed optimization.
3. Research competing explanations in relevant upstream sources. Our setup or
   evaluator may be wrong, the implementation may be inefficient, the claim may
   concern different hardware/work, variation may explain the outcome, or the
   expected relationship may be wrong. State what evidence distinguishes these
   possibilities before choosing a retry.
4. Use the cheapest check that can change the decision. Read-only inspection may
   suffice. For a native comparison, use --purpose diagnose and put the hypothesis,
   original run and distinguishing observation in --reason. Freeze controls and
   the correctness guard before output; use bounded matched repeats when needed.
   Collect component timings and loaded/peak memory relevant to the explanation,
   naming missing counters. Stay within approved material and resource limits.
5. Write the disposition beside the study: demonstrated setup/measurement defect,
   expectation inapplicable here, predicted benefit or ordering not reproduced
   in these conditions, or unresolved. Retain source links and decisive evidence
   without a separate report hierarchy. A correction creates a new observation
   and needs relevant quality checks and unused confirmation before selection.
   Preserve the original result.

Do not equate a timeout, configured window or safety stop with physical capacity.
Do not explain a slowdown as a hardware limitation from aggregate token rate
alone. If the decisive check needs other hardware or material, leave a concrete
follow-up question. Stop at the budget or when further checks have little value;
never loosen the oracle, retry until a winner appears or bypass the study's scope.
An unresolved cause permits keeping the established preset, not a general verdict
against the model, quant, engine or technique.

## Finish

Stop when the budget ends, the useful question is answered or further runs
have low information value. State which happened; a budget stop leaves the
untested boundary open. Write one concise conclusion beside the study:
selected run/lock, chip/memory/OS, quality, completion time, native rates,
loaded/peak memory and its limitations, tested input/output/continuation scope,
cache behavior, why exploration stopped, and the next useful untested point.

Prepare a focused Temper proposal with the best supported settings for this
hardware. Keep distinct speed, memory or context options only when their
tradeoffs matter. Do not turn test budgets into preset restrictions or mutate
and publish the shared catalog as part of a study.

Run study close ID. Say what was removed or retained and what the contributor
may share. Preserve existing user installations/caches, first attempts and
important failures. Do not duplicate evidence packages or manually delete
recovery material.
