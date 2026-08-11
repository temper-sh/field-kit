# qwen-fixed-template

Drop-in replacement for the `chat_template.jinja` shipped with Qwen 3.5 / 3.6
checkpoints. The stock Qwen templates contain Python-specific Jinja logic and
restrictions that break tool calling and agentic multi-turn on several engines,
and that invalidate the KV cache between turns.

## Provenance

| | |
|---|---|
| Source | <https://huggingface.co/froggeric/Qwen-Fixed-Chat-Templates> |
| File | `chat_template.jinja` (repository root) |
| Direct URL | see `FETCH` |
| License | Apache-2.0 — upstream's, not this repo's |
| Last vendored version | **v21**, sha256 `d203f334…b2b3bf997`, fetched 2026-07-27 |

## Fetched, not vendored

This directory holds no copy of the template. `FETCH` names the file and its
URL; `steps/30-models.sh` downloads it into `.state/patch-cache/` on every run
and applies it from there.

It used to be vendored, pinned by hash. Two things changed that on 2026-08-02:

1. **Licensing.** Shipping someone else's Apache-2.0 file drags their licence
   into a tree that is otherwise 0BSD, and leaves a permanent "this one
   directory is different" asterisk in a repo meant to be freely reusable.
   Fetching distributes nothing, so the question does not arise.
2. **Staleness.** The pin was v21 from 2026-07-03. A pinned template is a
   template nobody updates; the fixes upstream ships are exactly the fixes this
   stack wants.

**What the pin was actually buying, and how it is replaced.** The original
rationale was that a chat template changing silently under a model turns into
an afternoon of debugging empty responses. That risk is real and fetching
re-introduces it. The mitigation is *visibility, not immobility*: every fetch
is hashed against the previous one, and a change is reported as a `[note]`
naming both short hashes and the URL. So the template can move, but it cannot
move quietly. **If you see that note and the model then misbehaves, the
template is the first suspect** — diff `.state/patch-cache/` against the
upstream history before looking anywhere else.

Offline behaviour: a fetch failure falls back to the cached copy and proceeds.
It only `[fail]`s when there is no cached copy and no network. Under
`--dry-run` nothing is ever fetched — the cache is used if present, and
otherwise the file is reported as `[skip]` with the URL it would have pulled.

## Applying it

Declare the patch on a manifest entry:

```yaml
- id: some-qwen-model
  engine: rapid-mlx
  repo: org/Some-Qwen-Model
  patches: [qwen-fixed-template]
```

`steps/30-models.sh` copies every resolved file into every snapshot directory
of that model, by basename. The target is normally a symlink into the shared
`blobs/` store, so it is removed before the copy — editing it in place would
corrupt the blob for every revision that shares it.

That also makes patching self-healing: re-downloading a model restores the
stock template, the next `./setup.sh` sees the hash mismatch, and re-applies.

## Which models need it

Only models whose own `chat_template.jinja` is the unfixed upstream one. The
default coder in `models.yaml` — `froggeric/Qwen3.6-27B-MLX-4bit` — ships this
same fixed template already (its `chat_template.README.md` documents it), so it
declares `patches: []`, and **nothing in the shipping manifest triggers this
patch at all**. The stock `unsloth/*` builds do need it; see the
`qwen3.6-27b-mlx-unsloth` and `qwen3.6-35b-mlx` entries in
`models.example.yaml`.

## Adding another fetched file

Append a `<basename> <url>` line to `FETCH`. Lines starting with `#` are
comments. Any file placed in this directory directly is still applied as a
pinned vendored file — the two mechanisms coexist — but prefer `FETCH` unless
the file is one this project authored.
