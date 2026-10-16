"""Evaluate a first-attempt Flask patch with frozen original and independent tests.

Extracted from the Splash comparison. Candidate Python runs only inside the
macOS sandbox; returned result inspection never executes candidate code.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tarfile
import time
import xml.etree.ElementTree as ET

GROUP_TIMEOUT = 120
LOG_LIMIT = 8 * 1024 * 1024


class EvaluationFailure(RuntimeError):
    """A grading failure whose shutdown status must survive in the study."""
    def __init__(self, message, *, safe_to_cleanup):
        super().__init__(message)
        self.safe_to_cleanup = safe_to_cleanup


def unpack(archive_path, destination):
    """Extract only regular, bounded fixture files into a new directory."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 500 or sum(item.size for item in members) > 8 * 1024**2:
            raise ValueError("fixture exceeds its extraction bound")
        names = set()
        for item in members:
            if (not item.isfile() or item.name.startswith("/") or
                any(part in ("", ".", "..") for part in item.name.split("/")) or
                item.name in names or "\\" in item.name):
                raise ValueError("unsafe fixture member")
            names.add(item.name)
            path = destination / item.name
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source:
                path.write_bytes(source.read())
    return destination / "base"


def _stop(process):
    # Fork/spawn is denied by the inherited sandbox. Only the direct child can
    # exist. Popen.send_signal polls under its wait lock and never signals an
    # already reaped child; we never use an old process-group id as authority.
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
    rows = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,pgid="],
                          capture_output=True, text=True, check=True, timeout=5)
    remaining = []
    for line in rows.stdout.splitlines():
        pid, ppid, pgid = map(int, line.split())
        if pgid == process.pid:
            remaining.append({"pid": pid, "ppid": ppid, "pgid": pgid})
    return remaining


def _sandbox_profile(candidate, destination, oracle, python, baseline):
    interpreter = Path(python).resolve()
    # The locked standalone interpreter contains its libraries and stdlib.
    allowed = [candidate, destination, baseline, oracle, interpreter.parent.parent,
               Path("/System"), Path("/usr/lib"), Path("/usr/share"),
               Path("/private/etc"), Path("/dev")]
    reads = " ".join("(subpath " + json.dumps(str(path.resolve())) + ")" for path in allowed)
    return ("(version 1) (allow default) (deny network*) (deny process-fork) "
            "(deny file-read*) (allow file-read-metadata) "
            "(allow file-read* (literal \"/\") " + reads + ") (deny file-write*) "
            "(allow file-write* (subpath " + json.dumps(str(destination)) +
            ') (literal "/dev/null"))')


