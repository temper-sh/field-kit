# Architecture review — 2026-08-05

Status: **recommendations, not an approved execution queue**. This review was
made from source inspection at commit `bbe5659`, the live M5 Air / 32GB stack,
and the official upstream sources linked below. Claims copied from upstream
are not local measurements. No model or configuration change was made as part
of the review.

The existing decision on OCR and editor completion was reconfirmed: both stay
separate from the installed stack until an actual workflow needs them. A model
being small or available is not itself a trigger.

## Verdict

This is a strong personal local-AI stack. Its coherent parts reinforce each
other:

- first-attempt task success, rather than tokens/s, is the objective;
- one manifest generates the client and router views;
- one front door hides two engines without hiding their important flags;
- residency groups reflect the 32GB memory constraint;
- specialists are typed capabilities, not extra chat choices;
- runtime checks reproduce real failure shapes such as streaming tool calls,
  degenerate reranker scores and cross-group eviction;
- failed experiments and machine-specific scope are recorded rather than
  laundered into universal advice.

The main maturity gap is not another model. It is making the exact artifacts
and evidence as reproducible as the setup logic. The second is containing an
agent that can mutate files. Those two changes would improve trust more than a
larger specialist.

## Recommendations, in priority order

### 1. Lock resolved artifacts and make drift an explicit operation

**Observed in the repository:** `models.yaml` identifies Hugging Face repos and
quants but no immutable revisions. `steps/30-models.sh` runs `hf download
"$repo"` without `--revision`, considers any cached snapshot containing weights
sufficient, and llama-server entries resolve `-hf <repo>:<quant>` when loaded.
The fetched Qwen chat template follows a `resolve/main/` URL; when its hash
changes setup reports the drift and then applies the new file in the same run.

That is visible drift, but not reproducible or review-before-apply drift. A
fresh install can receive different weights or templates from the machine on
which a finding was measured.

Recommended shape:

1. Add optional `revision`, exact `file`/`mmproj`, and checksum fields to the
   model source description.
2. Generate a `models.lock.yaml` containing full Hugging Face commit SHAs,
   exact filenames and hashes, resolved engine versions, patch hashes, and the
   hardware/date on which acceptance passed. `models.yaml` remains the only
   hand-edited source of intent; the lock is derived resolution, like a package
   lockfile, rather than a competing manifest.
3. Make normal setup consume the lock. Give lock refresh an explicit command
   or flag rather than making it a side effect of a routine rerun.
4. For llama-server models, preserve lazy specialist pulls with a generated
   launcher that downloads the locked revision on first start and then passes
   local `-m`/`--mmproj` paths. Do not leave `-hf` to follow the remote branch,
   and do not turn locking into a requirement to download every parked model
   during setup.
5. On fetched-patch hash drift, retain the candidate and report `[manual]`;
   apply it only after an explicit accept/lock refresh and the relevant runtime
   tests.

Hugging Face supports tags and full commit hashes through `hf download
--revision`; a full commit SHA is the appropriate lock value. This does not
require vendoring third-party artifacts into this 0BSD repository.

### 2. Put a mutation boundary around the coder

The installed permission-gate example guards dangerous Bash strings only.
`write` and `edit` bypass it, while the observed destructive incident used
exactly that path: the coder overwrote the test script it had only been asked
to execute, then reported a result from the replacement.

Recommended shape:

1. Replace or wrap the fetched example with a repo-owned gate whose behavior is
   covered by the offline suite.
2. Treat overwriting an existing file and writing outside the active project
   as confirmation-worthy operations; keep new-file writes separately
   configurable.
3. Default write-capable evaluation and autonomous work to a disposable Git
   worktree. A permission prompt reduces risk; a recoverable filesystem
   boundary limits the damage when the model or gate is wrong.
4. Add runtime tests for Bash denial, existing-file overwrite, new-file write,
   and non-interactive fail-closed behavior.

### 3. Move live evidence into versioned records

The prose is unusually good, but it currently doubles as an operational
database. A small structured layer would let the prose remain the explanation
while keeping comparisons reproducible.

Recommended first records:

- `project_search` schema v2: stable query ID, backend, model revision,
  candidate/excerpt policy, discovery/rerank/total timings, results, and a
  separate human judgement naming the correct file;
