"""Read-only integrity and delivered-task checks for returned witnesses."""
from __future__ import annotations
import json
from pathlib import Path
from ...catalog import Refusal, canonical_json, digest, load_question_material
from .method import SCHEMA, grade, record_value
from ...answers import validate_answers


def review_tasks(package, completed: list[dict]) -> str:
    workloads = json.loads(package.files["workloads.json"])
    schema = workloads["schema"]
    if schema != "field-kit-workloads/v1" or package.package["mechanics"]["runtime_protocol"]["schema"] != SCHEMA:
        raise Refusal("this reader does not support the frozen workload schema")
    expected = {case["id"]: case["expected"] for case in workloads["cases"]}
    for terminal in completed:
        answers = terminal["answers"]
        validate_answers(answers, package.package["report"]["answer_fields"])
        rows = answers["completed-work"].get("value", {}).get("cases", [])
        if [row["id"] for row in rows] != list(expected)[:len(rows)]:
            raise Refusal("witness workload order differs")
        passed = []
        for row in rows:
            correct = grade(row["content"], expected[row["id"]])["correct"] and row.get("finish_reason") == "stop"
            passed.append(correct)
            if row["correct"] != correct:
                raise Refusal("witness grade differs from its delivered artifact")
        if answers["completed-work"]["state"] == "observed" and (len(rows) != len(expected) or not all(passed)):
            raise Refusal("witness claims completed work without every required artifact")
        review_study(package, answers, expected)
    return schema


def review_study(package, answers, expected):
    """Recompute public summaries and every tuning oracle from the frozen inputs."""
    import math
    from .method import confirm_candidate, context_summary, finite, performance_summary, variant_records
    base = json.loads(package.files["execution.lock.json"])
    protocol = json.loads(package.files["protocol.json"])
    model = package.package["profile"]["layout"]
    tuning = answers["tuning"]["value"]
    baseline = {"configuration": "baseline", "valid": answers["interaction"]["state"] == "observed",
                "correct": answers["completed-work"]["state"] == "observed",
                "cases": answers["completed-work"]["value"]["cases"],
                "resources": answers["fit"].get("value", {}), "material": answers["profile"]["value"]["baseline"]}
    if performance_summary(baseline) != answers["interaction"]["value"]:
        raise Refusal("baseline performance summary differs from the requests")

    def check_run(run, oracles, settings):
        material = run.get("material")
        if material is not None and material.get("settings") != settings:
            raise Refusal("tuning configuration differs from its declared hypothesis")
        if run["valid"] and material is None:
            raise Refusal("valid measurement lacks a bound configuration")
        rows = run["cases"]
        if [row["id"] for row in rows] != list(oracles)[:len(rows)] or (run["valid"] and len(rows) != len(oracles)):
            raise Refusal("tuning observation omitted or reordered a required task")
        passed = []
        for row in rows:
            correct = grade(row["content"], oracles[row["id"]])["correct"] and row.get("finish_reason") == "stop"
            if correct != row["correct"]:
                raise Refusal("tuning grade differs from the delivered answer")
            if run["valid"]:
                if row.get("measurement_valid") is not True or not finite(row.get("service_seconds"), positive=True):
                    raise Refusal("valid run contains an invalid request measurement")
                timing = row.get("timings")
                if not isinstance(timing, dict):
                    raise Refusal("valid run omitted native timing counters")
                for count, elapsed, rate in (("prompt_n", "prompt_ms", "prefill_tokens_per_second"),
                                             ("predicted_n", "predicted_ms", "generation_tokens_per_second")):
                    if not finite(timing.get(count)) or not finite(timing.get(elapsed)):
                        raise Refusal("native timing counter is not a finite nonnegative number")
                    if timing[count] > 0 and timing[elapsed] <= 0:
                        raise Refusal("positive token count lacks measured engine time")
                    calculated = 1000 * timing[count] / timing[elapsed] if timing[elapsed] > 0 else None
                    observed = row.get(rate)
                    if (calculated is None and observed is not None) or (calculated is not None and (not finite(observed) or not math.isclose(observed, calculated, rel_tol=1e-10))):
                        raise Refusal("reported throughput differs from native timing counters")
            passed.append(correct)
        if run.get("correct") and (len(rows) != len(oracles) or not all(passed)):
            raise Refusal("tuning run claims correctness without all required answers")
    for run in [baseline, *tuning.get("screens", []), *tuning.get("confirmation", [])]:
        settings = variant_records(base, model, run["configuration"])["layouts"][model]
        check_run(run, expected, settings)
    if tuning.get("confirmation"):
        decision = confirm_candidate(tuning["confirmation"], protocol["worthwhile_improvement_percent"], protocol["maximum_case_regression_percent"])
        if decision != tuning["decision"]:
            raise Refusal("tuning decision differs from the confirmation measurements")
    elif tuning["decision"].get("selection") not in ("baseline", "unresolved"):
        raise Refusal("candidate selected without confirmation")
    values = {f"R{i + 1:02d}": record_value(4242, i) for i in range(5)}
    oracles = {"distributed-ledger": {key: values[key] for key in ("R01", "R03", "R05")},
               "ledger-followup": {key: values[key] for key in ("R02", "R04")}}
    points = tuning.get("context_points", [])
    for point in points:
        settings = variant_records(base, model, "context", cache=point["cache"], window=point["window"])["layouts"][model]
        check_run(point, oracles, settings)
        if point["valid"]:
            rows = point["cases"]
            target = point["window"] - protocol["output_reserve_tokens"] - protocol["followup_reserve_tokens"]
            if point["input_tokens"] != target or rows[0]["usage"]["prompt_tokens"] != target:
                raise Refusal("context point did not measure the declared input length")
            if point["initial_seconds"] != rows[0]["service_seconds"] or point["followup_seconds"] != rows[1]["service_seconds"]:
                raise Refusal("context latency differs from the recorded requests")
    if "context_summary" in tuning:
        summary = context_summary(points, protocol["useful_initial_seconds"], protocol["useful_followup_seconds"])
        if summary != tuning["context_summary"] or summary != answers["context"]["value"]["summary"]:
            raise Refusal("context summary differs from the measured points")
    if points != answers["context"]["value"]["points"]:
        raise Refusal("context views differ")