def _counts(path):
    if not path.exists():
        return None
    suites = ET.parse(path).getroot()
    if suites.tag == "testsuite":
        suites = [suites]
    return {key: sum(int(s.attrib.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}


def _run_group(candidate, out, label, tests, python, oracle, baseline):
    destination = out / label
    destination.mkdir()
    temporary = destination / "tmp"
    temporary.mkdir()
    junit = destination / "junit.xml"
    log = destination / "output.txt"
    # Tests can read source/dependencies but cannot write outside their result
    # directory or access a network. This is a local experiment containment
    # boundary, not a claim to defeat deliberately hostile Python code.
    profile = _sandbox_profile(candidate, destination, oracle, python, baseline)
    (destination / "sandbox.sb").write_text(profile + "\n")
    bootstrap = (
        "import pathlib,sys; import flask,asgiref.sync; "
        "actual=pathlib.Path(flask.__file__).resolve(); "
        "expected=pathlib.Path(sys.argv.pop(1)).resolve(); "
        "assert actual==expected,(actual,expected); "
        "print('FLASK_SOURCE='+str(actual),flush=True); "
        "import pytest; raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    pytest_root = (baseline if label == "original" else
                   oracle if label == "hidden" else candidate)
    argv = ["/usr/bin/sandbox-exec", "-f", str(destination / "sandbox.sb"),
            python, "-B", "-c", bootstrap, str(candidate / "src/flask/__init__.py"),
            "-q", "--tb=short", "-p", "no:cacheprovider", "--import-mode=importlib",
            "-c", str(baseline / "pyproject.toml"), "--rootdir", str(pytest_root),
            "--confcutdir", str(pytest_root),
            "--basetemp", str(temporary / "pytest"), "--junitxml", str(junit),
            *tests]
    env = {"PATH": str(Path(python).parent) + ":/usr/bin:/bin",
           "TMPDIR": str(temporary), "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
           "PYTHONPATH": str(candidate / "src"), "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "NO_PROXY": "*", "PIP_NO_INDEX": "1"}
    (destination / "invocation.json").write_text(json.dumps({"argv": argv, "cwd": str(destination),
        "env": env, "timeout_seconds": GROUP_TIMEOUT, "log_limit_bytes": LOG_LIMIT}, indent=2) + "\n")
    start = time.monotonic()
    stopped = None
    interrupted = None
    with log.open("wb") as output:
        process = subprocess.Popen(argv, cwd=destination, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if time.monotonic() - start > GROUP_TIMEOUT:
                    stopped = "deadline"
                    break
                if log.stat().st_size > LOG_LIMIT:
                    stopped = "output-limit"
                    break
                time.sleep(0.05)
        except BaseException as error:
            interrupted = error
            stopped = type(error).__name__
        finally:
            try:
                remaining = _stop(process)
            except BaseException as error:
                raise EvaluationFailure("Could not verify pytest shutdown: " + str(error),
                                        safe_to_cleanup=False) from error
    result = {"exit_code": process.returncode, "seconds": time.monotonic() - start,
              "stopped": stopped, "output": str(log), 
              "summary": _counts(junit), "remaining_processes": remaining,
              "safe_to_cleanup": not remaining}
    (destination / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if remaining:
        raise EvaluationFailure(f"Unexpected pytest descendants remain: {remaining}",
                                safe_to_cleanup=False)
    if interrupted is not None:
        raise interrupted
    return result


def evaluate(candidate_path, output_dir, case, fixture, python, *, require_candidate_tests=True):
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("macOS sandbox-exec is required; candidate code was not run")
    baseline = Path(fixture) / "base"
    case_id = case["id"]
    candidate = Path(candidate_path).resolve(strict=True)
    out = Path(output_dir).resolve()
    if out == candidate or candidate in out.parents:
        raise ValueError("Evaluation output must be outside the candidate checkout")
    out.mkdir(parents=True, exist_ok=False)
    if not (candidate / "src/flask/__init__.py").is_file():
        raise ValueError("Expected a complete Flask source checkout")
    original = [str(baseline / name.split("::")[0]) +
                ("::" + name.split("::", 1)[1] if "::" in name else "")
                for name in case["original_tests"]]
    own_tests = []
    prefix = case["candidate_test_prefix"]
    for path in sorted((candidate / "tests").glob("test_*.py")):
        rel = str(path.relative_to(candidate))
        if rel.startswith(prefix) or (rel in case["edit_scope"]["existing_files"]
            and (not (baseline / rel).exists() or path.read_bytes() != (baseline / rel).read_bytes())):
            own_tests.append(str(path))
    groups = {}
    oracle = Path(fixture) / case["oracle"]
    groups["original"] = _run_group(candidate, out, "original", original, str(python), oracle.parent, baseline)
    groups["hidden"] = _run_group(candidate, out, "hidden", [str(oracle)], str(python), oracle.parent, baseline)
    if own_tests:
        groups["candidate"] = _run_group(candidate, out, "candidate", own_tests, str(python), oracle.parent, baseline)
    else:
        groups["candidate"] = {"exit_code": None, "seconds": 0, "output": None,
            "summary": None, "status": "missing", "required": require_candidate_tests}
    behavior = all(groups[name]["exit_code"] == 0 and not groups[name].get("stopped") and (groups[name].get("summary") or {}).get("tests", 0) > 0 for name in ("original", "hidden"))
    candidate_cases_executed = 0
    junit = out / "candidate/junit.xml"
    if junit.exists():
        module_prefix = prefix.replace("/", ".")
        candidate_cases_executed = sum(
            node.attrib.get("classname", "").startswith(module_prefix)
            and node.find("skipped") is None
            for node in ET.parse(junit).getroot().iter("testcase"))
    candidate_ok = ((groups["candidate"]["exit_code"] == 0 and not groups["candidate"].get("stopped") and
                     (candidate_cases_executed > 0 or not require_candidate_tests))
                    if own_tests else not require_candidate_tests)
    valid = all(groups[name].get("summary") is not None and not groups[name].get("stopped")
                for name in ("original", "hidden"))
    result = {"case_id": case_id, "candidate": str(candidate), "groups": groups, "valid": valid,
              "behavior_pass": behavior, "all_pass": behavior and candidate_ok,
              "require_candidate_tests": require_candidate_tests,
              "candidate_tests": own_tests,
              "candidate_test_cases_executed": candidate_cases_executed}
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
