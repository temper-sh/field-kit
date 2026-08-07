# field-kit

You were sent this because you have a Mac and a friend who wants to know
whether their local-LLM setup works on hardware that isn't theirs. This kit
installs that setup on your machine, checks that it actually works, measures
what your hardware can hold, and produces **one text block you paste back**.
Nothing phones home; the report is a file you read before you send it.

At the end you choose (it asks up front): **keep** the working stack, or have
everything the probe added **removed** — it records what was already on your
machine before touching anything, and the restore path removes only what it
added.

## Before anything: the 10-second check

`machine-report.sh` in this repo installs nothing, writes nothing, and needs
no clone — grab the one file and run it:

```bash
curl -fsSLO https://raw.githubusercontent.com/temper-sh/field-kit/master/machine-report.sh
bash machine-report.sh
```

Send the block it prints back either way. If it reports *below the 32GB
minimum*, stop here; the full probe has nothing to measure yet.

## Requirements

- Apple Silicon Mac, macOS, [Homebrew](https://brew.sh) installed
- ~25GB free disk (weights are ~20GB)
- A free afternoon: the big download is ~15GB, and the measurement stages
  want the machine otherwise idle — a browserful of tabs pollutes the numbers

## Run it

```bash
git clone https://github.com/temper-sh/field-kit.git && cd field-kit
FIELD_KIT_STACK_REPO=<the stack's git URL, or a path to a copy> ./probe.sh run
```

The stack repo isn't public yet, so `FIELD_KIT_STACK_REPO` is whatever your
friend gives you — a private URL, or a folder copied onto a drive alongside
the weights.

`probe.sh run` walks the stages below, stating each cost and asking before
proceeding. Every stage is also its own subcommand (`./probe.sh verify`), so
an interrupted probe resumes where it stopped.

| stage | what happens | cost |
|---|---|---|
| preflight | machine facts, disk/swap/port checks | seconds, installs nothing |
| install | the stack's real installer, three times (dry-run, real, repeat) | ~15GB download, ~20GB disk |
| verify | 12 live checks incl. streaming tool calls, plus a timing probe | ~4GB more, 10–20 min |
| fit | long-context soak watching for engine crashes | ~20 min, machine busy |
| perf | throughput measurements | 20–30 min, machine must be idle |
| cleanup | your keep-or-restore choice, executed | — |

`./probe.sh perf --ab` adds an optional second-engine comparison: an extra
~16GB download and 1–2 hours. Only say yes if you mean it.

## Skipping the big download

If your friend hands you the weights on a drive, copy the model folders
(directories named `models--org--name`) into `~/.cache/huggingface/hub/`
before running install. The installer detects them and skips the download —
the report notes that the weights were seeded so timings aren't misread.

## Letting an AI agent drive

If you use Claude Code or a similar agent CLI, you can hand it the wheel:

> Read AGENT.md and run the probe.

The agent handles environment problems and narrates progress; the decisions
that cost you something (the downloads, keep-or-restore, anything `sudo`)
stay yours — it is instructed to ask, and the kit refuses to proceed without
your answer either way.

## What lands on the machine

Everything the stack installs, where it lives, and how it is removed is the
stack repo's README (`stack/README.md` after fetch, section "Where it all
lands"). The restore path runs its `scripts/uninstall.sh` with the
provenance file this kit records before installing anything. Two optional
`sudo` tweaks are never run by the kit — they are printed for you to read
and decide.

## The report

Everything accumulates in `probe-results/report.md` — machine facts, the
three install summaries, check results, measurements (each labeled with the
GPU wired-limit state and swap level it ran under), crashes if any, and
every deviation from the script. `./probe.sh report` prints it. Read it,
then paste the whole block back to your friend.
