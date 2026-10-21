"""Translate observed counters without deriving native rates from wall time."""
import math


def mapping(value):
    return value if isinstance(value, dict) else {}


def count(value):
    return value if type(value) is int and value >= 0 else None


def number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def chat_usage(response):
    usage = mapping(response.get("usage"))
    return {
        "input_tokens": count(usage.get("prompt_tokens")),
        "output_tokens": count(usage.get("completion_tokens")),
        "cached_tokens": count(mapping(usage.get("prompt_tokens_details")).get("cached_tokens")),
        "prefill_tokens_per_second": None,
        "generation_tokens_per_second": None,
        "rate_sources": {"prefill": None, "generation": None},
    }


def llama_timings(response):
    """llama.cpp and Splash's native timings. Prompt count excludes cache reuse.

    Prefer the engine's explicit rate: its timed decode interval can exclude the
    first token even though predicted_n includes it. A count/time fallback is
    identified separately and used only when no explicit rate is reported.
    """
    result = chat_usage(response)
    timings = mapping(response.get("timings"))
    for label, prefix in (("prefill", "prompt"), ("generation", "predicted")):
        field = prefix + "_per_second"
        rate, source = number(timings.get(field)), None
        if rate is not None:
            source = "timings." + field
        elif field not in timings:
            tokens, elapsed = count(timings.get(prefix + "_n")), number(timings.get(prefix + "_ms"))
            if tokens is not None and elapsed is not None and elapsed > 0:
                try:
                    rate = number(tokens * 1000 / elapsed)
                except OverflowError:
                    rate = None
                if rate is not None:
                    source = f"timings.{prefix}_n / timings.{prefix}_ms"
        result[label + "_tokens_per_second"] = rate
        result["rate_sources"][label] = source
    return result
