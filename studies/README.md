# Studies

Start from the user's work and machine. Read the current assessment, then
investigate uncertainty that changes this study.

- The [Temper catalog](https://temper-sh.github.io/catalog/) lists installable
  candidates and scoped memory observations.
- The [model assessments](https://temper-sh.github.io/) compare models and
  layouts.
- [Temper contracts](https://github.com/temper-sh/temper/tree/master/docs/contracts)
  explain controls; match documentation to the installed release.
- Primary model cards, engine source/docs and release notes supply expectations,
  not measurements on this machine.

Record source/revision or access date for consequential claims. Do not mirror
the moving catalog here.

## Study briefs

The [Kapitsa overnight study](kapitsa-qwen-dory.md) compares Qwen3.8-27B with
the existing Splash composition on the owner's M5 / 32 GiB, tunes promising
settings, and includes a separate Dory revisit. Its completed assessment covers
occupied context, generated-history reuse, completed coding work and the unused
source audit. The brief links the results and local evidence exports, and
preserves the original material, questions and comparison confounds.

The [64 GiB studies](64gib-studies.md) finish Splash context/cache tuning,
compare Splash Q5/Q6 with owner-side completed-work review, and investigate
MLX choices for Muse and Gemma 31B. Gemma 26B's engine comparison selected
llama.cpp; its [Q4 continuation](gemma26-q4-tuning.md) prioritizes sampling,
thinking budgets and completed-work quality before larger quants. The briefs
also define what can be projected to 48 GiB and what still needs measurement
there.

Use [the brief example](study-brief.md). Most studies need a brief, candidate
locks and reusable workload inputs. Add deterministic tools for actual missing
operations rather than another per-study codebase.

Maintained briefs and workload inputs belong in this directory. Local execution
state belongs in .work/studies/ID/: keep brief.md current with run references,
choices and relevant rejected options. conclusions.md holds the final proposal.
The repair tool retains defects and local patches for maintainers.

A refresh after a software or model update compares tested identities with the
changed material, identifies affected behavior and reuses unchanged evidence.
