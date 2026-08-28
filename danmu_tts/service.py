import logging
import time
from typing import Dict, Any

from danmu_config import DanmuTtsConfig
from audio.audio_main import clean_text_for_tts
from .player import DanmuTtsPlayer

logger = logging.getLogger("danmu_tts")


class DanmuTtsService:
    def __init__(self, config: DanmuTtsConfig):
        self.config = config
        self.player = DanmuTtsPlayer(config)
        self._running = False
        self._stats = {
            "total_received": 0,
            "total_played": 0,
            "total_skipped": 0,
            "total_failed": 0,
            "last_received_time": 0,
            "last_play_time": 0,
        }

    def on_danmaku(self, message: str):
        self._stats["total_received"] += 1
        self._stats["last_received_time"] = time.time()

        if not self.config.enabled:
            return

        if not message or not message.strip():
            logger.debug("弹幕为空，跳过")
            self._stats["total_skipped"] += 1
            return

        cleaned = clean_text_for_tts(message)
        if not cleaned:
            logger.debug("弹幕清理后为空，跳过")
            self._stats["total_skipped"] += 1
            return

        if len(cleaned) > self.config.max_text_length:
            cleaned = cleaned[: self.config.max_text_length]
            logger.debug(f"弹幕截断到{self.config.max_text_length}字符")

        if self.player.is_busy():
            logger.debug(f"播放器正忙，跳过弹幕: {cleaned[:30]}...")
            self._stats["total_skipped"] += 1
            return

        success = self.player.play_text(cleaned)
        if success:
            self._stats["total_played"] += 1
            self._stats["last_play_time"] = time.time()
        else:
            self._stats["total_failed"] += 1
            logger.error(f"弹幕播放失败: {cleaned[:30]}...")

    def start(self):
        self._running = True
        logger.info("弹幕TTS服务已启动")

    def stop(self):
        self._running = False
        self.player.stop()
        logger.info("弹幕TTS服务已停止")

    def get_status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "enabled": self.config.enabled,
            "busy": self.player.is_busy(),
            "stats": dict(self._stats),
        }
