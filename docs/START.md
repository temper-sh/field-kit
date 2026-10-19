# Run a Qwen study

Use the one-message prompt in the [README](../README.md#start-with-one-message).
The agent follows [the contributor workflow](ASSISTANT.md), chooses useful
questions for your hardware and prepares one bounded study for approval.

## Setup

You need an Apple Silicon Mac, Temper 0.3.1 or newer, and hf for model downloads.
Install these if needed:

```sh
brew install temper-sh/temper/temper hf
git clone https://github.com/temper-sh/field-kit.git
cd field-kit
```

The first Field Kit command installs its private Python through Temper
(about 27 MB). It stays in .local/ for reuse. Model engines and weights are
installed only after approval.

Coding studies need pytest, asgiref and the frozen Flask task's dependencies.
The agent checks an existing grading environment first. A project-local test
environment can be prepared using Python 3.12 or newer:

```sh
python3 -m venv .local/grading
.local/grading/bin/python -m pip install -r runtime/grading-requirements.txt
```

This is a reusable Field Kit test tool, separate from engine environments.
The runner itself uses the standard library. Candidate code executes inside
macOS sandbox-exec with network and process spawning denied.

## Approval and progress

The plan names exact candidates and freezes engine/software, weights and
templates. It limits runs, elapsed time, planned downloads, context size and
request time. The agent chooses trials and tuning controls within that scope.

Keep the Mac awake and on power; close other demanding applications. Avoid
changing Field Kit, Temper or the grading environment during a study.

```sh
./field-kit status
./field-kit study status STUDY_ID
./field-kit stop
```

A background trial keeps the Mac awake until that trial ends. The agent must
return to review its result and choose the next experiment. Closing the agent
does not leave an autonomous study planner running.

## Stop, continue and close

Ctrl-C or stop preserves the attempt and asks Temper to stop its owned
processes. Wait for shutdown to finish.

```sh
./field-kit study resume STUDY_ID
./field-kit study close STUDY_ID
```

Resume preserves first attempts instead of silently repeating them. Close
works after the budget expires and can cancel safe unfinished preparation.
It removes owned installations, weight caches and reconstructible source copies.

An uncommitted attempt, retained run or uncertain shutdown blocks cleanup.
Keep its process status and report for recovery with the original Temper.
Do not delete recovery files or kill processes by name. Give the maintainer
the study ID, run ID and exact error.

## Results and sharing

studies/ID/study.json owns approval, budgets and run references; report.md
summarizes evidence. Referenced runs/ID/ directories hold results and execution
locks. Coding runs also hold review/tests.json and review.json.

Keep the study and referenced run directories together when sharing. They
include hardware details and local paths. The agent writes a concise conclusion
with the chosen exact configuration, quality, speed, loaded/peak memory,
measured context scope and unresolved questions. Publication is separate.