- a report for top-5 hit rate and MRR once at least 30 real queries are judged;
- machine-readable engine A/B rows with engine/model revisions, flags,
  hardware, prompt fixture revision and success outcome;
- one compatibility snapshot emitted after runtime acceptance, tying the lock
  to the versions and request shapes that passed.

Do the query-log schema change while only four live records exist. Add backend,
model and policy fields before a mixed Qwen/Jina A/B makes their absence
ambiguous.

### 4. Re-baseline Rapid-MLX 0.12.3 before carrying older tuning forward

The repository already says most cache findings have 0.11.x provenance while
the live venv is 0.12.3. That release crosses cache and speculative-decoding
changes, so re-derive the growing-prefix result, exact-repeat behavior, hang
rate and streaming-tool-call guard before adding more cache flags.

Then evaluate only the flags with a concrete case:

- **Sampling A/B:** remains the first quality-affecting experiment in PLAN §4.
- **`--enable-tool-logits-bias`:** the installed binary describes this as a
  bias toward structural tool-call tokens. Measure first-attempt task success,
  valid parsed-call rate, malformed/leaked calls and wall time.
- **`--kv-disk-checkpoint-interval 0`:** the checkpoint directory was 4.6GB at
  review time. If resume/shared-prefix evidence stays at zero, disabling the
  path saves disk and writes without spending memory.
- **Extraction consolidation:** compare NuExtract3 against the already deployed
  Qwen3.5-4B plus the same grammar. Retiring a redundant specialist is a better
  optimization than adding one.

Do **not** enable these from feature claims alone:

- SuffixDecoding: the installed help says hybrid classification disables the
  suffix/speculative paths; the coder is a verified hybrid.
- DFlash: the published supported path targets the 8-bit Qwen3.5/3.6 27B
  models, while this stack deliberately runs a 4-bit checkpoint.
- TurboQuant: consider it only if a measured context/memory requirement cannot
  fit with the current int8 KV policy, and include task accuracy in the A/B.
- PFlash: keep it off for agent work unless a code/tool-context recall corpus
  proves that discarding middle blocks is safe.

### 5. Preserve the use-case gate for specialist models

No additional resident model is recommended now. The candidates below are
bookmarks for a triggered experiment, not manifest entries to add pre-emptively.

| Capability | Candidate and experiment | Trigger |
|---|---|---|
| Editor FIM | Existing parked `qwen2.5-coder-1.5b-fim`; keep the base model and `/infill` endpoint. Measure completion acceptance and latency from the editor, separately from Pi. | Inline completion is actively wanted. |
| OCR | `GLM-OCR` 0.9B is the first current candidate because it is MIT-licensed and has a ggml-org llama.cpp GGUF. Compare exact transcription, tables/formulas, cold/warm latency and peak memory against Qwen3.5-4B and NuExtract3. | Recurring document/image OCR exposes errors or excessive latency in the existing models. |
| Code embeddings | A/B the Apache-2.0 `Qwen3-Embedding-0.6B` against the code-specific `jina-code-embeddings-0.5b` (CC-BY-NC). Use identical chunks and judge retrieval on real misses. | `project_search` falls below its agreed top-5 quality bar. |
| Listwise reranking | Keep the Jina v3.5 path and cutover checklist in PLAN §6.1. | At least 30 judged queries exist and the incumbent measurably misses. |

Explicit non-recommendations without a new workflow: a router/classifier,
larger 4B/8B rerankers, a second small general-purpose chat model, a guard
model for an entirely local path, translation, and speech. They duplicate
capability or consume memory without a measured failure to solve.

## Official sources checked

- [Hugging Face downloads and immutable revisions](https://huggingface.co/docs/huggingface_hub/main/en/guides/download)
- [Rapid-MLX flags and optimization status](https://github.com/raullenchai/Rapid-MLX)
- [Qwen2.5-Coder-1.5B base / FIM model card](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B)
- [GLM-OCR model card](https://huggingface.co/zai-org/GLM-OCR) and
  [ggml-org llama.cpp conversion](https://huggingface.co/ggml-org/GLM-OCR-GGUF)
- [Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
- [Jina code embeddings GGUF model card](https://huggingface.co/jinaai/jina-code-embeddings-0.5b-GGUF)
