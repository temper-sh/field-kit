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
        if packet["schema"] != "field-kit-evidence-export/v3":
            raise Refusal("unsupported witness export")
        session = json.loads(packet["session"])
        plan = packet["plan"]
        if packet["package"]["sha256"] != package.package_sha256 or packet["package"] != session["package"]:
            raise Refusal("witness does not bind the supplied package")
        if digest(canonical_json(plan)) != packet["plan_sha256"] or packet["plan_sha256"] != session["plan"]["sha256"]:
            raise Refusal("witness plan identity differs")
        if plan["question"]["package_sha256"] != package.package_sha256 or plan["inputs"]["package"]["sha256"] != package.package_sha256:
            raise Refusal("witness plan refers to a different question")
        expected_files = [{"path": name, "sha256": digest(value), "bytes": len(value)} for name, value in sorted(package.files.items())]
        if plan["inputs"]["files"] != expected_files or plan["investigation"] != package.package["investigation"]:
            raise Refusal("witness plan differs from frozen inputs")
        if session["state"] != "complete" or digest(packet["report"].encode()) != session["report"]["sha256"]:
            raise Refusal("witness report is incomplete or altered")
        if packet["machine"] != session["machine_facts"] or packet["machine"] != plan["machine"]["facts"]:
            raise Refusal("witness machine attribution differs")
        completed = []
        attempts = [item for item in session["attempts"] if item.get("kind") == "question-action"]
        if len(attempts) != len(packet["action_evidence"]):
            raise Refusal("witness omitted a first attempt")
        for attempt, evidence in zip(attempts, packet["action_evidence"]):
            if attempt["id"] != evidence["attempt"] or attempt["state"] != evidence["state"] or digest(canonical_json(evidence["action"])) != attempt["action"]["sha256"]:
                raise Refusal("witness attempt identity differs")
            if attempt["state"] == "complete":
                terminal = evidence["report"]
                if digest(canonical_json(terminal)) != attempt["protocol_report"]["sha256"] or terminal["plan_sha256"] != packet["plan_sha256"]:
                    raise Refusal("witness action report differs")
                from ...workflow import validate_action_report
                validate_action_report(terminal, canonical_json(terminal), attempt["action"]["sha256"], packet["plan_sha256"],
                                       session["package"]["protocol"]["schema"], package.package["profile"]["layout"], session["generation"])
                completed.append(terminal)
        if not completed or completed[-1]["answers"] != session["answers"]:
            raise Refusal("witness answers differ from the retained observation")
        answers = session["answers"]
        if "workloads.json" not in package.files:
            raise Refusal("this reader requires a frozen workload package")
        workload_schema = review_tasks(package, completed)
        return {"schema": "field-kit-witness-review/v1", "package": package.selector, "package_sha256": package.package_sha256, "export_sha256": digest(data), "machine": packet["machine"], "answers": answers, "cleanup": session.get("cleanup"), "attempts": len(attempts), "workload_schema": workload_schema, "boundary": "Integrity and delivered JSON task grades checked across all completed attempts. Context/tokenizer, cache, interface, resource and controller claims require protocol review. Operator/external-machine provenance, timing plausibility and public conclusions still require review; this is not authenticated remote attestation."}
    except (KeyError, TypeError, IndexError, ValueError) as error:
        if isinstance(error, Refusal):
            raise
        raise Refusal(f"malformed or inconsistent witness: {error}") from error
