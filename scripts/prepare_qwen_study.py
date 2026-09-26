#!/usr/bin/env python3
"""Compile the frozen Qwen matrix using Temper; no installation or inference."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main(args):
    root = Path(__file__).resolve().parents[1]
    package_root = root / "catalog/packages/qwen-machine-study@4"
    package = json.loads((root / "catalog/packages/qwen-machine-study@3/package.json").read_bytes())
    protocol = json.loads((root / "catalog/packages/qwen-machine-study@3/protocol.json").read_bytes())
    cells = []
    for layout in ("splash-q4", "splash-q5", "splash-q6", "rapid-mlx", "vllm-metal", "llama-q5", "llama-q6", "llama-q8"):
        family = "splash" if layout.startswith("splash") else "llama" if layout.startswith("llama") else layout
        cell = {"id": "coding-" + layout, "kind": "coding", "layout": layout, "family": family,
                "window": 118000, "buckets": ["36", "48-plus"] if family == "splash" else ["48-plus"],
                "lock": "executions/" + layout + ".json"}
        if layout == "llama-q8":
            cell["candidate"] = True
        cells.append(cell)
        with tempfile.TemporaryDirectory(prefix="qwen-compile-") as temporary:
            selection = Path(temporary) / "selection.json"
            write(selection, {"schema": "temper-selection/v2", "profile": layout})
            output = Path(temporary) / "execution.json"
            subprocess.run([str(args.temper), "catalog", "compile", "--catalog", str(args.catalog),
                            "--selection", str(selection), "--target", "darwin/arm64", "--out", str(output), "--json"],
                           check=True, capture_output=True)
            target = package_root / cell["lock"]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(output.read_bytes())
    # Coding reference first, then the 36 GiB filled-context ladder, then larger
    # quants. At 48 GiB+ the ladder is absent; there is no intermediate bucket.
    contexts = [{"id": "context-" + str(window), "kind": "context", "family": "splash", "layout": "splash-q4",
                 "window": window, "buckets": ["36"], "lock": "executions/splash-q4.json"}
                for window in (32768, 65536, 98304, 131072, 196608, 262144)]
    cells[1:1] = contexts
    protocol = {"schema": "field-kit-qwen-splash-study/v1", "cells": cells,
                "prepare_seconds": 21600, "request_seconds": 14400, "context_request_seconds": 1800,
                "process_log_bytes_max": 16 * 1024**2, "process_watch": protocol["process_watch"],
                "budget_note": "Each coding cell allows 6h preparation, two 4h requests, two 30m startups and grading/cleanup. These are ceilings, not duration estimates."}
    protocol["bucket_costs"] = {}
    for bucket in ("36", "48-plus"):
        sizes = []
        for cell in cells:
            if cell["kind"] != "coding" or bucket not in cell["buckets"]:
                continue
            lock = json.loads((package_root / cell["lock"]).read_bytes())
            sizes.append(sum(item["bytes"] for artifact in lock["records"]["artifacts"].values() for item in artifact["files"]))
        # Do not assume reflinks, hardlinks or cross-layout download reuse.
        protocol["bucket_costs"][bucket] = {"network_bytes_max": sum(sizes) + 16*1024**3,
            "temporary_disk_bytes_max": sum(sizes) + max(sizes) + 32*1024**3}
    write(package_root / "protocol.json", protocol)
    (package_root / "execution.lock.json").write_bytes((package_root / "executions/splash-q4.json").read_bytes())
    (package_root / "runner.py").write_text(
        '"""Question-owned entry point; the first-attempt runner belongs to this study."""\n'
        'from pathlib import Path\nimport sys\n'
        'sys.path.insert(0, sys.argv[sys.argv.index("--field-kit-runtime") + 1])\n'
        'from fieldkit_runtime.experiments.qwen.splash_study import main\n'
        'raise SystemExit(main(Path(__file__).resolve().parent))\n')
    (package_root / "PROMPT.md").write_text(
        '# Qwen on Splash and larger-memory Macs\n\n'
        'Run the frozen machine bucket. Preserve first attempts, partial responses, '
        'failed startup and failed memory admission. Never repair or retry a task. '
        'The 36 GiB context ladder stops at the first unsuccessful point; report a '
        'bracket, not a global ceiling. Passing Flask tests requires source review.\n')
    def identity(name):
        return {"path": name, "sha256": hashlib.sha256((package_root / name).read_bytes()).hexdigest()}
    package.update(revision=4,
        origin={"kind": "v3-preparation", "method": protocol["schema"], "baseline": "Extracted Flask first attempts from the 26 September Splash/llama comparison."},
        question="What context fits on 36 GiB with Splash, and which engines and larger quants are useful on larger Macs?",
        decision="Add attributable context brackets, engine/quant viability and completed-work performance after review.",
        summary="36 GiB: Splash filled context and UD-Q4/Q5/Q6. 48 GiB+: Splash, Rapid MLX, vLLM Metal and llama.cpp Q5/Q6, with Q8 as a preflighted candidate.",
        evidence_scope="One exact Mac and first-attempt workload. Different weight/template compositions remain distinct; no engine-only causal claim, global context maximum or broad quality qualification.")
    package["host"]["required_primitives"] = sorted(set(package["host"]["required_primitives"] + ["execution-paths"]))
    package["applicability"].update(chip_prefixes=["Apple M"], min_physical_memory_mib=36*1024, min_wired_limit_mib=27*1024)
    actions = []
    for cell in cells:
        watch = copy.deepcopy(protocol["process_watch"])
        if cell["family"] in ("splash", "vllm-metal"):
            size=(2 if cell["family"]=="splash" else 4)*1024**3
            watch["roles"].append({"id":"frontend", "rss_bytes_max":size,
                "current_footprint_bytes_max":size, "peak_footprint_bytes_max":size})
        if cell["family"]=="vllm-metal":
            watch["roles"].append({"id":"resource-tracker", "rss_bytes_max":256*1024**2,
                "current_footprint_bytes_max":256*1024**2, "peak_footprint_bytes_max":256*1024**2})
        watch["roles"].sort(key=lambda role:role["id"])
        actions.append({"id": cell["id"], "kind": "measurement", "parameters": [], "attempts_max": 1,
                        "runtime_minutes_max": 920 if cell["kind"] == "coding" else 440,
                        "evidence_bytes_max": (256 if cell["kind"] == "coding" else 128) * 1024**2, "process_watch": watch})
    actions.append({"id": "finish-study", "kind": "final-validation", "parameters": [], "attempts_max": 1,
                    "runtime_minutes_max": 90, "evidence_bytes_max": 16 * 1024**2, "process_watch": None})
    # Only one bucket is executed. The shared envelope covers the larger of the
    # two fixed routes; each action still has its own independent time bound.
    route_minutes = max(sum(next(a["runtime_minutes_max"] for a in actions if a["id"] == cell["id"])
                            for cell in cells if bucket in cell["buckets"]) for bucket in ("36", "48-plus")) + 90
    package["investigation"] = {"schema": "field-kit-investigation/v2", "parameters": [], "actions": sorted(actions,key=lambda a:a["id"]),
        "initial_action": {"id": "coding-splash-q4", "parameters": {}}, "final_validation_action": "finish-study",
        "action_selection": "result-directed", "total_attempts_max": 10, "total_runtime_minutes_max": route_minutes}
    package["cost"].update(live_runtime_minutes_max=route_minutes,
        network_bytes_max=max(cost["network_bytes_max"] for cost in protocol["bucket_costs"].values()),
        temporary_disk_bytes_max=max(cost["temporary_disk_bytes_max"] for cost in protocol["bucket_costs"].values()),
        retained_disk_bytes_max=3*1024**3, evidence_bytes_max=3*1024**3)
    package["consent"]["writes"].append("scoped generated Flask candidates and sandboxed test results")
    package["profile"] = {"layout": "splash-q4"}
    package["execution_lock"] = identity("execution.lock.json")
    package["mechanics"].update(mode="splash-q4", prompt=identity("PROMPT.md"), runner=identity("runner.py"),
        protocols=[identity(name) for name in ["protocol.json", "workloads.json", "flask.tar.gz"]
                   + ["executions/"+name+".json" for name in ("splash-q4", "splash-q5", "splash-q6", "rapid-mlx", "vllm-metal", "llama-q5", "llama-q6", "llama-q8")]],
        runtime_protocol={"id":"qwen-splash-study", "revision":1, "schema":protocol["schema"]})
    write(package_root / "package.json", package)
    write(root / "catalog/questions.json", {"schema":"field-kit-question-catalog/v3", "revision":4,
        "compiled_at":"2026-09-27T00:00:00Z", "questions":[{"id":package["id"], "revision":4, "availability":"qualifying",
            "package_path":"packages/qwen-machine-study@4/package.json", "package_sha256":identity("package.json")["sha256"],
            "reason":"Prepared matrix and pinned runners. Native qualification on eligible 36 GiB and 48 GiB+ Macs and a matching signed Temper release are pending."}]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temper", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    main(parser.parse_args())
