# Field Kit

Find useful local-AI presets for your Mac and return evidence that improves
Temper's catalog.

Your agent researches options, measures promising configurations, reviews their
work and follows the evidence toward useful quality, speed, memory and context
tradeoffs. Field Kit supplies supervised execution, measurements and recovery.

The runner supports Apple Silicon, installed Temper 0.3.0+ and Python 3.9+.
This rebuild has offline verification; native execution still needs an approved
trial. Earlier studies keep their original runtime.

## Start with your agent

> Help me find the best supported presets for this Mac using Field Kit.
> Read AGENTS.md and docs/ASSISTANT.md. Use my installed Temper unless I
> specify another version. Start from relevant catalog evidence, agree the
> scope, investigate promising candidates, recover from tool problems and
> include problems and repairs in the return.

For a first read-only check:

```sh
./field-kit doctor
```

The agent prepares a brief and exact candidates. You approve material and
resource limits once; the agent chooses useful trials within that scope.
New studies usually need prose and workload inputs rather than another runner.

## What comes back

Measurements retain exact configurations, hardware, request sizes, native rates
where available, elapsed time, loaded/peak memory and limitations. The agent
adds comparative quality review, chosen presets, open questions and reports of
defects encountered, including successful local repairs.

Results owns maintained public comparisons. Temper owns the installable catalog.
Field Kit returns evidence and proposals. Sharing and publication are explicit.

Study-owned software and weights are reused across trials. Close removes them
after verified shutdown, retaining evidence and repair reports. Existing user
installations are preserved. Nothing uploads automatically.

- [Guide the investigation](docs/ASSISTANT.md)
- [Research method](docs/METHOD.md)
- [Temper operations](docs/TEMPER.md)
- [Field Kit tools](docs/TOOLS.md)
- [Recover and report problems](docs/REPAIR.md)
- [Studies](studies/README.md)
- [Development](docs/DEVELOPMENT.md)

Licensed under [0BSD](LICENSE).
