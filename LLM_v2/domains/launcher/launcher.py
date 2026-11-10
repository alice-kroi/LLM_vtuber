# ============================================================
# LLM_v2/domains/launcher/launcher.py — launcher 域（合并：核心逻辑 + 域类）
# ============================================================
# 替换 LLM/launcher/launcher.py + LLM/launcher/launcher_world.py
# 行为完全对齐 V1，仅去掉 V1 tool_registry 写副作用。
# ============================================================

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from LLM_v2.core.capability_domain import (
    SubprocessCapabilityDomain,
    Actuator,
    CapabilityType,
)
from LLM_v2.domains.launcher.launcher_tools import (
    LAUNCHER_TOOL_NAMES,
    LAUNCHER_TOOLS_SCHEMA,
)

logger = logging.getLogger(__name__)


# ============ 核心逻辑（迁自 LLM/launcher/launcher.py）============

# ---- 运行时常量（来自本机探测/用户指定）----
MUMU_MANAGER = r"F:\MuMu Player 12\nx_main\MuMuManager.exe"
ALAS_EXE = r"F:\download\AzurLaneAutoScript\alas.exe"
ALAS_DIR = os.path.dirname(ALAS_EXE)
MAA_EXE = r"F:\maa\maa.exe"
MAA_DIR = os.path.dirname(MAA_EXE)

# ---- 控件校准（坐标法回退）----
ALAS_MAIN_TITLE = "Alas"
ALAS_START_REL: Tuple[float, float] = (0.41, 0.092)
MAA_MAIN_TITLE = "MAA -"
MAA_START_REL: Tuple[float, float] = (0.185, 0.89)


def _popen(exe: str, cwd: str) -> Dict[str, Any]:
    """启动 exe（断开 stdio 避免孙进程输出污染父进程 JSON 行协议）"""
    try:
        subprocess.Popen(
            [exe], cwd=cwd or os.path.dirname(exe),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"success": True, "launched": exe}
    except Exception as e:
        return {"success": False, "error": f"启动失败 {exe}: {e}"}


