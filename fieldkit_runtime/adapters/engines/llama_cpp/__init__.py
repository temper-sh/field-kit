"""llama-server observations through Temper's chat-completions interface."""
from ..base import EngineAdapter
from ..metrics import llama_timings


class LlamaCpp(EngineAdapter):
    def metrics(self, response):
        return llama_timings(response)
