"""
Agent Relationship — 公开返回类型
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class TrackResult:
    """track() 返回的一次交互结果"""

    balance: float
    relation_type: str
    impact: float
    interaction_count: int
    # ── v0.2.0: 风险感知 ──
    risk: str = "low"  # "low" | "medium" | "high" | "critical"
    prev_balance: Optional[float] = None  # 交互前的 balance
    risk_transition: Optional[str] = None  # "improved" | "worsened" | "stable"


@dataclass
class Health:
    """health() 返回的双边关系健康度"""

    balance: float
    trust: float
    relation_type: str
    interaction_count: int
    symbiotic: int
    parasitic: int
    conflictual: int
    power_asymmetry: float
    risk: str  # "low" | "medium" | "high" | "critical"


@dataclass
class NetworkReport:
    """network() 返回的全网关系矩阵"""

    pairs: Dict[Tuple[str, str], Optional[float]]
    avg_balance: float
    weakest_link: Optional[Tuple[str, str, float]]
    agent_count: int
    relationship_count: int

    def heatmap(self) -> str:
        """生成 ASCII 热力图"""
        agents = sorted(
            set(a for (a, _) in self.pairs)
            | set(b for (_, b) in self.pairs)
        )
        if not agents:
            return "(无关系数据)"

        col_w = max(max(len(a) for a in agents), 6)
        header = " " * (col_w + 2) + "".join(
            f"{a:^{col_w + 2}}" for a in agents
        )
        lines = [header]

        for ai in agents:
            row = f"{ai:<{col_w + 2}}"
            for aj in agents:
                if ai == aj:
                    row += f"{'—':^{col_w + 2}}"
                else:
                    key = (
                        (ai, aj) if (ai, aj) in self.pairs else (aj, ai)
                    )
                    val = self.pairs.get(key)
                    if val is None:
                        row += f"{'·':^{col_w + 2}}"
                    elif val >= 0.7:
                        row += f"{'🤝' + f'{val:.2f}':^{col_w + 2}}"
                    elif val >= 0.4:
                        row += f"{'👋' + f'{val:.2f}':^{col_w + 2}}"
                    elif val >= 0.2:
                        row += f"{'😐' + f'{val:.2f}':^{col_w + 2}}"
                    else:
                        row += f"{'💔' + f'{val:.2f}':^{col_w + 2}}"
            lines.append(row)
        return "\n".join(lines)

    def to_pandas(self):
        """导出为 pandas DataFrame"""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("需要 pandas: pip install pandas")

        agents = sorted(
            set(a for (a, _) in self.pairs)
            | set(b for (_, b) in self.pairs)
        )
        if not agents:
            raise ValueError("无关系数据可导出")

        data = {}
        for a in agents:
            row = []
            for b in agents:
                if a == b:
                    row.append(1.0)
                else:
                    key = (
                        (a, b) if (a, b) in self.pairs else (b, a)
                    )
                    row.append(self.pairs.get(key, 0.0))
            data[a] = row
        return pd.DataFrame(data, index=agents)


# ── v0.2.0: MolochZone 类型化 ──


@dataclass
class MolochZone:
    """Moloch 检测到的竞争升级区域"""

    agents: List[str]
    size: int
    avg_balance: float
    trend: str  # "stable" | "deteriorating" | "improving"
    severity: str  # "watch" | "moderate" | "high" | "critical" ...
    first_detected: bool  # 是否首次检出


@dataclass
class MolochReport:
    """detect_moloch() 返回的检测报告"""

    active: bool
    zones: List[MolochZone]
    total_relationships: int
    balanced_ratio: float
    at_risk_pairs: List[Tuple[str, str, float]] = field(
        default_factory=list
    )


@dataclass
class RepairPath:
    """repair_paths() 返回的修复路径"""

    path: str
    description: str
    cost: str  # "low" | "medium" | "high"
    success_probability: float
