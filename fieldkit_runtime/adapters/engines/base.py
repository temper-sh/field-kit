"""Observation contract; Temper still owns installation and process lifecycle."""
from .metrics import chat_usage


class EngineAdapter:
    health_path = "/health"
    helper_limits = {"router": 2 * 1024**3}

    def __init__(self, identifier):
        self.identifier = identifier

    @property
    def family(self):
        return self.identifier.split("/")[0]

    def metrics(self, response):
        return chat_usage(response)

    def memory_observer(self, read, native_prefix):
        """Optional observer with capture, poll and measurements methods.

        read(path) is a bounded, ownership-checked HTTP GET supplied by execution.
        native_prefix is where the engine's own routes start: /upstream/PRESET
        behind Temper's router, empty for a local engine. Construction does not perform I/O.
        """
        return None
