# Coding optimization plan — 2026-08-06

Status: **companion plan, recorded but not scheduled**. `PLAN.md` remains the
only active queue. Promote an experiment there before implementing it; this
file preserves the proposed sequence, gates and stop conditions so the ideas
do not become configuration changes merely because they are available.

Scope: the Pi + Qwen3.6 coding loop. OCR and editor FIM completion remain
separate, deferred capabilities with their own triggers in `PLAN.md` §6.4–6.5.
Neither belongs in this plan or in the resident coding stack.

## Objective

Improve first-attempt coding correctness and close the loop between mutation
and verification without adding another always-resident model. Latency and
token savings matter only after task success holds.

The current baseline already rules out several generic optimizations:

- the 27B coder runs 4-bit through Rapid-MLX 0.12.3 in non-thinking mode;
- prefix reuse makes a growing session cheap after its first turn;
- the builtin tool definitions still occupy about 4.2k prompt tokens;
- tool-output compression measured a small ceiling because the agent usually
  scopes commands itself;
- hash-addressed editing saved tokens but lost six strict passes, so smaller
  output is not assumed to mean better coding;
- an observed test-script overwrite shows that a successful-looking answer is
  not evidence that the requested validation actually ran.

## Rules for every experiment

1. **Task success is primary.** Record first-attempt success, valid tool-call
   rate and correctness before wall time, output tokens or prompt size.
2. **Change one boundary at a time.** Preserve the exact model revision,
   engine version, flags, prompt fixture and task corpus with each result.
3. **Exercise the real request shape.** Coding runs must stream parsed tool
   calls through llama-swap; a plain completion is not an agent acceptance
   test.
4. **Keep the incumbent easy to restore.** Every adopted extension or flag
   change needs a one-step disable path and offline coverage where possible.
5. **Do not infer routing with another model.** Modes and tool profiles are
   user-selected or deterministic until evidence justifies more machinery.
6. **Do not duplicate the coder in memory.** Thinking and non-thinking modes
   should share one serving process.

## Proposed order

| Order | Experiment | State | Promotion condition |
|---:|---|---|---|
| 1 | Selective thinking (`/deep`) | ready to probe | Hard coding tasks improve without breaking streamed tool calls. |
| 2 | Manual tool profiles | refines `PLAN.md` §4.8 | Schema shrinks without lowering first-attempt success. |
| 3 | Verification-debt extension | ready to design | Advisory mode has low false-positive and false-negative rates. |
| 4 | Fresh-context diff review | conditional workflow | Confirmed defects justify the extra pass on risky changes. |
| 5 | `related_code` tool | evidence-triggered | Search finds the file but the coder repeatedly misses related code or tests. |
| 6 | Weight-precision A/B | last resort | Persistent quality failures remain after the loop improvements above. |

The first three can be evaluated independently, but that is the preferred
adoption order: find out whether deliberate reasoning helps, reduce the tool
surface, then enforce validation against the resulting workflow.

## 1. Selective thinking for hard coding work

### Hypothesis

Qwen's own Qwen3.6 guidance recommends thinking mode for precise coding work.
The current server forces `--no-thinking`, which is sensible for routine agent
turns but prevents measuring whether deliberate reasoning improves difficult
debugging, architecture and refactoring tasks.

Always-on thinking is not proposed. The useful shape is an explicit `/deep`
session mode with non-thinking remaining the default.

### Probe before implementation

1. Capture the actual Pi request body so the experiment does not guess which
   sampling or chat-template fields the client already supplies.
2. Send paired direct requests to Rapid-MLX with
   `chat_template_kwargs.enable_thinking` false and true.
3. Establish whether a request can override the server-wide `--no-thinking`.
   This is not assumed from Qwen's generic serving guidance.
4. For the thinking arm, verify a plain response and check-12-shaped streamed,
   named tool selection. Reject the path if reasoning leaks into content,
   tool calls stop parsing, or streaming wedges.

If `--no-thinking` prevents request-level selection, the next probe is one
server without that global flag while every normal request explicitly sends
`enable_thinking: false`. Only proceed if false remains byte-shape compatible
with the current agent path and true can be selected per request.

