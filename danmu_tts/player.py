import os
import tempfile
import threading
import logging
import asyncio
from typing import Optional

from danmu_config import DanmuTtsConfig
from audio.audio_main import tts_request, play_audio

logger = logging.getLogger("danmu_tts")


DEFAULT_TTS_HOST = "127.0.0.1"
DEFAULT_TTS_PORT = 9880


class DanmuTtsPlayer:
    def __init__(self, config: DanmuTtsConfig):
        self.config = config
        self._tts_host = getattr(config, "tts_host", DEFAULT_TTS_HOST)
        self._tts_port = getattr(config, "tts_port", DEFAULT_TTS_PORT)
        self._lock = threading.Lock()
        self._busy = False
        self._stop_flag = threading.Event()
        self._current_thread: Optional[threading.Thread] = None

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def play_text(self, text: str) -> bool:
        with self._lock:
            if self._busy:
                logger.debug("播放器正忙，跳过播放请求")
                return False
            self._busy = True
            self._stop_flag.clear()

        self._current_thread = threading.Thread(
            target=self._play_worker,
            args=(text,),
            daemon=True,
        )
        self._current_thread.start()
        logger.info(f"开始播放弹幕文本: {text[:50]}...")
        return True

    def _play_worker(self, text: str):
        try:
            loop = asyncio.new_event_loop()
            try:
                audio_data = loop.run_until_complete(
                    tts_request(
                        text,
                        host=self._tts_host,
                        port=self._tts_port,
                    )
                )
            finally:
                loop.close()

            if self._stop_flag.is_set():
                logger.info("播放被停止标志中断")
                return

            if not audio_data.get("success"):
                logger.error(f"TTS请求失败: {audio_data.get('error')}")
                return

            audio_bytes = audio_data.get("audio_data")
            if not audio_bytes:
                logger.error("TTS返回音频数据为空")
                return

            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    suffix=".wav",
                    delete=False,
                    dir=tempfile.gettempdir(),
                ) as f:
                    f.write(audio_bytes)
                    temp_path = f.name

                if self._stop_flag.is_set():
                    return

                logger.info(f"播放音频文件: {temp_path}")
                result = play_audio(temp_path)

                if result.get("success"):
                    logger.info("音频播放完成")
                else:
                    logger.error(f"音频播放失败: {result.get('error')}")

            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

        except Exception as e:
            logger.error(f"播放弹幕文本异常: {e}", exc_info=True)
        finally:
            with self._lock:
                self._busy = False
                self._stop_flag.clear()

    def stop(self):
        logger.info("停止当前播放")
        self._stop_flag.set()
