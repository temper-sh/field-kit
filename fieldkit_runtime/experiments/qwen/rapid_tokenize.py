"""Tokenizer-only bridge for the pinned Rapid auto-tool text route.

Executed by the lock-bound Rapid interpreter. Never imports a Field Kit module
or loads model tensors. The chat response must independently match this count.
"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def count(target, body):
    from mlx_lm.utils import load_tokenizer
    from rapid_mlx.api.models import ChatCompletionRequest
    from rapid_mlx.api.utils import extract_multimodal_content
    from rapid_mlx.api.tool_calling import convert_tools_for_template
    from rapid_mlx.utils.tokenizer import (_apply_chat_template_sidecar,
        augment_eos_token_ids_from_generation_config, repair_byte_level_decoder)
    from rapid_mlx.utils.chat_template import apply_chat_template
    from rapid_mlx.service.helpers import (_TOOL_USE_SYSTEM_SUFFIX, _append_tool_use_suffix, count_prompt_tokens)
    config = json.loads((target / "config.json").read_bytes())
    tokenizer = load_tokenizer(target, {"trust_remote_code": False}, eos_token_ids=config["eos_token_id"])
    if any(m.get("reasoning_content") or m.get("reasoning") for m in body["messages"]):
        raise ValueError("prior reasoning is outside the frozen route")
    if body.get("tool_choice", "auto") != "auto":
        raise ValueError("only auto tool choice is supported")
    if not getattr(tokenizer, "chat_template", None):
        _apply_chat_template_sidecar(target, tokenizer)
    augment_eos_token_ids_from_generation_config(tokenizer, str(target))
    repair_byte_level_decoder(tokenizer)
    request = ChatCompletionRequest(**body)
    messages, images, videos = extract_multimodal_content(request.messages, preserve_native_format=True)
    if images or videos:
        raise ValueError("only the frozen text workload is supported")
    for message in messages:
        if message["role"] == "developer":
            message["role"] = "system"
    if request.tools:
        for message in messages:
            if message["role"] == "system":
                message["content"] = _append_tool_use_suffix(message.get("content"), _TOOL_USE_SYSTEM_SUFFIX)
                break
        else:
            messages.insert(0, {"role": "system", "content": _TOOL_USE_SYSTEM_SUFFIX.strip()})
    prompt = apply_chat_template(tokenizer, messages, tools=convert_tools_for_template(request.tools),
        enable_thinking=body["chat_template_kwargs"]["enable_thinking"], model_name=str(target),
        add_generation_prompt=True, chat_template_kwargs=request.chat_template_kwargs)
    bos = getattr(tokenizer, "bos_token", None)
    tokens = tokenizer.encode(prompt, add_special_tokens=bos is None or not prompt.startswith(bos))
    actual = count_prompt_tokens(SimpleNamespace(tokenizer=tokenizer), prompt)
    if actual <= 0 or actual != len(tokens):
        raise RuntimeError("Rapid native count differs from tokenizer encoding")
    return actual


if __name__ == "__main__":
    print(json.dumps({"count": count(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_bytes()))}))
