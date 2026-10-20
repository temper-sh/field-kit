"""Tests and an attributable source review, bound to an immutable first attempt."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .check import load_protocol, utc_now
from .coding import evaluation, patches
from .common import Refusal, canonical_json, digest, load_json
from .process import atomic_write, exclusive_lock, file_digest, runtime_identity


def _lock(directory):
    path = directory / ".review.lock"
    try:
        path.open("x").close()
    except FileExistsError:
        pass
    return exclusive_lock(path)


def evaluator(python: Path) -> dict[str, Any]:
    """Check the grading environment before spending hours on a coding trial."""
    python = python.absolute()  # Preserve a venv entry point; resolve only for hashing.
    script = ("import json,sys,importlib.metadata as m; "
              "import pytest,asgiref.sync,jinja2,werkzeug,click,itsdangerous,blinker; "
              "print(json.dumps({'version':sys.version,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))")
    try:
        result = subprocess.run([str(python), "-I", "-c", script], capture_output=True, timeout=30, check=True)
        identity = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise Refusal("coding review needs a ready Python environment with pytest, asgiref and Flask's dependencies;"
                      " supply it with --python. No candidate code was executed.") from error
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise Refusal("coding review requires macOS sandbox-exec")
    return {"path": str(python), "sha256": file_digest(python.resolve()), **identity}


def _source(repository: Path, directory: Path):
    result = load_json(directory / "result.json", 64 * 1024**2)
    if result.get("state") not in ("complete", "cancelled"):
        raise Refusal("finish or close the run before grading; its result is still changing")
    if result.get("protocol", {}).get("id") != "basic-check" or result["protocol"].get("revision") not in (1, 2, 3):
        raise Refusal("this review does not own that run's frozen tasks")
    if result.get("workload") and result["workload"]["kind"] != "coding":
        raise Refusal("source review applies to coding work")
    protocol, cases, _ = load_protocol(repository)
    return result, {case["id"]: case for case in cases}, protocol


def test(repository: Path, directory: Path, python: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    identity = evaluator(python)
    with _lock(directory):
        result, cases, protocol = _source(repository, directory)
        if result.get("quality_evaluator") and result["quality_evaluator"] != identity:
            raise Refusal("final grading needs the quality run's recorded evaluator")
        source_sha = file_digest(directory / "result.json")
        out = directory / "review"
        committed = out / "tests.json"
        if committed.exists():
            saved = load_json(committed, 16 * 1024**2)
            if saved["result_sha256"] != source_sha or saved["evaluator"] != identity:
                raise Refusal("existing tests belong to different inputs or evaluator; preserve them and investigate")
            return saved
        if out.exists():
            raise Refusal("review has an uncommitted attempt; keep it and inspect its evaluator shutdown before recovery")
        out.mkdir()
        fixture = out / "fixture"
        evaluation.unpack(repository / protocol["task"]["path"] / "flask.tar.gz", fixture)
        rows = []
        for row in result["cases"]:
            if row["id"] not in cases:
                raise Refusal(f"unknown frozen case: {row['id']}")
            case = cases[row["id"]]
            patch = row.get("patch")
            if not patch:
                rows.append({"id": row["id"], "status": "no-patch", "model_status": row["status"], "all_pass": False})
                continue
            arguments = patch.get("submission")
            if arguments is None:  # revision 1 archives retained the original response.
                arguments = patches.submission(load_json(directory / "cases" / row["id"] / "response.json", 64 * 1024**2))
            candidate = out / row["id"] / "candidate"
            candidate.parent.mkdir()
            applied = patches.apply(arguments, fixture / "base", candidate, case["edit_scope"])
            if digest(applied["diff"].encode()) != patch["diff_sha256"]:
                raise Refusal("the retained submission does not reproduce the recorded patch")
            # An unchanged-source control distinguishes a broken grader from a bad patch.
            control_out = out / row["id"] / "control"
            control_out.mkdir()
            original = [str(fixture / "base" / name) for name in case["original_tests"]]
            control = evaluation._run_group(fixture / "base", control_out, "original", original,
                                            str(python.absolute()), (fixture / case["oracle"]).parent, fixture / "base")
            if not evaluation.group_passes(control):
                rows.append({"id": row["id"], "status": "invalid-evaluator", "all_pass": False, "control": control,
                             "patch_sha256": patch["diff_sha256"]})
                continue
            graded = evaluation.evaluate(candidate, out / row["id"] / "tests", case, fixture, str(python.absolute()))
            rows.append({"id": row["id"], "status": "tested" if graded["valid"] else "invalid-evaluation",
                         "patch_sha256": patch["diff_sha256"], **graded})
        document = {"schema": "field-kit-coding-tests/v1", "result_sha256": source_sha, "at": utc_now(),
                    "field_kit": runtime_identity(),
                    "evaluator": identity, "fixture_sha256": protocol["task"]["fixture_sha256"],
                    "cases_sha256": protocol["task"]["cases_sha256"], "cases": rows}
        atomic_write(committed, canonical_json(document))
        return document


def assess(repository: Path, directory: Path, assessment: Path) -> dict[str, Any]:
    directory = directory.resolve(strict=True)
    with _lock(directory):
        result, cases, _ = _source(repository, directory)
        tests = load_json(directory / "review/tests.json", 16 * 1024**2)
        source_sha = file_digest(directory / "result.json")
        if tests["result_sha256"] != source_sha:
            raise Refusal("test results are stale")
        document = load_json(assessment)
        if document.get("result_sha256") != source_sha or not isinstance(document.get("reviewer"), str) or not document["reviewer"].strip():
            raise Refusal("review must name its reviewer and the exact result_sha256")
        reviews = document.get("cases")
        if not isinstance(reviews, list) or [row.get("id") for row in reviews] != [row["id"] for row in result["cases"]]:
            raise Refusal("review must cover every first attempt in recorded order")
        for source, tested, reviewed in zip(result["cases"], tests["cases"], reviews):
            if reviewed.get("patch_sha256") != (source.get("patch") or {}).get("diff_sha256"):
                raise Refusal("review patch identity differs from the recorded attempt")
            if reviewed.get("verdict") not in ("pass", "fail", "invalid"):
                raise Refusal("review verdict must be pass, fail or invalid")
            # These are explanations, not checkboxes. The agent reads the source,
            # the task and the test output; the runtime only enforces attribution.
            for key in ("behavior", "regressions", "test_quality"):
                if not isinstance(reviewed.get(key), str) or not reviewed[key].strip():
                    raise Refusal(f"review needs a concrete {key} assessment")
            if reviewed["verdict"] == "pass" and (tested["status"] != "tested" or not tested["all_pass"]):
                raise Refusal("a passing source review requires valid, passing original, independent and candidate tests")
            if tested["status"].startswith("invalid") and reviewed["verdict"] != "invalid":
                raise Refusal("a broken evaluation is missing evidence, not model failure")
        reviewed = {**document, "schema": "field-kit-coding-review/v1", "at": utc_now(),
                    "tests_sha256": file_digest(directory / "review/tests.json")}
        target = directory / "review.json"
        if target.exists():
            raise Refusal("this first attempt already has a review; preserve it when investigating a disagreement")
        atomic_write(target, canonical_json(reviewed))
        return reviewed


def verdict(directory: Path) -> str | None:
    """Only current tests plus current source review can qualify a candidate."""
    path = directory / "review.json"
    if not path.exists():
        return None
    review = load_json(path)
    if review.get("result_sha256") != file_digest(directory / "result.json") \
            or review.get("tests_sha256") != file_digest(directory / "review/tests.json"):
        raise Refusal("review no longer matches its run or tests")
    if not review["cases"]:
        return None
    return "pass" if all(row["verdict"] == "pass" for row in review["cases"]) else "not-qualified"


def prune(directory: Path) -> None:
    """Discard reproducible source copies only after a bound review exists."""
    try:
        status = verdict(directory)
    except Refusal:
        return  # Retain stale-review source copies without blocking runtime cleanup.
    if status is None:
        return
    for path in [directory / "review/fixture", *(directory / "review").glob("*/candidate"),
                 *(directory / "cases").glob("*/turn-*/candidate")]:
        if path.is_symlink():
            raise Refusal(f"refusing cleanup through symlink: {path}")
        if path.exists():
            shutil.rmtree(path)
    if status == "pass":
        # JSON responses and exact submissions retain the evidence. Successful
        # duplicate wire framing can go; failure streams stay for diagnosis.
        for path in (directory / "cases").glob("*/response.sse"):
            if not path.is_symlink():
                path.unlink()


def command(arguments, repository: Path) -> int:
    if arguments.assessment:
        result = assess(repository, arguments.run, arguments.assessment)
    else:
        try:
            result = test(repository, arguments.run, arguments.python)
        except evaluation.EvaluationFailure as error:
            raise Refusal(f"grading stopped: {error}. Retained {arguments.run / 'review'} for shutdown recovery;"
                          " no quality decision was committed.") from error
    print(json.dumps(result, indent=2))
    return 0
