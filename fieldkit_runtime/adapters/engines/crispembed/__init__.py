"""CrispEmbed's readiness route; it serves rerank presets and reports no native rates."""
from ..base import EngineAdapter


class CrispEmbed(EngineAdapter):
    health_path = "/health/ready"
