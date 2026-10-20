"""Bounded proposal, test and repair work; independent grading stays outside."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import time

from . import evaluation, patches
from ..common import canonical_json, digest
from ..process import atomic_write


SYSTEM = """Implement the requested change in the supplied Flask source snapshot.
Preserve unrelated behavior and include useful regression tests.
Use test_patch to apply a complete proposed patch in an isolated checkout and
run the existing and your own tests. Inspect the returned failures and repair
your proposal. Every proposal applies to the ORIGINAL supplied source, not to
the previous proposal. You may test revised proposals within the stated budget.
Finish with one submit_patch call containing the complete final implementation
and tests. Only report tests that actually ran. Independent tests and source
review assess the final submission; they are not available as repair feedback.
Use one tool call per response."""


def request_for(case, max_turns):
    request = copy.deepcopy(case["request"])
    request["messages"][0]["content"] = SYSTEM + f"\nYou have at most {max_turns} responses, including final submission."
    for message in request["messages"][1:]:
        message["content"] = message["content"].replace(
            "Do not claim tests ran.", "Only report tests actually returned by test_patch.")
    test = copy.deepcopy(patches.TOOLS[0])
    test["function"].update(name="test_patch", description="Test a complete proposal against the original source; receive visible test feedback.")
    request["tools"] = [test, *copy.deepcopy(patches.TOOLS)]
    return request


def visible_control(base, destination, case, python, deadline, *, observer=None):
    destination.mkdir()
    tests = [str(base / name.split("::")[0]) + ("::" + name.split("::", 1)[1] if "::" in name else "")
             for name in case["original_tests"]]
    control = evaluation._run_group(base, destination, "original", tests, str(python), base / "tests", base,
                                     deadline=deadline, observer=observer)
    if not evaluation.group_passes(control):
        raise evaluation.EvaluationFailure("unchanged-source control failed; no quality inference was requested",
                                           safe_to_cleanup=control["safe_to_cleanup"])


def feedback(result):
    """Expose visible diagnostics only, with bounded logs; never an oracle."""
    groups = {}
    for name in ("original", "candidate"):
        group = result["groups"][name]
        output = group.get("output")
        log = Path(output).read_text(errors="replace") if output else ""
        groups[name] = {key: group.get(key) for key in ("exit_code", "summary", "stopped", "status")}
        groups[name]["output"] = log[-24000:]
        groups[name]["output_truncated"] = len(log) > 24000
    return {"visible_tests_pass": result["all_pass"], "groups": groups,
            "instruction": "Inspect the failures. Repair the complete proposal against the original source, or submit_patch when ready."}


def run(case, stage, fixture, python, max_turns, deadline, request, summarize, row, *, observer=None):
    """One residency, durable attempts, no implicit retry after a failed effect."""
    base = fixture / "base"
    started = time.monotonic()
    detail = row["quality"] = {"status": "running", "max_turns": max_turns, "deadline": deadline, "turns": []}
    row["evaluator_safe_to_cleanup"] = True
    response = None
    try:
        visible_control(base, stage / "control", case, python, deadline, observer=observer)
        body = request_for(case, max_turns)
        for number in range(1, max_turns + 1):
            if time.time() >= deadline:
                raise TimeoutError("coding quality time budget exhausted")
            folder = stage / f"turn-{number:03d}"
            folder.mkdir()
            turn = {"number": number, "failure": None, "status": "requested"}
            detail["turns"].append(turn)
            atomic_write(folder / "turn.json", canonical_json(turn))
            response = request(folder, body, turn)
            turn.update(summarize(response), seconds=response.get("service_seconds"),
                        first_token_seconds=response.get("first_token_seconds"),
                        finish_reason=response.get("finish_reason"), stream_complete=response.get("stream_complete"),
                        reasoning_characters=len(response["message"].get("reasoning") or ""))
            if turn["failure"] or not response.get("stream_complete") or response.get("finish_reason") == "length":
                row["failure"] = turn["failure"] or {"kind": "incomplete", "message": "quality response did not finish"}
                detail["status"] = "incomplete"
                atomic_write(folder / "turn.json", canonical_json(turn))
                break
            message = response["message"]
            calls = message.get("tool_calls") or []
            call = calls[0] if len(calls) == 1 else {}
            tool = (call.get("function") or {}).get("name")
            reply = None
            try:
                if tool not in ("test_patch", "submit_patch") or not call.get("id"):
                    raise patches.InvalidPatch("Use one test_patch or submit_patch call with an ID.")
                arguments = patches.submission(response, tool=tool)
                # Validate before producing any candidate effects.
                patches.staged_texts(arguments, base, case["edit_scope"])
                turn["tool"] = tool
                if tool == "submit_patch":
                    turn["status"] = "submitted"
                    detail["status"] = "submitted"
                    atomic_write(folder / "turn.json", canonical_json(turn))
                    break
                applied = patches.apply(arguments, base, folder / "candidate", case["edit_scope"])
                turn.update(status="testing", patch_sha256=digest(applied["diff"].encode()))
                atomic_write(folder / "turn.json", canonical_json(turn))
                result = evaluation.evaluate(folder / "candidate", folder / "tests", case, fixture, python,
                                             visible_only=True, deadline=deadline, observer=observer)
                turn["visible_tests_pass"] = result["all_pass"]
                turn["status"] = "tested"
                if any(group.get("stopped") for group in result["groups"].values()):
                    row["failure"] = {"kind": "evaluation-stopped", "message": "visible tests hit a time or output guard"}
                    detail["status"] = "incomplete"
                    atomic_write(folder / "turn.json", canonical_json(turn))
                    break
                reply = feedback(result)
            except patches.InvalidPatch as error:
                turn["status"] = "invalid-proposal"
                reply = {"error": str(error), "instruction": "Repair the complete proposal against the original source."}
            atomic_write(folder / "turn.json", canonical_json(turn))
            # Reasoning is retained on disk, not replayed as an answer.
            assistant = {"role": "assistant", "content": message.get("content") or ""}
            if len(calls) == 1 and call.get("id") and tool in ("test_patch", "submit_patch"):
                assistant["tool_calls"] = calls
                returned = {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(reply)}
            else:
                returned = {"role": "user", "content": json.dumps(reply)}
            body["messages"].extend([assistant, returned])
        if detail["status"] == "running":
            detail["status"] = "turn-budget-exhausted"
            row["failure"] = {"kind": "quality-budget-exhausted", "message": "no final submission within the response budget"}
    except evaluation.EvaluationFailure as error:
        row["evaluator_safe_to_cleanup"] = error.safe_to_cleanup
        detail["status"] = "invalid-evaluator"
        raise
    except (TimeoutError, KeyboardInterrupt):
        detail["status"] = "interrupted"
        raise
    except Exception:
        detail["status"] = "runtime-failure"
        raise
    finally:
        detail["elapsed_seconds"] = time.monotonic() - started
        atomic_write(stage / "quality.json", canonical_json(detail))
    return response
