"""Bounded llama-server measurements, including observable streaming latency."""
from __future__ import annotations

import http.client
import json
import threading
import time

from .probe import ProbeError
from .study import finite, grade, record_value


RESPONSE_LIMIT = 4 * 1024**2


def read_stream(response, started, *, clock=time.monotonic) -> dict:
    content, reasoning = [], []
    first_token = first_answer = None
    usage = timings = finish = None
    received = 0
    done = False
    while True:
        line = response.readline(65537)
        received += len(line)
        if received > RESPONSE_LIMIT or len(line) > 65536:
            raise ProbeError("stream exceeded its response bound")
        if not line:
            break
        stripped = line.strip()
        if not stripped or stripped.startswith(b":"):
            continue
        if not stripped.startswith(b"data:"):
            raise ProbeError("unexpected stream framing")
        data = stripped[5:].strip()
        if data == b"[DONE]":
            done = True
            break
        event = json.loads(data)
        if not isinstance(event, dict) or event.get("error"):
            raise ProbeError("server returned an invalid stream event")
        if event.get("usage") is not None:
            usage = event["usage"]
        if event.get("timings") is not None:
            timings = event["timings"]
        choices = event.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ProbeError("expected one streamed choice")
        for choice in choices:
            if choice.get("index", 0) != 0:
                raise ProbeError("unexpected choice index")
            delta = choice.get("delta", {})
            answer = delta.get("content") or ""
            thought = delta.get("reasoning_content") or ""
            if not isinstance(answer, str) or not isinstance(thought, str):
                raise ProbeError("non-text streamed content")
            elapsed = clock() - started
            if (answer or thought) and first_token is None:
                first_token = elapsed
            if answer and first_answer is None:
                first_answer = elapsed
            content.append(answer)
            reasoning.append(thought)
            if choice.get("finish_reason") is not None:
                finish = choice["finish_reason"]
    return {"content": "".join(content), "reasoning_characters": sum(map(len, reasoning)),
            "first_token_seconds": first_token, "first_answer_seconds": first_answer,
            "service_seconds": clock() - started, "usage": usage, "timings": timings, "finish_reason": finish,
            "stream_complete": done and finish is not None and first_token is not None}


def monitored_call(probe, path: str, payload: dict, timeout: float, *, streaming=False):
    if timeout <= 0:
        raise TimeoutError("study time budget exhausted")
    connection = http.client.HTTPConnection(probe.host, probe.port, timeout=timeout)
    results, failures = [], []
    done = threading.Event()
    started = time.monotonic()

    def worker():
        try:
            connection.request("POST", path, json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ProbeError(f"model HTTP status {response.status}: {response.read(512)!r}")
            if streaming:
                result = read_stream(response, started)
            else:
                raw = response.read(RESPONSE_LIMIT + 1)
                if len(raw) > RESPONSE_LIMIT:
                    raise ProbeError("response exceeded 4 MiB")
                result = json.loads(raw)
            results.append(result)
        except Exception as error:
            failures.append(error)
        finally:
            connection.close()
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while not done.wait(0.1):
            probe.ensure_healthy()
            probe.observe_engine()
            if time.monotonic() - started > timeout:
                raise TimeoutError("request reached the approved time limit")
        probe.ensure_healthy()
        if not probe.observe_engine():
            raise ProbeError("response has no observed bound engine")
        probe.validate_owned_boundary()
        if failures:
            raise failures[0]
        return results[0]
    finally:
        connection.close()


def measure_chat(probe, model: str, messages: list, expected: dict, window: int, reserve: int, timeout: float) -> dict:
    result = monitored_call(probe, "/v1/chat/completions", {
        "model": model, "messages": messages, "stream": True,
        "stream_options": {"include_usage": True}, "timings_per_token": True,
    }, timeout, streaming=True)
    usage, timings = result["usage"], result["timings"]
    problems = []
    if not result["stream_complete"]:
        problems.append("stream ended without a completed response")
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in ("prompt_tokens", "completion_tokens")):
        problems.append("response omitted valid input/output token counts")
    elif usage["prompt_tokens"] + reserve > window or usage["completion_tokens"] > reserve:
        problems.append("request exceeded its context or output allowance")
    if not isinstance(timings, dict) or any(not finite(timings.get(key)) for key in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n")):
        problems.append("response omitted valid engine timing counters")
    else:
        for tokens, milliseconds in (("prompt_n", "prompt_ms"), ("predicted_n", "predicted_ms")):
            if timings[tokens] > 0 and timings[milliseconds] <= 0:
                problems.append("positive token count has no elapsed engine time")
    result.update(
        correct=grade(result["content"], expected)["correct"] and result["finish_reason"] == "stop",
        measurement_valid=not problems, measurement_problems=problems,
        prefill_tokens_per_second=1000 * timings["prompt_n"] / timings["prompt_ms"] if not problems and timings["prompt_ms"] > 0 else None,
        generation_tokens_per_second=1000 * timings["predicted_n"] / timings["predicted_ms"] if not problems and timings["predicted_ms"] > 0 else None,
    )
    return result


def construct_context(target: int, token_count) -> tuple[list[dict], dict, dict]:
    """Fit a source-backed distributed ledger to the native chat token count."""
    values = {f"R{i + 1:02d}": record_value(4242, i) for i in range(5)}
    # Filler positions spread the records through the input. Adjust only the tail.
    fractions = (1 / 16, 3 / 16, 1 / 4, 1 / 4, 3 / 16)
    padding = [int(target * fraction) for fraction in fractions]
    tail = 0
    expected = {key: values[key] for key in ("R01", "R03", "R05")}
    followup = {key: values[key] for key in ("R02", "R04")}
    for _ in range(12):
        parts = ["Read this ledger. Filler is irrelevant; preserve the record values exactly."]
        for amount, (key, value) in zip(padding, values.items()):
            parts += [" x" * amount, f"\n{key}={value}\n"]
        parts += [" x" * tail, '\nReturn only a JSON object mapping R01, R03 and R05 to their values.']
        messages = [{"role": "user", "content": "".join(parts)}]
        actual = token_count(messages)
        if actual == target:
            return messages, expected, followup
        change = target - actual
        if tail + change >= 0:
            tail += change
        elif padding[-1] + tail + change >= 0:
            padding[-1] += tail + change
            tail = 0
        else:
            raise ProbeError("native tokenizer cannot fit the declared context target")
    raise ProbeError("native context construction did not converge")
