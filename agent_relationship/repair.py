"""Descriptive recovery paths. Host authority is never expanded by a diagnosis."""

import time
from typing import Dict, Tuple
from .models import RelationProfile


class RepairMechanism:
    """Legacy metadata API; use task-bound RecoveryLedger plans for recovery."""

    REPAIR_COOLDOWN = 3600

    def can_attempt_repair(self, profile: RelationProfile) -> Tuple[bool, str]:
        elapsed = time.time() - profile.last_repair_time
        if profile.last_repair_time > 0 and elapsed < self.REPAIR_COOLDOWN:
            return False, f"修复冷却中 ({int(self.REPAIR_COOLDOWN - elapsed)}秒)"
        return True, "可为原任务准备受限恢复方案"

    def execute_repair_attempt(self, profile: RelationProfile) -> Dict:
        # This deprecated query must not mutate state or grant original task execution.
        return {"status": "plan_required", "can": False,
                "message": "请使用 propose_repair 为具体异常创建方案，并回收验证结果"}

    @staticmethod
    def available_paths() -> list:
        return [
            {"path": "verify_handoff", "description": "核对交接资料与验收条件", "cost": "low",
             "success_probability": None},
            {"path": "restricted_retest", "description": "在限定范围内复测一次", "cost": "medium",
             "success_probability": None},
            {"path": "reassign_task", "description": "由宿主核验能力和权限后改派原任务", "cost": "high",
             "success_probability": None},
        ]
