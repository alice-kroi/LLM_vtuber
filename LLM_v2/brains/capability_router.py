# ============================================================
# LLM_v2/brains/capability_router.py — 思维脑的能力按需启停
# ============================================================
# 职责：
#   1. 任务开始时扫描 LLM_v2/domains/（assembly.rediscover），拿到最新能力目录
#   2. 由思维脑 LLM 看目录 + 任务，决定临时启用/关闭哪些能力域
#   3. 记录项目作用域（baseline + 本作用域临时启用的域 + 用量）
#   4. 项目结束/闲置后，由 LLM 决定关闭哪些非受保护域
#
# 设计约束：
#   - 运行时启停（start_domain/stop_domain），不写死配置；
#     显式持久开关仍由 WebUI / set_domain_enabled 负责。
#   - 受保护域（memory/research/coder/bili_live/tts/live2d 等）永不自动关闭。
#   - LLM 失败/超时/返回非法 JSON 时，自动回落关键词规则。
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_CN_STOP = {
    "工具", "能力", "用于", "可以", "一个", "这个", "那个", "进行", "通过",
    "获取", "查询", "计算", "转换", "支持", "输入", "输出", "返回", "结果",
    "用户", "需要", "如果", "以及", "相关", "数据", "信息", "内容", "操作",
    "方法", "函数", "使用", "时候", "什么", "怎么", "是否", "并且", "或者",
    "然后", "现在", "已经", "没有", "我们", "你们", "他们", "任务", "功能",
}
_EN_STOP = {
    "the", "and", "for", "with", "tool", "function", "returns", "return",
    "input", "output", "user", "using", "used", "this", "that", "from",
    "into", "can", "will", "should", "value", "values", "result", "results",
}

# 领域关键词提示：比纯 token 重叠可靠；新域没有提示时靠 label 匹配兜底
_DOMAIN_HINTS: Dict[str, tuple] = {
    "video_studio": ("视频", "剪辑", "字幕", "封面", "口播", "转场", "配音",
                     "成片", "抖音", "小红书"),
    "clip_maker": ("切片", "录像", "高光"),
    "bili_video": ("b站", "bilibili", "bv号", "投稿", "up主", "收藏", "点赞", "投币"),
    "bili_live": ("直播间", "弹幕", "直播"),
    "obs_control": ("obs", "录屏", "推流", "场景"),
    "stream_assist": ("点歌", "歌单", "签到", "高光", "直播互动"),
    "sing": ("唱歌", "翻唱", "歌声"),
    "tts": ("语音", "朗读", "配音", "说话"),
    "research": ("查资料", "调研", "搜索", "学习", "资料", "文档", "教程"),
    "memory": ("记住", "回忆", "记忆", "偏好", "档案"),
    "browser": ("网页", "浏览器", "网站", "打开网页"),
    "desktop": ("桌面", "窗口", "截图", "键鼠", "电脑"),
    "desktop_agent": ("桌面操作", "自动操作", "点按"),
    "ui_perception": ("界面", "控件", "识别"),
    "live2d": ("live2d", "表情", "动作", "虚拟形象"),
    "pvz": ("植物大战僵尸", "pvz"),
    "cultist": ("密教", "模拟器"),
    "qq_napcat": ("qq", "群聊", "空间"),
    "launcher": ("启动游戏", "一键启动", "alas", "maa"),
    "hf_hub": ("huggingface", "模型下载", "hf"),
    "coder": ("代码", "编程", "bug", "测试", "重构", "实现", "函数", "脚本"),
    "evolve": ("自进化", "新工具", "扩展能力", "造工具", "加个工具"),
    "capability_ops": ("能力管理", "自生成能力", "熔断", "准入"),
    "security": ("安全", "渗透", "扫描", "漏洞", "攻防", "nmap", "nuclei"),
}

_END_PHRASES = (
    "项目结束", "项目完成了", "任务结束", "结束了", "收工", "不用了",
    "先这样", "关闭能力", "关掉能力", "可以关了", "停掉能力",
)

MAX_ENABLE = 3
MAX_DISABLE = 5
SCAN_COOLDOWN_SEC = 20.0
IDLE_FINISH_SEC = 600.0
LLM_TIMEOUT_SEC = 6.0
PLAN_CACHE_TTL = 60.0

