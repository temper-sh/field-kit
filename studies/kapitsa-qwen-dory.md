# Kapitsa on M5: Qwen comparison and Dory revisit

**Study complete.** Read the [assessment and measured tradeoffs](../.work/studies/kapitsa-qwen-dory-2026-10-23/conclusions.md)
and [execution brief](../.work/studies/kapitsa-qwen-dory-2026-10-23/brief.md).
The selected Qwen profile improves completed Flask work and generated-history
reuse, with a tradeoff in large cold prefill and long-context peak memory.
Dory's speed tuning does not resolve the tested source/coding failures.

Both studies are closed with cleanup proof; Default remains stopped and staged
weights are retained. The local evidence exports are the
[main study](../.work/kapitsa-qwen-dory-2026-10-23-results.zip) and
[fidelity/control study](../.work/kapitsa-dory-fidelity-2026-10-23-results.zip).
No default, catalog, service integration or publication changed.

## Retained preparation protocol

The following is the pre-execution protocol. Preparation-only observations,
proposed effects and allocations below describe that original state; current
outcomes and executed decisions are owned by the linked completed assessment.

The owner requested an overnight study, beginning with Qwen versus Splash,
tuning when justified, and revisiting Nemotron Dory. This brief selects the
material, questions and proposed overnight allocation. Starting inference,
downloading weights and temporarily stopping the running layout remain the
execution step. This is an agent-led Field Kit study, not a new unattended
runner or a Temper integration project.

Preparation checks: `./field-kit verify` passed; all 140 offline tests passed
under Field Kit's pinned Python 3.14.7. The Splash lock matches its source
byte-for-byte and passes installed Temper's `execution inspect`. The fresh
audit workload validates and its four expected states match local execution.
No model inference, model-weight download, study initialization or layout
change occurred. Exact Kapitsa file declarations/preview require the staged
release and weights; native compatibility remains an execution preflight.

Decisions to make:

1. Does Kapitsa offer a useful Qwen3.8-27B composition against the existing
   Splash composition on this machine, considering completed work, latency,
   memory and continued context use?
2. If promising or close, which supported controls improve it, and why?
3. Does Dory on Kapitsa change the earlier small-helper assessment, especially
   its quiet-wrong source audit, reasoning cost and context behavior?

## Established evidence

The preparation-time `./field-kit doctor` reports Apple M5, 32 GiB, macOS
26.6.1 / 25G76, a 24 GiB Metal/wired limit, installed Temper 0.3.2, Field Kit
Python 3.14.7 and an available Flask grading environment. The **Default**
layout is running at `127.0.0.1:8080`. Recheck these facts at execution.
The internal APFS volume has about 63 GiB free; the attached Crucial X10 has
about 575 GiB free. Store new weights on that SSD and runtime/state on APFS.

- [Splash 1.4.0 investigation](../../labs/workstreams/model-runtime-optimization/method/splash-1.4.0-qwen27-m5.md):
  adoption remains on hold; **1.3.2 is the comparison baseline**. The matched
  release comparison did not establish a speed improvement. Its 1.3.2 batch
  loaded/peaked at 19.47/22.10 GiB engine physical footprint. The separate
  tuned 1.4.0 98k configuration demonstrated 91,842 input, 4,096 generated
  tokens and continuation at 19.62/22.91 GiB footprint. Do not transfer that
  capacity or those measurements to 1.3.2 or Kapitsa.
- [Latest Qwen/Flint/Muse comparison](../.work/studies/flint-qwen-muse-2026-10-22/conclusions.md):
  Qwen/Splash 1.3.2 at 112,000 context with thinking off completed pagination
  repair in 2m05s, at 18.87/19.61 GiB loaded/peak **system wired growth**.
  Its Flask async submission failed; the decorators implementation had a
  review defect and no final submission. Medium thinking timed out on async
  without a complete proposal. These are scoped outcomes, not proof that
  thinking off is generally better. The prepared
  [Splash lock](kapitsa-qwen-dory/splash-baseline.lock.json) preserves the exact
  thinking-off composition from run `20261022T030216692393Z`.
