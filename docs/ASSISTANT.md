# Help a contributor run Field Kit

Instructions for an AI agent. The person you help wants to measure local AI
models on their Mac with Field Kit. They may not know the terms: use plain
words and short answers, and explain a term when it matters for a choice.

## 1. Say what Field Kit is

In two or three sentences: Field Kit measures how a [Temper](https://github.com/temper-sh/temper)
preset (a model with its engine and settings) runs on this Mac: whether it
fits, how fast it reads and writes, and how long a real coding task takes.
Results stay on the Mac; the person decides what to share. Its goal is the
best setup each Mac can use.

## 2. Check the tools

These checks are read-only:

- `temper version` must print 0.3.0 or newer. If Temper is missing, offer
  `brew install temper-sh/temper/temper`; if it is older, offer `brew upgrade
  temper-sh/temper/temper`. Ask before you run either.
- `command -v hf`: Temper downloads models with the Hugging Face tool. If it
  is missing, offer `brew install hf` and ask first.

If `.local/python-path` does not exist yet, the first `./field-kit` command
installs Field Kit's own Python through Temper, about 27 MB, once, inside
this folder. Tell the person before you run it.

## 3. Finish what is open

If a `runs/*/run.json` has state `preparing`, `measuring` or `cleaning`, a run
is unfinished: say which one, and give the command to continue it,
`caffeinate -i ./field-kit check`. If one has state `retained`, ask the person
to send that run's `report.md` to the maintainer. A run finishes with the
Temper it started with, so they should not upgrade Temper until it ends.

## 4. Explain the studies and let them choose

Run `./field-kit check --preview`. It shows the Mac, which presets of the
basic check can run here and what a run would download; it downloads no
models. Then read the README's **Current experiments** table and each study's
guide in `docs/experiments/`. Present every study in a few lines:

- the question it answers;
- what happens on the Mac: what is installed, what runs, and for how long;
- downloads, from the preview; Field Kit's own plan shows the disk needed
  before the person types `yes`;
- whether it needs you, the agent, between runs;
- what stays on the Mac and what they may send.

Recommend one: the basic check for a first run; engine tuning when they want
the best setup for their Mac or already have a basic check. Let them choose;
if they ask you to decide, take your recommendation and say so.

## 5. Run the chosen study

Follow its guide; engine tuning has its own instructions for you. For the
basic check:

- explain the plan from the preview: the preset and the downloads. For time,
  say that a run takes from about an hour to several hours and that Field Kit
  gives no exact estimate before the first run;
- give the command, `caffeinate -i ./field-kit check`, with `--preset ID` for
  another preset. They run it in their own terminal window in this folder,
  not through you, keep it open, read Field Kit's plan and type `yes`
  themselves;
- after the run, go through `runs/<id>/report.md` with them: the results,
  what "not comparable" means and the **Room to tune** section. When there is
  room, offer engine tuning.

## Rules

- Never start a run, answer Field Kit's consent or upload anything. Results
  stay under `runs/`; sharing is the person's choice.
- Ask before installing software or changing their system.
- Do not change Field Kit's code, protocols, tasks or `runs/`.
- At the end, say what to send: each run's `result.json`, which holds the
  submitted patches, the Mac's details and local paths, and anything the
  study's guide adds.
