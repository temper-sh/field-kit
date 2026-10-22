# Runtime adapters

New models normally need a Temper preset/lock and workload input. Field Kit
has three small adapter families: **preset interfaces** (what a Temper preset
serves), **engine observations** and, within chat, **model conversation
formats**. A new format works with every supported engine that exposes chat
completions. There is no engine × model class matrix.

## Preset interfaces

The lock's preset names its interface. The
[interface registry](../fieldkit_runtime/adapters/interfaces/__init__.py) maps it
to one module; today `chat-completions` maps to
[`chat/v1`](../fieldkit_runtime/adapters/interfaces/chat.py). A module provides
`validate_request`, the allowed `EXPECT` checks, `build` (the request, including
history replay), `send` (capture, keeping partial output) and `observe`
(decoding and measurements). Another Temper interface, such as reranking, adds
one module and one registry entry; sessions, drivers and custody stay unchanged.
Presets with other interfaces are refused before any effect. Local engines use
the same interface and engine adapters; their declaration names both.

## Select a model format

The default `openai/v1` consumes structured chat completions without interpreting
text as markup. Use it for any model whose engine already supplies the required
content and tool calls. No model-name registration is needed.

Choose Flint explicitly for its reasoning history and tagged output:

```sh
./field-kit study run my-study --candidate my-flint-preset \
  --workload trials/tools.json --model-adapter flint/v1 \
  --reason "Check Flint tool format and history before completed coding work"
```

This requires an approved Temper lock for that preset; an adapter does not
install or qualify Flint. Alternatively, add `"model_adapter": "flint/v1"`
at the top of the workload JSON. The CLI selection overrides that field and is
frozen in the attempt's workload. Both adapter IDs and the runtime source hash
are retained with each attempt. Unknown IDs are reported before preparation.

`append_previous` uses the model adapter's assistant-message serializer. Tool
envelopes keep their original IDs, names and argument bytes. Flint replays
`reasoning_content` on every assistant turn, including rejected/no-call turns.
The default OpenAI adapter omits the nonstandard reasoning field. A workload
with tool continuations must supply the corresponding tool-result messages;
the runner never executes a generated tool call. Completed-task harnesses own
tool execution, feedback, retries and their quality judgments.

## Add an engine

Each engine has a folder under
[`adapters/engines/`](../fieldkit_runtime/adapters/engines/).
The [registry](../fieldkit_runtime/adapters/engines/__init__.py) maps exact
Temper engine-adapter versions to types and constructs a fresh adapter.
Selection comes from the verified lock, not a model-name heuristic.

For another chat-completions engine with ordinary counters:

```python
# adapters/engines/example/__init__.py
from ..base import EngineAdapter

class Example(EngineAdapter):
    health_path = "/health/ready"
    helper_limits = {"router": 2 * 1024**3, "frontend": 2 * 1024**3}
```

Import `Example` in the engine registry and add `"example/v1": Example` to
`ADAPTERS`. The ID must match an engine actually supported by Temper. No edits
to candidate admission, request execution or process monitoring are needed.

| Engine surface | Responsibility |
|---|---|
| `health_path` | Readiness route relative to `/upstream/PRESET` |
| `helper_limits` | Temper helper-role names and proposed per-process byte limits; engine memory remains governed by study policy |
| `metrics(response)` | Native token counts/rates, cache counts and rate sources; unavailable measurements stay `None` |
| `memory_observer(read, preset)` | Optional observer with `capture(phase)`, `poll()` and `measurements()`; default is absent |

`read(path)` is a bounded GET with process ownership checks. Observer creation
performs no I/O. Its measurements add named native records to the common memory
record; they must not overwrite common counters. See
[Splash](../fieldkit_runtime/adapters/engines/splash/__init__.py) for the one
existing native-memory observer. Its source, instance, loaded and peak counters,
partial failures and invalidation rules remain intact.

Study preview discloses and freezes helper limits. vLLM Metal proposes 3 GiB
for its frontend after a retained Gemma run observed 2.10 GiB there; its router
and resource tracker retain 2 GiB. Old approvals retain their original limits.
Startup keeps router/system monitoring until Temper publishes all required
roles, then binds the full process set. Missing identities cannot permit a
successful measured request, and changed identities still stop the run.

