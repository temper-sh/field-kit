"""Splash process topology, timings and optional native memory observation."""
from ..base import EngineAdapter
from ..metrics import llama_timings
from .memory import SplashMemory


class Splash(EngineAdapter):
    helper_limits = {"router": 2 * 1024**3, "frontend": 2 * 1024**3}

    def metrics(self, response):
        return llama_timings(response)

    def memory_observer(self, read, preset):
        source = f"/upstream/{preset}/status"
        return SplashMemory(lambda: read(source), source, 5)
