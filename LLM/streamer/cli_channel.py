#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI 渠道：从 stdin 读用户输入，投主播脑统一收件箱。

用法：主程序加 --cli 启动；直接在终端输入内容即可与主播脑对话。
"""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)


async def start_cli_channel() -> None:
    from LLM.streamer.inbox import get_inbox
    from LLM.streamer.message import StreamerMessage

    inbox = get_inbox()
    if inbox is None:
        print("[CLI] 主播脑收件箱未初始化，无法对话")
        return
    print("[CLI] 直接输入内容与主播脑对话（回车发送，Ctrl+C 退出）")
    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
        except Exception:
            break
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        msg = StreamerMessage(channel="cli", msg_type="chat",
                              user_id="cli", user_name="用户", content=line)
        try:
            await inbox.submit(msg)
        except Exception as e:
            logger.warning(f"CLI 投递失败: {e}")
