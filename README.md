# Field Kit

Let your AI agent find which local-AI settings work best on your Mac, and send
back the evidence that improves [Temper](https://github.com/temper-sh/temper)'s
catalog.

Status: works on Apple Silicon Macs with Temper 0.3.0 or newer and an AI agent
that can run commands, such as Claude Code or Codex. A study needs the Mac to
itself for several hours, and it measures only what Temper can install.

## Why this is useful

A short speed test can miss what matters in real work. In one study on an M5 Mac
with 32 GiB, a 512-token test showed no difference between eight and four
threads for an experimental model. Over a long, realistic answer, eight threads
slowed from 39 to 11 tokens per second, while four held steady. With four
threads, the same code repair finished in 4 min 21 s instead of 8 min 32 s,
with the identical patch and the same memory.

Field Kit is how an agent finds results like that on your machine and proves
them. It runs each candidate under supervision and records speed, memory and
context use. It reviews the work the model produced and keeps every failure.
The agent decides what to try next. Field Kit makes sure the measurements are
exact and that nothing is left running.

## Run a study

You need git, Temper 0.3.0 or newer, and room for the model files the study
names; the agent shows their sizes before anything downloads.

1. **Get Field Kit.** Clone it onto the Mac's internal disk (any APFS disk
   works), because studies install their software inside this folder:

   ```sh
   git clone https://github.com/temper-sh/field-kit.git
   cd field-kit
   ./field-kit doctor
   ```

   The first command asks Temper to install Field Kit's own Python, about
   27 MB, once. `doctor` changes nothing; it reports Temper, your Mac and any
   running Temper layout.

2. **Start your agent in this folder and give it the study.** If you were sent
   a prepared study, name its brief:

   > Read AGENTS.md and docs/ASSISTANT.md, then run the study in
   > studies/splash-context-cache.md on this Mac. Reuse my existing weights
   > cache. Report every problem and repair, and tell me which archive to
   > return.

   To look for good settings on your own instead:

   > Help me find the best supported presets for this Mac using Field Kit.
   > Read AGENTS.md and docs/ASSISTANT.md, agree the scope with me, and
   > report every problem and repair.

3. **Approve once.** The agent shows the exact presets, downloads, disk space,
   time limit and the Temper layout Field Kit will stop. Nothing is downloaded
   or measured until you type `yes`.

4. **Leave the Mac to it.** Keep it on AC power and close other AI apps.
   **While Field Kit runs, do not use Temper.** The agent waits on long
   requests itself; you don't need to watch.

5. **Send back the archive.** At the end the agent closes the study and
   exports one `.zip` file. Send that file to whoever asked for the study.

If you ran an earlier Field Kit study from an archive, use a new clone for this
one, and keep the old folder until its study has been returned. Update with
`git pull` between studies, not during one.

## What to expect

- **Your Temper layout.** If one is running, Field Kit stops it before
  measuring and starts it again at the end.
- **Time.** Long-context requests can take 20–30 minutes each; a study usually
  runs for several hours within the time limit you approve.
- **Disk.** Model files go to your Hugging Face cache, or to a folder you name,
  such as an external SSD. Files you already have are reused and kept. At the
  end, the study removes only the files it downloaded, unless you chose to keep
  them, and removes the software it installed.
- **Safety stops.** Field Kit stops a measurement on swap growth, overheating
  or CPU throttling, and verifies that every process it started has exited.
- **Privacy.** Everything runs locally and nothing uploads. The archive holds
  the study's prompts, the models' answers, the measurements, and your Mac's
  model, chip, memory and macOS version. Look through it before you send it.
- **If something goes wrong.** Each study folder has a `restore.md` with the
  one command that returns your Mac to normal. The agent recovers from routine
  problems and records them in the archive.

## Learn more

- [How the agent conducts a study](docs/ASSISTANT.md)
- [The research method it follows](docs/METHOD.md)
- [Field Kit's commands and records](docs/TOOLS.md)
- [Working with Temper presets and locks](docs/TEMPER.md)
- [Recover from problems and report them](docs/REPAIR.md)
- [The bundled coding harness](harnesses/coding/README.md)
- [Prepared studies](studies/README.md)

## Development

Field Kit uses only Python's standard library, on its own pinned Python.
`./field-kit verify` checks the bundled inputs without a model, and
`python3 -m unittest discover` (Python 3.14) runs the tests. The
[development guide](docs/DEVELOPMENT.md) describes the design, and
[engine and model adapters](docs/ADAPTERS.md) explains how to add another
engine or model format.

Licensed under [0BSD](LICENSE).