def inspect(path: Path, package_path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024**2:
        raise Refusal("witness must be a regular JSON file no larger than 128 MiB")
    data = path.read_bytes()
    return inspect_bytes(data, package_path)


def inspect_bytes(data: bytes, package_path: Path) -> dict:
    package = load_question_material(package_path)
    try:
        packet = json.loads(data)
        if packet["schema"] != "field-kit-evidence-export/v4":
            raise Refusal("use the producing Field Kit revision to review this historical export")
        session, plan = packet["session"], packet["plan"]
        if session["schema"] != "field-kit-session/v4" or session["state"] != "complete":
            raise Refusal("witness session is incomplete or unsupported")
        if session["package"] != {"selector": package.selector, "sha256": package.package_sha256,
                                  "protocol": package.package["mechanics"]["runtime_protocol"]}:
            raise Refusal("witness does not bind the supplied package")
        if digest(canonical_json(plan)) != session["plan"]["sha256"]:
            raise Refusal("witness plan differs from the consented inputs")
        if plan["question"]["selector"] != package.selector or plan["inputs"]["package"]["sha256"] != package.package_sha256:
            raise Refusal("witness plan refers to a different question")
        expected_files = [{"path": name, "sha256": digest(value), "bytes": len(value)} for name, value in sorted(package.files.items())]
        if plan["inputs"]["files"] != expected_files or plan["investigation"] != package.package["investigation"]:
            raise Refusal("witness plan differs from frozen inputs")
        if session["machine_facts"] != plan["machine"]["facts"] or session["temper"] != plan["host"]["temper"] or session["field_kit_runtime"] != plan["host"]["field_kit_runtime"]:
            raise Refusal("witness machine or producer attribution differs")
        if session["execution"]["lock_sha256"] != package.package["execution_lock"]["sha256"]:
            raise Refusal("witness execution differs from the frozen lock")
        attempts = session["attempts"]
        if [row["id"] for row in attempts] != [f"attempt-{index + 1:04d}" for index in range(len(attempts))]:
            raise Refusal("witness omitted or reordered an attempt")
        from ...workflow import _collect_action_evidence
        evidence = _collect_action_evidence(session, package.package["investigation"], package.package["profile"]["layout"])
        completed = [row["report"] for row in evidence if row["state"] == "complete"]
        if not completed or completed[-1]["action"]["id"] != package.package["investigation"]["final_validation_action"]:
            raise Refusal("witness has no completed final validation")
        if package.package["mechanics"]["runtime_protocol"]["schema"] == "field-kit-qwen-splash-study/v1":
            from .splash_witness import review
            workload_schema = review(package, completed, session["machine_facts"])
        else:
            workload_schema = review_tasks(package, completed)
        return {"schema": "field-kit-witness-review/v2", "package": package.selector,
                "machine": session["machine_facts"], "answers": session["answers"],
                "cleanup": session.get("cleanup"), "attempts": len(evidence), "workload_schema": workload_schema,
                "boundary": "Frozen inputs, run/attempt references and delivered grades checked. Returned Python is never executed by this reader. Test execution, tokenizer, resource and timing claims are reported observations requiring review; this is not authenticated remote attestation."}
    except (KeyError, TypeError, IndexError, ValueError) as error:
        if isinstance(error, Refusal):
            raise
        raise Refusal(f"malformed or inconsistent witness: {error}") from error
