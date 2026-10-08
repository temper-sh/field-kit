#!/usr/bin/env python3
"""Derive revision 5 scheduling from frozen revision 4 inputs, without inference."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fieldkit_runtime.catalog import canonical_json, digest, load_question_material

GIB = 1024**3


def main():
    source = load_question_material(ROOT / "catalog/packages/qwen-machine-study@4/package.json")
    destination = ROOT / "catalog/packages/qwen-machine-study@5"
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("workloads.json", "flask.tar.gz", "execution.lock.json"):
        shutil.copyfile(source.package_root / name, destination / name)
    (destination / "executions").mkdir(exist_ok=True)
    for path in sorted((source.package_root / "executions").glob("*.json")):
        shutil.copyfile(path, destination / "executions" / path.name)
    previous = json.loads(source.files["protocol.json"])
    tasks = [item["id"] for item in json.loads(source.files["workloads.json"])["cases"]]
    cells, configurations, actions = [], [], []
    old_actions = {item["id"]: item for item in source.package["investigation"]["actions"]}
    order = ("splash-q4", "splash-q4-context", "splash-q5", "llama-q5", "splash-q6",
             "llama-q6", "rapid-mlx", "vllm-metal", "llama-q8")
    for identity in order:
        contextual = identity == "splash-q4-context"
        selected = [cell for cell in previous["cells"] if
                    (cell["kind"] == "context" if contextual else cell["id"] == "coding-" + identity)]
        run_cells = []
        for cell in selected:
            for task in ([None] if contextual else tasks):
                item = copy.deepcopy(cell)
                if task:
                    item["id"] += "-" + task
                    item["task_ids"] = [task]
                run_cells.append(item)
                action = copy.deepcopy(old_actions[cell["id"]])
                action.update(id=item["id"], runtime_minutes_max=140 if contextual else 320)
                actions.append(action)
        cells.extend(run_cells)
        # Authoring reads the exact frozen composition. Runtime treats locks as
        # opaque. Allow a second weight-sized working set plus 32 GiB for the
        # installed software, conversion/temporary files and retained evidence.
        lock = json.loads(source.files[selected[0]["lock"]])
        model_bytes = sum(file["bytes"] for artifact in lock["records"]["artifacts"].values()
                          for file in artifact["files"])
        configurations.append({"id": identity, "preset": selected[0]["preset"],
            "buckets": selected[0]["buckets"], "cells": [cell["id"] for cell in run_cells],
            "summary": "Splash Q4 filled-context ladder" if contextual else identity + ": two frozen coding tasks",
            "cost": {"network_bytes_max": model_bytes + 16 * GIB,
                     "temporary_disk_bytes_max": 2 * model_bytes + 32 * GIB,
                     "retained_disk_bytes_max": GIB, "evidence_bytes_max": GIB}})
    actions.append(copy.deepcopy(old_actions["finish-study"]))
    protocol = {"schema": "field-kit-qwen-chunk-study/v1", "configurations": configurations,
                "cells": cells, "prepare_seconds": 1800, "request_seconds": previous["request_seconds"],
                "context_request_seconds": previous["context_request_seconds"],
                "process_log_bytes_max": previous["process_log_bytes_max"],
                "process_watch": previous["process_watch"],
                "budget_note": "Initial setup allows 6h. Each coding task allows 30m preparation verification, 30m startup, 4h request and 20m grading/shutdown. Context points allow two 30m requests. Final cleanup allows 90m. These are ceilings, not estimates."}
    (destination / "protocol.json").write_bytes(canonical_json(protocol))
    (destination / "runner.py").write_text(
        '"""Run only the configuration bound into this revision 5 package."""\n'
        'from pathlib import Path\nimport sys\n'
        'sys.path.insert(0, sys.argv[sys.argv.index("--field-kit-runtime") + 1])\n'
        'from fieldkit_runtime.experiments.qwen.chunks import ChunkStudy\n'
        'from fieldkit_runtime.experiments.qwen.splash_study import main\n'
        'raise SystemExit(main(Path(__file__).resolve().parent, study_type=ChunkStudy))\n')
    (destination / "PROMPT.md").write_text(
        '# One Qwen configuration\n\nRun only the selected configuration. Each coding task '
        'is a separate retained first attempt. Stop after this configuration; never advance '
        'to another without a new selection and plan consent. Preserve failures and partial '
        'responses. The optional 36 GiB context ladder stops at its first unsuccessful point.\n')
    def file_identity(name):
        return {"path": name, "sha256": digest((destination / name).read_bytes())}
    package = copy.deepcopy(source.package)
    package.update(revision=5,
        origin={"kind": "frozen-source-extraction", "source": source.selector, "source_sha256": source.package_sha256},
        summary="Choose one configuration, retain each task, then clean up and stop. Splash Q4 is the first suggested run.")
    package["investigation"] = {"schema": "field-kit-investigation/v2", "parameters": [],
        "actions": sorted(actions, key=lambda item: item["id"]),
        "initial_action": {"id": cells[0]["id"], "parameters": {}},
        "final_validation_action": "finish-study", "action_selection": "result-directed",
        "total_attempts_max": 7, "total_runtime_minutes_max": 930}
    package["cost"].update(live_runtime_minutes_max=930,
        network_bytes_max=max(item["cost"]["network_bytes_max"] for item in configurations),
        temporary_disk_bytes_max=max(item["cost"]["temporary_disk_bytes_max"] for item in configurations))
    package["consent"]["writes"].append("private Hugging Face download cache inside the dedicated installation")
    package["consent"]["cleanup"] = "After verified shutdown, remove this configuration's installation and private download cache; preserve all pre-existing shared caches and retained reports."
    package["execution_lock"] = file_identity("execution.lock.json")
    package["mechanics"].update(prompt=file_identity("PROMPT.md"), runner=file_identity("runner.py"),
        protocols=[file_identity(name) for name in ["protocol.json", "workloads.json", "flask.tar.gz"] +
                   ["executions/" + path.name for path in sorted((destination / "executions").glob("*.json"))]],
        runtime_protocol={"id": "qwen-chunk-study", "revision": 1, "schema": protocol["schema"]})
    (destination / "package.json").write_bytes(canonical_json(package))
    load_question_material(destination / "package.json")
    index = {"schema": "field-kit-question-catalog/v3", "revision": 5, "compiled_at": "2026-10-08T00:00:00Z",
        "questions": [{"id": package["id"], "revision": 5, "availability": "qualifying",
            "package_path": "packages/qwen-machine-study@5/package.json",
            "package_sha256": digest((destination / "package.json").read_bytes()),
            "reason": "One selected configuration with retained task checkpoints. Independent machine measurements remain pending."}]}
    (ROOT / "catalog/questions.json").write_bytes(canonical_json(index))


if __name__ == "__main__":
    main()
