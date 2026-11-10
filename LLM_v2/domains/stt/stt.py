# ============================================================
# LLM_v2/domains/stt/stt.py — 语音转写（faster-whisper）
# ============================================================
# 依赖：faster-whisper + imageio-ffmpeg
# 首次使用会从 HF 镜像下载模型；之后走本地缓存。
# 风险：R1（读音频、写临时 wav）
# ============================================================

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

from LLM_v2.core.capability_domain import Actuator, CapabilityType, tool_schema

_TOOL_NAMES = ["stt_transcribe", "stt_health"]

_TOOLS_SCHEMA = [
    tool_schema(
        "stt_transcribe",
        "[语音] 把本地音频/视频里的说话转成文字（faster-whisper）。"
        "支持 wav/mp3/mp4 等，ffmpeg 会先转成 16k 单声道。",
        {
            "path": {"type": "string", "description": "音频/视频文件路径"},
            "language": {"type": "string", "description": "语言，默认 zh；auto 自动",
                         "default": "zh"},
            "model": {"type": "string", "description": "模型大小，默认 tiny",
                      "default": "tiny"},
            "beam_size": {"type": "integer", "description": "beam 搜索宽度，默认 1",
                          "default": 1},
        },
        required=["path"],
    ),
    tool_schema("stt_health", "[语音] 检查 faster-whisper / ffmpeg / 模型缓存是否就绪。"),
]

_MODELS: Dict[str, Any] = {}


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("找不到 ffmpeg（pip install imageio-ffmpeg）")
        return exe


def _to_wav16k(src: Path) -> Path:
    out = Path(tempfile.gettempdir()) / f"stt_{int(time.time() * 1000)}.wav"
    res = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
         "-ar", "16000", "-ac", "1", str(out)],
        capture_output=True, text=True, timeout=180)
    if res.returncode != 0 or not out.exists():
        raise RuntimeError((res.stderr or "ffmpeg 转换失败")[:400])
    return out


def _model_cached() -> bool:
    try:
        from huggingface_hub.constants import HF_HUB_CACHE
        hub = Path(HF_HUB_CACHE)
    except Exception:  # noqa: BLE001
        hub = Path.home() / ".cache" / "huggingface" / "hub"
    if not hub.exists():
        return False
    return any(p.name.startswith("models--Systran--faster-whisper")
               for p in hub.iterdir())


class SttDomain(Actuator):
    id = "stt"
    label = "语音转写"
    prefix = "stt_"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    call_timeout = 300.0

    def allowed_names(self) -> List[str]:
        return list(_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return "【语音转写】可把本地音频/视频转成文字；模型默认 tiny，缓存后离线可用。"

    def stt_health(self) -> Dict[str, Any]:
        try:
            import faster_whisper  # noqa: F401
            fw = True
        except Exception:  # noqa: BLE001
            fw = False
        try:
            ff = _ffmpeg()
        except Exception as e:  # noqa: BLE001
            ff = ""
            ff_err = str(e)
        else:
            ff_err = ""
        return {
            "success": True,
            "faster_whisper": fw,
            "ffmpeg": ff,
            "ffmpeg_error": ff_err,
            "model_cached": _model_cached(),
            "note": "首次使用会自动下载模型（HF 镜像 https://hf-mirror.com）",
        }

    def stt_transcribe(self, path: str, language: str = "zh",
                       model: str = "tiny", beam_size: int = 1) -> Dict[str, Any]:
        src = Path(path)
        if not src.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        try:
            from faster_whisper import WhisperModel
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"faster-whisper 未安装: {e}"}
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")

        key = str(model)
        try:
            import numpy as np
            from scipy.io import wavfile

            wav = _to_wav16k(src)
            rate, data = wavfile.read(wav)
            audio = data.astype(np.float32) / 32768.0
            try:
                wav.unlink()
            except Exception:  # noqa: BLE001
                pass

            m = _MODELS.get(key)
            if m is None:
                m = WhisperModel(key, device="cpu", compute_type="int8")
                _MODELS[key] = m
            lang = None if (language or "").lower() in ("auto", "") else language
            segments, info = m.transcribe(
                audio, language=lang, beam_size=max(1, int(beam_size or 1)),
                vad_filter=True)
            out = [{"start": round(s.start, 2), "end": round(s.end, 2),
                    "text": s.text.strip()} for s in segments]
            text = "".join(x["text"] for x in out).strip()
            if not text:
                return {"success": False, "error": "没有识别到语音"}
            return {"success": True, "text": text, "segments": out,
                    "language": info.language,
                    "duration": round(float(info.duration), 2)}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}


def build_stt_domain() -> SttDomain:
    return SttDomain()


__all__ = ["SttDomain", "build_stt_domain"]
