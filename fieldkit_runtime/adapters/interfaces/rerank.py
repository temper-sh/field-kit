"""rerank/v1: one query scored against its documents by the engine's own rerank route.

The engine's response is kept unchanged. Scores are recorded in document order;
an incomplete or malformed result is a failure, never a ranking. Quality belongs
to the suite that owns the relevance judgments.
"""
import copy
import math
import time
from ...common import Refusal

IDENTIFIER = "rerank/v1"
ROUTE = "/rerank"
EXPECT = ()
# Rerankers have no conversation history, so no model format applies.
MODEL_FORMAT = False


def validate_request(request):
    if not isinstance(request, dict) or set(request) != {"query", "documents"}:
        raise Refusal("a rerank step needs exactly a query and documents; Field Kit supplies model and top_n")
    if not isinstance(request["query"], str) or not request["query"].strip():
        raise Refusal("rerank query must be a nonempty string")
    documents = request["documents"]
    if not isinstance(documents, list) or not documents or not all(isinstance(text, str) and text for text in documents):
        raise Refusal("rerank documents must be a nonempty list of nonempty strings")


def build(step, preset, model, previous):
    request = copy.deepcopy(step["request"])
    return request, {**request, "model": preset, "top_n": len(request["documents"])}


def send(engine, files, body, timeout):
    """Return the response with its wall time, and any transport failure."""
    started = time.monotonic()
    try:
        response, error = engine.call(engine.native_path(ROUTE), body, timeout), None
    except (Exception, KeyboardInterrupt) as caught:
        response, error = None, caught
    return {"body": response, "service_seconds": time.monotonic() - started}, error


def scores(response, count):
    """Scores in request order. llama-server and CrispEmbed answer with results[index, relevance_score]."""
    entries = response.get("results", response.get("data")) if isinstance(response, dict) else None
    if not isinstance(entries, list) or len(entries) != count:
        raise ValueError(f"rerank response has {len(entries) if isinstance(entries, list) else 'no'} results for {count} documents")
    ordered = [None] * count
    for entry in entries:
        index = entry.get("index") if isinstance(entry, dict) else None
        score = entry.get("relevance_score", entry.get("score")) if isinstance(entry, dict) else None
        if type(index) is not int or not 0 <= index < count or ordered[index] is not None:
            raise ValueError(f"rerank response has an invalid or duplicate index {index!r}")
        if type(score) not in (int, float) or not math.isfinite(score):
            raise ValueError(f"rerank response has a non-finite or missing score for document {index}")
        ordered[index] = float(score)
    return ordered


def observe(response, body, step, engine, model):
    body_response, elapsed = response["body"], response["service_seconds"]
    count = len(body["documents"])
    usage = body_response.get("usage") if isinstance(body_response, dict) else None
    tokens = (usage or {}).get("prompt_tokens", (usage or {}).get("total_tokens"))
    tokens = tokens if type(tokens) is int and tokens >= 0 else None
    measured = {"elapsed_seconds": elapsed, "documents": count, "input_tokens": tokens,
                "documents_per_second": count / elapsed if elapsed else None,
                "input_tokens_per_second": tokens / elapsed if tokens is not None and elapsed else None,
                "rate_sources": {"documents": "request wall time",
                                 "input_tokens": "usage over request wall time" if tokens is not None else None},
                "scores": None, "response_complete": False}
    if body_response is None:
        return None, measured  # the transport failure is already recorded
    try:
        measured.update(scores=scores(body_response, count), response_complete=True)
    except ValueError as error:
        measured["failure"] = {"kind": "runtime-failure", "message": str(error)}
    return body_response, measured
