"""Explicitly skip semantic assessment; this is neither a model nor a mock."""
from .base import LLMBackend
from ..assessment import Assessment


class NoAssessment(LLMBackend):
    def chat(self, *args, **kwargs):
        raise RuntimeError("Semantic assessment is disabled")

    def assess_interaction(self, *args, **kwargs):
        return Assessment(status="unknown", source="not_requested", impact_a=None,
                          impact_b=None, reason="未请求语义评估；仅使用宿主记录的任务证据。",
                          error_code="assessment_not_requested")
