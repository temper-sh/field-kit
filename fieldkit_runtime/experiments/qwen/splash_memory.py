"""Observe Splash's Metal allocations separately from process RSS/footprint."""
from __future__ import annotations

import copy
import time

from ...catalog import Refusal
from ...probe import ProbeError
from .measurement import HTTPResponseError

SCHEMA = "field-kit-splash-memory/v1"


def snapshot(status):
    if not isinstance(status, dict) or status.get("ready") is not True:
        raise ValueError("Splash is not ready for a native memory observation")
    actual, instance, transport = status.get("memory_actual"), status.get("instance"), status.get("transport")
    if not isinstance(actual, dict) or not isinstance(instance, dict) or not isinstance(transport, dict):
        raise ValueError("Splash status omitted memory_actual, instance or transport")
    if transport.get("ready") is not True or transport.get("recovering") is not False or transport.get("status_stale") is not False:
        raise ValueError("Splash native status is stale or recovering")
    restarts = transport.get("restarts")
    if type(restarts) is not int or restarts < 0:
        raise ValueError("Splash status omitted its native restart count")
    result = {"instance_id": instance.get("id"), "frontend_pid": instance.get("pid"), "engine_restarts": restarts}
    for key in ("current_bytes", "peak_bytes"):
        value = actual.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Splash omitted a nonnegative integer memory_actual." + key)
        result[key] = value
    if result["peak_bytes"] < result["current_bytes"]:
        raise ValueError("Splash native peak is below its current allocation")
    if not isinstance(result["instance_id"], str) or not result["instance_id"] or type(result["frontend_pid"]) is not int or result["frontend_pid"] <= 0:
        raise ValueError("Splash status omitted its process identity")
    return result


def state(record):
    if record["invalidated"]:
        return "invalid"
    if record["last"] is None:
        return "unmeasured"
    if record["loaded"] is None or not record["finished"] or record["issues"]:
        return "partial"
    return "observed"


class SplashMemory:
    """Bounded status reads; retain load and last native lifetime high-water mark.

    The native peak retains transients between polls. Never combine it with
    process counters or with a new instance after a reset. Missing readings do
    not erase the independent task/timing evidence.
    """

    def __init__(self, read_status, source, interval_seconds, *, clock=time.monotonic):
        self.read_status, self.clock = read_status, clock
        self.interval, self.next_poll = interval_seconds, 0.0
        self.record = {"schema": SCHEMA, "source": source, "samples": 0,
                       "loaded": None, "last": None, "finished": False,
                       "invalidated": False, "issues": []}

    def capture(self, phase):
        if self.record["invalidated"]:
            return
        try:
            value = snapshot(self.read_status())
            previous = self.record["last"]
            if previous and (any(value[k] != previous[k] for k in ("instance_id", "frontend_pid", "engine_restarts"))
                             or value["peak_bytes"] < previous["peak_bytes"]):
                self.record["invalidated"] = True
                raise ValueError("Splash instance changed or native peak counter reset")
            if phase == "loaded":
                self.record["loaded"] = value
            self.record["last"] = value
            self.record["samples"] += 1
            if phase == "final":
                self.record["finished"] = True
        except (Exception, KeyboardInterrupt) as error:
            issue = (phase + ": " + str(error))[:240]
            if issue not in self.record["issues"] and len(self.record["issues"]) < 8:
                self.record["issues"].append(issue)
            if isinstance(error, ProbeError) and not isinstance(error, HTTPResponseError):
                # Ownership and resource guards must propagate to the task's
                # normal stop path, even when detected by an optional read.
                self.record["invalidated"] = True
                raise
            if isinstance(error, KeyboardInterrupt):
                raise
        finally:
            self.next_poll = self.clock() + self.interval

    def poll(self):
        if self.clock() >= self.next_poll:
            self.capture("request")

    def result(self):
        return copy.deepcopy({**self.record, "state": state(self.record)})


def review(record, preset):
    """Check reported counter meaning and consistency, without claiming attestation."""
    try:
        if (not isinstance(record, dict) or record["schema"] != SCHEMA
                or record["source"] != "/upstream/" + preset + "/status"
                or type(record["samples"]) is not int or record["samples"] < 0
                or any(type(record[k]) is not bool for k in ("finished", "invalidated"))
                or not isinstance(record["issues"], list) or len(record["issues"]) > 8
                or any(not isinstance(issue, str) or not issue for issue in record["issues"])):
            raise ValueError("invalid source, sample count or observation state")
        for name in ("loaded", "last"):
            value = record[name]
            if value is not None:
                checked = snapshot({"ready": True, "instance": {"id": value["instance_id"], "pid": value["frontend_pid"]},
                                    "transport": {"ready": True, "recovering": False, "status_stale": False, "restarts": value["engine_restarts"]},
                                    "memory_actual": value})
                if checked != value:
                    raise ValueError("invalid native memory snapshot")
        loaded, last = record["loaded"], record["last"]
        if bool(record["samples"]) != (last is not None) or (record["finished"] and last is None):
            raise ValueError("missing native memory observation")
        if loaded and record["finished"] and record["samples"] < 2:
            raise ValueError("load and final observations require separate readings")
        if loaded and (not last or any(loaded[k] != last[k] for k in ("instance_id", "frontend_pid", "engine_restarts"))
                       or loaded["peak_bytes"] > last["peak_bytes"]):
            raise ValueError("native memory observations cross a reset")
        if record["invalidated"] and not record["issues"]:
            raise ValueError("invalidated native memory has no reason")
        if record["state"] != state(record):
            raise ValueError("native memory state differs from its observations")
    except (KeyError, TypeError, ValueError) as error:
        raise Refusal("invalid Splash memory evidence: " + str(error)) from error


def report_lines(rows):
    details = []
    lines = ["", "Splash native Metal allocations (GiB). These overlap process counters; do not add them.", "",
             "| Configuration | Task | After load GiB | Engine lifetime peak GiB | Observation |",
             "|---|---|---:|---:|---|"]
    for row in rows:
        for index, resources in enumerate(row["resources"]):
            memory = resources.get("native_memory")
            case = row["cases"][index]["id"] if row["kind"] == "coding" and index < len(row["cases"]) else row["kind"]
            loaded = peak = "unmeasured"
            observed = memory["state"] if memory else "not collected"
            if memory and observed != "invalid":
                if memory["loaded"] is not None:
                    loaded = f'{memory["loaded"]["current_bytes"]/1024**3:.2f}'
                if memory["last"] is not None:
                    peak = ("≥" if observed == "partial" else "") + f'{memory["last"]["peak_bytes"]/1024**3:.2f}'
            lines.append(f"| {row['id']} | {case} | {loaded} | {peak} | {observed} |")
            if memory and memory["issues"]:
                detail = "; ".join(memory["issues"]).replace("\n", " ").replace("|", "/")
                details.append(f"{row['id']} native memory: {detail}")
    lines += ["", "Loaded is measured after readiness, before tokenization or inference. Peak includes model loading and all requests in that engine process; a context point includes its continuation.",
              "Partial peaks are lower bounds. Native allocations, process footprint/RSS and configured limits are different quantities; none alone establishes minimum machine RAM."]
    for detail in details:
        lines += ["", detail]
    return lines
