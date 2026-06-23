"""
Agent Relationship — 多 Agent 系统的关系感知 Skill。

给任何多 Agent 系统注入「社会层」：关系建模、Moloch 检测、关系修复。

用法:
    from agent_relationship import RelationshipTracker

    tracker = RelationshipTracker()
    tracker.track("alice", "bob", {"action": "help", "result": "success"})
    h = tracker.health("alice", "bob")
    print(tracker.summary())

四引擎:
    - "mock"      — 零配置，确定性模拟
    - "openai"    — GPT / Codex
    - "deepseek"  — V4 Flash / V4 Pro
    - "anthropic" — Claude / Claude Code
"""

from .tracker import RelationshipTracker
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
    "TrackResult",
    "Health",
    "NetworkReport",
    "MolochReport",
    "MolochZone",
    "RepairPath",
]

__version__ = "0.2.2"