- [Dory pilot](../../labs/workstreams/model-runtime-optimization/method/dory-low-memory-m5.md):
  two-loop, per-loop KV, community MLX affine 4-bit. Thinking off gave a
  correct atomic repair in 3.36s and holiday audit in 7.94s, but a materially
  wrong `python-dotenv` audit in 14.40s. That short residency loaded/peaked at
  2.23/4.20 GiB process physical footprint. The corrected-format 31.6k lookup
  passed in 75.76s, peaking at 6.00 GiB. Reasoning-on 4,096-token stops were
  output-control stops; an 8,192-token diagnostic completed. No general coding
  reliability or actual 8/16 GiB machine fit was established.

Wired growth, process footprint and native allocations overlap and have
different blind spots. These historical rows are context, not a matched
memory ranking. New attempts measure all applicable counters together.

## Exact material and expectations

Pin **Kapitsa 0.2.9**, tag commit
`e86dea79b2751d04a3fa78bfa7746f4ebf981515`. The
[arm64 release](https://github.com/nace-ai/kapitsa/releases/tag/v0.2.9) archive
is `kapitsa-0.2.9-darwin-arm64.tar.gz`, 3,501,620 bytes, SHA-256
`370b2ec86b0d27fcb6cf9e19a563e30174e89146889bdf3adc1c3c5d01898d19`.
Do not follow moving `main` during the run. Its preparation-time head was
`e6e1f46a52536a4ad9b23e6b024a7891f5d92a7d`; the later 35B prefill change is
not a reason to move this 27B/Dory study off the release.

| Candidate/material | Frozen identity | Purpose |
| --- | --- | --- |
| Splash baseline | Prepared lock above: Splash 1.3.2, Unsloth UD-Q4_K_XL, Frog v22.5, Inco DFlash2, int8 KV, 24 GiB cap, 112,000 context, thinking off | Current practical control; retain the exact lock |
| Kapitsa Qwen target | `lmstudio-community/Qwen3.8-27B-MLX-4bit@6067b15cf581666a4aecf6af3afaba4bb5efc20c` | Upstream native affine 4-bit/group-64 target; start on the optimized supported path |
| Qwen draft | `incoai/Qwen3.8-27B-DFlash2@015e795645c74b1a0eeef3b570031fb62e769bc5` | Same source draft as Splash; reuse exact cached bytes |
| Kapitsa Dory | `nvidia/NVIDIA-Nemotron-Labs-Dory1.0-4B-NVFP4@41f02335e1a1ad3289b8b32485e2265cbe97cfdf` | Primary revisit, NVIDIA NVFP4 weights |
| Dory fidelity diagnostic | `nvidia/NVIDIA-Nemotron-Labs-Dory1.0-4B-BF16@b366b87f762ea334ba3eb55912031baba1515810` | Conditional download/run if a quality failure or precision question could change the conclusion |
| Small helper control | Existing `mlx-community/Qwen3.5-4B-4bit@0e7ffd5c629ef7719d4cbc04069232580bfa9d9c` | Optional matched helper check on Kapitsa, plain decoding, no new drafter |

The old Dory affine-4-bit checkpoint and experimental MLX port stay historical
controls. Kapitsa's Dory path is BF16/NVFP4; do not relabel that old checkpoint
as an equivalent Kapitsa run or automatically rebuild its old runtime.

Source model/draft file totals checked through Hugging Face metadata are
16,081,498,220 bytes for native Qwen, 3,848,819,135 for the draft,
3,219,766,623 for Dory NVFP4 and 7,967,783,317 for conditional BF16. These
are download sizes, **not runtime memory**. Combined they are about 29 GiB
before reuse. Proposed allowance: **32 GiB new downloads**, including up to
2 GiB software/metadata headroom, with existing GGUF/Frog/draft files reused.
Download only the exact needed files; do not use model aliases that also fetch
an unneeded MTP head or silently select another quant. New weights are kept
under `/Volumes/Crucial X10/temper-research`; no existing weights are removed.

Primary-source findings at the pinned revision:

- [Usage and controls](https://github.com/nace-ai/kapitsa/blob/e86dea79b2751d04a3fa78bfa7746f4ebf981515/docs/usage.md)
  document M5-specific prefill paths, KFlash, context, disk caching and Dory
  loop semantics. [KFlash's mechanism](https://github.com/nace-ai/kapitsa/blob/e86dea79b2751d04a3fa78bfa7746f4ebf981515/docs/kflash.md)
  motivates testing dense-Qwen draft blocks 8 versus 16. A one-slot M5 server
  can select 16 automatically; record the effective path, not just the flag.
- [Benchmark conditions](https://github.com/nace-ai/kapitsa/blob/e86dea79b2751d04a3fa78bfa7746f4ebf981515/docs/benchmarks.md)
  use different machines, quantizations and often temperature 0.6. Their
  speed claims are hypotheses here, not evidence on this base M5/32 GiB.
- [Catalog source](https://github.com/nace-ai/kapitsa/blob/e86dea79b2751d04a3fa78bfa7746f4ebf981515/src/hub/catalog.cpp)
  pins the repositories above. Native Qwen is different from Splash's
  Unsloth mixed GGUF. Importing the same GGUF also converts several tensor
  formats; it does not create an equal-arithmetic engine control.
- Dory defaults to **two loops, last-loop KV**. The prior pilot used per-loop
  KV. These are different computational semantics, particularly across prefill
  chunks. Per-loop mode additionally permits self-speculation. Visible thinking,
  loop count, KV mode and speculation must be controlled separately.
- Dory's default `force_nonempty_content` can copy unfinished reasoning into
  content. Set it false and inspect finish reason and actual final answers.
  Requests must set `loops` explicitly: effort mappings otherwise change it.

The first Qwen result therefore answers **which usable composition is better**.
If attribution could change selection, run the same native MLX target, draft,
template treatment and requests through Splash as a diagnostic. A matched
engine claim additionally needs verified rendering/tokenization and disclosed
KV/placement differences. An imported-GGUF comparison is an optional conversion
diagnostic, not a required third arm. Do not spend the Dory allocation chasing
perfect cross-engine equivalence.

## Allocation and controls

Proposed allocation: **eight hours wall time from execution authorization,
at most six hours native residency**, including warmups, failed starts and
diagnostics. Preparation downloads count against wall time. This request has
not scheduled a start time or authorized a running overnight job.

| Wall-time allocation | Work |
| --- | --- |
| First hour | Fetch/verify selected material, declarations and exact preview; check interfaces and cleanup |
| Next hour | Qwen baseline/candidate screening and initial completed-work check |
| Next 90 minutes | Conditional Qwen tuning, context and cache investigation |
| Next two hours, protected | Dory revisit and discriminating diagnostics |
| Next 90 minutes, protected | Freeze finalists; matched confirmation and completed work |
| Final hour | Review, conclusions, cleanup, restoration and export |

Switch from Qwen tuning to Dory by 3h30m wall time. When preparation overruns,
cut optional Qwen diagnostics first. A useful conclusion, unsupported interface
or resource boundary can finish an arm early. Keep a hard overall deadline
even if separate material sessions are needed; a new session does not renew
the allocation. No arbitrary trial-count quota or exhaustive parameter grid.

- Fixed within each matched batch: exact model/draft/template bytes, sampling,
  request/history, concurrency one, cache state, output allowance and hardware
  power conditions. For Qwen begin with temperature 1, top-p .95, top-k 20,
  seed 421 and thinking off, matching the latest retained coding control.
  Medium thinking is a separate paired treatment if it changes usable quality.
  Dory historical replays use their original seed 17, temperature 1, top-p .95,
  top-k 0. Cross-engine equal seeds do not guarantee equal random samples.
- Bounded: context through the documented 262,144-token model limit, always
  subject to memory and time guards. Start short screening at a 65,536
  allocation and compare actual short and approximately 16k inputs; retain a
  baseline run at its 112,000 allocation as the deployment control. Matched
  allocation variants are separate observations.
- Tunable Qwen: KFlash block 8/16/auto, speculation on/off, prefill chunk,
  useful context, then disk-cache policy. Dory: per-loop/last-loop KV,
  self-speculation, 1/2/4 loops and visible thinking. No change of system wired
  limits, unrelated services, hidden quant fallback or private input export.
- Per request: short smoke 120s; ordinary work 900s; long-context or coding
  requests up to 2,700s. Set each batch's actual deadline prospectively from
  measured prefill plus generated output plus margin. Do not lengthen a running
  request or call a deadline a model limit. Outer trial maximum 3,600s.
  Align frontend and harness timeouts too: the copied Splash lock has an
  1,800s request timeout. A longer diagnostic needs a separately frozen
  supported variant; a 2,700s client timeout alone does not extend the server.
- Coding: pagination 900s/six responses; a selected Flask case 1,800s/six
  responses plus final grading. Reuse qualified prior quality evidence during
  tuning. Do not force a long Flask task onto Dory before small work warrants it.

Use Field Kit's unchanged five-second watcher: engine RSS/footprint up to
28 GiB, system wired growth up to 24 GiB on these facts, swap growth 512 MiB,
and thermal/CPU-throttle stops. Record pressure alongside these counters.
Stop on observed non-normal memory pressure; do not claim the current watcher
automatically enforces a pressure-level guard it does not implement. Keep
missing/unavailable pressure samples explicit. Do not retry a resource failure
unchanged or relax guards to complete a point.

## Prepare execution through Field Kit

Use installed Temper for Splash. Kapitsa uses the existing local-engine route
with `kapitsa/v1` and `openai/v1`. Its new observation adapter supplies `/health`,
standard usage/cache counts and common process/system memory monitoring.
Native prefill/decode rates are absent from the 0.2.9 chat response: retain
missing values unless unambiguously matched native logs supply them. First
token latency is not prefill time. Software fixture checks do not establish
native serving, fit or quality.

1. Read `AGENTS.md`, `docs/ASSISTANT.md`, this brief and any existing study
   state. Re-run `./field-kit doctor`. Use AC power, record low-power/thermal
   state, and avoid other model workloads. The execution scope includes only
   stopping/restoring the named **Default** layout if it is still the running
   layout. A changed layout needs its own explicit authorization.
2. Stage the verified release under study-owned APFS storage; do not install a
   Homebrew service. Set `KAPITSA_NO_AUTO_UPDATE=1`, `KAPITSA_NO_PROMPT=1` and
   a study-owned `KAPITSA_HOME` for every command. Pre-fetch pinned files before
   serving. Disable disk cache initially and leave `--allow-switch` absent.
   Give each server an explicit local model directory, draft directory and
   `--model-id` matching Field Kit's candidate ID. No client-triggered download
   or model switch belongs in a measured attempt.
3. Build local declarations after files exist. Their `files` map must enumerate
   every consumed target/draft shard, index, config, tokenizer and template;
   directory arguments alone do not freeze contents. The executable directory
   must contain its required runtime libraries. Record the release checksum,
   source revision and software digest. Keep writable KFlash calibration and
   prefix caches outside those frozen model/software directories. Record whether
   calibration is fresh or reused and exclude calibration warmup from matched
   steady-state timing while reporting startup separately.
4. Qwen launch arguments begin with `serve --model ABS_TARGET --model-id ID
   --host 127.0.0.1 --port {port} --slots 1 --context 65536 --mem-budget 24
   --dflash ABS_DRAFT --disk-cache 0`. Confirm the logged byte budget and actual
   acceleration; the flag is not proof of a 24 GiB allocation. Plain decoding
   requires **both** `--dflash none --spec 0`.
5. Dory begins with `serve --model ABS_DORY --model-id ID --host 127.0.0.1
   --port {port} --slots 1 --context 65536 --mem-budget 12 --loops 2
   --loop-kv per-loop --spec 0 --disk-cache 0`. This 12-unit engine allowance
   is a study choice for helper work, not a minimum-RAM claim. Record actual
   units/bytes. Every Dory request sets `loops: 2` and
   `chat_template_kwargs: {enable_thinking: false, force_nonempty_content: false}`
   for the initial arm; change one control explicitly in later arms.
6. Freeze model declarations and the Splash lock, then use `study init
   --preview` with eight hours, 32 GiB planned downloads, maximum context
   262144, request limit 2700s, trial limit 3600s and weights cleanup `keep`.
   Local declarations need existing files, so material staging precedes their
   hash-based preview. The brief's external weight/software downloads must be
   counted explicitly: Field Kit's local-engine preview is not their installer
   or a cumulative network meter. Initialize only within the owner's execution
   authorization; preserve its original wall deadline after staging.
7. Verify streamed content/reasoning, a nested multiline tool call and actual
   tool-result continuation, loop reporting, loaded model ID, usage and
   shutdown. Verify unsupported request fields rather than assuming llama.cpp
   controls work. Kapitsa lacks the harness's `/apply-template` and `/tokenize`
   routes: use its existing `--output-tokens` mode, initially 16,384 per coding
   response, and use the same cap on the paired Splash run. Confirm actual
   history plus output fits. If that cap truncates useful work, preserve the
   first attempt and prospectively allocate a larger diagnostic; it is not a
   product output limit. No pretend tokenizer endpoint or silently clipped
   history is acceptable.

Record any local adapter/harness repair through `./field-kit repair` with its
patch and decisive reproduction. Native interface checks remain unrun at
preparation. Do not use Kapitsa's broad `bench compare` as the primary runner:
its selected weights, templates and task are not this comparison.

## Qwen: compare first, tune conditionally

1. Run the same cheap short/populated engine screen, tool round trip and
   pagination repair on the saved Splash baseline and native Kapitsa Qwen.
   The first screen uses fresh residency/cache, equal request controls and
   real answers. Record source correctness and interface validity separately.
   Use the existing `workload engine`, `workload tools`, `workload cache` and
   `workloads/coding-pagination-repair.json` tools, freezing inputs before output.
2. Locate the cost: loading, prefill, output length, generation and repeated
   history. Advance correct promising candidates **and near ties**. If Kapitsa
   is slower or worse, check effective draft path, quant/template differences,
   memory fit, calibration and output length before selecting or rejecting it.
   A faster incorrect answer does not justify tuning for adoption; independent
   resource/cache questions may still be worth answering.
3. Test at most the next useful mechanisms: block 8 versus 16/auto at the same
   one-slot allocation; then plain versus the better speculative setting;
   prefill chunks 512/1024/2048 only if prefill is material. Freeze a reason and
   stop rule for each pair. `NI_DFLASH_BLOCK` is an engine environment control.
   Do not add unverified switches or measure a shortened response as an
   equal-work decode improvement. Use the same realistic prompt for timing;
   a fixed-count stressor requires verified engine support for ignoring EOS.
4. Extend useful context after healthy passes: approximately 32k, 64k, 96k,
   120k occupied input, then larger only if time and resources justify it.
   Actual tokenizer counts own occupancy. At the selected point require a
   correct cold lookup, actual appended history, early-source correction,
   sustained generated output and another correct continuation. Aim for at
   least 4,096 actually generated tokens in the capacity check; a maximum-token
   allowance or early EOS alone proves no output reserve. Sparse lookup is
   separate from long-context coding quality.
5. Compare in-memory cache first. Only then, if conversation switching/restart
   is useful and budget remains, compare `--disk-cache 0` with a study-owned
   8G cache at a safe populated point, including A→B→A and process restart.
   Check current answers, cached-token counts, time and memory. Freeze which
   physical disk holds the cache; OS-warm reads are not cold-SSD throughput.
6. Freeze the finalist; repeat a matched baseline/finalist pair, reversing
   order when variance could decide the choice. Run the retained Flask
   decorators case as completed-work regression, including independent tests
   and review of subclass delegation. Add async-stream only if its distinct
   failure question warrants the remaining allocation. Do not rewrite the
   historical failures or assume tests alone qualify the implementation.

## Dory: revisit the failure, then the tradeoffs

Start even if Qwen is a clear loss or still inconclusive. Use the protected
allocation to answer Dory's own question.

1. Replay the pilot's exact holiday audit, `python-dotenv` audit and atomic
   reservation repair from
   [`questions_and_oracles` in the retained record](../../labs/workstreams/model-runtime-optimization/results/dory-low-memory-m5-2026-10-21.json).
   Start **NVFP4, two loops, per-loop KV, speculation off, visible thinking
   off**. The changed weights/runtime prevent an isolated speedup claim over
   MLX. Check the previously false environment-mutation/interpolation claims
   and repeated-SKU handling explicitly. These are exposed regression tasks.
2. Compare last-loop versus per-loop with speculation still off. Check short
   work and prompts beyond one 2,048-token prefill chunk, since cache semantics
   can diverge there. Include cold and actual-history corrections. A gain in
   memory or speed is useful only alongside the observed quality difference.
3. At a promising per-loop setting compare `--spec 0` with depth 2. If four
   loops plausibly fix a reasoning failure, compare two/four loops with matched
   visible-thinking settings; use one loop only for a plausible fast-helper
   option. Confirm `usage.kapitsa.loops` on every response. Loop changes
   invalidate prefix reuse; do not compare a warm two-loop run to cold four-loop
   work without stating it. Avoid a loops × KV × thinking × precision grid.
4. Compare visible thinking on for a named difficult task with 8,192 output
   tokens from the start, preserving the original 4,096-token historical stops.
   It must return a real final answer with `force_nonempty_content: false`.
   More reasoning tokens or copied thought content is not completed work.
5. If errors remain and precision is a plausible cause, use the pinned BF16
   diagnostic at the same loops/KV/thinking controls. Record the NVFP4 default
   Q8 head versus BF16 head; `NI_DORY_HEAD=bf16` can isolate that additional
   quantization first. Do not conclude quantization caused a failure merely
   because BF16 changes the answer. Repeat only a consequential discrepancy.
6. Recheck populated lookup/correction around 8k/24k/32k and extend to 64k or
   beyond only after healthy passes. Preserve the original prompt-format
   failure and corrected-format arm separately. Confirm output and continued
   use; a large configured window or sparse lookup is not coding capacity.
7. Run pagination repair on the selected Dory configuration with visible test
   feedback, independent grading and source review. If a comparative helper
   decision needs it, use the cached Qwen3.5-4B control on Kapitsa with plain
   decoding and explicit thinking settings. A full Dory Flask case is optional,
   only after small completed work makes it worthwhile.

## Confirmation, evidence and morning handoff

Reserve [the new transactional source audit](kapitsa-qwen-dory/confirmation-audit.json)
until settings are frozen. Its oracle was prepared before model output and
checked by executing the supplied implementation locally. Run it once on the
Splash baseline, Qwen finalist and Dory finalist with their declared settings.
Only `request` goes to the model; `expect` and `study_oracle` are evaluator-only.
It distinguishes rollback, conflicting duplicate IDs, already-seen events and
a zero-delta new account. Grade content and JSON conformance separately.
It establishes this source-audit behavior, not general coding reliability.
If exposed during diagnosis, it ceases to be unused confirmation.

Use `study queue` for each frozen batch, then return to interpret it. Use
foreground completion or `study wait`, not repeated status polling. Adaptive
decisions remain with the agent; queueing a matrix does not replace diagnosis.
If a setup defect blocks one model, retain it and continue the other arm where
independent. Never bypass the ownership/resource monitor to obtain a result.

Write `conclusions.md` under `.work/studies/kapitsa-qwen-dory-RUN/` with:

- Separate decisions for Qwen and Dory: selected exact composition or no
  demonstrated advantage; worthwhile tradeoff; unresolved question; stop reason.
- First attempts, final outcomes, test failures, quiet-wrong content,
  incomplete work, repairs, elapsed time and attributed unblinded source review.
- Actual input/output/continuation scope, native rates where observed, first
  visible answer latency, cache counters and startup/calibration conditions.
- Loaded and startup/workload peak memory in bytes/GiB with the exact counter:
  engine RSS/physical footprint, frontend/router where applicable, native
  allocation if available, and separate system wired/pressure/swap observations.
  Do not sum them. Kapitsa rates/native allocations currently remain missing;
  mapped weights, GPU/ANE services and five-second sampling can escape or distort
  process counters. Neither file size nor incomplete footprint is required RAM.
- Exact engine/model/template/draft identities, effective settings, all local
  repairs, meaningful rejected alternatives and next failed/unmeasured point.

Close owned studies, prove every engine/harness/listener stopped, restore only
the previously running approved Default layout, and export locally. Keep new
weights and existing study evidence; remove only owned temporary software/cache
where cleanup was scoped. Do not publish, send, commit, change a catalog/default,
or claim Temper support. The morning handoff should say what to use, why,
what it costs in time/memory, and what remains unknown.

To start after reviewing this scope, the operator handoff is:

> Run `field-kit/studies/kapitsa-qwen-dory.md` on this M5 within its eight-hour
> wall/six-hour native allocation and 32 GiB download ceiling. Temporarily stop
> and restore the running Default layout, use the Crucial X10 research cache,
> keep downloaded weights, and report both Qwen and Dory even if one is blocked.
> Make ordinary study-local repairs within scope; preserve first attempts and
> stop at the declared limits. Do not publish or change my live selections.
