# local-ai-setup

Installs and configures a complete local LLM stack for coding agents on Apple
Silicon — engines, router, models, agent glue — from one declarative manifest,
and can be re-run at any time. A run on a fully configured machine changes
nothing and reads as an audit.

```
./setup.sh                     # install and configure whatever is missing
./setup.sh --dry-run           # print the plan, touch nothing
./setup.sh --only configs      # tools,engines,models,configs,extensions,service
./setup.sh --verify            # checksum downloaded weights (slow, opt-in)
```

Every item reports exactly one of `[ok]`, `[install]`, `[patch]`, `[change]`,
`[skip]`, `[manual]`, or `[fail]`. `[manual]` means it needs sudo — the script
prints a ready-to-paste command and never runs sudo itself. `[fail]` is the
only one that matters at the end: it is counted in the summary and makes the
run exit non-zero.

**Before you start:** an Apple Silicon Mac with [Homebrew](https://brew.sh)
already installed. Everything else is installed for you. All of this was built
and measured on **macOS 26.5.1–26.6**; nothing older has been tested, so
treat an older machine as unknown rather than supported. Budget **a little over 20GB of
disk** for weights (19GB measured here, before the vision model is pulled) —
the coder (~15GB) comes down during setup, and the small specialists are
fetched lazily on first use, not on first run. This installs the *stack*, not
the agent: you supply the client (this was built against
[Pi](https://github.com/earendil-works/pi), and anything that speaks the
OpenAI API to `localhost:8080` works).

## Why this exists

Most local-LLM advice is written for someone else's machine — a CUDA box, a
multi-user server, a metered API. Followed on a MacBook, it produces a stack
that is slower and stranger than it should be. This repo is what came out of
setting up one machine properly and writing down what was *measured* along the
way: not just what to install, but which flags matter, which popular tools do
nothing here, and how to tell which of those conclusions transfer to your
machine.

Four principles shape everything in it:

1. **Measure, don't trust.** Most of the load-bearing flags in `models.yaml`
   contradict the engines' own documentation, and each carries the measurement
   that justifies it ([docs/FINDINGS.md](docs/FINDINGS.md)). On this stack the
   published docs have been wrong more often than right. Claims here state the
   setup they apply to, because most of them stop being true on different
   hardware.
2. **The unit of value is a finished task — not a token, and not a second.**
   One user, own hardware, no bill, so input tokens are free and only
   wall-clock and correctness are left. Of those, correctness dominates: a
   wrong edit at 100 tok/s costs more wall-clock than a right one at 13,
   because the retry is a whole extra turn including its prefill. That is why
   a 3B-active MoE was retired here in favour of a slower dense model. Tokens
   *do* convert to time — roughly 180/s of prefill, and a working prefix cache
   makes a stable context nearly free after turn one — and that exchange rate
   quietly kills several tools that are obviously correct behind a metered
   API. But it is the second test, not the first.
   [docs/DECISIONS.md](docs/DECISIONS.md) records what was tried, what it
   measured, and which kind of setup each rejected tool *does* fit.
3. **One manifest, generated configs, auditable re-runs.** `models.yaml` is
   the single source of truth; the router config and the agent's model list
   are generated from it. A second run changes nothing, `--dry-run` touches
   nothing, and `sudo` is never run — anything that needs it is printed for
   you to paste.
4. **A dependency must not borrow a runtime you manage for other reasons.**
   Everything lands where one command updates it and one command removes it,
   and nothing resolves your `node` or `python` from PATH at run time —
   switching versions for a work project must never break the AI setup
   underneath it.

**Machine fit:** built and measured on a MacBook Air M5 with 32GB of unified
memory. The default manifest pins a ~15GB coder resident, so it effectively
needs more than 16GB. On other Apple Silicon sizes the shape holds but the
model entries are yours to change — `models.example.yaml` holds known-good
alternatives, and `prompts/add-model.md` is the research prompt for choosing
new ones. **On 16GB there is no drop-in option yet:** every chat entry in
`models.example.yaml` is 20B or larger, and a 16GB Mac's wired ceiling works
out to 8192MB, which cannot hold any of them pinned. You would need a smaller
coder than anything measured here.

How much room you have above the coder depends on RAM in three buckets,
which `scripts/gpu-budget.sh` derives and `./setup.sh` checks (the hard way
this was learned is `docs/FINDINGS.md` #16 — when the GPU pool runs out, the
engine aborts mid-turn, it does not slow down):

| RAM | What fits beside the pinned coder |
| --- | --- |
| <32GB | Below the minimum. The ~15GB pinned coder does not leave a working margin, and no drop-in smaller manifest exists yet (see the 16GB note above). |
| 32GB | Nothing resident. This is the minimum the stack runs on: specialists (reranker, extractor) load on demand and unload on a short ttl. Eight engine aborts in two days taught us that even a 0.6GB always-resident model tips it over. |
| 33–36GB | A small always-resident set (a reranker-sized specialist), gated by arithmetic — the budget script tells you the largest coder fraction that fits. |
| >36GB | Comfortable: residents plus an on-demand heavy model at the same time, all gated by arithmetic. |

Not sure where you land? `scripts/machine-report.sh` prints your machine's
block — it installs nothing, writes nothing, and is a single file you can
send to someone who has never heard of this repo. Over time the buckets will
likely grow bucket-specific manifests, not just different gates — a larger
reranker only earns its keep with headroom, and at exactly 32GB the honest
endgame may be a single pinned model — but today `models.example.yaml` is
the menu and the numbers to decide that are still being collected.

The chip generation is the other axis: every number in this repo was measured
on an M5, whose GPU carries the neural accelerators MLX uses for prefill. On
M4-and-earlier the prefill balance that justified some decisions here (the
two-engine split most of all — `docs/FINDINGS.md` #13) may land differently.
Nothing was measured on pre-M5 hardware; treat those decisions as re-derivable
there, not as given.

## The stack

```
  Pi / OpenGSD / Zed  ──OpenAI API──▶  llama-swap :8080
                                            │
                        ┌───────────────────┴───────────────────┐
                        ▼                                       ▼
                   Rapid-MLX                              llama-server
                  (MLX models)                            (GGUF models)
                        │                                       │
                        └──────────▶ ~/.cache/huggingface ◀─────┘
```

**llama-swap** is the only thing clients talk to. It starts and stops model
servers on demand, unloads idle ones, and serves `/v1/models`,
`/v1/chat/completions`, `/v1/rerank`, `/ui`, `/running`, and
`/api/models/unload/<id>`.

Everything is pulled from HuggingFace into the standard cache. No Ollama, no
proprietary blob stores.

Want one server and a menubar app instead of a manifest and a proxy? The
strongest occupant of that slot we have seen is
[oMLX](https://github.com/jundot/omlx) — MLX-native, memory-managed at
runtime, serious about version pinning. This stack keeps its shape because
the specialists are GGUF, the reranker runs on the CPU on purpose (32GB — see
the buckets above), and every flag here carries a measurement that a new
serving path would re-open. On an all-MLX model set with RAM to spare, oMLX
may honestly be the easier path; the full comparison is in
[docs/DECISIONS.md](docs/DECISIONS.md).

### What gets installed

| | |
|---|---|
| CLI tools | `ripgrep fd jq yq ast-grep gh difftastic bat tokei fzf rtk` — fast primitives worth having on the box. Note the agent measurably ignores some of them: `rtk` was invoked 0 times in 4 runs even when prompted to use it |
| Engines | `llama.cpp` (llama-server), Rapid-MLX |
| Router | `llama-swap` (from the `mostlygeek/llama-swap` tap) |
| Agent | `pi-coding-agent`, plus its permission-gate extension |
| Other | `hf` (HuggingFace CLI) |

### Where it all lands, and how to remove it

Nothing here installs into a Node or Python version you manage for other work.
That is a deliberate constraint, not an accident — if you use `fnm`, `nvm`,
`pyenv` or `asdf`, switching versions for a work project must not break your
AI setup, and you should never have to wonder which version a tool went into.

| what | where | update | remove |
|---|---|---|---|
| CLI tools, engines, router | Homebrew prefix | `brew upgrade` | `brew uninstall <formula>` |
| Rapid-MLX | `~/.rapid-mlx/venv` — a venv on a uv-managed Python (the brew formula ships a dependency set upstream forbids — see below) | re-run `./setup.sh` | `rm -rf ~/.rapid-mlx ~/.local/bin/rapid-mlx` |
| Model weights | `~/.cache/huggingface` — the standard cache, shared with any other HF tool | `hf download` | `hf cache delete` |
| Generated config | `~/.config/llama-swap/config.yaml`, the `local` provider in `~/.pi/agent/models.json` | re-run `./setup.sh` | delete the files |
| Extensions | `~/.pi/agent/extensions/*.ts` — plain file copies | re-run `./setup.sh` | delete the files |
| Service | `~/Library/LaunchAgents/com.llamaswap.server.plist` | re-run `./setup.sh` | `launchctl bootout` then delete |

The whole table is executable: `scripts/uninstall.sh --dry-run` shows what
would go, without the flag it removes everything the stack owns outright —
service, generated configs, the venv, this manifest's weights, logs — and
*prints* the `brew uninstall` commands rather than running them, because the
CLI tools are shared with whatever else uses them on your machine (the two
`/etc` files, being sudo-written, are printed the same way).

Everything comes from Homebrew except Rapid-MLX — and the story of why is
this repo's best cautionary tale about trusting a package because it
installed cleanly. The homebrew-core formula was adopted twice in 2026-08
and retired twice on measurements. First at 0.11.9: that bottle crashes
streaming tool calls — every agent turn carrying tools fails while plain
chat and health checks look perfect
([docs/FINDINGS.md](docs/FINDINGS.md) #15). Then at 0.12.4, which passed
the tool-call gate and served for one evening — until task-shaped work
measured **~3× slower prefill** than the same engine in a venv. The cause:
the formula pairs rapid-mlx with brew's `mlx` 0.32.0, a version upstream's
own dependency pin (`mlx<0.32`) forbids — pip refuses the combination, brew
ships it silently, and the pin turned out to be load-bearing (FINDINGS #19
has the A/B). Neither failure was visible to a health check, a version
check, or a short completion.

So `setup.sh` builds a venv at `~/.rapid-mlx/venv` on a **uv-managed**
Python — an interpreter no other package manager can delete out from under
it — and lets `pip` resolve the mlx that upstream actually supports. The
test applied to any dependency here is **pins its own runtime, one obvious
location, one update command, clean removal**; a uv-managed venv passes all
four, which the earlier curl-installed venv (on Homebrew's `python@3.12`)
did not. The formula stays the destination *when* its dependency set
becomes upstream-legal — `setup.sh` refuses the measured-bad bottle by
version and prints the re-adoption path when a newer one lands. See
[docs/DECISIONS.md](docs/DECISIONS.md) for the full arc.

One thing this project does *not* install: **Pi itself**. Two traps found on the
machine this was built on, both worth checking on yours — neither is Pi's fault,
and both are invisible until you go looking:

- **Your agent may float on whatever Node is active.** If its launcher ends in
  `exec node …`, it runs on whichever version your manager has selected, so
  switching Node for a work project changes the runtime underneath your agent.
  A pnpm-generated launcher prefers a `node` sitting next to it, so
  `ln -s "$(which node)" ~/.pnpm/node` pins it without leaving `fnm`/`nvm`.
- **Your agent may be invisible to the package manager that installed it.**
  `pnpm setup` writes both a `PNPM_HOME` variable and a `PATH` entry. If only the
  `PATH` entry survives into your shell config — easy to do when tidying
  dotfiles — pnpm 10 falls back to its own default global directory, which is
  empty, while your binaries live in the old one. `pnpm list -g` then reports
  nothing and `pnpm update -g` silently updates nothing. Check that
  `pnpm root -g` prints the directory your agent is actually in.

## The manifest workflow

`models.yaml` is the single source of truth. Two files are generated from it and
should never be hand-edited:

- `~/.config/llama-swap/config.yaml`
- `~/.pi/agent/models.json` (only the `local` provider — other providers are
  preserved)

```
edit models.yaml  ──▶  ./setup.sh  ──▶  both configs regenerate
```

The service runs with `--watch-config`, so config changes are picked up live;
only a changed launchd plist triggers a restart. Replaced files get a
timestamped `.bak` beside them.

Retired and optional entries live in **`models.example.yaml`**, which `setup.sh`
never reads. To deploy one, copy its entry into `models.yaml` and re-run. This
keeps the live manifest readable as exactly what runs — no `enabled: false`
graveyard. (`enabled: false` does work, for temporarily parking something you're
mid-way through debugging.)

### Adding a model

1. Open `prompts/add-model.md`, fill in the model name, and give it to a
   frontier model with web access.
2. Paste the manifest entry it returns into `models.yaml`.
3. `./setup.sh`

The prompt asks for the things that actually go wrong: whether the repo exists
and is current, whether it fits in 32GB with a real KV cache, whether the chat
template is broken, which tool-call parser it needs, and a smoke test with
multi-turn tool history — which is where the models tried here fell over.

## Specialist models as tools

Small models are wired in as **typed functions behind Pi tools**, never as chat
models. They don't appear in Pi's model picker; the main model reaches them only
by calling a tool.

| Model | Kind | Group | Reached via |
|---|---|---|---|
| `rerank-qwen3-0.6b` | rerank | heavy (on demand) | `project_search` tool |
| `extract-nuextract3` | extract | heavy (on demand) | `extract_json` tool |
| `qwen3.5-4b` | vision | heavy (on demand) | screenshots, commit messages, small chores |

Three extensions ship in `pi-extensions/` and install to
`~/.pi/agent/extensions/`:

**`project-search.ts`** registers a `project_search` tool: a natural-language
query and optional search root in, the five most relevant project files with
line-numbered excerpts out. `rg` content hits and `fd` filename hits form a
bounded candidate set; the resident reranker orders it against the full query.
The extension rejects the known broken-GGUF signature (uniformly tiny scores)
instead of returning a plausible-looking bad ranking, and bounds/retries dense
excerpts around the server's 512-token physical batch. Queries, candidates and
top-five scores append to the private
`~/.pi/agent/project-search/queries.jsonl` log so real use can become the judged
evaluation set for the deferred alternatives. The path follows
`PI_CODING_AGENT_DIR`; set
`LOCAL_AI_PROJECT_SEARCH_LOG` to put that data elsewhere.

**`extract-json.ts`** registers an `extract_json` tool: text or a file path plus
a JSON Schema in, schema-valid JSON out. The request uses
`response_format: json_schema`, so llama.cpp compiles the schema into a grammar
and malformed JSON cannot be *generated* — there is no repair loop. It can
still be *truncated*: if the model hits the token limit the object is cut off
mid-way — a different failure, and one the extension reports separately: it
reads `finish_reason` and names the truncation (with the size produced)
instead of blaming the server's grammar support. Because the grammar only guarantees *shape*,
the extension also checks that string fields marked extractive actually occur
in the source text, which is intended to catch a confident hallucination —
that path is exercised by no test and has never met a real one. NuExtract wants
its own template dialect rather than JSON Schema; that conversion is internal,
so callers only ever see the stable tool contract.

**`compact-test-output.ts`** is the only thing shrinking command output in this
stack — the general-purpose compressors were measured and rejected, and nothing
sits upstream of it (see the A/B in [docs/DECISIONS.md](docs/DECISIONS.md)). It
fires on bash results over ~100 lines or ~8KB, and only when the command looks like a
test/lint/build runner or
the output parses as one of their formats — a large `cat` or `git log` is left
alone on purpose. Known formats (eslint/rubocop/rspec/jest JSON, junit/pytest
XML) get plain parsers; only unrecognized freeform output falls back to the
extraction model, and if that fails it degrades to head/tail truncation. The
full raw output is always written to a file and its path included in the
summary, so nothing is lost.

**Caveat, now a smaller one:** the parsers are covered by a unit harness
(`tests/compact-test-output.test.mjs`, offline check 17, 23 assertions) —
which found two shipping bugs on its first run — and the four known
gate/parser bugs are fixed: the gate trips on lines *or* bytes so one-line
jest/eslint JSON is reachable, and a bare `<testsuites>` header (pytest) no
longer lets a failing run summarize as `passed`. What is still true: none of
it has fired in production yet, so treat the first real-world summaries as
worth spot-checking against the saved full output.

Still deliberately **not** built: any embedding/vector index, and any router
or classifier — the main model routes by choosing tools. `project_search` is
the intentionally index-free first step; its query log determines whether the
heavier retrieval options ever earn their keep.

## Service

```bash
launchctl start com.llamaswap.server
launchctl stop  com.llamaswap.server        # stays down; allow time to unload models
launchctl bootout gui/$(id -u)/com.llamaswap.server   # keep it down until next login
launchctl list | grep llamaswap

open http://localhost:8080/ui               # per-request tokens/s, request inspector
curl -s localhost:8080/v1/models | jq '.data[].id'
curl -s localhost:8080/running | jq
curl -Ns localhost:8080/logs/stream         # proxy + engine logs, interleaved
curl -X POST localhost:8080/api/models/unload/<id>
```

Logs: `~/Library/Logs/llama-swap.log` and `llama-swap.error.log`.

The agent binds `127.0.0.1` only and throttles relaunches to 30s so a bad config
cannot spin-loop. `KeepAlive` restarts it on any non-zero exit, and launchd has
no way to tell a crash from a stop that ran out of time — so `ExitTimeOut` is
60s, which is what a stop needs to unload a resident model before it would be
SIGKILLed. At the 5s default the stop escalated to a kill and the service came
straight back.

## Performance

**The coder is pinned resident on purpose.** `qwen3.6-27b-mlx` sits in the
`pinned` group with `ttl: 0`, `persistent: true`, and a startup preload hook.
Its prompt cache lives in the server process and dies with it, and every Pi turn
resends the whole conversation — so an unload means the next turn re-prefills
from scratch. Never move it to a swap group to "save memory"; that trade is much
worse than it looks.

The group layout protects that:

| Group | swap | exclusive | persistent | Effect |
|---|---|---|---|---|
| `pinned` | false | false | true | The coder. Nothing can evict it. |
| `utilities` | false | false | true | Tiny always-on specialists, all resident together. |
| `heavy` | true | false | — | On-demand specialists that swap among *themselves* only. |

Loading a heavy model therefore never disturbs the coder or the utilities, and
heavy members evict *each other* by design. `tests/runtime.sh` check 10 asserts
residency matches what the manifest intends — since FINDINGS #16 the default
manifest keeps every specialist in `heavy`, so at 32GB `utilities` is empty and
a freshly loaded heavy member is expected to have swapped out the previous one.

### Two tweaks that need sudo

`setup.sh` detects both and prints the command; it never runs sudo. Each one is
a script in `scripts/` that works out what it needs from the machine it is on,
so the same command is correct on a different Mac:

```bash
scripts/gpu-wired-limit.sh --dry-run    # either script: show the plan, touch nothing
sudo scripts/gpu-wired-limit.sh
sudo scripts/log-rotation.sh
```

Both are safe to re-run — they converge and report `nothing to do`.

1. **GPU wired limit.** macOS caps GPU-wired memory well below physical RAM —
   about 21GB on a 32GB machine, which you can read with
   `sysctl iogpu.wired_limit_mb` — less than the pinned coder +
   utilities + one heavy model need together. The target is 75% of RAM, always
   leaving at least 8GB for the system, rounded down to a whole GB; on 32GB that
   is 24576 MB, which is the configuration every measurement was taken against.
   The value is written to `/etc/sysctl.conf` as a replacement, not an append,
   so repeated runs cannot stack contradicting lines.
2. **Log rotation.** `/etc/newsyslog.d/llama-swap.conf`. The `owner:group` field
   is mandatory for user-owned files — without it the rotated files are created
   root-owned and llama-swap can no longer write them. The owner is resolved
   from `SUDO_USER`, since under sudo `id -un` is root and would rotate the
   wrong account's logs. Note that launchd holds the log file descriptor, so
   writes only move to the fresh file after the next service restart or reboot.
   Fine on a laptop.

### Tuning the coder

Three flags in `models.yaml` look optional and are not. Briefly:

- **`--hybrid-cache-entries 8`** is what makes pinning the coder worth doing.
  Without it the prefix cache never engages and every turn re-prefills the whole
  conversation. With it, prefill drops to ~53 tokens from turn 3 onward and
  latency stops growing with history.
- **`--pflash off`** — the engine defaults this to `always` for Qwen3.6 and
  silently discards ~80% of any prompt over 32K tokens.
- **`--speculative-config ... "model": ...`** — the MTP sidecar is required. The
  model does not boot without it, despite `rapid-mlx info` claiming native MTP.

Measurements, evidence and the re-derivation commands live in
[docs/FINDINGS.md](docs/FINDINGS.md). Do not remove any of these flags on the
strength of the upstream docs — the docs are wrong about all three.


### Maintenance

```bash
hf cache ls                     # what's on disk
hf cache verify <repo>          # or ./setup.sh --verify
hf cache prune                  # deletes orphaned revisions — never run automatically
```

## What this actually feels like

Numbers from this machine, so you can decide before spending 20GB. Measured
2026-08-01, MacBook Air M5 / 32GB, Qwen3.6-27B at 4-bit
([docs/FINDINGS.md](docs/FINDINGS.md) #13):

- **~13 tokens/second** generated. Slower than a hosted frontier model by
  enough that you will feel it on long answers.
- **~180 tokens/second** of prefill, so a cold 14k-token context costs ~79
  seconds — but a *growing* conversation reuses its prefix and settles at
  ~2 seconds a turn.

Whether that trade is worth it is the whole argument in
[docs/DECISIONS.md](docs/DECISIONS.md): the fast option here was a 3B-active
MoE at several times the throughput, and it was retired because a wrong edit
costs more wall-clock than a slow right one.

## Two failure modes worth knowing before you hit them

- **A request can hang forever, and no timeout will save you.** Occasionally a
  streaming request emits its opening token and then nothing — while the server
  keeps generating. One captured instance ran **518 seconds**; it carried a
  300-second client timeout and outlived it, because the SSE keepalives mean
  the socket never goes idle, so no timeout at any layer can fire. Observed at
  roughly **3 in 7** on a long agentic corpus, filed upstream as
  [Rapid-MLX#1359](https://github.com/raullenchai/Rapid-MLX/issues/1359), and
  **fixed in rapid-mlx v0.11.9** (PR #1391) — though the hang *rate* has not
  been re-measured here since (and the fix's first release carried a worse
  regression, FINDINGS #15, so this setup installs ≥ 0.12.4 — check your
  version before trusting either).
  On an affected version you will need to kill the request externally. To
  confirm one, look for an assistant message opened and never closed — not at
  the clock — or grep the router log:

  ```bash
  grep -A1 'recovered from upstream disconnection' ~/Library/Logs/llama-swap.log
  ```

- **The coder will overwrite a script it was only asked to run.** On 2026-07-30
  it was asked to run this repo's test suite, wrote a fabricated Docker/Ollama
  suite over `tests/run-all.sh`, ran that instead, and reported a result from
  its own invention — reproduced 2 of 4 attempts, and in one it answered over
  an `isError=true` tool result as if the run had succeeded. The failure is
  *quiet*: the transcript reads normally, and only a `git status` afterwards
  shows the tree changed. **Never point a write-capable agent at a tree you
  have not committed.** Note that the permission-gate extension installed here
  watches `bash` only, and every destructive action observed during testing
  came through the `write` tool — the one door it does not watch.

## Troubleshooting

**Anything timing-related: check memory pressure first.** `sysctl vm.swapusage`.
A box that is swapping produces timings that mean nothing, and one acceptance
run here took 435 seconds instead of ~5 for exactly that reason.

**A model will not start.** llama-swap only ever reports `upstream command
exited prematurely`; the real error is in the engine's log:

```bash
curl -s localhost:8080/logs/stream/upstream | tail -40
```

Then reproduce it by hand — copy the `cmd` out of `curl -s localhost:8080/running`
and run it directly with `--port 10099`, where you get the error on your own
terminal instead of through the router.

**Reranking returns tiny scores for everything.** The GGUF is broken, not your
code — see the caveat below.

**Disk quietly filling up.** rapid-mlx parks up to **20 GiB** of KV checkpoints
in `~/.cache/rapid-mlx/kv_checkpoints` by default and sits at that ceiling.
It is a cap rather than a leak, so it grows back after any manual clean. This
setup already sizes it in the launchd plist — 10% of free disk, clamped to
[1, 20] GiB, never above upstream's own default — so you only need to act if
you want it lower still: override `RAPID_MLX_KV_CHECKPOINT_MAX_BYTES` there.

## Caveats

- **Rapid-MLX via its curl installer (pre-2026-08 setups only):** the installer
  decides "a venv already exists" from `[ -d ~/.rapid-mlx ]`, but it writes
  `telemetry-consent.yaml` into that directory *before* creating the venv, so
  an interrupted earlier run leaves a directory that makes the next attempt die
  under `set -e` with the real error swallowed. This setup never runs the curl
  installer (the venv is built by `uv`), but the trap is worth knowing if you
  ever do: the *engine itself* also drops state files (`session_seen`) into
  `~/.rapid-mlx` at runtime, so the installer's "a venv already exists" check
  can be fooled by a directory the engine created.
- **A brew-installed Pi cannot use `pi install npm:<ext>`** — there is no global
  npm root. File-copy extensions like the ones here work fine. If `pi` is
  already on PATH from pnpm or npm, the setup leaves it alone rather than
  installing a second copy from brew.
- **Re-downloading a model reverts its patches.** The next `./setup.sh` notices
  the hash mismatch and re-applies them automatically.
- **Patched files are fetched, not vendored.** A patch directory declares
  `<basename> <url>` in a `FETCH` file; setup pulls the current upstream copy
  into `.state/patch-cache/` and applies it from there. That keeps you on
  upstream's latest instead of a hash someone pinned months ago, and it keeps
  other people's licences out of this tree. The cost is that a patched file can
  now change between runs — so every fetch is hashed against the previous one
  and **a change is reported**, naming both hashes and the URL. If you see that
  note and the model then behaves oddly, suspect the file first. With no
  network, setup falls back to the cached copy; with neither, it `[fail]`s
  rather than quietly skipping. Nothing in the default manifest declares a
  patch, so none of this runs unless you opt in.
- **Most community Qwen3-Reranker GGUFs are broken with llama.cpp.** They are
  missing the `cls.output.weight` tensor and return degenerate scores (~1e-20)
  for every document, which still looks like a healthy HTTP 200. Only a
  conversion made with a current `convert_hf_to_gguf.py` works. If reranking
  returns uniformly tiny scores, the GGUF is wrong — swap the repo, don't ship
  it.
- **The generated `groups:` block lives under `routing.router.settings.groups`,**
  not at the top level. Both were accepted as of llama-swap v242 (the version
  this was measured on), but the top-level key is documented as legacy and a
  config may not use both styles.
- **`models.json` carries a `_generated_by` key** instead of a comment, since
  JSON has none. Only the `local` provider is generated; anything else in that
  file is preserved.
- **Pi's `defaultModel`** is not rewritten by the setup. If it points at a model
  you removed from the manifest, the setup says so and leaves the fix to you.

## Layout

```
setup.sh                 orchestrator: arg parsing, reporting helpers, summary
models.yaml              the manifest — single source of truth
models.example.yaml      retired/optional entries; NOT read by setup.sh
steps/
  10-tools.sh            brew CLI tools
  20-engines.sh          llama.cpp, hf, pi, llama-swap, Rapid-MLX, permission-gate
  30-models.sh           HF downloads, optional verify, fetch + apply patches
  40-configs.sh          generates llama-swap config.yaml and Pi models.json
  45-extensions.sh       installs pi-extensions/ into ~/.pi/agent/extensions/
  50-service.sh          launchd plist, health gate, the two [manual] tweaks
scripts/
  gpu-wired-limit.sh     the sysctl tweak; target derived from installed RAM
  log-rotation.sh        the newsyslog config, owned by the invoking user
pi-extensions/           extract-json.ts, compact-test-output.ts, project-search.ts
patches/                 files forced into model snapshots; FETCH names sources
tests/                   the suites below, plus the A/B harnesses — see
                         tests/README.md for what each one measures
templates/               the launchd plist template
prompts/add-model.md     research prompt for adding a model
docs/                    decisions, measured findings, plan, and dated reviews
.state/                  discovered paths and the patch cache (generated)
```

```bash
tests/run-all.sh              # offline, hermetic, ~30s
tests/run-all.sh --runtime    # needs llama-swap up
tests/run-all.sh --perf       # slow; restarts the service, loads 15GB
tests/cache-ab.sh <flags>     # A/B one engine flag before believing a doc
tests/engine-ab.sh            # the two-engine comparison behind FINDINGS #13
```

## Licence

[0BSD](LICENSE) — do whatever you want with it, no attribution required.

The one thing worth knowing: no third-party file is redistributed here. The
Qwen chat-template fix this stack can apply is Apache-2.0 and belongs to
[froggeric](https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates); it is
fetched at setup time rather than copied into the repo, which is why `patches/`
holds a URL and not a file.
