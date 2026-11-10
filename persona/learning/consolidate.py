# ============================================================
# persona/learning/consolidate.py — 夜间巩固（P2，纯规则）
# ============================================================
# Auto-Dreamer 的"慢巩固"：不在线追加，而是定期把记忆压缩：
#   1. 合并归一化后同值的重复偏好（保留证据并集）
#   2. 淘汰低重要度的旧情景（被偏好引用的证据不删）
#   3. 退役 effective 过低的流程
#   4. 写一条 learn_reflections 审计
#
# 全程不调用 LLM；LLM 反思按设计文档 §13 暂缓（先观察规则效果）。
# ============================================================

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from persona.learning.episode import age_days
from persona.learning.store import LearningStore

CONSOLIDATION_MIN_HOURS = 20
EPISODE_MAX_AGE_DAYS = 30
EPISODE_IMPORTANCE = 0.4
PROCEDURE_MIN_EFFECTIVE = 0.35


def needs_consolidation(store: LearningStore,
                        min_hours: float = CONSOLIDATION_MIN_HOURS) -> bool:
    """距上次夜间巩固超过 min_hours 才需要再跑（启动时补跑用）"""
    last = store.last_reflection_at("nightly")
    if not last:
        return True
    return age_days(last) * 24 >= min_hours


def consolidate(store: LearningStore, *, dry_run: bool = False,
                episode_max_age_days: int = EPISODE_MAX_AGE_DAYS,
                episode_importance: float = EPISODE_IMPORTANCE,
                procedure_min_effective: float = PROCEDURE_MIN_EFFECTIVE) -> Dict[str, Any]:
    """执行一次规则巩固，返回变更报告"""
    groups = store.duplicate_preference_groups()
    merged = store.merge_preferences(groups, dry_run=dry_run)
    pruned = store.prune_episodes(episode_max_age_days, episode_importance,
                                  dry_run=dry_run)
    retired = store.retire_stale_procedures(procedure_min_effective,
                                            dry_run=dry_run)

    report = {
        "merged_preferences": merged,
        "pruned_episodes": pruned,
        "retired_procedures": retired,
        "dry_run": dry_run,
        "at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
    }
    if not dry_run:
        store.log_reflection("nightly", None, report, applied=True, model="rules")
    return report


__all__ = ["consolidate", "needs_consolidation", "CONSOLIDATION_MIN_HOURS"]
