"""
Agent Relationship — 关系修复机制

当 balance 低于阈值时:
  a) 低风险小额交互 (临时降低门槛)
  b) 通过中间人间接对话
  c) 请求第三方调解
"""

from typing import Dict, Tuple
import time

from .models import RelationProfile


class RepairMechanism:
    """三条修复路径 + 冷却机制"""

    REPAIR_COOLDOWN = 3600  # 两次修复之间最小间隔 (秒)

    def can_attempt_repair(
        self, profile: RelationProfile
    ) -> Tuple[bool, str]:
        """是否可以进行修复尝试"""
        if profile.balance >= 0.3:
            return (True, "关系尚可，无需特殊修复")

        cooldown_remaining = time.time() - profile.last_repair_time
        if cooldown_remaining < self.REPAIR_COOLDOWN:
            remaining = self.REPAIR_COOLDOWN - cooldown_remaining
            return (False, f"修复冷却中 ({int(remaining)}秒)")

        return (True, "可以进行修复尝试")

    def execute_repair_attempt(
        self, profile: RelationProfile
    ) -> Dict:
        """
        执行一次修复尝试。

        临时降低交互门槛为 balance * 1.5 (最多到 0.3)，允许一次低风险交互。
        """
        temp_threshold = min(0.3, profile.balance * 1.5)

        profile.repair_attempts += 1
        profile.last_repair_time = time.time()

        return {
            "status": "repair_granted",
            "temp_threshold": temp_threshold,
            "repair_attempt": profile.repair_attempts,
            "message": "已允许一次低风险交互，balance 将据此更新",
        }

    @staticmethod
    def available_paths() -> list:
        """返回所有可用的修复路径"""
        return [
            {
                "path": "indirect_dialogue",
                "description": "通过中间人进行间接对话",
                "cost": "low",
                "success_probability": 0.6,
            },
            {
                "path": "low_risk_interaction",
                "description": "尝试一次低风险小额交互",
                "cost": "medium",
                "success_probability": 0.4,
            },
            {
                "path": "third_party_mediation",
                "description": "请求第三方介入调解",
                "cost": "high",
                "success_probability": 0.7,
            },
        ]
