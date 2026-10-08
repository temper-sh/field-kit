#!/usr/bin/env python3
"""Freeze revision 6 memory observations without changing the dispatched tasks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fieldkit_runtime.catalog import canonical_json, digest, load_question_material


def main():
    source = load_question_material(ROOT / "catalog/packages/qwen-machine-study@5/package.json")
    destination = ROOT / "catalog/packages/qwen-machine-study@6"
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in source.files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    protocol = json.loads(source.files["protocol.json"])
    protocol["schema"] = "field-kit-qwen-chunk-study/v2"
    protocol["native_memory"] = {
        "engine": "splash", "source": "/status", "counter": "memory_actual",
        "interval_seconds": 5, "timeout_seconds": 2,
        "capture": "after readiness, during requests, before shutdown",
        "missing": "retain unavailable or partial memory separately from task and timing evidence",
        "peak_scope": "native engine lifetime, including load and context continuation; never sum with process counters"}
    (destination / "protocol.json").write_bytes(canonical_json(protocol))
    (destination / "runner.py").write_bytes(source.files["runner.py"].replace(b"revision 5", b"revision 6"))
    package = copy.deepcopy(source.package)
    package.update(revision=6, origin={"kind": "frozen-source-extraction", "source": source.selector,
                                      "source_sha256": source.package_sha256})
    for reference in [package["mechanics"]["runner"], *package["mechanics"]["protocols"]]:
        reference["sha256"] = digest((destination / reference["path"]).read_bytes())
    package["mechanics"]["runtime_protocol"] = {"id": "qwen-chunk-study", "revision": 2, "schema": protocol["schema"]}
    (destination / "package.json").write_bytes(canonical_json(package))
    load_question_material(destination / "package.json")
    index = {"schema": "field-kit-question-catalog/v3", "revision": 6, "compiled_at": "2026-10-08T00:00:00Z",
        "questions": [{"id": package["id"], "revision": 6, "availability": "qualifying",
            "package_path": "packages/qwen-machine-study@6/package.json",
            "package_sha256": digest((destination / "package.json").read_bytes()),
            "reason": "One configuration with retained task checkpoints and native Splash memory observations. Results require review."}]}
    (ROOT / "catalog/questions.json").write_bytes(canonical_json(index))


if __name__ == "__main__":
    main()
