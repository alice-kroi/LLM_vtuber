# ============================================================
# LLM_v2/domains/desktop/desktop.py — V2 内建桌面域
# ============================================================
# 替换 V1 LLM/skill_world.py:DesktopWorld
# 封装 tool/computer_use.py 的 31 个 os_*/ui_*/vision_* 工具
# 内建域（非子进程），通过 DomainAssembly.call_tool 直接调用
# ============================================================

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import (
    CapabilityDomain,
    CapabilityType,
    tool_schema,
    Transceiver,
)

logger = logging.getLogger(__name__)


# 桌面域允许的工具（与 V1 DesktopWorld._DESKTOP_TOOL_NAMES 对齐）
_DESKTOP_TOOL_NAMES = [
    "os_open_path", "os_list_directory", "os_list_windows", "os_activate_window",
    "os_minimize_window", "os_show_desktop", "os_click_xy", "os_type_text",
    "os_press_key", "os_scroll", "ui_list_elements", "vision_click",
    "os_check", "os_capture_window", "os_open_file", "os_find_file",
    "os_browser_search", "os_clipboard_get", "os_clipboard_set",
    "os_list_processes", "os_list_apps", "os_launch_app",
    # 文件 / Shell 工具
    "os_create_file", "os_mkdir", "os_run_command", "os_read_file", "os_edit_file",
    # 结构化视觉工具
    "os_list_elements_json", "os_click_id", "os_type_by_label",
    # 智能粘贴
    "os_paste_text",
]


_DESKTOP_PROMPT = """可用工具：
- os_open_path: 系统级打开"我的电脑/盘符/文件夹/URL/应用"
- os_list_directory: 读取目录下的文件夹/文件列表
- os_list_windows / os_activate_window: 列窗口 / 激活窗口
- os_minimize_window / os_show_desktop: 最小化遮挡窗口 / 一键显示桌面
- os_click_xy / os_type_text / os_press_key / os_scroll: 键鼠操作
- ui_list_elements: UI Automation 读标准控件真实坐标
- vision_click: 视觉定位点击（webview/非标准界面用）
- os_capture_window: 截取指定窗口内容，观察界面现状
- os_check: 断言式核验（验证操作结果，只报差异）
- os_open_file: 用默认程序打开指定文件（文档/图片/程序）
- os_find_file: 按文件名关键词递归搜索文件位置
- os_browser_search: 打开浏览器搜索关键词（受控 Edge，可继续操作）
- os_clipboard_get / os_clipboard_set: 读/写系统剪贴板
- os_list_processes: 列出进程（确认软件是否在跑）
- os_list_apps / os_launch_app: 列可启动软件 / 启动软件（exe 路径或应用名）
- os_create_file / os_mkdir / os_run_command / os_read_file / os_edit_file: P0-1 文件/Shell 工具
  · os_create_file: 写/创建文本文件（项目文件首选，比 UI 键入快百倍）
  · os_mkdir: 创建目录（默认支持多级）
  · os_run_command: 执行 shell（git init / pip install / python main.py / dir 等）
  · os_read_file: 读文件（>5MB 用 offset+max_bytes 分块）
  · os_edit_file: Search-Replace 精确编辑（Aider 协议）
- os_list_elements_json / os_click_id / os_type_by_label: P0-minus 结构化视觉工具
  · os_list_elements_json: 截图 + 本地 PaddleOCR + UIA 合并，返回 [{id,type,name,text,bbox,conf}]
    → 想程序化决策（点哪儿/输哪儿）就用它，不要靠 vision_click 猜
  · os_click_id: 按元素 id 点击；强烈推荐同时传 bbox 锁定坐标（避免 OCR 重跑 id 漂移）
  · os_type_by_label: 按 label 自动定位输入框（中英双语 OS dialog 用）；label 支持 "中文|english"
- os_paste_text: 智能粘贴：剪贴板可用走 Ctrl+V，被锁自动降级到直接键入

执行原则：
1. 先盘点再动手：先 os_list_windows 看桌面遮挡/可用窗口。
2. 开目录/程序优先 os_open_path（系统Shell最稳），别模拟 win+r。
3. 读"某盘/文件夹里有什么"用 os_list_directory，不要全靠截图猜。
4. 发现目标被遮挡/找不到 → os_minimize_window 或 os_show_desktop 清理后重试。
5. 点标准控件用 ui_list_elements 拿坐标 → os_click_xy；webview/游戏用 vision_click。
6. **OS dialog（Save As / 设置 / 属性）**：用 os_list_elements_json 拿到元素表 → 用 os_type_by_label
   输入文本 / 用 os_click_id + bbox 点按钮，不要靠 vision_click 猜坐标。
7. **关键步骤用 os_check 验证结果**（窗口/文件/控件/画面断言），不成立就修正重试，
   绝不假装成功；os_check 失败回执含差异原因，据此换招或换目标。
8. 需要观察界面现状用 os_capture_window（只截指定窗口，避免全屏无关信息）。
9. 启动软件用 os_launch_app（exe 路径或应用名），确认是否在跑用 os_list_processes。
10. 写/改项目代码优先用文件工具链（os_create_file/os_read_file/os_edit_file/os_run_command），
    别用 UI 键入；UI 键入仅在 VS Code 这类 IDE 内部交互时使用。
"""


