# Recover locally and report back

The agent owns routine diagnosis and repair within approved scope.

1. Preserve the attempt and error. Locate the failure: preparation, engine,
   request, measurement, harness, evaluator or export.
2. Inspect relevant versions, help, requests and concise logs. Check the
   [Temper guide](TEMPER.md) and previous repairs.
3. Apply the smallest useful repair: compatible installed commands, corrected
   paths/inputs, isolated grading dependencies or a local tool patch.
4. Verify cheaply. Repackage retained evidence without rerunning models.
   Regrade preserved output without inventing another model attempt.
5. Continue within approval. Independent candidates/questions remain usable.
   Repeated inference creates a new trial and preserves the first.

study stop requests owned shutdown. After a dead worker, study recover
reconciles recorded process state; it never silently repeats inference.
Investigate uncertain ownership; never kill by name or delete recovery files.

Tool changes are attributed per attempt. New weights/templates/engine software
outside approval require a scoped extension. Use installed Temper by default.

## Common repairs

| Symptom | Action |
|---|---|
| New Temper command missing | Inspect help; use existing fork/copy/check/files and execution commands. Upgrade only when required capability has no installed route. |
| Launcher Python unavailable | Set FIELD_KIT_PYTHON to installed Python 3.9+. Help never downloads Python. |
| Interrupted preparation | Recover the worker and start a new trial with the same owned root/cache; Temper reuses verified material. |
| Occupied port | Identify listener read-only; never stop unrelated services. Use a different owned root for engine-port conflicts. |
| Missing submission | Review retained patch/tests and attribute the harness outcome separately. |
| Broken grading environment | Repair it in isolation and regrade retained work; record evaluator changes. |
| Missing counters | Investigate support and report missing data; preserve other valid observations. |
| Broken export | Repair packaging of retained evidence; check membership, including candidate test logs. Export an idle open study when cleanup is blocked. |

## Report every defect, including repaired ones

A compact Markdown report names component/version, failing operation, expected
and actual behavior, minimal reproduction, confirmed cause or hypothesis,
workaround/repair, verification, effect on evidence and remaining upstream fix.

```sh
./field-kit repair my-study --report repair.md --patch repair.diff
```

Reports and patches accompany export. Put supporting evidence in the study's
evidence directory and reference it. Exclude credentials and unrelated private
files. A successful workaround must not hide maintenance work.

If initialization itself fails, keep the report locally and attach it once
the study exists. If initialization cannot be repaired, return that report
directly; the reporting format must not become another blocker.

For unresolved blockers, immediately give study/run ID, exact error, attempted
repairs and the smallest required action. Return existing measurements and
repair reports. Sending/opening issues/publication require explicit action.
