"""Task-linked collaboration diagnostics and recovery bookkeeping.

Observation-only SDK: the host controls authorization and task execution.
The default Mock backend provides explicit simulated fixtures.
"""

from .tracker import RelationshipTracker
from .assessment import Assessment
from .types import (
    TrackResult,
    Health,
    NetworkReport,
    MolochReport,
    MolochZone,
    RepairPath,
)

__all__ = [
    "RelationshipTracker",
    "Assessment",
    "TrackResult",
    "Health",
    "NetworkReport",
    "MolochReport",
    "MolochZone",
    "RepairPath",
]

__version__ = "0.3.0"
