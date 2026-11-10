# ============================================================
# persona/learning/__init__.py — 学习子系统（P0：偏好记忆）
# ============================================================
# 设计见 docs/AI_LEARNING_CAPABILITY.md
#   P0 只做情景 + 偏好两张表，规则抽取，不调用 LLM。
# 注意：项目根目录已有一个旧的 learning/ 包（LLM_v2 完全未引用），
#       这里是 persona 自己的学习层，二者不混用。
# ============================================================

from persona.learning.preference import PreferenceHit, extract
from persona.learning.signals import polarity, to_reward
from persona.learning.store import LearningStore

__all__ = ["LearningStore", "PreferenceHit", "extract", "to_reward", "polarity"]
