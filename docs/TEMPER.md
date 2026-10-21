# Temper operations for Field Kit

Use the installed temper unless the user selects another build. Field Kit needs
the execution commands available from 0.3.0. Discover capabilities with
./field-kit doctor and command help. Authoring, installation and running are
separate operations; saving a preset does not activate it.

## Discover

```sh
temper version
temper machine facts --json
temper preset fields qwen3.8-27b-q4xl-splash --json
temper execution --help
```

Use --temper /absolute/path/to/temper with Field Kit for an explicitly selected
binary. Do not build a checkout or upgrade by default. The doctor reads help
for optional conveniences: preset plan (0.3.1+) and the interactive editor
(check installed support).

## Prepare an isolated candidate

Use a study authoring root. Select the catalog source intentionally; catalog
update retrieves metadata, not model weights. If metadata is already prepared,
reuse it. Do not update a user's live selection as part of an experiment.

```sh
study_root="$PWD/trials/authoring"
temper catalog update --root "$study_root"
temper preset fork qwen3.8-27b-q4xl-splash splash-context --root "$study_root" --json
temper preset fields splash-context --root "$study_root" --json
```

Read fields before editing: controls depend on engine and installed version.
When the installed editor is available:

```sh
temper preset edit splash-context --root "$study_root"
```

For scripted use, edit only the desired overrides in
trials/authoring/presets.d/splash-context.json. For example:

```json
{
  "schema": "temper-preset/v1",
  "origin": "qwen3.8-27b-q4xl-splash",
  "changes": {"context_window_tokens": 131072}
}
```

This is an authoring example, not evidence that 131072 populated tokens fit.

```sh
temper preset check splash-context --root "$study_root" --json
temper catalog compile --root "$study_root" --preset splash-context \
  --target darwin/arm64 --out trials/splash-context.lock.json --json
```

Fork follows the origin's current settings when compiled. Copy freezes the
selected preset values and permits independently authored material. Execution
locks freeze the exact tested configuration either way.

## Common operations

| Question | Operation |
|---|---|
| Another context/cache setting? | Fork; inspect fields; change overrides; check and compile a new lock. |
| Acceleration versus plain decoding? | Copy/fork compatible presets, inspect speculation fields, compile both. Include required draft material in approval. |
| Another quant? | preset weights ID REPO@REVISION FILE, then check/compile. Verify model lineage and template compatibility. |
| Another engine? | Start from a compatible preset for that engine or a self-contained copy. Temper validates the engine-specific fields. |
| Template experiment? | preset template ID REPO@REVISION FILE or --local FILE.jinja, then check/compile. A template change is new material. |
| Freeze already-approved context/output only? | execution configure derives a lock with these supported controls; it does not expose every tuning control. |
| Installation size/requirements? | preset plan ID --root ROOT --json where available. For catalog presets on older versions, init --dry-run --preset ID --root ROOT --json. |

Examples of the material helpers:

```sh
temper preset copy qwen3.8-27b-q4xl-mtp qwen-quant --root "$study_root" --json
temper preset weights qwen-quant unsloth/Qwen3.8-27B-GGUF Qwen3.8-27B-UD-Q4_K_XL.gguf \
  --root "$study_root" --json
temper preset check qwen-quant --root "$study_root" --json
```

Use an exact upstream revision for a real controlled comparison. The helpers
resolve and record exact files; their lookup may access the network.

If a newer planning command is unavailable, keep authoring and lock inspection
on the installed version. Review known sizes and unlisted dependency costs.
Do not silently claim a complete estimate. Field Kit's known-material total
is not a network byte meter, full disk estimate or memory-fit prediction.

## Execution and recovery

Field Kit invokes these commands and supervises their processes:

- execution inspect --lock FILE --json: validate and inspect.
- execution prepare --lock FILE --root ROOT --installation ID --json:
  install exact material; may download and leave recoverable partial work.
- execution serve with the prepared generation and a new status-file:
  run the owned engine and publish process/shutdown observations.
- execution remove with the same owned root/installation/lock: remove software.

Use Field Kit's run/stop/recover/close path to retain measurement and ownership
evidence. Manual invocation is a diagnostic action requiring the same scope.
Never manipulate a user's live layout to make a trial work.

The maintained [preset contract](https://github.com/temper-sh/temper/blob/master/docs/contracts/user-presets.md)
and [execution contract](https://github.com/temper-sh/temper/blob/master/docs/contracts/execution-runtime.md)
own full details. Match their revision to the installed release.
