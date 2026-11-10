# ============================================================
# LLM_v2/domains/sing/sing.py — AI 唱歌 / 语音合成（GPT-SoVITS）
# ============================================================
# 依赖：本地 GPT-SoVITS v2pro API（默认 127.0.0.1:9880）
#   启动：runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
# 说明：这是"翻唱式"人声合成（用参考音频克隆音色），不是乐谱级歌声合成。
# 风险：R2（本机 HTTP + 可拉起本地服务）
# ============================================================

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from LLM_v2.core.capability_domain import Actuator, CapabilityType, tool_schema

_ROOT = Path(__file__).resolve().parents[3]
_SOVITS_DIR = _ROOT / "GPT-SoVITS-v2pro"
_OUT_DIR = _ROOT / "persona" / "sing_output"
_REF_DIR = Path(os.environ.get(
    "SOVITS_REF_DIR", r"D:\Github\【GPT-SoVITS】爱莉希雅V2\参考音频"))
_HOST = os.environ.get("SOVITS_HOST", "127.0.0.1")
_PORT = int(os.environ.get("SOVITS_PORT", "9880"))

_TOOL_NAMES = ["sing_health", "sing_render", "sing_list", "sing_start_api"]

_TOOLS_SCHEMA = [
    tool_schema("sing_health", "[唱歌] 检查 GPT-SoVITS 语音服务是否在线。"),
    tool_schema(
        "sing_render",
        "[唱歌] 用本地 GPT-SoVITS 合成一段人声（翻唱式），保存为 wav。",
        {
            "text": {"type": "string", "description": "要唱的歌词/句子"},
            "tone": {"type": "string", "description": "参考音频语气，如 严肃/假装（可选）"},
            "ref_audio": {"type": "string", "description": "参考音频路径（可选）"},
            "prompt_text": {"type": "string", "description": "参考音频对应文本（可选）"},
            "out_name": {"type": "string", "description": "输出文件名（可选）"},
            "speed": {"type": "number", "description": "语速倍率，默认 1.0"},
        },
        required=["text"],
    ),
    tool_schema("sing_list", "[唱歌] 列出已生成的音频。",
                {"limit": {"type": "integer", "default": 20}}),
    tool_schema("sing_start_api",
                "[唱歌] 启动本地 GPT-SoVITS API（CPU 加载较慢，最多等 180 秒）。",
                {"wait_seconds": {"type": "integer", "default": 120}}),
]


def _port_open(host: str = _HOST, port: int = _PORT, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:  # noqa: BLE001
        return False


def _pick_ref(tone: str = "", ref_audio: str = "") -> str:
    if ref_audio and Path(ref_audio).exists():
        return ref_audio
    if _REF_DIR.is_dir():
        files = sorted(p for p in _REF_DIR.iterdir()
                       if p.suffix.lower() == ".wav")
        if tone:
            for p in files:
                if p.name.startswith(f"【{tone}】"):
                    return str(p)
        if files:
            return str(files[0])
    return ""


class SingDomain(Actuator):
    id = "sing"
    label = "AI 唱歌"
    prefix = "sing_"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    call_timeout = 300.0

    def allowed_names(self) -> List[str]:
        return list(_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return ("【AI 唱歌】用本地 GPT-SoVITS 克隆音色合成人声；"
                "输出在 persona/sing_output/。")

    def sing_health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "api_online": _port_open(),
            "host": _HOST,
            "port": _PORT,
            "ref_dir": str(_REF_DIR),
            "ref_available": _REF_DIR.is_dir(),
            "hint": "离线时调用 sing_start_api 启动本地服务",
        }

    def _out(self, name: str) -> Path:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        filename = (name if name.endswith(".wav") else f"{name}.wav") if name \
            else f"sing_{time.strftime('%Y%m%d-%H%M%S')}.wav"
        return _OUT_DIR / Path(filename).name

    def sing_render(self, text: str, tone: str = "", ref_audio: str = "",
                    prompt_text: str = "", out_name: str = "",
                    speed: float = 1.0) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"success": False, "error": "text 不能为空"}
        if not _port_open():
            return {"success": False,
                    "error": "GPT-SoVITS API 未启动（可用 sing_start_api）"}
        ref = _pick_ref(tone, ref_audio)
        if not ref:
            return {"success": False,
                    "error": f"缺少参考音频（{_REF_DIR} 不存在）"}
        prompt = prompt_text or Path(ref).stem.replace("【", "").replace("】", "")
        payload = {
            "text": text[:200], "text_lang": "zh",
            "ref_audio_path": ref, "aux_ref_audio_paths": [],
            "prompt_text": prompt, "prompt_lang": "zh",
            "top_k": 5, "top_p": 1.0, "temperature": 1.0,
            "text_split_method": "cut5", "batch_size": 1,
            "speed_factor": float(speed or 1.0), "fragment_interval": 0.3,
            "seed": -1, "parallel_infer": False, "repetition_penalty": 1.35,
            "sample_steps": 32, "super_sampling": False,
            "streaming_mode": 0, "media_type": "wav",
        }
        req = urllib.request.Request(
            f"http://{_HOST}:{_PORT}/tts",
            data=json.dumps(payload).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=290) as resp:
                audio = resp.read()
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"合成失败: {e}"}
        if not audio or not audio[:4] == b"RIFF":
            return {"success": False,
                    "error": f"返回不是 WAV: {audio[:120]!r}"}
        out = self._out(out_name or "")
        out.write_bytes(audio)
        return {"success": True, "path": str(out), "size": len(audio),
                "ref_audio": Path(ref).name, "text": text[:60]}

    def sing_list(self, limit: int = 20) -> Dict[str, Any]:
        if not _OUT_DIR.is_dir():
            return {"success": True, "count": 0, "files": []}
        files = sorted(_OUT_DIR.glob("*.wav"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        rows = [{"name": p.name, "path": str(p), "size": p.stat().st_size}
                for p in files[:max(1, int(limit or 20))]]
        return {"success": True, "count": len(rows), "files": rows}

    def sing_start_api(self, wait_seconds: int = 120) -> Dict[str, Any]:
        if _port_open():
            return {"success": True, "already": True, "api_online": True}
        python = _SOVITS_DIR / "runtime" / "python.exe"
        if not python.exists():
            return {"success": False, "error": f"找不到 runtime python: {python}"}
        try:
            subprocess.Popen(
                [str(python), "api_v2.py", "-a", _HOST, "-p", str(_PORT),
                 "-c", "GPT_SoVITS/configs/tts_infer.yaml"],
                cwd=str(_SOVITS_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        deadline = time.time() + max(10, int(wait_seconds or 120))
        while time.time() < deadline:
            if _port_open(timeout=0.5):
                return {"success": True, "already": False, "api_online": True}
            time.sleep(2)
        return {"success": False, "error": "启动超时（查看 SoVITS 控制台日志）",
                "api_online": False}


def build_sing_domain() -> SingDomain:
    return SingDomain()


__all__ = ["SingDomain", "build_sing_domain"]
