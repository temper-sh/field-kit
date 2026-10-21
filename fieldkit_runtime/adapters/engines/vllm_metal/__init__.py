"""vLLM Metal observations; frontend residency is separate from model memory."""
from ..base import EngineAdapter


class VLLMMetal(EngineAdapter):
    # The retained Gemma MLX investigation observed a legitimate 2.10 GiB
    # frontend. This prospective limit is disclosed and frozen at study preview.
    helper_limits = {"router": 2 * 1024**3, "frontend": 3 * 1024**3,
                     "resource-tracker": 2 * 1024**3}