# 这些域即使 LLM 想关也不许关：基础运行、事件接收、人格/语音/元能力
PROTECTED_DOMAINS = {
    "memory", "research", "coder", "desktop", "central_info", "time_info",
    "capability_ops", "evolve", "mcp_bridge", "live2d", "tts",
    "bili_live", "qq_napcat", "stream_assist", "desktop_pet",
}


def _tokens(text: str) -> Set[str]:
    raw = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{3,}", raw)) - _EN_STOP
    grams: Set[str] = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", raw):
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in _CN_STOP:
                grams.add(gram)
    return words | grams


@dataclass
class _Scope:
    id: str
    goal: str
    baseline: Set[str]
    enabled: Set[str] = field(default_factory=set)
    started_at: float = 0.0
    last_activity: float = 0.0
    last_goal: str = ""
    usage_start: Dict[str, int] = field(default_factory=dict)


class CapabilityRouter:
    """思维脑的能力按需启停器（运行时作用域，不改持久配置）

    决策模式：
        llm  — 由思维脑 LLM 看能力目录后决定（失败自动回落 rule）
        rule — 关键词 + 分词重叠（零 LLM 成本）
    """

    def __init__(self, runtime, llm=None, mode: Optional[str] = None):
        self.runtime = runtime
        self._llm = llm
        self.mode = (mode or os.getenv("CAPABILITY_ROUTER_MODE") or "llm").lower()
        self._scope: Optional[_Scope] = None
        self._last_scan = 0.0
        self._usage: Dict[str, int] = {}
        self._plan_cache: Dict[str, tuple] = {}
        self._register_usage_listener()

    # ---------- 依赖 ----------

    def _register_usage_listener(self) -> None:
        """统计每个域在本进程内的工具调用次数，供项目结束决策参考。"""
        bus = getattr(self.runtime, "bus", None)
        if bus is None:
            return
        try:
            from LLM_v2.events import EVENTS
            bus.on(EVENTS.DOMAIN_TOOL_CALLED, self._on_domain_tool_called)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[cap-router] 注册用量监听失败: {e}")

    async def _on_domain_tool_called(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        did = str(payload.get("domain") or "")
        if did:
            self._usage[did] = self._usage.get(did, 0) + 1

    def _get_llm(self):
        if self._llm is None:
            try:
                from LLM_v2.core.llm_adapter import get_llm
                self._llm = get_llm()
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[cap-router] LLM 不可用: {e}")
        return self._llm

    def _assembly(self):
        return (getattr(self.runtime, "assembly", None)
                or getattr(self.runtime, "_assembly", None))

    async def _scan(self, force: bool = False) -> None:
        asm = self._assembly()
        if asm is None:
            return
        now = time.monotonic()
        if not force and now - self._last_scan < SCAN_COOLDOWN_SEC:
            return
        self._last_scan = now
        try:
            await asm.rediscover()
            logger.info("[cap-router] 已扫描本地能力域目录")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[cap-router] 扫描能力域目录失败（继续用现有目录）: {e}")

    def _catalog(self) -> List[Dict[str, Any]]:
        asm = self._assembly()
        if asm is None:
            return []
        try:
            return list(asm.domain_catalog())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[cap-router] 读取能力目录失败: {e}")
            return []

    # ---------- 决策 ----------

    def _plan(self, goal: str, catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按任务文本推荐需要临时启用的域（只推荐当前关闭的）。"""
        raw_goal = (goal or "").lower()
        g = re.sub(r"\s+", "", raw_goal)      # 让 "B 站" 也能命中 "b站"
        goal_tokens = _tokens(goal)
        picks: List[Dict[str, Any]] = []

        # "看视频/刷视频" 是消费视频，不是创作视频；除非同时出现创作词
        _watch_only = any(k in g for k in ("看视频", "看个视频", "刷视频",
                                           "刷个视频", "播放视频", "看会儿视频"))
        _create_words = ("剪", "做", "生成", "编辑", "字幕", "封面", "口播",
                         "转场", "配音", "成片", "导出")

        for d in catalog:
            did = str(d.get("id") or "")
            if not did or d.get("enabled"):
                continue
            if did == "video_studio" and _watch_only \
                    and not any(k in g for k in _create_words):
                continue
            hints = [h for h in _DOMAIN_HINTS.get(did, ()) if h in g]
            label = str(d.get("label") or "").lower()
            label_hit = bool(label and label in g)
            if not hints and not label_hit:
                continue

            hay = " ".join([
                did,
                str(d.get("label") or ""),
                " ".join(d.get("tools") or []),
                str(d.get("prompt") or ""),
            ])
            dom_tokens = _tokens(hay)
            overlap = (len(goal_tokens & dom_tokens) /
                       max(1, len(goal_tokens)))
            score = min(0.6, 0.4 * len(hints))
            if label_hit:
                score += 0.25
            score += 0.25 * min(1.0, overlap)
            if score < 0.35:
                continue
            picks.append({
                "id": did,
                "label": d.get("label") or did,
                "score": round(score, 3),
                "reasons": hints or [str(d.get("label") or did)],
            })

        picks.sort(key=lambda x: x["score"], reverse=True)
        return picks[:MAX_ENABLE]

    def _rule_decision(self, phase: str, goal: str, catalog: List[Dict[str, Any]],
                       scope: Optional[_Scope]) -> Dict[str, Any]:
        if phase == "task":
            picks = self._plan(goal, catalog)
            return {
                "enable": [p["id"] for p in picks],
                "disable": [],
                "reason": "关键词匹配",
                "source": "rule",
            }
        temp = sorted(scope.enabled - scope.baseline) if scope else []
        return {
            "enable": [],
            "disable": temp,
            "reason": "关闭本次临时开启的能力",
            "source": "rule",
        }

    def _used_since(self, scope: Optional[_Scope], domain_id: str) -> int:
        if scope is None:
            return int(self._usage.get(domain_id, 0))
        return int(self._usage.get(domain_id, 0)
                   - scope.usage_start.get(domain_id, 0))

    @staticmethod
    def _task_candidates(catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in catalog:
            if d.get("enabled"):
                continue
            risk = str(d.get("risk") or "R1").upper()
            # R3（凭据/高副作用）不自动开启，必须人工确认
            if risk == "R3":
                continue
            out.append({
                "id": str(d.get("id") or ""),
                "label": str(d.get("label") or ""),
                "risk": risk,
                "tools": list(d.get("tools") or [])[:6],
                "prompt": str(d.get("prompt") or "")[:180],
            })
        return [x for x in out if x["id"]]

    def _close_candidates(self, scope: _Scope,
                          catalog: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in catalog:
            did = str(d.get("id") or "")
            if not did or not d.get("enabled") or did in PROTECTED_DOMAINS:
                continue
            out.append({
                "id": did,
                "label": str(d.get("label") or ""),
                "used": self._used_since(scope, did),
                "temporary": did in scope.enabled,
                "default_enabled": bool(d.get("default_enabled")),
            })
        out.sort(key=lambda x: (x["used"], not x["temporary"], x["id"]))
        return out

    @staticmethod
    def _as_list(value: Any) -> List[str]:
        if isinstance(value, str):
            return [value.strip().lower()]
        if isinstance(value, (list, tuple, set)):
            return [str(x).strip().lower() for x in value]
        return []

    @staticmethod
    def _parse_plan(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        raw = str(text).strip()
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return None
            try:
                data = json.loads(match.group(0))
            except Exception:  # noqa: BLE001
                return None
        return data if isinstance(data, dict) else None

    async def _llm_decide(self, phase: str, goal: str,
                          catalog: List[Dict[str, Any]],
                          scope: Optional[_Scope]) -> Optional[Dict[str, Any]]:
        """让思维脑 LLM 看能力目录后决定开关；失败返回 None 由调用方回落规则。"""
        if phase == "task":
            candidates = self._task_candidates(catalog)
        else:
            candidates = self._close_candidates(scope, catalog) if scope else []
        if not candidates:
            return None
        llm = self._get_llm()
        if llm is None:
            return None

        active = sorted(str(d.get("id")) for d in catalog if d.get("enabled"))
        cache_key = f"task|{(goal or '')[:200]}|{','.join(active)}"
        if phase == "task":
            cached = self._plan_cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < PLAN_CACHE_TTL:
                return dict(cached[1])

        if phase == "task":
            system = (
                "你是维尔薇的能力调度器，只决定『完成当前任务需要临时开启哪些能力域』。\n"
                "规则：\n"
                f"1. 只能从候选 id 中选，禁止编造；最多 {MAX_ENABLE} 个。\n"
                "2. 只开真正需要的；普通闲聊、问答、看视频不要开创作能力。\n"
                "3. 不确定就不开；当前已开启的不要重复开。\n"
                "4. 只输出 JSON，不要解释、不要 markdown。\n"
                '格式：{"enable":["id"],"disable":[],"reason":"一句中文"}'
            )
            user = (
                f"任务：{(goal or '')[:300]}\n"
                f"当前已开启：{active}\n"
                f"候选（当前关闭）：{json.dumps(candidates, ensure_ascii=False)}"
            )
        else:
            goal_text = (scope.last_goal or scope.goal) if scope else ""
            system = (
                "你是维尔薇的能力调度器。项目/任务结束，决定关闭哪些能力域。\n"
                "规则：\n"
                f"1. 只能从候选 id 中选，禁止编造；最多 {MAX_DISABLE} 个。\n"
                "2. 优先关闭本次临时开启、使用次数少的域。\n"
                "3. 常驻域只有确认不再需要时才关；受保护域不在候选里。\n"
                "4. 只输出 JSON，不要解释、不要 markdown。\n"
                '格式：{"enable":[],"disable":["id"],"reason":"一句中文"}'
            )
            user = (
                f"项目/最近任务：{goal_text[:300]}\n"
                f"本次临时开启：{sorted(scope.enabled) if scope else []}\n"
                f"候选关闭：{json.dumps(candidates, ensure_ascii=False)}"
            )

        try:
            response = await asyncio.wait_for(
                llm.chat_simple(system_prompt=system, user_text=user),
                timeout=LLM_TIMEOUT_SEC,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[cap-router] LLM 决策失败（回落规则）: {e}")
            return None

        data = self._parse_plan(response)
        if not data:
            logger.warning(f"[cap-router] LLM 返回非 JSON（回落规则）: "
                           f"{str(response)[:160]}")
            return None

        valid = {str(c["id"]) for c in candidates}
        if phase == "task":
            enable = [x for x in self._as_list(data.get("enable"))
                      if x in valid][:MAX_ENABLE]
            disable: List[str] = []
        else:
            enable = []
            disable = [x for x in self._as_list(data.get("disable"))
                       if x in valid and x not in PROTECTED_DOMAINS][:MAX_DISABLE]

        plan = {
            "enable": enable,
            "disable": disable,
            "reason": str(data.get("reason") or "")[:200],
            "source": "llm",
        }
        if phase == "task":
            self._plan_cache[cache_key] = (time.monotonic(), dict(plan))
            if len(self._plan_cache) > 200:
                cutoff = time.monotonic() - PLAN_CACHE_TTL * 2
                self._plan_cache = {
                    k: v for k, v in self._plan_cache.items() if v[0] >= cutoff
                }
        return plan

    async def _decide(self, phase: str, goal: str,
                      catalog: List[Dict[str, Any]],
                      scope: Optional[_Scope]) -> Dict[str, Any]:
        if self.mode in ("rule", "off"):
            return self._rule_decision(phase, goal, catalog, scope)
        plan = await self._llm_decide(phase, goal, catalog, scope)
        if plan is not None:
            return plan
        return self._rule_decision(phase, goal, catalog, scope)

    # ---------- 作用域 ----------

    async def prepare(self, goal: str, source: str = "user") -> Dict[str, Any]:
        """任务开始：扫描目录 → 建/续项目作用域 → 临时启用需要的域。"""
        asm = self._assembly()
        if asm is None:
            return {"success": False, "error": "assembly 不可用"}

        await self._scan()
        catalog = self._catalog()
        now = time.time()

        if self._scope is None:
            baseline = {str(d.get("id")) for d in catalog if d.get("enabled")}
            self._scope = _Scope(
                id=f"cap-{int(now)}",
                goal=(goal or "")[:200],
                baseline=baseline,
                started_at=now,
                last_activity=now,
                last_goal=(goal or "")[:200],
                usage_start=dict(self._usage),
            )
        else:
            self._scope.last_activity = now
            self._scope.last_goal = (goal or "")[:200]

        decision = await self._decide("task", goal, catalog, self._scope)
        enabled: List[str] = []
        failed: List[Dict[str, str]] = []
        for did in decision.get("enable") or []:
            did = str(did)
            if did in self._scope.baseline or did in self._scope.enabled:
                continue
            try:
                await asyncio.wait_for(asm.start_domain(did), timeout=15.0)
                self._scope.enabled.add(did)
                enabled.append(did)
                logger.info(f"[cap-router] 临时启用 {did}（{decision.get('source')}）")
            except Exception as e:  # noqa: BLE001
                failed.append({"id": did, "error": str(e)[:200]})
                logger.warning(f"[cap-router] 启用 {did} 失败（跳过）: {e}")

        return {
            "success": True,
            "scope_id": self._scope.id,
            "source": decision.get("source") or "rule",
            "goal": self._scope.goal,
            "enabled": enabled,
            "failed": failed,
            "recommended": list(decision.get("enable") or []),
            "reason": decision.get("reason") or "",
            "mode": self.mode,
            "baseline_count": len(self._scope.baseline),
        }

    async def finish(self, reason: str = "user_end") -> Dict[str, Any]:
        """项目结束：由思维脑决定关闭哪些能力域。"""
        scope = self._scope
        if scope is None:
            return {"success": True, "reason": reason, "disabled": [],
                    "failed": [], "message": "没有正在进行的项目作用域"}
        asm = self._assembly()
        if asm is None:
            self._scope = None
            return {"success": False, "error": "assembly 不可用"}

        catalog = self._catalog()
        decision = await self._decide(
            "close", scope.last_goal or scope.goal, catalog, scope)
        candidates = {str(c["id"]) for c in self._close_candidates(scope, catalog)}
        requested = [str(x) for x in (decision.get("disable") or [])]

        disabled: List[str] = []
        failed: List[Dict[str, str]] = []
        for did in requested:
            if did not in candidates or did in PROTECTED_DOMAINS:
                continue
            try:
                await asyncio.wait_for(asm.stop_domain(did), timeout=15.0)
                disabled.append(did)
                logger.info(f"[cap-router] 项目结束，关闭能力 {did}"
                            f"（{decision.get('source')}）")
            except Exception as e:  # noqa: BLE001
                failed.append({"id": did, "error": str(e)[:200]})
                logger.warning(f"[cap-router] 关闭 {did} 失败（跳过）: {e}")

        kept = sorted(set(scope.enabled) - set(disabled))
        result = {
            "success": True,
            "reason": reason,
            "scope_id": scope.id,
            "disabled": disabled,
            "failed": failed,
            "kept": kept,
            "decision_source": decision.get("source") or "rule",
            "llm_reason": decision.get("reason") or "",
            "mode": self.mode,
        }
        self._scope = None
        return result

    async def maybe_finish_idle(self, max_idle: float = IDLE_FINISH_SEC
                                ) -> Optional[Dict[str, Any]]:
        """用户回来时顺带检查：上一个项目是否已经闲置够久。"""
        scope = self._scope
        if scope is None:
            return None
        if time.time() - scope.last_activity < max_idle:
            return None
        return await self.finish("idle_timeout")

    @staticmethod
    def detect_end(text: str) -> bool:
        t = (text or "").strip()
        return any(p in t for p in _END_PHRASES)

    def status(self) -> Dict[str, Any]:
        scope = self._scope
        if scope is None:
            return {"active": False, "mode": self.mode}
        return {
            "active": True,
            "mode": self.mode,
            "scope_id": scope.id,
            "goal": scope.goal,
            "last_goal": scope.last_goal,
            "enabled": sorted(scope.enabled),
            "baseline": sorted(scope.baseline),
            "usage": {k: self._used_since(scope, k) for k in scope.enabled},
            "idle_sec": round(time.time() - scope.last_activity, 1),
        }


__all__ = ["CapabilityRouter", "MAX_ENABLE", "MAX_DISABLE",
           "PROTECTED_DOMAINS", "IDLE_FINISH_SEC"]
