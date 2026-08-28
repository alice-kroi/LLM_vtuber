#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视觉弹幕服务模块

定时截取屏幕画面，通过视觉模型生成弹幕评论，注入消息队列。
"""

import asyncio
import logging
import time
from typing import Optional

from danmu_config import VisionDanmuConfig
from tool.vision_tool import capture_window_screenshot, _call_vision_model, vision_config

logger = logging.getLogger("vision_danmu")


class VisionDanmuService:
    """视觉弹幕服务，定时截图并生成弹幕评论"""

    def __init__(self, config: VisionDanmuConfig, message_queue: asyncio.Queue):
        self._config = config
        self._queue = message_queue
        self._stop_event = asyncio.Event()
        self._last_comment_time: float = 0.0
        self._stats = {
            "capture_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "last_capture_time": None,
        }
        logger.info("视觉弹幕服务已初始化，截图间隔=%d秒，冷却期=%d秒",
                     self._config.capture_interval, self._config.cooldown)

    async def start(self):
        """启动定时截图循环"""
        logger.info("视觉弹幕服务启动")
        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                if self._can_proceed():
                    await self._on_capture_tick()
                await asyncio.sleep(self._config.capture_interval)
            except asyncio.CancelledError:
                logger.info("视觉弹幕服务任务被取消")
                break
            except Exception as e:
                logger.error("视觉弹幕服务循环异常: %s", e)
                await asyncio.sleep(1)

        logger.info("视觉弹幕服务已停止")

    async def stop(self):
        """发送停止信号并等待循环退出"""
        logger.info("视觉弹幕服务收到停止信号")
        self._stop_event.set()

    @property
    def is_running(self) -> bool:
        """服务是否正在运行"""
        return not self._stop_event.is_set()

    async def _on_capture_tick(self):
        """执行一次完整的截图→分析→注入流程"""
        self._stats["capture_count"] += 1
        self._stats["last_capture_time"] = time.time()

        screenshot_result = await capture_window_screenshot(
            self._config.target_window or None
        )

        if not screenshot_result.get("success"):
            logger.error("截图失败: %s", screenshot_result.get("error", "未知错误"))
            self._stats["failure_count"] += 1
            return

        logger.info("截图成功: %s (%dx%d)",
                     screenshot_result.get("window_title", "未知"),
                     screenshot_result.get("width", 0),
                     screenshot_result.get("height", 0))

        image_base64 = screenshot_result["image_base64"]
        comment = await self._generate_comment(image_base64)

        if not comment:
            logger.error("弹幕评论生成失败")
            self._stats["failure_count"] += 1
            return

        self._last_comment_time = time.time()
        self._stats["success_count"] += 1

        danmu_message = {
            "role": "user",
            "content": comment,
            "source": "vision",
            "name": "视觉弹幕",
        }
        await self._queue.put(danmu_message)
        logger.info("视觉弹幕已注入队列: %s", comment)

    async def _generate_comment(self, image_base64: str) -> str:
        """构建prompt，调用视觉模型生成弹幕评论"""
        prompt = (
            f"{self._config.persona}\n\n"
            "请观看当前画面，发表一条弹幕评论。"
            "评论要求：20-30字，口语化，像真实观众发的弹幕。"
        )

        result = await asyncio.to_thread(
            _call_vision_model, image_base64, prompt
        )

        if not result.get("success"):
            logger.error("视觉分析失败: %s", result.get("error", "未知错误"))
            return ""

        comment = result.get("result", "").strip()
        if not comment:
            return ""

        if len(comment) > self._config.max_comment_length:
            comment = comment[: self._config.max_comment_length] + "..."

        return comment

    def _can_proceed(self) -> bool:
        """检查是否可以执行截图分析"""
        if not vision_config.enabled:
            logger.debug("视觉功能未启用，跳过本次截图")
            return False

        now = time.time()
        if now - self._last_comment_time < self._config.cooldown:
            remaining = int(self._config.cooldown - (now - self._last_comment_time))
            logger.debug("冷却期中，剩余 %d 秒", remaining)
            return False

        return True

    def get_status(self) -> dict:
        """获取服务状态统计信息"""
        now = time.time()
        cooldown_remaining = 0.0
        if self._last_comment_time > 0:
            elapsed = now - self._last_comment_time
            if elapsed < self._config.cooldown:
                cooldown_remaining = self._config.cooldown - elapsed

        return {
            "enabled": self._config.enabled,
            "capture_interval": self._config.capture_interval,
            "target_window": self._config.target_window,
            "capture_count": self._stats["capture_count"],
            "success_count": self._stats["success_count"],
            "failure_count": self._stats["failure_count"],
            "last_capture_time": self._stats["last_capture_time"],
            "cooldown_remaining": round(cooldown_remaining, 1),
            "is_running": not self._stop_event.is_set(),
        }