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

Field Kit runs on its pinned CPython 3.14.7 (`runtime/python-macos-arm64.lock.yaml`),
installed by Temper into `.local/` on first use, and uses only its standard
library. It never falls back to a system or PATH `python3`. Development and CI
may set `FIELD_KIT_PYTHON` to another Python 3.14 or newer; CI runs 3.14. The
launcher tests need macOS `plutil` and are skipped elsewhere.
Tests use fake Temper/model boundaries and local subprocesses. They verify
software behavior; they do not establish native engine performance or fit.

## Study format

A study records its format. Field Kit refuses to load one made with another
format: open it with the Field Kit revision that created it. Nothing migrates
or cleans up such a study implicitly.

## Boundaries

Field Kit evaluates presets: each attempt measures one subject. A `temper`
subject is an exact preset lock served by the installed Temper, on any machine;
it is the only source of catalog evidence. A `local` subject is an engine build,
arguments and files on the owner's machine before Temper supports them; it is
research evidence. Both share attempts, sessions, drivers, workloads and records,
so a local result and its later Temper rerun compare directly.

```text
cli ─► study ─► attempt: begin → prepare → measure each session → commit
                 ├─ session    one Temper serve under watch; always finished
                 ├─ interface  chosen by the lock's preset: chat/v1 or rerank/v1
                 ├─ driver     frozen requests │ an existing harness command, through the relay
                 └─ records    the only record shapes and file names
```

| Module | Owns |
|---|---|
| approval | The read-only approval document: candidates, limits, Temper's check/plan, storage and layout effect |
| study | Custody: reserve, idle checks, recovery, close and repair attachment |
| attempt | One attempt's phases, in order, and its committed outcome |
| attempt_queue | Planned attempts one at a time; stops at a STOP file or after an attempt that did not complete |
| subjects | The two subject kinds: preparation, probe, health path, model name; local declarations and variants |
| local_probe | One engine Field Kit starts itself: bound, watched, stopped and proven gone without Temper |
| session | One Temper serve under resource watch; leaving it always stops the serve and records memory |
| drivers | What sends the work: frozen request steps or an installed harness |
| relay | A harness's HTTP traffic to the engine, passed unchanged and recorded per request |
| records | Record shapes, states and file names: the stable surface for other tools and returned studies |
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
| grading | The coding harness's test environment: Field Kit's Python plus hash-pinned wheels in .local/grading |
| harnesses/coding | A bundled command-workload harness and its frozen tasks; it uses this checkout's stream and model formats |

A new study should normally add knowledge and workload inputs. Completed work
uses existing harnesses and project tests. Add an adapter only for a real
interface gap; do not create a new executor per study. The
[adapter guide](ADAPTERS.md) covers the extension contracts, registration and
Flint's format/history behavior. Existing formats need only a lock and input;
each genuinely new engine or format gets one folder and one registry entry.

Versioned briefs and inputs live in studies/. Generated execution state lives
in .work/studies/ and stays outside version control.

Record exact Field Kit/Temper/Python identity per attempt. Repairs between
attempts are allowed. A changed runtime cannot overwrite an old attempt.
A lost worker is reconciled before cleanup or another trial; uncertainty about
live owned processes must remain visible.

Offline tests cover a detached task's stop, wait and export, missing-harness
recovery, changed runtime between attempts, and preservation of candidate test
logs and repair patches. Native performance and fit need an authorized study.