class DesktopDomain(Transceiver):
    """桌面电脑操作能力域（V2 内建，H-2 Transceiver 类型）

    V1 对齐：替换 LLM/skill_world.py:DesktopWorld
    H-2：标记 type=Transceiver（operator 调感知工具后 emit PERCEPTION_UPDATED 由 OperatorBrain 负责）
    """

    id = "desktop"
    label = "桌面电脑操作"
    prefix = "os_"
    default_enabled = True
    type = CapabilityType.TRANSCEIVER

    def inbound_event_types(self):
        """Desktop 能力声明：感知类工具被调后将 emit PERCEPTION_UPDATED
        （实际由 OperatorBrain 在调完 capture 类工具后 emit）
        """
        return ["perception_updated"]

    def outbound_event_types(self):
        """Desktop 能力声明：监听 TTS_SPEAK 事件（未来扩展：可联动表情）"""
        return []

    def __init__(self):
        super().__init__()
        # 把 tool.computer_use 的所有 os_* 函数绑定为实例方法
        # （DomainAssembly.call_tool 会通过 getattr 找到它们）
        from tool import computer_use
        for name in _DESKTOP_TOOL_NAMES:
            func = getattr(computer_use, name, None)
            if func is not None:
                # 绑定为实例方法（descriptor 协议）
                setattr(self, name, func)
            else:
                logger.warning(f"[DesktopDomain] tool.computer_use.{name} 不存在")

    def allowed_names(self) -> List[str]:
        return list(_DESKTOP_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        """复用 V1 COMPUTER_USE_TOOLS_SCHEMA，过滤出本域允许的工具"""
        from tool.computer_use import COMPUTER_USE_TOOLS_SCHEMA
        allowed = set(self.allowed_names())
        return [s for s in COMPUTER_USE_TOOLS_SCHEMA
                if s["function"]["name"] in allowed]

    def system_prompt_fragment(self) -> str:
        return _DESKTOP_PROMPT

    def preflight(self) -> Optional[str]:
        """前置检查：依赖 pyautogui + pygetwindow 是否就绪"""
        try:
            from tool.computer_use import _HAS_PYAUTOGUI, _HAS_PYGETWINDOW
            missing = []
            if not _HAS_PYAUTOGUI:
                missing.append("pyautogui")
            if not _HAS_PYGETWINDOW:
                missing.append("pygetwindow")
            if missing:
                return f"OS 依赖未安装: {', '.join(missing)}"
            return None
        except Exception as e:
            return f"preflight 异常: {e}"


def build_desktop_domain() -> DesktopDomain:
    """工厂函数（供 DomainAssembly 自动发现调用）"""
    return DesktopDomain()


__all__ = ['DesktopDomain', 'build_desktop_domain']