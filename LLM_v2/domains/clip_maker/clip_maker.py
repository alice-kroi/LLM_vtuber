# ============================================================
# LLM_v2/domains/clip_maker/clip_maker.py — 直播切片能力域
# ============================================================
# 依赖：ffmpeg（优先 imageio-ffmpeg 自带二进制，其次 PATH）
# 风险：R1（读本地录制、写 persona/clips）
# ============================================================

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List

from LLM_v2.core.capability_domain import Actuator, CapabilityType, tool_schema

_ROOT = Path(__file__).resolve().parents[3]
_OUT_DIR = _ROOT / "persona" / "clips"
_DEFAULT_INPUT = _ROOT / "persona" / "recordings"
_MEDIA_EXT = {".mp4", ".mkv", ".flv", ".mov", ".ts", ".webm", ".avi",
              ".wav", ".mp3", ".m4a", ".aac"}

_TOOL_NAMES = ["clip_list", "clip_cut", "clip_thumbnail", "clip_concat"]

_TOOLS_SCHEMA = [
    tool_schema("clip_list", "[切片] 列出某个目录下的录像/音频文件。",
                {"directory": {"type": "string", "description": "目录，默认 persona/recordings"},
                 "limit": {"type": "integer", "default": 20}}),
    tool_schema("clip_cut", "[切片] 从录像里截取一段，默认输出到 persona/clips/。",
                {"path": {"type": "string", "description": "源文件路径"},
                 "start": {"type": "number", "description": "开始秒数"},
                 "duration": {"type": "number", "description": "时长秒数"},
                 "out_name": {"type": "string", "description": "输出文件名（可选）"}},
                required=["path", "start", "duration"]),
    tool_schema("clip_thumbnail", "[切片] 在指定时间点截一张封面图（jpg）。",
                {"path": {"type": "string", "description": "源文件路径"},
                 "at": {"type": "number", "description": "秒数，默认 0"},
                 "out_name": {"type": "string", "description": "输出文件名（可选）"}},
                required=["path"]),
    tool_schema("clip_concat", "[切片] 把多个片段按顺序拼成一个文件。",
                {"paths": {"type": "array", "items": {"type": "string"},
                           "description": "片段路径列表"},
                 "out_name": {"type": "string", "description": "输出文件名（可选）"}},
                required=["paths"]),
]


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("找不到 ffmpeg（可 pip install imageio-ffmpeg）")
        return exe


def _run(args: List[str], timeout: float = 180.0) -> subprocess.CompletedProcess:
    return subprocess.run([_ffmpeg(), "-hide_banner", "-loglevel", "error", *args],
                          capture_output=True, text=True, timeout=timeout)


class ClipMakerDomain(Actuator):
    id = "clip_maker"
    label = "直播切片"
    prefix = "clip_"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    call_timeout = 300.0

    def allowed_names(self) -> List[str]:
        return list(_TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(_TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return "【直播切片】可列出录像、截取片段、抓封面、拼接；输出在 persona/clips/。"

    def _out(self, name: str, suffix: str) -> Path:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        if name:
            filename = name if Path(name).suffix else f"{name}{suffix}"
        else:
            filename = f"clip_{time.strftime('%Y%m%d-%H%M%S')}{suffix}"
        return _OUT_DIR / Path(filename).name

    def clip_list(self, directory: str = "", limit: int = 20) -> Dict[str, Any]:
        base = Path(directory) if directory else _DEFAULT_INPUT
        if not base.exists():
            return {"success": True, "directory": str(base), "count": 0, "files": []}
        files = []
        for p in sorted(base.iterdir()):
            if p.is_file() and p.suffix.lower() in _MEDIA_EXT:
                files.append({"name": p.name, "path": str(p), "size": p.stat().st_size})
            if len(files) >= int(limit or 20):
                break
        return {"success": True, "directory": str(base), "count": len(files), "files": files}

    def clip_cut(self, path: str, start: float, duration: float,
                 out_name: str = "") -> Dict[str, Any]:
        src = Path(path)
        if not src.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        out = self._out(out_name, src.suffix or ".mp4")
        try:
            res = _run(["-ss", str(float(start)), "-i", str(src), "-t", str(float(duration)),
                        "-c", "copy", "-avoid_negative_ts", "make_zero", "-y", str(out)])
            if res.returncode != 0:
                res = _run(["-ss", str(float(start)), "-i", str(src), "-t", str(float(duration)),
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                            "-y", str(out)])
            if res.returncode != 0:
                return {"success": False, "error": (res.stderr or "ffmpeg 失败")[:500]}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        return {"success": out.exists(), "path": str(out),
                "size": out.stat().st_size if out.exists() else 0}

    def clip_thumbnail(self, path: str, at: float = 0.0,
                       out_name: str = "") -> Dict[str, Any]:
        src = Path(path)
        if not src.exists():
            return {"success": False, "error": f"文件不存在: {path}"}
        out = self._out(out_name, ".jpg")
        try:
            res = _run(["-ss", str(float(at)), "-i", str(src), "-frames:v", "1",
                        "-q:v", "2", "-y", str(out)])
            if res.returncode != 0:
                return {"success": False, "error": (res.stderr or "ffmpeg 失败")[:500]}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        return {"success": out.exists(), "path": str(out),
                "size": out.stat().st_size if out.exists() else 0}

    def clip_concat(self, paths: Any, out_name: str = "") -> Dict[str, Any]:
        if isinstance(paths, str):
            items = [x.strip() for x in paths.split(",") if x.strip()]
        else:
            items = [str(x) for x in (paths or [])]
        if len(items) < 2:
            return {"success": False, "error": "至少需要两个片段"}
        missing = [p for p in items if not Path(p).exists()]
        if missing:
            return {"success": False, "error": f"文件不存在: {missing[:3]}"}
        out = self._out(out_name, Path(items[0]).suffix or ".mp4")
        list_file = _OUT_DIR / f"concat_{int(time.time())}.txt"
        list_file.write_text(
            "\n".join(f"file '{Path(p).resolve().as_posix()}'" for p in items),
            encoding="utf-8")
        try:
            res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                        "-c", "copy", "-y", str(out)])
            if res.returncode != 0:
                res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
                            "-y", str(out)])
            if res.returncode != 0:
                return {"success": False, "error": (res.stderr or "ffmpeg 失败")[:500]}
        finally:
            try:
                list_file.unlink()
            except Exception:  # noqa: BLE001
                pass
        return {"success": out.exists(), "path": str(out),
                "size": out.stat().st_size if out.exists() else 0}


def build_clip_maker_domain() -> ClipMakerDomain:
    return ClipMakerDomain()


__all__ = ["ClipMakerDomain", "build_clip_maker_domain"]