def _detached_launch(exe: str, cwd: str) -> Dict[str, Any]:
    """脱离当前进程树启动 GUI 应用（不受父进程/终端退出影响）。

    通过 explorer.exe 委托一个临时 bat：bat 先 cd 到目标工作目录再 start exe。
    直接 Popen 的进程会随调用方进程树一起被清理，且 ALAS 更新器依赖正确的工作目录。
    """
    if not exe or not os.path.exists(exe):
        return {"success": False, "error": f"可执行文件不存在: {exe}"}
    workdir = cwd or os.path.dirname(exe)
    try:
        cache_dir = os.path.join(
            os.environ.get("TEMP") or os.path.expanduser("~"),
            "llm_vtuber_launcher")
        os.makedirs(cache_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(exe))[0]
        bat_path = os.path.join(cache_dir, f"start_{stem}.bat")
        content = (
            "@echo off\r\n"
            f'cd /d "{workdir}"\r\n'
            f'start "" "{exe}"\r\n'
            "exit\r\n"
        )
        with open(bat_path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        subprocess.Popen(
            ["explorer.exe", bat_path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"success": True, "launched": exe, "workdir": workdir,
                "method": "explorer-bat", "script": bat_path}
    except Exception as e:
        return {"success": False, "error": f"独立启动失败 {exe}: {e}"}


_MUMU_TASK_BUSY = 60019  # device task in progress


def _mumu_cmd(*args: str, timeout: float = 30.0) -> Dict[str, Any]:
    """执行 MuMuManager 命令并解析 JSON 返回（不吞错误）。
    返回 {"success": True, "data": ...} 或 {"success": False, "error": ...}。
    命令出错时 stdout 是 {"errcode": N, "errmsg": "..."}；info 类命令是 {"0": {...}, "1": {...}}。
    """
    try:
        cp = subprocess.run([MUMU_MANAGER, *args],
                            timeout=timeout, capture_output=True, text=True)
    except Exception as e:
        return {"success": False, "error": f"MuMuManager 命令失败: {e}"}
    raw = (cp.stdout or "").strip()
    if not raw:
        return {"success": False, "error": f"MuMuManager 无输出 (exit={cp.returncode}): {' '.join(args)}"}
    try:
        data = json.loads(raw)
    except Exception:
        return {"success": False, "error": f"MuMuManager 输出非 JSON: {raw[:200]}"}
    if isinstance(data, dict) and isinstance(data.get("errcode"), int) and data["errcode"] != 0:
        return {"success": False, "errcode": data["errcode"],
                "error": data.get("errmsg") or f"errcode={data['errcode']}"}
    return {"success": True, "data": data}


def _query_adb_port(index: int) -> Optional[int]:
    """读取实例 ADB 端口（供 MAA 等外部工具对齐连接地址）"""
    r = _mumu_cmd("info", "-v", str(index))
    if not r.get("success"):
        return None
    data = r.get("data") or {}
    inst = data.get(str(index)) or {}
    return inst.get("adb_port")


def launch_mumu(instances: List[int] = (0, 1), wait_sec: float = 15.0,
                busy_retries: int = 3, busy_wait_sec: float = 10.0) -> Dict[str, Any]:
    """逐个实例用 MuMuManager 启动，解析 errcode，60019 忙则等待重试，不吞错误"""
    if not os.path.exists(MUMU_MANAGER):
        return {"success": False, "error": f"MuMuManager 不存在: {MUMU_MANAGER}"}
    per: Dict[str, Any] = {}
    for i in instances:
        r = _mumu_cmd("control", "-v", str(i), "launch")
        if not r.get("success") and r.get("errcode") == _MUMU_TASK_BUSY:
            # 上一条实例任务未结束：等待后重试，最多 busy_retries 次
            recovered = False
            for _ in range(busy_retries):
                time.sleep(busy_wait_sec)
                r = _mumu_cmd("control", "-v", str(i), "launch")
                if r.get("success") or r.get("errcode") != _MUMU_TASK_BUSY:
                    recovered = True
                    break
            if not recovered:
                per[str(i)] = {"success": False, "errcode": _MUMU_TASK_BUSY,
                               "error": "实例任务忙（device task in progress）多次重试仍失败，"
                                        "需重启 MuMu 多开器主程序后重试"}
                continue
        if not r.get("success"):
            per[str(i)] = {"success": False, "errcode": r.get("errcode"),
                           "error": r.get("error")}
            continue
        per[str(i)] = {"success": True, "errcode": r.get("data", {}).get("errcode", 0)}
    time.sleep(wait_sec)
    # 实例就绪后再读 ADB 端口（供 MAA 等外部工具对齐连接地址）
    for i in instances:
        if per.get(str(i), {}).get("success"):
            per[str(i)]["adb_port"] = _query_adb_port(i)
    ok = all(v.get("success") for v in per.values())
    return {"success": ok, "vmindex": list(instances), "instances": per,
            "after_wait_sec": wait_sec,
            "windows": present_windows(["MuMu Player", "MuMuNx"])}


def launch_alas() -> Dict[str, Any]:
    """启动 ALAS：优先脱离进程树启动，失败时回退普通 Popen。"""
    r = _detached_launch(ALAS_EXE, ALAS_DIR)
    return r if r.get("success") else _popen(ALAS_EXE, ALAS_DIR)


def launch_maa() -> Dict[str, Any]:
    """启动 MAA：优先脱离进程树启动，失败时回退普通 Popen。"""
    r = _detached_launch(MAA_EXE, MAA_DIR)
    return r if r.get("success") else _popen(MAA_EXE, MAA_DIR)


def _get_window(title_keyword: str):
    try:
        import pygetwindow as gw
        for w in gw.getWindowsWithTitle(title_keyword):
            if w.title:
                return w
    except Exception:
        return None
    return None


def activate_window(title_keyword: str) -> bool:
    w = _get_window(title_keyword)
    if not w:
        return False
    try:
        if w.isMinimized:
            w.restore()
        if not w.isActive:
            w.activate()
        return True
    except Exception:
        return False


def present_windows(title_keywords: List[str]) -> List[str]:
    try:
        import pygetwindow as gw
        wsz = gw.getAllWindows()
    except Exception:
        return []
    hits = []
    for w in wsz:
        if w.title and any(k.lower() in w.title.lower() for k in title_keywords):
            hits.append(w.title)
    return hits


def _first_window(title_keywords: List[str],
                  exclude_keywords: Tuple[str, ...] = ()):
    try:
        import pygetwindow as gw
        for w in gw.getAllWindows():
            t = w.title or ""
            if not t:
                continue
            if any(e.lower() in t.lower() for e in exclude_keywords):
                continue
            if any(k.lower() in t.lower() for k in title_keywords):
                return w
    except Exception:
        return None
    return None


def click_window_rel(title_keywords: List[str], rel: Tuple[float, float],
                     timeout_sec: float = 30.0,
                     exclude_keywords: Tuple[str, ...] = ()) -> Dict[str, Any]:
    """坐标法：等目标窗口出现，按窗口内相对偏移换算屏幕绝对坐标并点击"""
    deadline = time.time() + timeout_sec
    win = None
    while time.time() < deadline:
        w = _first_window(title_keywords, exclude_keywords)
        if w is not None:
            win = w
            break
        time.sleep(0.6)
    if win is None:
        return {"success": False, "error": f"超时未见窗口: {title_keywords}"}
    abs_x, abs_y = win.left + int(win.width * rel[0]), win.top + int(win.height * rel[1])
    try:
        import pyautogui
        if win.isMinimized:
            win.restore()
        if not win.isActive:
            win.activate()
        time.sleep(0.5)
        pyautogui.click(abs_x, abs_y)
        return {"success": True, "window": win.title, "relative": rel,
                "abs_xy": (abs_x, abs_y), "method": "coordinate"}
    except Exception as e:
        return {"success": False, "error": f"坐标点击失败: {e}", "window": win.title,
                "need_manual_calibration": True}


def click_button_in_window(title_keywords: List[str], button_names: List[str],
                           wait_for_title: Optional[List[str]] = None,
                           timeout_sec: float = 45.0) -> Dict[str, Any]:
    """等待目标窗口出现后，用 uiautomation 定位并点击按钮"""
    deadline = time.time() + timeout_sec
    win_title = None
    while time.time() < deadline:
        cur = present_windows(wait_for_title or title_keywords)
        if cur:
            win_title = cur[0]
            break
        time.sleep(0.6)
    if not win_title:
        return {"success": False, "error": f"超时未见目标窗口: {wait_for_title or title_keywords}"}

    activate_window(win_title)
    time.sleep(0.8)
    try:
        import uiautomation as uia
    except Exception as e:
        return {"success": False, "error": f"uiautomation 未安装: {e}",
                "window": win_title, "need_manual_calibration": True}

    attempted = []
    try:
        top = uia.WindowControl(searchDepth=1, SubName=win_title[:40])
        found = _search_button(top, button_names, attempted)
        if found is not None:
            found.Click(simulateMove=False)
            return {"success": True, "window": win_title,
                    "clicked_button": found.Name, "searched": attempted[:8]}
    except Exception as e:
        return {"success": False, "error": f"点击失败: {e}", "window": win_title,
                "searched": attempted[:8], "need_manual_calibration": True}

    return {"success": False, "window": win_title,
            "error": "未找到启动按钮控件", "searched": attempted[:8],
            "need_manual_calibration": True}


def _search_button(root, button_names: List[str], attempted: List[str]):
    import uiautomation as uia
    if root is None:
        return None
    try:
        if root.ControlType in (uia.ControlType.ButtonControl,
                                uia.ControlType.MenuItemControl,
                                uia.ControlType.TabItemControl,
                                uia.ControlType.CheckBoxControl):
            nm = (root.Name or "").strip()
            if nm:
                attempted.append(nm)
                if any(b in nm for b in button_names):
                    return root
        for child in root.GetChildren():
            got = _search_button(child, button_names, attempted)
            if got is not None:
                return got
    except Exception:
        pass
    return None


def start_all(mumu_instances: List[int] = (0, 1), click_start: bool = True,
              alas_wait_titles=("Alas", "Azur Lane"), maa_wait_titles=("MAA",)) -> Dict[str, Any]:
    """编排：MuMu→ALAS→MAA，并在各自主界面点启动"""
    report: Dict[str, Any] = {}
    report["mumu"] = launch_mumu(mumu_instances)

    report["alas_launch"] = launch_alas()
    report["alas_start"] = None
    if click_start:
        try:
            report["alas_start"] = click_button_in_window(
                alas_wait_titles, ["启动", "开始", "运行"], wait_for_title=alas_wait_titles)
        except Exception as e:
            report["alas_start"] = {"success": False, "error": str(e)}
        r = report["alas_start"] or {}
        if not r.get("success") or r.get("need_manual_calibration"):
            # 可选 CDP 兜底（若无 alas_web 则跳过）
            try:
                from LLM_v2.domains.launcher import alas_web  # noqa: F401
                from LLM_v2.domains.launcher.alas_web import alas_web_click
                report["alas_start_web"] = alas_web_click("启动")
            except ImportError:
                pass
            except Exception as e:
                report["alas_start_web"] = {"success": False, "error": str(e)}
            wr = report.get("alas_start_web") or {}
            if not wr.get("success"):
                report["alas_start_coord"] = click_window_rel(
                    [ALAS_MAIN_TITLE], ALAS_START_REL,
                    exclude_keywords=("Updater",))

    report["maa_launch"] = launch_maa()
    report["maa_start"] = None
    if click_start:
        try:
            report["maa_start_coord"] = click_window_rel(
                [MAA_MAIN_TITLE], MAA_START_REL)
        except Exception as e:
            report["maa_start_coord"] = {"success": False, "error": str(e)}
        cr = report["maa_start_coord"] or {}
        if not cr.get("success"):
            try:
                report["maa_start"] = click_button_in_window(
                    ["MAA -"], ["开始", "启动", "运行", "Link Start"],
                    wait_for_title=["MAA -"])
            except Exception as e:
                report["maa_start"] = {"success": False, "error": str(e)}

    report["final_windows"] = present_windows(["MuMu", "Alas", "Azur Lane", "MAA"])
    return report


def status() -> Dict[str, Any]:
    return {"present_windows": present_windows(
        ["MuMuNx", "MuMu Player", "Alas", "Azur Lane", "MAA"])}


# ============ 域 Prompt ============

_LAUNCHER_PROMPT = """可用工具（底层启动，非视觉）：
- launcher_start_all: 一键拉起 MuMu 指定实例 + ALAS + MAA，并在各自主界面点启动
- launcher_mumu: 用 MuMuManager 底层启动指定编号模拟器实例（如 [0,1]）
- launcher_alas / launcher_maa: 底层启动对应应用
- launcher_status: 上报相关窗口是否已存在（判断是否进入应用）
- launcher_stop: 结束本轮编排（不关模拟器/游戏）

执行原则：
1. 一次性完成"启动模拟器+两款+"用 launcher_start_all，不要拆成多次。
2. launcher_status 只在需要确认是否已运行时调用，避免无谓轮询。
3. 点启动靠 Windows 底层控件定位；若某应用按钮控件缺失，返回里会标 need_manual_calibration，交给人工补一次坐标。
4. launcher_* 属于电脑操作类，调用走异步线程池，不阻塞主流程。
"""


# ============ 域类 ============

class LauncherDomain(SubprocessCapabilityDomain, Actuator):
    """模拟器与游戏启动域（V2 子进程域，H-2 Actuator 类型）"""

    id = "launcher"
    label = "模拟器与游戏启动域"
    prefix = "launcher_"
    default_enabled = False     # 默认关闭（依赖 MuMuManager + ALAS + MAA 路径）
    type = CapabilityType.ACTUATOR

    call_timeout = 90.0
    tool_timeouts = {
        "launcher_start_all": 240.0,
        "launcher_mumu": 60.0,
        "launcher_alas": 10.0,
        "launcher_maa": 10.0,
        "launcher_status": 10.0,
        "launcher_stop": 5.0,
    }

    def launch_args(self) -> List[str]:
        return [os.path.join(os.path.dirname(__file__), "launcher_engine.py")]

    def allowed_names(self) -> List[str]:
        return list(LAUNCHER_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        allowed = set(self.allowed_names())
        return [s for s in LAUNCHER_TOOLS_SCHEMA if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _LAUNCHER_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：MuMuManager 必须存在（launcher 域需本机 MuMu 12）"""
        if not os.path.exists(MUMU_MANAGER):
            return f"MuMuManager 不存在: {MUMU_MANAGER}（launcher 域需本机 MuMu 12）"
        return None

    async def _run_child_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """基类钩子：实际由 _proxy_tool 调用 _call_child 处理"""
        return await self._call_child(method, params)


def build_launcher_domain() -> LauncherDomain:
    return LauncherDomain()


__all__ = ['LauncherDomain', 'build_launcher_domain', 'launch_mumu', 'launch_alas',
           'launch_maa', 'start_all', 'status', 'MUMU_MANAGER', 'ALAS_EXE', 'MAA_EXE',
           'ALAS_MAIN_TITLE', 'MAA_MAIN_TITLE', 'ALAS_START_REL', 'MAA_START_REL',
           'click_button_in_window', 'click_window_rel']