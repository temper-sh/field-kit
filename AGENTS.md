# Working in Field Kit

If the person wants to run a study or measure their Mac, follow
`docs/ASSISTANT.md`. The rest of this file is for changing Field Kit.

When changing Field Kit, read `README.md` and `docs/START.md`. When changing an
experiment, read its guide in `docs/experiments/` and its package. Development
plans and migration history live outside this repository. In the local-AI
workspace, also follow the parent instructions and read `../REQUIREMENTS.md`,
`../PLAN.md` and `../FIELD-KIT-PLAN.md` when present. Workspace cutover remains
a separate decision.

Field Kit owns portable questions, exact consent, bounded execution, sessions,
measurements, reports and cleanup. Temper owns execution-lock compilation,
installation, rendering, binding and process primitives. Consume its public
commands; never synthesize software locks or copy catalog facts into packages.

Field Kit's goal is the maximum usable performance on each machine: the
largest context window and the fastest speeds that still finish the task at
baseline quality, without swap growth, memory failure or heat stops. Prompts,
protocols, guides and reports encourage pushing settings toward that limit; a
catalog default is a starting point, never the answer. When asked to tune a
preset, follow the [continuation method](docs/experiments/qwen-hardware.md#continue-toward-useful-limits).
Plan useful extensions before execution and continue after healthy passes
within approval. Budget exhaustion is an incomplete search, not a measured
maximum. Diagnostic output allowances must not become arbitrary preset caps.

The current research scope is Qwen. Catalog presentation groups do not grant
coding eligibility. The study agent prioritizes hardware evidence gaps, runs
cheap smoke checks, reviews coding tests and source before tuning, investigates
anomalies, and confirms finalists on unused work. Do not call patch submission
quality success or a configured window measured context capacity.
Coding quality uses a bounded visible-test and repair workflow, with final
independent tests and source review. Coding performance uses one response and
a reviewed degradation check. Full runs assess or reassess quality; do not
repeat them at every performance tuning point. Preserve first-attempt outcomes
separately from final results. The [coding modes](docs/experiments/basic-check.md#coding-modes)
also serve local Labs/Workshop work.
Apply the [diagnosis procedure](docs/ASSISTANT.md#research-unexpected-results)
to any material mismatch with expectations, including quant quality reversals
and unexpected gains. Check both our setup and the expectation itself.

Study consent is bounded by exact material and explicit resource/time limits;
settings within that approved scope do not require repeated permission. Temper
owns model-engine installation. The project-local grading venv is a reusable
development test tool; never install its dependencies in an engine environment.
Reuse study-owned weights and software across trials and remove them on close
only after verified shutdown. Preserve user-owned caches and recovery material.

The runtime uses the Python standard library. Keep discovery and planning
read-only. Preserve first attempts and distinguish invalid evidence from an
incorrect model answer. Keep each experiment's hypotheses, decision thresholds,
inputs and requirements fixed before candidate output. Preserve baseline and
tuning observations separately when the experiment compares configurations.

Write public documentation for people running experiments. Keep the README and
run guide general; put model-specific requirements, choices and methods in the
experiment guide. Describe what the current commands actually support. Use plain
language and explain technical terms where they affect a reader's decision.
Keep the README's **Current experiments** table as the public experiment index:
one row per current package, linked to its guide, with purpose, requirements and
status consistent with the catalog. Other general guides should link to this
table rather than keep their own experiment lists.

Prepare questions and run hermetic tests within the requested development
scope. A live experiment requires its machine owner's exact-plan consent.
Publication, uploads and live-service changes remain separate effects.
Results stay local; sharing is the contributor's action. Workshop reviews
returned evidence, Labs investigates surprises, and Results owns public claims.
Never commit or push unless requested.

Verify with `./field-kit verify` and `python3 -m unittest discover -v`.
