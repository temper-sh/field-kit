# Develop Field Kit

The agent owns research decisions. Knowledge and tool manuals provide context.
The runtime owns exact attempts, material scope, process supervision, resource
limits, recovery and evidence return. There is no model roster or coding
qualification state machine.

## Verify

```sh
./field-kit verify
python3 -m unittest discover -v
git diff --check
```

Python 3.9+ and its standard library are sufficient. CI exercises 3.9 and 3.14.
Tests use fake Temper/model boundaries and local subprocesses. They verify
software behavior; they do not establish native engine performance or fit.

## What was retained

The process probe, macOS resource watcher, response-stream capture and Splash
native-memory observer retain their focused tests. New study/execution code
uses those boundaries. Model-specific planning, coding gates, the fixed Flask
executor/oracle and per-campaign preparation command were retired from this
branch. Their history remains on master at 75629ad.

Old active studies keep their original runtime and state. No implicit migration
or destructive cleanup of those studies is performed here.

## Boundaries

| Module | Owns |
|---|---|
| study | Approved material/limits, attempt custody, close and repair attachment |
| execution | One trial, reusable request sessions or a supplied task harness |
| material | Comparison of fixed Temper material with settings variants |
| workloads | Reusable request inputs and narrow semantic answer checks |
| jobs | Bound worker identity, detached launch, stop and blocking wait |
| measurements | Native rates and named memory counters |
| probe / watcher | Temper process binding, shutdown and resource observation |
| serving / stream / splash_memory | HTTP capture and engine observations |
| export | Complete idle-study return, including defects and local patches |
| temper | Installed public execution commands; no catalog/installer duplication |

A new study should normally add knowledge and workload inputs. Completed work
uses existing harnesses and project tests. Add an adapter only for a real
interface gap; do not create a new executor per study.

Versioned briefs and inputs live in studies/. Generated execution state lives
in .work/studies/ and stays outside version control. Historical runs are not
migrated implicitly; their matching runtime remains on the legacy branch.

Record exact Field Kit/Temper/Python identity per attempt. Repairs between
attempts are allowed. A changed runtime cannot overwrite an old attempt.
A lost worker is reconciled before cleanup or another trial; uncertainty about
live owned processes must remain visible.

The initial rebuild is verified offline, including a detached task's stop,
wait and export, missing-harness recovery, changed runtime between attempts,
and preservation of candidate test logs and repair patches. Read-only discovery
and study preview have also run against installed Temper 0.3.0 without setup or
model effects. A native trial requires owner
authorization and should exercise smoke, cache continuation, interruption,
close and export before the branch is promoted for testers.
