"""Prepare the bounded Q4 campaign through Temper's public authoring commands.

These are experiment inputs, not recommendations. Temper owns catalog facts,
Hugging Face file resolution, compilation and installation.
"""
from __future__ import annotations

import copy
from pathlib import Path
import tempfile

from .common import Refusal, canonical_json, load_json
from .process import exclusive_lock
from .temper import Temper

MLX_TARGET = "rapid-mlx/Qwen3.8-27B-4bit-MTP-MLX@aa985c29ff5b334cbfdcbbc787d47e66e9d9e456"
MLX_FILES = ("model-*.safetensors", "model.safetensors.index.json", "config.json",
             "generation_config.json", "tokenizer.json", "tokenizer_config.json",
             "chat_template.jinja", "vocab.json", "mtp/model.safetensors")
WEIGHTS = "qwen38-27b-mlx-q4"
DRAFT = "qwen38-27b-mlx-mtp"
CONTEXT = 65536
REQUEST_SECONDS = 14400
CANDIDATES = ("q4-64-llama", "q4-64-splash", "q4-64-rapid", "q4-64-vllm")


def configure(document: dict, family: str) -> dict:
    result = copy.deepcopy(document)
    preset = result["preset"]
    profile = copy.deepcopy(preset["profiles"][0])
    profile.update(min_memory_gib=64, context_window_tokens=CONTEXT)
    preset["profiles"] = [profile]
    preset["presentation"] = {
        "display_name": f"Qwen3.8 27B Q4 / {family} / 64 GiB study",
        "description": "Unmeasured experiment candidate; assess before recommending.",
        "group": "other"}
    if family == "splash":
        profile["engine_config"].update(max_memory_bytes=48 * 1024**3,
                                        request_timeout_seconds=REQUEST_SECONDS)
    elif family in ("rapid", "vllm"):
        preset["engine"] = "rapid-mlx" if family == "rapid" else "vllm-metal"
        preset["template"] = "builtin"
        preset["speculation"] = {"method": "none", "source": "none", "max_draft_tokens": 0}
        preset.pop("exclude_engine_versions", None)
        result.pop("templates", None)
        result["artifacts"] = {preset["artifact"]: result["artifacts"][preset["artifact"]]}
        weights = result["artifacts"][preset["artifact"]]
        weights["format"] = "mlx-safetensors" if family == "rapid" else "safetensors"
        weights["presentation"] = {"model_name": "Qwen3.8 27B",
                                   "weights_name": "MLX affine 4-bit / group 64"}
        common = {"max_num_seqs": 1, "gpu_memory_utilization": 0.75,
                  "prefix_cache": "off", "reasoning_parser": "qwen3"}
        if family == "rapid":
            if DRAFT in document.get("artifacts", {}):
                result["artifacts"][DRAFT] = copy.deepcopy(document["artifacts"][DRAFT])
                preset["speculation"] = {"method": "mtp", "source": "artifact",
                                         "draft_artifact": DRAFT, "max_draft_tokens": 3}
            # The released adapter requires a finite default. Each synthetic
            # request supplies its own diagnostic allowance. This is not a
            # measured output ceiling or remaining-context support.
            preset["request_defaults"]["max_output_tokens"] = CONTEXT
            tuning = {**common, "max_concurrent_requests": 1, "prefill_batch_size": 1,
                      "completion_batch_size": 1, "prefill_step_size": 1024,
                      "kv_cache_dtype": "bf16", "pflash": "off",
                      "tool_call_parser": "qwen3_coder_xml",
                      "request_timeout_seconds": REQUEST_SECONDS}
            profile["engine_config"] = {"kind": "rapid-mlx/v1", "rapid_mlx": tuning}
        else:
            preset["request_defaults"].pop("max_output_tokens", None)
            tuning = {**common, "block_size": 16, "chunked_prefill": True,
                      "kv_cache_dtype": "auto", "language_model_only": True,
                      "max_num_batched_tokens": 1024, "tool_call_parser": "qwen3_coder"}
            profile["engine_config"] = {"kind": "vllm-metal/v1", "vllm_metal": tuning}
    return result


def prepare(repository: Path, temper: Temper, destination: Path) -> None:
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / f".{destination.name}.prepare.lock"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        pass
    with exclusive_lock(lock):
        if destination.exists() or destination.is_symlink():
            raise Refusal("candidate destination already exists; retain it and choose a new directory")
        temper.identity()
        with tempfile.TemporaryDirectory(prefix=".q4-prepare-", dir=destination.parent) as temporary:
            root = Path(temporary) / "temper"
            output = Path(temporary) / "candidates"
            output.mkdir()
            temper.catalog_update(root)
            for candidate, origin in ((CANDIDATES[0], "qwen3.8-27b-q4xl-mtp"),
                                      (CANDIDATES[1], "qwen3.8-27b-q4xl-splash"),
                                      (CANDIDATES[2], "qwen3.8-27b-q4xl-mtp")):
                temper.document(["preset", "copy", origin, candidate, "--root", root], 120)
            path = root / "presets.d" / f"{CANDIDATES[2]}.json"
            source = load_json(path)
            intermediate = configure(source, "rapid")
            # Stage the engine change before Temper resolves/selects the MLX
            # material. The old, now unselected GGUF record keeps its true format.
            intermediate["artifacts"] = source["artifacts"]
            path.write_bytes(canonical_json(intermediate))
            temper.document(["preset", "weights", CANDIDATES[2], MLX_TARGET, *MLX_FILES,
                             "--weights-id", WEIGHTS, "--format", "mlx-safetensors",
                             "--root", root], 600)
            rapid = load_json(path)
            target = rapid["artifacts"][WEIGHTS]
            draft = copy.deepcopy(target)
            draft["format"] = "safetensors"
            draft["files"] = [file for file in target["files"] if file["path"] == "mtp/model.safetensors"]
            if len(draft["files"]) != 1:
                raise Refusal("pinned Rapid Qwen package is missing its MTP sidecar")
            target["files"] = [file for file in target["files"] if file["path"] != "mtp/model.safetensors"]
            rapid["artifacts"] = {WEIGHTS: target, DRAFT: draft}
            for candidate, family in zip(CANDIDATES, ("llama", "splash", "rapid", "vllm")):
                path = root / "presets.d" / f"{candidate}.json"
                document = configure(rapid if family in ("rapid", "vllm") else load_json(path), family)
                path.write_bytes(canonical_json(document))
                # Real Temper checks each composition, including software
                # compatibility. No weights or engine environments are installed.
                temper.compile(root, candidate, 64, Path(temporary) / f"{candidate}.lock.json")
                (output / path.name).write_bytes(path.read_bytes())
            if destination.exists() or destination.is_symlink():
                raise Refusal("candidate destination appeared during preparation; nothing replaced")
            output.rename(destination)
    print(f"Prepared {len(CANDIDATES)} Q4 candidates: {destination}")
    print("No models installed. Rapid includes its pinned MTP sidecar; compare plain decoding within the study.")
    print(f"Next: {repository / 'docs/experiments/qwen-q4-64.md'}")
