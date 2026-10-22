# Working in Field Kit

## Running studies

Read [the agent guide](docs/ASSISTANT.md), then relevant study knowledge.
The goals are trustworthy catalog data and the best supported presets for
this particular machine and useful work.

Use installed Temper unless the user selects another build. Author presets,
compile locks, install and run through Temper; Field Kit never duplicates it.
Read [its operations](docs/TEMPER.md) and [Field Kit's tools](docs/TOOLS.md) as
needed.

Research decisions belong to the agent. Follow [the method](docs/METHOD.md):
smoke, shortlist including near ties, research/tune, smoke again, select useful
winners, then deep context/cache/memory investigation. Strong coding quality
claims require completed-task evaluation and comparative source review.
Tests passing, useful code, harness completion and measured resources are
different observations. Coding qualification is not an execution gate.

Local engines (builds or settings Temper cannot express yet) are for the
owner's machine and are research evidence only; tester studies use Temper presets.

Recover from small problems within approved scope. [Report every defect and
local repair](docs/REPAIR.md), including workarounds that let the study finish.
Preserve first attempts and return patches and decisive reproduction evidence.

Keep a concise brief with decisions, reasons and open questions. Use foreground
job completion or blocking wait; repeated agent polling wastes tokens.
Approval covers ordinary trials and repairs within the stated material/limits.
New effects outside it require a concrete extension. Stop and restore a user
layout only as the study approval states; do not otherwise alter it. Do not
publish as part of a study.

## Changing Field Kit

Read README.md and docs/DEVELOPMENT.md. Follow parent workspace instructions,
REQUIREMENTS.md and PLAN.md when present.

Keep studies mostly prose and inputs. Reuse project tests and established
harnesses for completed work. Add a shared tool only for an actual repeated
operation. Return evidence and proposals; maintained comparisons and the
catalog are changed outside Field Kit.

Use Python's standard library. Preserve process ownership, resource monitoring,
interruption evidence and repeatable cleanup. Attribute runtime changes per
attempt; repairing between trials does not erase old observations.

Verify with ./field-kit verify and python3 -m unittest discover -v.
Native model runs/downloads, publication and live-service changes require
their existing explicit authorization. Never commit or push unless requested.
