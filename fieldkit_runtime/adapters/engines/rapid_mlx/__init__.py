"""Rapid's readiness route and helper process; native rates remain unmeasured."""
from ..base import EngineAdapter


class RapidMLX(EngineAdapter):
    health_path = "/health/ready"
    helper_limits = {"router": 2 * 1024**3, "crash-log": 2 * 1024**3}