### Implementation shape

- A small repo-owned Pi extension owns a session boolean and a `/deep` toggle.
- Its provider-request hook injects the Rapid-MLX chat-template field; default
  false, explicit true only while the mode is active.
- The mode is visible in the UI and can be turned off without restarting the
  server.
- It does not create a second llama-swap model ID or a second 27B process.

### Evaluation

Use two predeclared sets rather than averaging unlike work together:

- routine edits and tool-use tasks, to verify that the default path is
  unchanged;
- difficult debugging, multi-file refactoring and design tasks, for the
  thinking A/B.

Record first-attempt correctness, unnecessary edits, valid tool calls, test
outcome, wall time, output tokens and hangs. Adopt `/deep` only if it produces
additional correct hard-task passes; equal quality with more time is a reject.
Sampling must be captured with the result because Qwen recommends different
settings for thinking and non-thinking modes.

## 2. Manual tool profiles

### Hypothesis

The measured 4.2k-token builtin schema is a larger coding-context target than
command-output compression. The installed Pi extension API can change active
tools, so explicit workflow profiles can reduce prompt occupancy and constrain
mutation without a classifier in front of the coder.

### Initial profiles

| Profile | Intended surface |
|---|---|
| `/inspect` | Read, grep, find, list and `project_search`; no write/edit. |
| `/change` | Inspection tools plus edit, write and shell. |
| `/verify` | Read and shell/validation tools; no write/edit. |
| `/review` | Diff, read, search, `project_search` and validation; no write/edit. |

Exact tool names come from `pi.getAllTools()`, not a copied list. Provide an
obvious command that restores the full default set. Profiles are manual: an
incorrectly inferred profile would turn a prompt-saving feature into a hidden
quality failure.

### Evaluation

First record prompt tokens for each profile. Then replay the coding corpus with
the full tool set and the intended profile. Record:

- first-attempt task success;
- attempts to use a hidden tool or manual profile switches mid-task;
- wrong or more expensive substitute commands;
- turn-1 latency and permanent context occupancy.

Adopt a profile only when it preserves success. Low historic use of a tool is
not enough: `PLAN.md` §4.8 already records why usage frequency is not proof
that removal is free.

## 3. Verification debt and targeted validation

### Hypothesis

The agent needs a deterministic signal that a file mutation occurred after
the last successful validation. Prompt instructions alone cannot establish
that, and a pre-existing dirty worktree must not be confused with agent work.

### Advisory extension first

A repo-owned extension observes Pi's tool events and keeps session-local state:

- successful `write` and `edit` calls advance `lastMutation`;
- recognized test, lint, typecheck and `git diff --check` commands record their
  exit status and the mutation they follow;
- a later mutation makes earlier validation stale;
- at agent completion, stale validation produces one follow-up request to run
  the relevant check or explain why none applies.

Only mutations observed in the current session count. Never infer ownership
from `git status`, because those changes may belong to the user or another
session. A command merely containing the word `test` is not evidence: start
with known repository entry points and declared package scripts, and require a
zero exit status.

Keep the first version advisory. It may recommend commands, but it must not
silently run arbitrary project scripts or block a user who explicitly asked
for no tests. Expected exemptions include documentation-only work, a project
with no applicable checks, and an explicit user decision; the final response
must state the exemption rather than pretending verification happened.

### Deterministic test-impact helper

If advisory data shows that choosing the right command is the weak point, add
a `verify_changes` tool. It should inspect declared project entry points such
as package scripts, `pyproject.toml`, `Cargo.toml`, Makefiles and this repo's
test documentation, then propose the smallest relevant check before a broader
suite. Keep execution explicit and argv-based. It must never create or rewrite
a test script in order to run it.

### Required coverage

- mutation followed by no validation;
- mutation followed by a failed check;
- successful check followed by another mutation;
- successful check after the final mutation;
- a pre-existing dirty worktree with no session mutation;
- documentation-only change and explicit exemption;
- tool error/cancellation and session reset.

Promotion from advisory to a stronger gate requires real-session evidence that
both missed warnings and spurious warnings are rare.

## 4. Fresh-context review for risky diffs

