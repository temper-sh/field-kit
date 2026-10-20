# Field Kit

Find useful Qwen presets for your Mac: quality that holds up under review,
speed, memory use, cache behavior and a context size tested with real input.

An AI agent checks the hardware, chooses unanswered questions this Mac can
help with, and runs a bounded study. Larger Macs can fill engine and context
gaps left by the reference M5 / 32 GiB.

## Current experiments

| Experiment | Question | Requirements | Status |
|---|---|---|---|
| [Qwen hardware study](docs/experiments/qwen-hardware.md) | Which Qwen quant, engine and settings work best on this hardware? | Apple Silicon; Temper 0.3.1+ and hf; an AI coding agent; enough memory/disk for selected candidates; a grading Python environment. | Available for local validation. Complete hardware studies are still needed before new catalog claims. |

## Start with one message

```text
Help me find the maximum usable Qwen presets for this Mac. Use my Field Kit folder if I have one, or clone https://github.com/temper-sh/field-kit into ~/field-kit, then follow docs/ASSISTANT.md. Prioritize evidence missing from the catalog, plan useful extensions after healthy passes, investigate results that conflict with expectations, review coding quality during the study, and clean up owned resources afterwards.
```

The agent prepares candidates and explains the downloads, time and limits.
You approve the study once. It continues useful tuning within that scope after
healthy passes; different weights, engines or templates require another plan.
An exhausted test budget leaves the upper boundary open.

For the tester's M5 Pro / 64 GiB, the [Q4 campaign](docs/experiments/qwen-q4-64.md)
compares engines first, then assesses and tunes the strongest candidate.

The workflow is smoke → engine measurements → reviewed quality → diagnosis and tuning → final
confirmation. Quality assessments allow the model to test and repair its code.
Performance trials use one response, with a review for degradation; they do not
repeat the full quality run. A submitted patch does not count as successful coding work.

## What stays on your Mac

Every trial stops its engine. Study-owned downloads and software are reused
across trials and removed when the study closes. Your existing caches and
installations are preserved. Results, patches, reviews, exact configurations
and important failures stay available for inspection and optional sharing.
Nothing uploads automatically.

A short standalone check is also available:

```sh
./field-kit check --preview
./field-kit check
```

This defaults to a Qwen smoke test. The [run guide](docs/START.md) covers setup,
stopping, recovery and cleanup. The [measurement reference](docs/experiments/basic-check.md)
defines the evidence. [Development](docs/DEVELOPMENT.md) covers offline checks.

Licensed under [0BSD](LICENSE).
