"""Task-linked collaboration diagnostics and recovery bookkeeping.

Observation-only SDK: the host controls authorization and task execution.
The default Mock backend provides explicit simulated fixtures.
"""

from .tracker import RelationshipTracker
from .assessment import Assessment
from .storage import StorageError, StorageVersionError
from .state import StateError
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
    "StorageError",
    "StorageVersionError",
    "StateError",
    "TrackResult",
    "Health",
    "NetworkReport",
    "MolochReport",
    "MolochZone",
    "RepairPath",
]

__version__ = "0.5.0"