### Hypothesis

A second pass from the same 27B model in a fresh context is more independent
than asking the editing conversation to affirm its own work. It also avoids a
resident critic model that would be smaller, less capable and another routing
decision to maintain.

Start as a manual workflow. Give a new or forked read-only session:

- the original requirement and explicit constraints;
- the final diff, validation results and any stated exemptions;
- the `/review` profile, with no write/edit tools;
- permission to inspect relevant files and tests.

Request a bounded verdict: blocking defect, non-blocking concern, missing test,
or pass. The editing session owns any resulting fix; the reviewer does not
mutate the tree.

Initially use it for security/permission code, setup and service changes,
model-manifest changes, cross-file refactors and other manually identified
high-risk work. Do not encode size thresholds until actual use shows a useful
boundary. Record confirmed findings, false positives and extra wall time. Keep
the workflow only if it finds real defects often enough to justify that cost.

## 5. `related_code` — only after an observed context miss

`project_search` answers “which files are relevant?” A distinct coding failure
is finding the right file but missing its callers, definitions, implementations
or closest tests. Do not build another retrieval layer until judged session
logs show that failure.

If triggered, make `related_code` a bounded, index-free tool over existing
`rg` and `ast-grep`:

- input: a symbol or path plus an optional relationship to inspect;
- output: definitions, references/importers, implementations or overrides,
  nearest likely tests, and short line-numbered excerpts;
- predefined operations for common languages, with a textual fallback;
- literal argv, project-root containment, output limits and timeouts;
- deterministic unit tests using injected command results.

Do not expose raw AST pattern construction as the normal interface. Do not add
an LSP daemon, vector store or persistent index for this first version. Its
acceptance test is whether it repairs the specific dependency/test misses that
opened the work, not whether it can return many relationships.

## 6. Weight precision — conditional quality experiment

The main coder's 4-bit weights are a plausible quality variable, but changing
them is expensive and should come after the coding loop itself is improved.
Open this experiment only if recurring subtle failures remain on a stable,
judged corpus.

Compare the incumbent against one higher-precision conversion at a time,
starting with 5-bit. Use identical prompts, sampling, thinking mode and engine
version; run one coder at a time. Record:

- first-attempt correctness and the exact failure class;
- streamed tool-call validity;
- prompt/decode performance;
- peak wired memory, swap and survival of the resident utility group;
- long-context stability rather than only short benchmark prompts.

A published 6-bit community conversion lists roughly 20.36 GiB of weights
before KV cache and utilities, so it is unlikely to preserve this 32GB stack's
co-residency. It is not a default recommendation. Adopt higher precision only
for a material, repeatable quality gain that still fits the measured GPU budget;
otherwise keep the 4-bit incumbent.

## Explicit non-goals

- **Always-on thinking:** routine work should not pay for an unmeasured hard-
  task benefit.
- **A router/classifier model:** manual profiles are visible and reversible;
  inferred routing adds a new silent failure mode.
- **A small resident critic or second chat model:** use a fresh context on the
  main coder before adding a less capable reviewer.
- **Suffix decoding or DFlash:** the installed Rapid-MLX path restricts suffix
  and speculative features for this hybrid model, while its documented DFlash
  target is the 8-bit 27B path rather than this 4-bit deployment.
- **More tool-output compression:** the measured ceiling is small on this
  cached, command-scoping agent; tool schema is the better target.
- **OCR or editor completion:** both remain separate until their user workflow
  exists, as already decided in `PLAN.md`.

## Sources to re-check before promotion

- [Qwen3.6-27B model card](https://huggingface.co/Qwen/Qwen3.6-27B) — thinking
  mode and sampling guidance.
- [Rapid-MLX](https://github.com/raullenchai/Rapid-MLX) — serving flags and
  optimization compatibility; local 0.12.3 behavior remains the acceptance
  authority.
- [Qwen3.6 27B 6-bit MLX conversion](https://huggingface.co/DreamFoundries/Qwen3.6-27B-6bit)
  — community weight-size reference, not comparative quality evidence.

Upstream guidance is a hypothesis source. The corpus and runtime checks decide
what this machine adopts.
