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
branch. Their history remains on legacy at 75629ad.

Old active studies keep their original runtime and state. No implicit migration
or destructive cleanup of those studies is performed here. Study schema v3 (one
Temper root per study, a configured weights cache) refuses v2 studies.

## Boundaries

Field Kit evaluates presets: each attempt measures one Temper preset served by
the installed Temper.

```text
cli ─► study ─► attempt: begin → prepare → measure each session → commit
                 ├─ session    one Temper serve under watch; always finished
                 ├─ interface  chosen by the lock's preset (chat/v1 today)
                 ├─ driver     frozen requests │ an existing harness command
                 └─ records    the only record shapes and file names
```

| Module | Owns |
|---|---|
| approval | The read-only approval document: candidates, limits, Temper's check/plan, storage and layout effect |
| study | Custody: reserve, idle checks, recovery, close and repair attachment |
| attempt | One attempt's phases, in order, and its committed outcome |
| session | One Temper serve under resource watch; leaving it always stops the serve and records memory |
| drivers | What sends the work: frozen request steps or an installed harness |
| records | Record shapes, states and file names: the stable surface for Workshop and returned studies |
| recovery | Proof that nothing an attempt owned still runs; never repeats inference |
| context | Machine-facing collaborators, passed in; tests pass fakes instead of patching modules |
| lockview | The one reader of Temper lock fields Field Kit uses |
| machine | Approved stop/restore of the user's layout; read-only large-process listing |
| storage | Study root and weights cache locations, volume facts, disk checks, exact study-added file cleanup |
| material | The variant rule: settings may change, approved material may not |
| workloads | Reusable inputs and their structure; request rules belong to the interface |
| jobs | Bound worker identity, detached launch, stop and blocking wait |
| adapters/interfaces | Per Temper preset interface: request rules, capture, decoding and answer checks |
| adapters/engines | Readiness, helper roles, native rates and optional native memory observers |
| adapters/models | Format decoding and assistant-history serialization, independent of engine |
| measurements | Approved resource limits, failure kinds and the named memory record |
| probe / watcher | Temper process binding, shutdown and resource observation |
| serving / stream | Bounded HTTP and raw chat-completions capture |
| export | Complete idle-study return, including defects and local patches |
| temper | Installed public execution commands; no catalog/installer duplication |

A new study should normally add knowledge and workload inputs. Completed work
uses existing harnesses and project tests. Add an adapter only for a real
interface gap; do not create a new executor per study. The
[adapter guide](ADAPTERS.md) covers the extension contracts, registration and
Flint's format/history behavior. Existing formats need only a lock and input;
each genuinely new engine or format gets one folder and one registry entry.

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
