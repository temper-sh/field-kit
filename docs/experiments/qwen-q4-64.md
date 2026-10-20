# Qwen Q4 on the 64 GiB M5 Pro

Compare Qwen3.8 27B Q4 engine paths, then tune the strongest useful candidate.
Splash is the leading hypothesis, not the predetermined winner. This campaign
keeps Q4 fixed; Q5/Q6 and other models are separate studies.

The agent follows [ASSISTANT](../ASSISTANT.md) for discovery, consent, recovery
and review. This guide narrows its candidate and question selection.

## Prepare

Preserve earlier studies in their original folder. Recover and close any
unfinished study with its original Field Kit and Temper before starting.
Stop active Temper layouts before upgrading. This campaign requires the
released Temper 0.3.2 or newer for explicit Rapid MTP sidecar support:

```sh
brew update
brew upgrade temper-sh/temper/temper
temper version
```

For a first installation, follow [setup](../START.md#setup).
Extract the tester package into a new folder, then run:

```sh
./field-kit verify
./field-kit check --preview
```

Field Kit uses the released `temper` on `PATH` and records its exact version
and binary identity. Keep the same Temper and Field Kit throughout an open
study. The tester archive contains no Temper binary.

Verify the host is the intended M5 Pro / 64 GiB. Read available disk, wired
memory allowance, power and thermal state. Use the existing grading Python,
or follow [setup](../START.md#setup). The tester archive includes the four
prepared files in `trials/qwen-q4-64/`. When starting from source, prepare them:

```sh
./field-kit study prepare-q4 --out trials/qwen-q4-64
```

This resolves signed catalog software, exact GGUF records and pinned MLX
metadata through Temper, then compiles all four compositions for 64 GiB.
It downloads small metadata files; it installs no model weights or engines.
The resulting files remain experiment inputs until measured and reviewed.

| Candidate | Target / template | Initial acceleration |
|---|---|---|
| `q4-64-llama` | Catalog Q4_K_XL GGUF / Frog | Embedded MTP, 3 proposals |
| `q4-64-splash` | The same GGUF and Frog bytes | Catalog DFlash2 sidecar |
| `q4-64-rapid` | MLX affine 4-bit, group 64 / packaged template | Explicit pinned Qwen MTP sidecar, 3 proposals |
| `q4-64-vllm` | The same MLX target and template bytes | Plain decoding |

Both pairs share target bytes within their format. GGUF Q4_K_XL and MLX
affine Q4 are different conversions; template and cache implementations also
differ across the pairs. This is a deployment comparison. Attribute acceleration
gains only to matched on/off trials on the same engine and material.

All candidates start with a 65,536-token declared window and thinking enabled.
Splash gets a 48 GiB engine cap; machine admission and observed resource guards
still apply. Splash and Rapid request timeouts are 14,400 seconds, matching the
proposed runner ceiling below. A smaller budget requires aligned deadlines.
Rapid's current adapter requires an explicit output default; its 65,536-token
default is not a measured output capacity. Revisit it with the context window,
check effective server context, and leave input/output headroom.

## Why the Rapid material matters

[Rapid's published Qwen setup](https://github.com/raullenchai/Rapid-MLX/blob/v0.16.3/docs/benchmarks/recent-large-models-m3-ultra.md)
uses a package with the MTP head. The older `mlx-community/Qwen3.8-27B-4bit`
snapshot contains target weights without that head. Its config's
`mtp_num_hidden_layers` field does not prove the tensors exist.

The preparer pins
`rapid-mlx/Qwen3.8-27B-4bit-MTP-MLX@aa985c29ff5b334cbfdcbbc787d47e66e9d9e456`.
Its three target shard hashes match
`mlx-community/Qwen3.8-27B-4bit@10c35caafbb80f7dc6a7a432cdd11af10a6d4818`.
The target and `mtp/model.safetensors` become independent locked artifacts.
[Rapid 0.16.3's loader](https://github.com/raullenchai/Rapid-MLX/blob/v0.16.3/rapid_mlx/spec_decode/mtp/qwen3_5_inject.py)
accepts that sidecar file explicitly. Temper supplies its verified local path
in `--speculative-config`; it does not rely on alias auto-selection.

That establishes an intended compatible path, not a speedup on this Mac.
Verify successful head injection and the active decoding path in startup logs
and request evidence. If it falls back, record fallback and diagnose it before
making an MTP claim. Upstream Studio measurements do not predict M5 Pro speed.

## Plan once on the tester's machine

The following is a starting exploration budget, not a capacity claim. Adjust
it to the owner's available time/disk and the actual plan before approval.
It includes engine comparison, repeats, quality, useful extensions and cleanup.

```sh
./field-kit study plan \
  --preset-file trials/qwen-q4-64/q4-64-llama.json \
  --preset-file trials/qwen-q4-64/q4-64-splash.json \
  --preset-file trials/qwen-q4-64/q4-64-rapid.json \
  --preset-file trials/qwen-q4-64/q4-64-vllm.json \
  --python .local/grading/bin/python \
  --max-runs 32 --hours 36 --download-gib 120 \
  --max-context 262144 --request-seconds 14400 \
  --quality-turns 8 --quality-seconds 14400 \
  --out trials/qwen-q4-64-study.json
./field-kit study start qwen-q4-64 --plan trials/qwen-q4-64-study.json
```

The machine owner approves this exact plan before downloads and inference.
An admission refusal is a recorded limitation to diagnose, not permission to
drop the engine silently or replace Qwen with another model. If only a subset
is executable, name the reason and build the actual plan from that subset.

## Screen engines before expensive quality work

For each admitted candidate, run smoke, inspect the outcome, then run the
matched engine screen. Run candidates serially. Example for Rapid:

```sh
./field-kit study run qwen-q4-64 --candidate q4-64-rapid \
  --workload smoke --output-tokens 2048 \
  --reason "Q4 Rapid MTP load, tool parser and memory on M5 Pro 64 GiB" --background
./field-kit study run qwen-q4-64 --candidate q4-64-rapid \
  --workload engine --records 768 --output-tokens 4096 \
  --reason "Matched short and populated input, before quality selection" --background
```

Wait for completion and inspect `study status` between commands. The cheap
tool smoke has a 300-second request ceiling; the engine screen has 900 seconds
per request. Check every engine before spending the budget on full coding work.

The engine screen asks for the same 32 service identifiers at short and
populated input lengths. Each request starts a fresh engine, preventing prefix
reuse between the two cases. The oracle accepts the correct JSON array, with
normal JSON whitespace variation. Wrong, truncated or incomplete responses
cannot win. A failure may mean retrieval, reasoning, output allowance, parser
or runtime trouble; it is not automatically bad model quality.

Compare correctness, request wall time, first-token latency, actual input/output
tokens, native prefill/decode counters when present, startup time, and **loaded
and peak system wired growth in GiB**. Keep Splash's native memory counters
separate. Wired growth includes other applications; process footprint may miss
GPU allocations. Missing counters remain missing. Reasoning and output lengths
can differ, so wall time is time to this answer, not pure kernel throughput.
These synthetic results do not establish coding quality or maximum context.

Also create a Rapid variant with speculation set to
`{"method":"none","source":"none","max_draft_tokens":0}` and run the same engine
screen with `--preset-file`. Keep the artifact records in the user file;
Temper omits the unused draft from its execution closure. Field Kit allows
that omission within approved material. This is the matched MTP/plain pair.

Repeat the leading candidates in reversed order when the margin is small,
output lengths differ materially or memory/thermal conditions drift. Investigate
unexpected losses or gains using the [diagnosis procedure](../ASSISTANT.md#research-unexpected-results).
For MTP, check actual activation, acceptance, draft depth and bf16 cache before
concluding that the technique is unhelpful. Try useful smaller/larger depths
within the approved budget when the evidence warrants it.

## Assess the leader, then tune it

Choose a provisional leader from correct, comparable engine results and memory
fit. Run `--workload coding --coding-mode quality --purpose screen` on that
candidate. The model gets visible tests and bounded repairs. Run the final
independent tests and source review immediately using the
[review commands](basic-check.md#quality-review). Include the candidate's tests
and regressions in review; a submitted patch alone is insufficient.

If the candidate cannot qualify, diagnose a specific cause or assess the runner-up.
Preserve first attempts. Do not label all four engines poor coders because one
configuration times out or spends its allowance reasoning.

Once quality qualifies:

1. Establish a matched one-shot coding performance reference. Use performance
   mode for speed tuning and review each answer for degradation. Full quality
   runs are for assessment or reassessment, not every performance point.
2. Tune promising acceleration, batching and cache choices one mechanism at
   a time. Measure cold, appended-history and changed-prefix cache behavior;
   report correctness as well as latency and memory.
3. Increase actual input and context after healthy passes. Bracket a real
   memory/latency/correctness boundary, then refine the useful range. Keep
   output and continuation room and check effective settings. A nominal
   262,144 ceiling is neither achieved capacity nor an obligation to reach it.
4. Confirm the chosen composition on unused `template-decorators` work with
   `--purpose confirm --coding-mode quality` and final independent review.
   Complete tuning first; consumed confirmation work is no longer unseen.

Follow [continuation toward useful limits](qwen-hardware.md#continue-toward-useful-limits).
Context/cache probes measure lookup and continuation at recorded token counts.
Their small answers do not prove a large generated-output reserve or
long-context coding quality. Name those limits in the recommendation; retain
an open boundary when time or suitable workload evidence runs out.

## Return the complete study

Write `studies/qwen-q4-64/conclusions.md` with the engine comparison, selected
run/lock and exact settings; first-attempt and completed coding quality; time,
loaded/peak memory and workload sizes; measured context/cache scope; blocked
paths, anomalies and remaining boundaries. Include the initial engine results
even when only one candidate gets quality assessment.

```sh
./field-kit study close qwen-q4-64
./field-kit study export qwen-q4-64 --out qwen-q4-64-results.zip
```

Close stops and removes study-owned runtime material after verified shutdown,
preserving existing caches. Export includes every referenced run, exact lock,
requests/responses, patches and existing reviews, and refuses missing run/case
evidence. Failed attempts and missing reviews remain visible. It excludes model
caches and reconstructible source copies. Review the archive's hardware details
and local paths before sharing; nothing uploads automatically.
