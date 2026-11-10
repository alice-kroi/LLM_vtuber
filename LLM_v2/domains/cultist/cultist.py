# ============================================================
# LLM_v2/domains/cultist/cultist.py — 密教模拟器能力域
# ============================================================
# 类型 Actuator：只提供"读局面 / 放卡 / 开始 / 结算 / 等时间"工具。
# 通过游戏内 CSRest mod（127.0.0.1:8765）读写局面，不模拟键鼠，
# 因此不受游戏以管理员权限运行导致的输入拦截影响。
# ============================================================

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType
from LLM_v2.domains.cultist.cultist_client import (
    CultistAPIError,
    CultistClient,
    find_card,
    find_verb,
    pick_slot,
)
from LLM_v2.domains.cultist.cultist_tools import (
    CULTIST_TOOL_NAMES,
    CULTIST_TOOLS_SCHEMA,
)
from LLM_v2.domains.cultist.cultist_world import get_world

logger = logging.getLogger(__name__)


_CULTIST_PROMPT = """【密教模拟器】通过本地 REST 桥操作游戏（需游戏已启动且 CSRest 生效）。
- cs_launch：确保游戏可用（没开就自动启动并等待接口就绪）
- cs_status：读当前局面（卡片/动词/槽位/待结算产出）
- cs_move：把卡片放进动词槽位（或用 to="tabletop" 移回桌面）
- cs_start：开始 Unstarted 动词
- cs_conclude：结算 Complete 动词，产出卡会回到桌面
- cs_wait：快进时间
- cs_start_game：开始新一局（会覆盖当前进度）
- cs_world：读本域信息缓存（局面/目录/配方/失败/经验，不访问游戏）
- cs_knowledge：检索本域已学到的游戏知识
- cs_learn：记录一条新经验

执行约定：
1. 先 cs_status 看局面，再决定动作；不要凭记忆编造卡片或动词。
2. 一次只做一件事；动词在 Ongoing 时不要重复 start，等 Complete 后 conclude。
3. 结算优先：有 Complete 的动词先 conclude，再安排下一步。
4. 工具返回 success=false 时如实说明失败原因，不要假装已操作。
5. 每次动作都会自动写入本域缓存；完整细节用本域工具，中央信息（ci_*）只给摘要和指针。
"""


