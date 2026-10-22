"""Explicit mapping from Temper adapter versions to fresh observation adapters."""
from ... import lockview
from ...common import Refusal
from .crispembed import CrispEmbed
from .llama_cpp import LlamaCpp
from .rapid_mlx import RapidMLX
from .splash import Splash
from .vllm_metal import VLLMMetal

ADAPTERS = {
    "crispembed/v1": CrispEmbed,
    "llama-server/v1": LlamaCpp,
    "splash/v1": Splash,
    "splash/v2": Splash,
    "rapid-mlx/v1": RapidMLX,
    "vllm-metal/v1": VLLMMetal,
}


def resolve(identifier):
    if not isinstance(identifier, str) or identifier not in ADAPTERS:
        raise Refusal(f"unknown engine adapter {identifier!r}; see docs/ADAPTERS.md")
    return ADAPTERS[identifier](identifier)


def from_lock(lock):
    return resolve(lockview.engine_adapter(lock))
