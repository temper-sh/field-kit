"""Review the frozen matrix and submissions without executing returned Python."""
import copy
import difflib
import json
from pathlib import Path
import tempfile

from ...catalog import Refusal
from .coding import performance
from .evaluation import unpack
from .method import grade, record_value
from .patches import InvalidPatch, staged_texts, submission
from .splash_study import context_summary, matrix, next_cell


def review(package, completed, facts):
    protocol = json.loads(package.files["protocol.json"])
    workloads = json.loads(package.files["workloads.json"])
    cases = {case["id"]: case for case in workloads["cases"]}
    cells = matrix(protocol, facts)
    prior = []
    with tempfile.TemporaryDirectory(prefix="field-kit-review-") as temporary:
        archive = Path(temporary) / "fixture.tar.gz"
        archive.write_bytes(package.files["flask.tar.gz"])
        base = unpack(archive, Path(temporary) / "fixture")
        for report in completed:
            answers = report["answers"]
            rows = answers["completed-work"]["value"]["rows"]
            if answers["context"]["value"] != context_summary(rows):
                raise Refusal("context bracket differs from measured points")
            if report["action"]["id"] == "finish-study":
                if rows != prior:
                    raise Refusal("final review changed measured cells")
                continue
            cell = next_cell(cells, prior)
            if cell is None or report["action"]["id"] != cell["id"] or len(rows) != len(prior) + 1 or rows[:-1] != prior:
                raise Refusal("witness skipped, repeated or rewrote a matrix cell")
            row = rows[-1]
            if any(row[key] != cell[key] for key in ("id", "layout", "kind", "window")):
                raise Refusal("witness matrix identity differs")
            material = row.get("material")
            if material:
                lock = json.loads(package.files[cell["lock"]])
                expected = copy.deepcopy(lock["records"]["layouts"][cell["layout"]])
                expected["context_window_tokens"] = cell["window"]
                expected["request_defaults"]["max_output_tokens"] = 4096 if cell["kind"] == "context" else 100000
                if cell["family"] == "splash":
                    expected["engine_config"]["max_memory_bytes"] = min(facts["physical_memory_bytes"] * 3 // 4, facts["wired_limit_mib"]*1024**2, 96*1024**3)
                if material["settings"] != expected:
                    raise Refusal("witness settings differ from the frozen cell")
            expected_ids = list(cases) if cell["kind"] == "coding" else ["distributed-ledger", "ledger-followup"]
            if [item["id"] for item in row["cases"]] != expected_ids[:len(row["cases"])]:
                raise Refusal("witness omitted, reordered or repeated a task")
            for item in row["cases"]:
                measured = performance(item, item["native_input_tokens"])
                # A process watcher may invalidate an otherwise valid stream.
                if item["measurement_valid"] and (not measured["measurement_valid"] or item.get("failure")):
                    raise Refusal("witness claimed valid timing without a completed native-counted stream")
                for key in ("prefill_tokens_per_second", "generation_tokens_per_second", "observed_output_tokens_per_second"):
                    if item.get(key) != measured[key]:
                        raise Refusal("witness performance differs from its counters")
                if cell["kind"] == "context":
                    keys = (1, 3, 5) if item["id"] == "distributed-ledger" else (2, 4)
                    expected = {f"R{i:02d}": record_value(4242, i - 1) for i in keys}
                    if item["expected"] != expected:
                        raise Refusal("context oracle differs from the frozen ledger")
                    actual = grade(item["message"]["content"], item["expected"])["correct"] and item["finish_reason"] == "stop" and item["measurement_valid"]
                    if item["id"] == "distributed-ledger":
                        actual = actual and item["native_input_tokens"] == cell["window"] - 5120
                    if item["correct"] != actual:
                        raise Refusal("context grade differs from the delivered answer")
                    continue
                if item.get("patch"):
                    try:
                        texts, originals = staged_texts(submission(item), base, cases[item["id"]]["edit_scope"])
                    except InvalidPatch as error:
                        raise Refusal("retained patch cannot be applied: " + str(error)) from error
                    diff = "".join("".join(difflib.unified_diff(originals[name].splitlines(keepends=True), text.splitlines(keepends=True),
                        fromfile="a/"+name, tofile="b/"+name)) for name, text in sorted(texts.items()))
                    if item["patch"] != {"changed_files": sorted(texts), "diff": diff}:
                        raise Refusal("retained patch differs from its tool submission")
                if item.get("status") == "tests-pass-needs-review":
                    evaluation = item.get("evaluation") or {}
                    if not item["measurement_valid"] or not item.get("patch") or not evaluation.get("valid") or not evaluation.get("all_pass") or evaluation.get("candidate_test_cases_executed", 0) <= 0:
                        raise Refusal("passing task has no applied patch and complete test record")
                    for name in ("original", "hidden", "candidate"):
                        group = evaluation["groups"][name]
                        if group["exit_code"] != 0 or group.get("stopped") or (group.get("summary") or {}).get("tests", 0) <= 0:
                            raise Refusal("passing task contains an incomplete or failing test group")
            if cell["kind"] == "context":
                if row["target_input_tokens"] != cell["window"] - 5120:
                    raise Refusal("context target differs from the frozen point")
                if row["status"] == "passed" and (len(row["cases"]) != 2 or not all(item["correct"] for item in row["cases"])):
                    raise Refusal("context passed without two correct, valid answers")
            prior = rows
    return workloads["schema"]