llama.cpp and Splash share the observed native timing shape. Explicit native
rates take precedence over count/time arithmetic: the engine's decode interval
can exclude the first token. The fallback is labelled in `rate_sources`.
Usage totals and wall time never substitute for prefill/decode timing. Rapid
and vLLM retain usage counts but leave native rates unmeasured until a supported
native timing source is added to their adapters.

Temper owns installation and runtime effects. Adapters do not introduce their
own installers, launchers, HTTP clients, lifecycle state or retry loops.

## Add a model format

Each distinct format lives under
[`adapters/models/`](../fieldkit_runtime/adapters/models/).
The [registry](../fieldkit_runtime/adapters/models/__init__.py) is the sole
selection map. For a model that only requires reasoning in history:

```python
# adapters/models/example/__init__.py
from ..openai import OpenAI

class Example(OpenAI):
    reasoning_field = "reasoning_content"
```

Import it and add `"example/v1": Example` to the model registry. Models sharing
a format reuse the same adapter; there is no roster of weights or presets.

Override `parse_message(message, request, complete)` only when a real format
requires decoding. It is a pure operation receiving a copy of the collected
message and the actual request, including tool schemas. Return the normalized
message or raise `FormatError` for ambiguous/incomplete output. `normalize`
retains `wire_message` when decoding changes it, records the adapter and parse
state, and leaves raw SSE intact. A parse problem does not erase rates or memory,
does not turn missing quality evidence into failure, and does not block an
independent request. An undecoded output cannot be replayed as a new turn.

An actual new parser needs format-specific implementation and fixtures. The
few-line extension applies to reused formats and registration, not arbitrary
new grammars. Keep parsing here instead of in a study's task runner.

## Flint's current coverage

[Flint](../fieldkit_runtime/adapters/models/flint/__init__.py) accepts native
structured replies unchanged and decodes completed output beginning with
`<think>…</think>` and/or `<tool_call>name<arg_key>…</arg_key><arg_value>…</arg_value>…</tool_call>`.
It never scans ordinary prose or fenced examples for embedded calls.

The request's tool schema distinguishes literal top-level strings from JSON
objects, arrays, numbers, booleans and null. The JSON decoder consumes containers
before looking for the next delimiter, preserving multiline source, Unicode and
literal tag text inside nested JSON strings. Raw string arguments use Flint's
literal delimiter format; an embedded closing delimiter there is ambiguous.
Union types, `$ref` schemas and other unimplemented shapes are reported as format
issues instead of guessed. This is decoding, not general JSON Schema validation.

Only complete tagged responses yield normalized calls. Malformed, interrupted
and length-stopped tagged output remains evidence. IDs for raw calls are assigned
by Field Kit from the request/history and output and marked as such; IDs from
native structured responses are preserved. Decoding cannot establish the first
usable-answer timestamp, so that measurement is left missing and the wire
timestamp is retained separately.

The retained 21 October Flint investigation confirmed a harness history defect:
reasoning was dropped between assistant turns. Its exact native parser passed
nested/multiline fixtures and 665 partial prefixes; the original malformed
generated call remains unexplained. These offline checks support the adapter
tests, not a claim that native Flint generation or its Temper integration has
been validated by this runtime revision.

## External harnesses and verification

Command workloads receive `FIELD_KIT_MODEL_ADAPTER` alongside endpoint, model and
output-directory variables. This is a selection hint, not an HTTP proxy: Field
Kit cannot rewrite a separate harness's conversation. The attempt records its
adapter scope as `external-harness`. A harness must consume that hint or supply
its own equivalent format/replay handling, and retain its effective selection.

A Python harness using this checkout can share the same small API:

```python
from fieldkit_runtime.adapters import models

adapter = models.resolve("flint/v1")
response = adapter.normalize(captured_response, actual_request)
assistant = adapter.assistant_message(response)
```

The harness still decides whether the task is complete and whether a call is
valid to execute. Pin its Field Kit source version when reusing this API.

Run `python3 -m unittest discover -v` and `./field-kit verify`. Regressions cover
engine extension through a full trial, format decoding, native rate semantics,
Flint history after rejected calls, partial output, approval limits and process
ownership. New adapters need representative responses and a focused integration
check. Native performance and fit still require an authorized study.
