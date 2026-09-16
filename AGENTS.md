# Working in Field Kit

Read `README.md` and `docs/START.md`. When changing an experiment, read its guide
in `docs/experiments/` and its package. Development plans and migration history
live outside this repository. In the local-AI workspace, also follow the parent
instructions and read `../REQUIREMENTS.md`, `../PLAN.md` and
`../FIELD-KIT-PLAN.md` when present. Workspace cutover remains a separate decision.

Field Kit owns portable questions, exact consent, bounded execution, sessions,
measurements, reports and cleanup. Temper owns execution-lock compilation,
installation, rendering, binding and process primitives. Consume its public
commands; never synthesize software locks or copy catalog facts into packages.

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