class CultistDomain(Actuator):
    """密教模拟器（Actuator，默认启用）"""

    id = "cultist"
    label = "密教模拟器"
    prefix = "cs_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 60.0
    tool_timeouts = {
        "cs_launch": 240.0,
        "cs_status": 25.0,
        "cs_move": 60.0,
        "cs_start": 60.0,
        "cs_conclude": 60.0,
        "cs_wait": 60.0,
        "cs_start_game": 150.0,
        "cs_world": 10.0,
        "cs_knowledge": 10.0,
        "cs_learn": 10.0,
    }

    def __init__(self):
        super().__init__()
        self._client = CultistClient()
        self._world = get_world()

    def allowed_names(self) -> List[str]:
        return list(CULTIST_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in CULTIST_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _CULTIST_PROMPT

    def central_card(self) -> Dict[str, Any]:
        """中央信息：只给摘要 + 详情渠道，完整牌桌留在本域工具里"""
        snap = self._world.snapshot(include_state=True)
        if not snap.get("available"):
            return {
                "summary": "密教模拟器：缓存已关闭（内存已清空，磁盘快照保留）",
                "channels": [
                    {"name": "重新启用后", "tools": ["cs_status", "cs_world"],
                     "description": "启用本域后可恢复并读取完整牌桌"},
                ],
            }
        state = snap.get("state") or {}
        counts = snap.get("counts") or {}
        cards = state.get("cards") or []
        verbs = state.get("verbs") or []
        verb_bits = "、".join(
            f"{v.get('label')}({v.get('state')})" for v in verbs[:4])
        card_bits = "、".join(
            f"{c.get('label')}x{c.get('quantity', 1)}" for c in cards[:5])
        summary = (f"密教模拟器：{state.get('legacy_label') or '未开局'}；"
                   f"桌面 {len(cards)} 张卡"
                   + (f"（{card_bits}）" if card_bits else "")
                   + (f"；动词 {verb_bits}" if verb_bits else "")
                   + f"；已积累 {counts.get('lessons', 0)} 条经验、"
                     f"{counts.get('recipes', 0)} 条配方")
        return {
            "summary": summary,
            "channels": [
                {"name": "实时牌桌", "tools": ["cs_status"],
                 "description": "当前卡片/动词/槽位/待结算产出（完整）"},
                {"name": "域内缓存", "tools": ["cs_world"],
                 "description": "最近局面、卡片/动词目录、配方观察记录"},
                {"name": "经验知识", "tools": ["cs_knowledge"],
                 "description": "检索经验、配方与失败记录"},
                {"name": "实操", "tools": ["cs_move", "cs_start", "cs_conclude", "cs_wait"],
                 "description": "放卡/开始/结算/等待"},
            ],
        }

    def _push_central(self) -> None:
        """把当前摘要与缓存状态推到中央信息层（域内详情不复制过去）"""
        try:
            from LLM_v2.core.central_context import get_central
            card = self.central_card()
            get_central().update_domain(
                self.id, label=self.label,
                summary=card.get("summary") or "",
                channels=card.get("channels") or [],
                status="started" if self._world.is_open() else "active",
                tools=self.allowed_names(),
                cache=self._world.info(),
            )
        except Exception:  # noqa: BLE001
            pass

    # ---------- 生命周期：缓存与能力域协同 ----------

    async def start(self) -> None:
        self._world.open()
        self._push_central()

    async def stop(self) -> None:
        self._world.close()
        self._push_central()

    def cache_info(self) -> Dict[str, Any]:
        return self._world.info()

    def close_cache(self) -> None:
        self._world.close()

    # ---------- 工具 ----------

    STEAM_APPID = "718670"

    @staticmethod
    def _steam_roots() -> List[Path]:
        """可能的 Steam 安装根目录（注册表 + 常见路径）。"""
        roots: List[Path] = []
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                path, _ = winreg.QueryValueEx(k, "SteamPath")
                if path:
                    roots.append(Path(path))
        except Exception:  # noqa: BLE001
            pass
        for raw in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
                    r"D:\Steam", r"E:\Steam"):
            roots.append(Path(raw))
        return [r for r in roots if r.exists()]

    def _game_exe(self) -> Optional[Path]:
        """在 Steam 库里找 cultistsimulator.exe。"""
        env = os.getenv("CULTIST_GAME_EXE")
        if env and Path(env).exists():
            return Path(env)
        libraries: List[Path] = []
        for root in self._steam_roots():
            libraries.append(root)
            vdf = root / "steamapps" / "libraryfolders.vdf"
            if not vdf.exists():
                continue
            try:
                text = vdf.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if '"path"' in line:
                        parts = line.split('"')
                        if len(parts) >= 4:
                            libraries.append(Path(parts[3].replace("\\\\", "\\")))
            except Exception:  # noqa: BLE001
                pass
        for lib in dict.fromkeys(libraries):
            exe = lib / "steamapps" / "common" / "Cultist Simulator" / "cultistsimulator.exe"
            if exe.exists():
                return exe
        return None

    def _api_alive(self) -> bool:
        try:
            self._client._request("GET", "api/game-state/legacy", timeout=3.0)
            return True
        except Exception:  # noqa: BLE001
            return False

    def cs_launch(self, wait_sec: float = 180) -> Dict[str, Any]:
        """确保游戏可用：没开就启动，然后等 CSRest 接口就绪。"""
        if self._api_alive():
            return {"success": True, "already": True, "waited_sec": 0}

        try:
            wait_sec = max(15.0, min(300.0, float(wait_sec or 180)))
        except (TypeError, ValueError):
            wait_sec = 180.0

        method = ""
        error = ""
        # 1) 优先用 Steam 协议启动（Steam 在跑时最稳）
        try:
            if os.name == "nt":
                os.startfile(f"steam://rungameid/{self.STEAM_APPID}")  # type: ignore[name-defined]
            else:
                subprocess.Popen(["steam", f"steam://rungameid/{self.STEAM_APPID}"])
            method = "steam"
        except Exception as e:  # noqa: BLE001
            error = f"Steam 启动失败: {e}"

        # 2) 等接口；迟迟不出现则直接跑 exe 兜底
        started = time.time()
        exe_tried = False
        while time.time() - started < wait_sec:
            if self._api_alive():
                waited = int(time.time() - started)
                self._world.learn(
                    "启动游戏",
                    f"用 {method or 'exe'} 启动《密教模拟器》，约 {waited}s 后 "
                    f"CSRest 接口（127.0.0.1:8765）就绪。",
                    source="bootstrap", confidence=0.9,
                )
                return {"success": True, "already": False, "method": method,
                        "waited_sec": waited}
            if not exe_tried and time.time() - started > wait_sec / 2:
                exe_tried = True
                exe = self._game_exe()
                if exe is not None:
                    try:
                        subprocess.Popen([str(exe)], cwd=str(exe.parent))
                        method = f"{method}+exe" if method else "exe"
                    except Exception as e:  # noqa: BLE001
                        error = f"{error}；exe 启动失败: {e}".strip("；")
            time.sleep(2)

        detail = (f"启动后 {int(wait_sec)}s 内接口仍未就绪"
                  + (f"（{error}）" if error else "")
                  + "；请确认 Steam 已登录、游戏已装 CSRest mod。")
        self._world.learn("启动游戏失败", detail, source="bootstrap", confidence=0.5)
        return {"success": False, "error": detail, "waited_sec": int(wait_sec)}

    def cs_status(self) -> Dict[str, Any]:
        try:
            state = self._client.status()
        except CultistAPIError as e:
            return {"success": False, "error": str(e)}
        self._world.update_state(state)
        self._push_central()
        counts = {
            "cards": len(state.get("cards") or []),
            "verbs": len(state.get("verbs") or []),
        }
        hints = self._world.recipe_hints(state, limit=4)
        return {"success": True, **state, "counts": counts,
                "recipe_hints": hints,
                "cache": self._world.info(),
                "learned": self._world.snapshot(include_state=False).get("counts", {})}

    def cs_move(self, card: str = "", verb: str = "", slot: str = "",
                to: str = "") -> Dict[str, Any]:
        try:
            state = self._client.status()
        except CultistAPIError as e:
            return {"success": False, "error": str(e)}
        if not state.get("legacy"):
            return {"success": False, "error": "当前不在牌桌上，请先 cs_start_game"}

        found, err = find_card(state, card)
        if found is None:
            self._world.record_failure(f"move:missing:{card}", err)
            return {"success": False, "error": err}

        v: Dict[str, Any] = {"verb_id": "tabletop", "label": "桌面"}
        if str(to or "").strip().lower() in ("tabletop", "桌面", "desktop"):
            target_path = "~/tabletop"
            target_desc = "桌面"
            slot_name = "tabletop"
        else:
            v, err = find_verb(state, verb)
            if v is None:
                self._world.record_failure(f"move:verb:{verb}", err)
                return {"success": False, "error": err}
            s, err = pick_slot(v, slot)
            if s is None:
                self._world.record_failure(f"move:slot:{verb}:{slot}", err)
                return {"success": False, "error": err}
            target_path = s.get("path")
            slot_name = str(s.get("name") or "")
            target_desc = f"{v.get('label')} 的槽位 {slot_name}"

        # 幂等：已经在目标位置就不重复移动（游戏会抛异常）
        current_path = str(found.get("path") or "")
        if current_path == target_path or current_path.startswith(f"{target_path}/"):
            return {"success": True,
                    "moved": {"card": found.get("label"), "to": target_desc,
                              "note": "already_there"},
                    "state": state}

        try:
            self._client.move(found.get("path"), target_path)
        except CultistAPIError as e:
            msg = f"移动失败: {e}（可能是该槽位不接受这张卡，或卡片已被配方消耗）"
            self._world.record_move(found, v, slot_name, False, str(e))
            return {"success": False, "error": msg}

        after = self._client.status()
        self._world.update_state(after)
        self._push_central()
        # 校验：有些条件槽位会把卡"吞掉"（移动成功但卡消失），不能算成功
        card_id = found.get("id")
        still_there = any(c.get("id") == card_id for c in after.get("cards") or [])
        if not still_there:
            still_there = any(
                c.get("id") == card_id
                for vv in after.get("verbs") or []
                for s in vv.get("slots") or []
                for c in s.get("cards") or []
            )
        if not still_there:
            err = "卡片移动后从桌面消失，该槽位可能不接受这张卡"
            self._world.record_move(found, v, slot_name, False, err)
            return {
                "success": False,
                "error": f"卡片「{found.get('label')}」{err}。请看新局面试别的槽位。",
                "state": after,
            }
        self._world.record_move(found, v, slot_name, True)
        return {
            "success": True,
            "moved": {"card": found.get("label"), "to": target_desc},
            "state": after,
        }

    def cs_start(self, verb: str = "") -> Dict[str, Any]:
        try:
            state = self._client.status()
        except CultistAPIError as e:
            return {"success": False, "error": str(e)}
        self._world.update_state(state)
        v, err = find_verb(state, verb, prefer_state="Unstarted")
        if v is None:
            self._world.record_failure(f"start:{verb}", err)
            return {"success": False, "error": err}
        if v.get("state") != "Unstarted":
            return {"success": False,
                    "error": f"动词「{v.get('label')}」当前状态是 {v.get('state')}，不能开始"}
        inputs = [c for s in v.get("slots") or [] for c in s.get("cards") or []]
        try:
            result = self._client.execute(v.get("path"))
        except CultistAPIError as e:
            # 连接被重置/409 时先用局面校验：如果动词已不在 Unstarted，说明其实已开始
            try:
                after = self._client.status()
            except CultistAPIError:
                after = state
            same = next((x for x in after.get("verbs") or []
                         if x.get("id") == v.get("id")), None)
            if same is None or same.get("state") != "Unstarted":
                self._world.update_state(after)
                return {"success": True,
                        "started": {"verb": v.get("label"), "recipe": None,
                                    "note": "已开始（接口报错但局面已变化）"},
                        "state": after}
            slots = "；".join(
                f"{s.get('name')}={'、'.join(c.get('label') or '' for c in s.get('cards') or []) or '空'}"
                for s in v.get("slots") or []
            )
            msg = (f"开始失败: {e}（配方条件不满足；当前槽位：{slots}。"
                   f"换一张卡或换槽位再试）")
            self._world.record_failure(f"start:{v.get('verb_id')}", msg)
            return {"success": False, "error": msg}
        after = self._client.status()
        self._world.update_state(after)
        self._push_central()
        recipe = {"recipe": result.get("executedRecipeLabel") or result.get("executedRecipeId")}
        self._world.record_recipe_started(v, recipe, inputs)
        return {
            "success": True,
            "started": {
                "verb": v.get("label"),
                "recipe": recipe["recipe"],
            },
            "state": after,
        }

    def cs_conclude(self, verb: str = "") -> Dict[str, Any]:
        try:
            state = self._client.status()
        except CultistAPIError as e:
            return {"success": False, "error": str(e)}
        v, err = find_verb(state, verb, prefer_state="Complete")
        if v is None:
            return {"success": False, "error": err}
        if v.get("state") != "Complete":
            return {"success": False,
                    "error": f"动词「{v.get('label')}」当前状态是 {v.get('state')}，还不能结算"}
        try:
            outputs = self._client.conclude(v.get("path"))
        except CultistAPIError as e:
            # 连接重置/409：用局面校验是否其实已结算
            try:
                after = self._client.status()
            except CultistAPIError:
                after = state
            same = next((x for x in after.get("verbs") or []
                         if x.get("id") == v.get("id")), None)
            if same is None or same.get("state") != "Complete":
                pending = [c for o in v.get("outputs") or []
                           for c in o.get("cards") or []]
                self._world.update_state(after)
                self._world.record_recipe_outputs(v, pending)
                return {"success": True,
                        "concluded": {
                            "verb": v.get("label"),
                            "outputs": [{"label": c.get("label"),
                                         "quantity": c.get("quantity", 1)}
                                        for c in pending],
                            "note": "已结算（接口报错但局面已变化）",
                        },
                        "state": after}
            self._world.record_failure(f"conclude:{v.get('verb_id')}", str(e))
            return {"success": False, "error": f"结算失败: {e}"}
        after = self._client.status()
        self._world.update_state(after)
        self._push_central()
        self._world.record_recipe_outputs(v, outputs)
        return {
            "success": True,
            "concluded": {
                "verb": v.get("label"),
                "outputs": [{"label": o.get("label"), "quantity": o.get("quantity", 1)}
                            for o in outputs],
            },
            "state": after,
        }

    def cs_wait(self, seconds: float = 10) -> Dict[str, Any]:
        try:
            seconds = max(1.0, min(180.0, float(seconds or 10)))
        except (TypeError, ValueError):
            seconds = 10.0
        try:
            self._client.beat(seconds)
        except CultistAPIError as e:
            return {"success": False, "error": f"快进失败: {e}"}
        after = self._client.status()
        self._world.update_state(after)
        self._push_central()
        return {"success": True, "waited": seconds, "state": after}

    def cs_start_game(self, legacy_id: str = "aspirant") -> Dict[str, Any]:
        try:
            self._client.start_game(legacy_id or "aspirant")
        except CultistAPIError as e:
            return {"success": False, "error": f"开始新局失败: {e}"}
        after = self._client.status()
        self._world.update_state(after)
        self._push_central()
        return {"success": True, "legacy": after.get("legacy"),
                "legacy_label": after.get("legacy_label"), "state": after}

    # ---------- 共享缓存 / 学习 ----------

    def cs_world(self, include_state: bool = True) -> Dict[str, Any]:
        """读共享世界缓存（不访问游戏）"""
        return self._world.snapshot(include_state=bool(include_state))

    def cs_knowledge(self, query: str = "", limit: int = 8) -> Dict[str, Any]:
        """检索已学到的游戏知识"""
        return self._world.search(query, limit=limit)

    def cs_learn(self, topic: str = "", text: str = "",
                 source: str = "play", confidence: float = 0.6) -> Dict[str, Any]:
        """记录一条游戏知识/经验"""
        result = self._world.learn(topic, text, source=source or "play",
                                   confidence=confidence)
        if result.get("success"):
            result["counts"] = self._world.snapshot(include_state=False).get("counts", {})
            self._push_central()
        return result


def build_cultist_domain() -> CultistDomain:
    return CultistDomain()


__all__ = ["CultistDomain", "build_cultist_domain"]
