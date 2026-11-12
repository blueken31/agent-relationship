"""Versioned, explicit assessment results shared by all backends."""

from dataclasses import dataclass
import math
from typing import Optional


def finite_number(value: object, minimum: float, maximum: float) -> bool:
    """JSON booleans, strings, NaN and infinities are never numeric evidence."""
    return (
        type(value) in (int, float)
        and minimum <= value <= maximum
        and math.isfinite(value)
    )


@dataclass(frozen=True)
class Assessment:
    """An observation, not permission to execute an action.

    ``confidence`` is an optional evidence indicator, not a calibrated
    probability. The bundled model and mock backends leave it at zero rather
    than inventing a confidence from the impact score.
    """

    status: str
    source: str
    impact_a: Optional[float]
    impact_b: Optional[float]
    reason: str
    error_code: Optional[str] = None
    confidence: float = 0.0
    goal_status: str = "unknown"
    model: Optional[str] = None
    version: str = "1"

    def __post_init__(self) -> None:
        if self.status not in ("assessed", "unknown"):
            raise ValueError("invalid assessment status")
        if self.source not in ("model", "mock", "not_requested"):
            raise ValueError("invalid assessment source")
        if self.source == "not_requested" and (self.status != "unknown" or self.goal_status != "unknown"):
            raise ValueError("an unrequested assessment cannot supply judgments")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("assessment reason is required")
        if self.goal_status not in ("met", "violated", "unknown"):
            raise ValueError("invalid goal status")
        if not finite_number(self.confidence, 0.0, 1.0):
            raise ValueError("invalid evidence indicator")
        if self.status == "assessed":
            if not all(finite_number(v, -1.0, 1.0) for v in (self.impact_a, self.impact_b)):
                raise ValueError("assessed impacts must be finite numbers in [-1, 1]")
        elif self.impact_a is not None or self.impact_b is not None:
            raise ValueError("unknown assessments must not contain impact estimates")
