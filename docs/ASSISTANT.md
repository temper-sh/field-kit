# Help a contributor run Field Kit

Instructions for an AI agent. The person you help wants to measure local AI
models on their Mac with Field Kit. They may not know the terms: use plain
words and short answers, and explain a term when it matters for a choice. Do
the work for them; ask only where these instructions say so.

## 1. Say what Field Kit is

In two or three sentences: Field Kit measures how a [Temper](https://github.com/temper-sh/temper)
preset (a model with its engine and settings) runs on this Mac: whether it
fits, how fast it reads and writes, and how long a real coding task takes.
Its goal is the best setup each Mac can use. Results stay on the Mac; the
person decides what to share.

## 2. Get ready

- **Field Kit folder.** Use the folder you were opened in when it is a Field
  Kit clone, or the one the person named, or `~/field-kit`. If there is none,
  ask once whether they keep one elsewhere; if not, clone
  `https://github.com/temper-sh/field-kit` into `~/field-kit`. In an existing
  clone, check `runs/*/run.json` for an unfinished run first (state
  `preparing`, `measuring` or `cleaning`): a run cannot continue after Field
  Kit's code changes, so finish it (step 3) before you update with
  `git pull`.
- **Temper.** `temper version` must print 0.3.1 or newer. If Temper is
  missing, offer `brew install temper-sh/temper/temper`; if it is older, offer
  `brew upgrade temper-sh/temper/temper`. Ask before you run either, and do not
  upgrade while a run is unfinished: a run finishes with the Temper it started
  with.
- **`hf`.** Temper downloads models with the Hugging Face tool. If `command -v
  hf` finds nothing, offer `brew install hf` and ask first.

If `.local/python-path` does not exist yet, the first `./field-kit` command
of any kind, `status` included, installs Field Kit's own Python through
Temper, about 27 MB, once, inside the Field Kit folder. Tell the person before
you run it.

## 3. Finish what is open

`./field-kit status` shows a background run and the newest run. If a run is
unfinished (state `preparing`, `measuring` or `cleaning`) and nothing is
running, offer to continue it (step 6). If a run has state `retained`, ask the
person to send its `report.md` to the maintainer. A study's log in
`trials/<preset>/log.md` shows a study in progress: continue it with that
study's guide.

## 4. Choose the study

Run `./field-kit check --preview`. It shows the Mac, the presets of the basic
check that can run here and their downloads, and downloads no models. Read the
README's **Current experiments** table and each study's guide in
`docs/experiments/`, and the reports of earlier runs in `runs/`. Then
recommend the study that adds the most for this Mac:

- no basic check of any preset on this Mac yet → the **basic check** of the
  preview's default preset (marked `→`), unless the person names another;
- a basic check on this Mac that filled its window, or left a lot of memory
  or time → **engine tuning**. A run on an older Temper or catalog still
  counts for its outcomes; only its speeds and memory stop comparing;
- 48 GB of memory or more, well beyond the preset's model, for example a 64 GB
  Mac with a 16 GB model → **weights comparison**, which runs larger builds of
  the model. Do not recommend it first on a smaller Mac.

Present the recommendation and the other studies in a few lines each: the
question, what runs on the Mac and for how long, the downloads, whether you
plan between runs, and what they may send (each guide's **What you send**;
for the basic check, the run's `result.json`). Let the person choose; if they ask
you to decide, take your recommendation and say so.

## 5. Plan the run

Follow the chosen study's guide; engine tuning and weights comparison have
their own instructions for you. Before every run, print its exact plan with
`./field-kit check --plan` and the run's options (`--preset ID` or
`--preset-file FILE`). Show the plan to the person as it is, from its title line (`Basic check of
…` or `Tuning trial …`) to the end: the preset or trial, downloads, disk, time
limits and cleanup. For time, add that a run
takes from about an hour to several hours, and quote the task times of earlier
runs of the same preset when there are any. Then ask whether to start it.

## 6. Start it

Only after the person says yes to that plan:

```sh
./field-kit check --yes --background <the options of the plan, none for the default preset>
```

It runs detached, keeps the Mac awake and writes to `runs/background.log`.
Tell them they can close you and the terminal; the Mac should stay on power.
When they come back, `./field-kit status` shows how far it is, and
`./field-kit stop` stops it the way Ctrl-C does. If they prefer to watch it,
give them the command without `--background` and `--yes` for their own
terminal instead; Field Kit then asks them to type `yes`.

## 7. After the run

Go through `runs/<id>/report.md` with them: the results, what "not comparable"
means and the **Room to tune** section. Then follow the study's next step, or
recommend the next study.

## Rules

- Start a run only after a yes to its exact plan from `--plan`. Never pass
  `--yes` without one.
- Ask before installing software or changing their system. Never upload
  anything; results stay under `runs/` and sharing is the person's choice.
- Do not change Field Kit's code, protocols, tasks or `runs/`.
- At the end, say what to send: each run's `result.json`, which holds the
  submitted patches, the Mac's details and local paths, and anything the
  study's guide adds.
