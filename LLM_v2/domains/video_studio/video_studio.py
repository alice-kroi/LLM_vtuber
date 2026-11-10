# ============================================================
# LLM_v2/domains/video_studio/video_studio.py — 视频创作能力域
# ============================================================
# 原生内存域（Actuator）：ffmpeg/ffprobe 封装，不依赖外部服务。
# 输出统一落在 persona/video_studio/。
# 与 clip_maker 的分工：clip_maker 只做直播切片；本域负责成片创作
# （裁切、转场拼接、文字/字幕、配乐、封面、平台规格导出）。
# ============================================================

from __future__ import annotations

import asyncio
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from LLM_v2.core.capability_domain import Actuator, CapabilityType

from .video_studio_tools import (
    PLATFORM_PRESETS,
    TOOL_NAMES,
    TOOL_TIMEOUTS,
    TOOLS_SCHEMA,
    TRANSITIONS,
)

_ROOT = Path(__file__).resolve().parents[3]
_OUT_DIR = _ROOT / "persona" / "video_studio"
_VIDEO_EXT = {".mp4", ".mkv", ".flv", ".mov", ".ts", ".webm", ".avi", ".m4v"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_SUB_EXT = {".srt", ".ass", ".ssa", ".vtt"}

_PROMPT = """【视频创作】可用工具：
- video_doctor: 自检 ffmpeg/滤镜/中文字体/预设
- video_probe: 读时长、分辨率、帧率、音轨
- video_cut: 截取片段（accurate=false 更快）
- video_concat: 拼接；给 transition 走 xfade 转场（要求分辨率一致）
- video_text: 叠加文字（多行、位置、底色）
- video_subtitle: 烧录 SRT/ASS/VTT 字幕
- video_audio: 加 BGM（混合或替换、循环、淡入淡出）
- video_cover: 截封面，可套平台尺寸并加标题
- video_export: 按平台规格转码导出
- video_tts: 文案合成 WAV 配音（不播放）
- video_slideshow: 图片/纯色背景生成静音底片
- video_narrated: 一键口播成片（文案→按句配音→底片→字幕→BGM→导出）

工作流 A（有素材）：video_probe → 剪辑/拼接 → 文字/字幕/配乐 → video_cover → video_export。
工作流 B（无素材）：video_narrated（可给 images；不给则纯色背景）。
输出统一在 persona/video_studio/；转场拼接前先确认分辨率一致。
"""


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("找不到 ffmpeg（可 pip install imageio-ffmpeg）")
        return exe


def _ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(args: List[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_ffmpeg(), "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, stdin=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )


def _run_meta(path: Path) -> str:
    """用 ffmpeg -i 读取元信息（ffprobe 缺失时的兜底）。"""
    res = subprocess.run(
        [_ffmpeg(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, stdin=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )
    return res.stderr or ""


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fps_from_fraction(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def _probe_with_ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    exe = _ffprobe()
    if not exe:
        return None
    res = subprocess.run(
        [exe, "-v", "error", "-show_entries",
         "format=duration,size,bit_rate:stream=codec_type,codec_name,width,height,"
         "r_frame_rate,sample_rate,channels,bit_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, stdin=subprocess.DEVNULL,
        creationflags=_creationflags(),
    )
    if res.returncode != 0:
        return None
    try:
        import json
        data = json.loads(res.stdout or "{}")
    except Exception:  # noqa: BLE001
        return None

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": _to_float(fmt.get("duration")),
        "size": int(_to_float(fmt.get("size"))),
        "bitrate": int(_to_float(fmt.get("bit_rate"))),
        "width": int(_to_float(video.get("width"))),
        "height": int(_to_float(video.get("height"))),
        "fps": _fps_from_fraction(str(video.get("r_frame_rate") or "")),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audio.get("codec_name") or ""),
        "sample_rate": int(_to_float(audio.get("sample_rate"))),
        "channels": int(_to_float(audio.get("channels"))),
        "has_audio": bool(audio),
    }


def _probe_with_ffmpeg(path: Path) -> Dict[str, Any]:
    text = _run_meta(path)
    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))

    width = height = fps = 0
    video_codec = ""
    audio_codec = ""
    sample_rate = channels = 0
    has_audio = False
    bitrate = 0

    mb = re.search(r"bitrate:\s*(\d+)\s*kb/s", text)
    if mb:
        bitrate = int(mb.group(1)) * 1000

    for line in text.splitlines():
        if " Video: " in line:
            mv = re.search(r"Video:\s*([A-Za-z0-9_]+)", line)
            if mv:
                video_codec = mv.group(1)
            mr = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if mr:
                width, height = int(mr.group(1)), int(mr.group(2))
            mf = re.search(r"([\d.]+)\s*fps", line)
            if mf:
                fps = _to_float(mf.group(1))
        elif " Audio: " in line:
            has_audio = True
            ma = re.search(r"Audio:\s*([A-Za-z0-9_]+)", line)
            if ma:
                audio_codec = ma.group(1)
            msr = re.search(r"(\d+)\s*Hz", line)
            if msr:
                sample_rate = int(msr.group(1))
            if " stereo" in line:
                channels = 2
            elif " mono" in line:
                channels = 1
            else:
                mc = re.search(r"(\d+)\s*channels?", line)
                if mc:
                    channels = int(mc.group(1))

    return {
        "duration": duration,
        "size": path.stat().st_size if path.exists() else 0,
        "bitrate": bitrate,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "sample_rate": sample_rate,
        "channels": channels,
        "has_audio": has_audio,
    }


def _probe_media(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise ValueError(f"文件不存在: {path}")
    info = _probe_with_ffprobe(path)
    if info is None:
        info = _probe_with_ffmpeg(path)
    if not info.get("width") and not info.get("has_audio"):
        raise ValueError(f"无法识别媒体文件: {path.name}")
    return info


def _resolve_input(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _ROOT / p
    p = p.resolve()
    if not p.exists() or not p.is_file():
        raise ValueError(f"文件不存在: {path}")
    return p


def _resolve_font(font: str = "") -> Optional[Path]:
    if font:
        p = Path(font)
        if not p.is_absolute():
            p = _ROOT / p
        if p.exists() and p.is_file():
            return p
        # 常见中文字体名 → Windows 字体文件（fontconfig 未必能解析中文名）
        if os.name == "nt":
            base = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            mapped = {
                "microsoft yahei": base / "msyh.ttc",
                "微软雅黑": base / "msyh.ttc",
                "simhei": base / "simhei.ttf",
                "黑体": base / "simhei.ttf",
                "simsun": base / "simsun.ttc",
                "宋体": base / "simsun.ttc",
            }
            hit = mapped.get(font.strip().lower())
            if hit and hit.exists():
                return hit
        # 其它字体名交给 fontconfig 解析
        return None

    if os.name == "nt":
        base = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidates = [base / "msyh.ttc", base / "msyhbd.ttc", base / "simhei.ttf"]
    elif sys.platform == "darwin":
        candidates = [
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def _filter_path(path: Path) -> str:
    """ffmpeg 滤镜参数里的路径转义（Windows 盘符冒号是重点）。"""
    text = str(path).replace("\\", "/")
    text = text.replace(":", "\\:")
    return text.replace("'", "\\'")


def _out(name: str, suffix: str, subdir: str = "") -> Path:
    base = _OUT_DIR / subdir if subdir else _OUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    if name:
        filename = name if Path(name).suffix else f"{name}{suffix}"
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        filename = f"video_{stamp}{suffix}"
    return base / Path(filename).name


def _work_dir() -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    work = _OUT_DIR / f"_work_{stamp}"
    work.mkdir(parents=True, exist_ok=True)
    return work


def _concat_line(path: Path) -> str:
    safe = str(path.resolve()).replace("'", "'\\''")
    return f"file '{safe}'"


def _split_script(text: str, max_chars: int = 40) -> List[str]:
    """按句切分口播文案；长句再按逗号或硬切。"""
    text = (text or "").strip()
    if not text:
        return []
    limit = max(10, int(max_chars or 40))
    raw = re.split(r"(?<=[。！？!?；;])|\n+", text)
    result: List[str] = []
    for chunk in raw:
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) <= limit:
            result.append(chunk)
            continue
        buf = ""
        for piece in re.split(r"(?<=[，,])", chunk):
            if not piece:
                continue
            if len(buf) + len(piece) <= limit:
                buf += piece
            else:
                if buf.strip():
                    result.append(buf.strip())
                buf = piece
        if buf.strip():
            result.append(buf.strip())
    # 没有任何标点时按字数硬切
    if not result:
        result = [text[i:i + limit] for i in range(0, len(text), limit)]
    # 仍超长（例如单句 100 字无逗号）继续硬切
    final: List[str] = []
    for item in result:
        if len(item) <= limit:
            final.append(item)
        else:
            final.extend(item[i:i + limit] for i in range(0, len(item), limit))
    return final


def _srt_timestamp(seconds: float) -> str:
    ms = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _load_tts_engine(preferred: str = "auto") -> Any:
    import audio.engines  # noqa: F401  触发引擎注册
    from audio.tts_engine import resolve_engine
    engine = resolve_engine(preferred or "auto")
    if engine is None:
        raise RuntimeError("无可用 TTS 引擎（检查 config.ini [tts] / 豆包 / edge-tts / GPT-SoVITS）")
    return engine


def _write_audio_result(result: Any, out: Path) -> None:
    """把 TTS 结果写成 WAV：sovits 直接给 WAV，豆包/edge 给 PCM。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    data = getattr(result, "audio_data", b"") or b""
    fmt = str(getattr(result, "format", "") or "").lower()
    if fmt == "wav":
        out.write_bytes(data)
        return
    if fmt == "pcm":
        rate = int(getattr(result, "sample_rate", 0) or 24000)
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(data)
        return
    raise RuntimeError(f"不支持的 TTS 输出格式: {fmt}")


def _make_silence(path: Path, duration: float, sample_rate: int) -> None:
    rate = int(sample_rate or 24000)
    res = _run(["-f", "lavfi", "-i", f"anullsrc=r={rate}:cl=mono",
                "-t", f"{max(0.0, float(duration)):.3f}",
                "-c:a", "pcm_s16le", "-y", str(path)], timeout=60)
    if res.returncode != 0:
        raise RuntimeError(res.stderr or "生成静音失败")


def _concat_wavs(items: List[Path], out: Path, work: Path) -> None:
    list_file = work / f"audio_{int(time.time() * 1000)}.txt"
    list_file.write_text("\n".join(_concat_line(p) for p in items), encoding="utf-8")
    try:
        res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-c", "copy", "-y", str(out)], timeout=300)
        if res.returncode != 0:
            res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                        "-c:a", "pcm_s16le", "-y", str(out)], timeout=300)
        if res.returncode != 0:
            raise RuntimeError(res.stderr or "配音拼接失败")
    finally:
        try:
            list_file.unlink()
        except Exception:  # noqa: BLE001
            pass


def _size_for(preset: str, width: int, height: int) -> tuple[int, int]:
    cfg = PLATFORM_PRESETS.get(preset) or PLATFORM_PRESETS["douyin"]
    w = int(width) if int(width) > 0 else int(cfg["width"] or 1080)
    h = int(height) if int(height) > 0 else int(cfg["height"] or 1920)
    w = max(2, w - w % 2)
    h = max(2, h - h % 2)
    return w, h


def _image_segment(img: Path, out: Path, width: int, height: int,
                   fps: float, duration: float) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps:.3f}"
    )
    res = _run(["-loop", "1", "-i", str(img), "-t", f"{duration:.3f}",
                "-vf", vf, *_encode_args(), "-an", "-y", str(out)], timeout=300)
    if res.returncode != 0:
        raise RuntimeError(res.stderr or f"图片转视频失败: {img.name}")


def _subtitle_filter(sub: Path, font: str, font_size: int, margin_v: int,
                     video_height: int = 1080) -> str:
    font_name = "Microsoft YaHei" if Path(font).suffix else (font or "Microsoft YaHei")
    # SRT 经 libass 包装后默认 PlayResY=288，FontSize/MarginV 是脚本单位而非像素。
    # 按目标画面高度换算，保证 font_size 近似为输出像素。
    scale = 288.0 / max(1, int(video_height or 1080))
    ass_size = max(1, int(round(int(font_size) * scale)))
    ass_margin = max(1, int(round(int(margin_v) * scale)))
    ass_outline = max(1, int(round(2 * scale)))
    style = (
        f"FontName={font_name},FontSize={ass_size},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
        f"BorderStyle=1,Outline={ass_outline},Shadow=0,"
        f"MarginV={ass_margin},Alignment=2"
    )
    return f"subtitles=filename='{_filter_path(sub)}':force_style='{style}'"


def _write_temp_text(text: str) -> Path:
    fd, tmp = tempfile.mkstemp(prefix="vs_text_", suffix=".txt")
    os.close(fd)
    path = Path(tmp)
    path.write_text(text, encoding="utf-8")
    return path


def _drawtext_filter(
    text: str,
    *,
    position: str,
    font: str,
    font_size: int,
    font_color: str,
    box: bool,
    box_color: str,
    margin: float,
    start: float,
    end: float,
) -> tuple[str, Path]:
    tmp = _write_temp_text(text)
    font_file = _resolve_font(font)
    if font_file:
        font_opt = f"fontfile='{_filter_path(font_file)}'"
    else:
        font_opt = f"font='{font or 'Microsoft YaHei'}'"

    ratio = max(0.0, min(0.5, _to_float(margin, 0.08)))
    if position == "top":
        y_expr = f"h*{ratio:.4f}"
    elif position == "center":
        y_expr = "(h-text_h)/2"
    else:
        y_expr = f"h-text_h-h*{ratio:.4f}"

    parts = [
        font_opt,
        f"textfile='{_filter_path(tmp)}'",
        f"fontcolor={font_color}",
        f"fontsize={int(font_size)}",
        "x=(w-text_w)/2",
        f"y={y_expr}",
        "line_spacing=8",
    ]
    if box:
        parts += ["box=1", f"boxcolor={box_color}", "boxborderw=18"]
    if end > 0:
        parts.append(f"enable='between(t,{float(start)},{float(end)})'")
    elif start > 0:
        parts.append(f"enable='gte(t,{float(start)})'")
    return "drawtext=" + ":".join(parts), tmp


def _encode_args(crf: int = 20) -> List[str]:
    return [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(int(crf)),
        "-pix_fmt", "yuv420p",
    ]


def _fail(message: str) -> Dict[str, Any]:
    return {"success": False, "error": str(message)[:500]}


def _ok(out: Path, **extra: Any) -> Dict[str, Any]:
    if not out.exists():
        return _fail("输出文件未生成")
    data = {
        "success": True,
        "path": str(out),
        "size": out.stat().st_size,
    }
    data.update(extra)
    return data


class VideoStudioDomain(Actuator):
    id = "video_studio"
    label = "视频创作"
    prefix = "video_"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    call_timeout = 900.0
    tool_timeouts = dict(TOOL_TIMEOUTS)

    def allowed_names(self) -> List[str]:
        return list(TOOL_NAMES)

    def tools(self) -> List[Dict[str, Any]]:
        return list(TOOLS_SCHEMA)

    def system_prompt_fragment(self) -> str:
        return _PROMPT

    # ---------- 自检 ----------

    def video_doctor(self) -> Dict[str, Any]:
        try:
            exe = _ffmpeg()
            ver = _run(["-version"], timeout=30)
            version = (ver.stdout or "").splitlines()[0] if ver.stdout else ""
            filters = _run(["-filters"], timeout=30)
            table = filters.stdout or ""
            supported = {
                name: (name in table)
                for name in ("drawtext", "subtitles", "ass", "xfade",
                             "acrossfade", "amix", "afade")
            }
            font = _resolve_font("")
            try:
                _OUT_DIR.mkdir(parents=True, exist_ok=True)
                writable = os.access(_OUT_DIR, os.W_OK)
            except Exception:  # noqa: BLE001
                writable = False
            tts: Dict[str, Any] = {}
            try:
                import audio.engines  # noqa: F401
                from audio.tts_engine import get_engine, get_engine_names, resolve_engine
                tts = {}
                for name in get_engine_names():
                    eng = get_engine(name)
                    tts[name] = bool(eng and eng.available())
                selected = resolve_engine("auto")
                tts["selected"] = selected.name if selected else ""
            except Exception as e:  # noqa: BLE001
                tts = {"error": str(e)[:200]}
            return {
                "success": True,
                "ffmpeg": exe,
                "version": version,
                "ffprobe": _ffprobe() or "",
                "filters": supported,
                "font": str(font) if font else "",
                "output_dir": str(_OUT_DIR),
                "writable": writable,
                "presets": list(PLATFORM_PRESETS),
                "transitions": len(TRANSITIONS),
                "tts": tts,
            }
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    # ---------- 元信息 ----------

    def video_probe(self, path: str) -> Dict[str, Any]:
        try:
            src = _resolve_input(path)
            info = _probe_media(src)
            return {"success": True, "path": str(src), **info}
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    # ---------- 裁切 ----------

    def video_cut(self, path: str, start: float, duration: float,
                  out_name: str = "", accurate: bool = True) -> Dict[str, Any]:
        try:
            src = _resolve_input(path)
            start = max(0.0, _to_float(start))
            duration = _to_float(duration)
            if duration <= 0:
                return _fail("duration 必须大于 0")
            out = _out(out_name, src.suffix or ".mp4")

            base = ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
            if accurate:
                res = _run([*base, *_encode_args(), "-c:a", "aac", "-b:a", "192k",
                            "-movflags", "+faststart", "-y", str(out)])
            else:
                res = _run([*base, "-c", "copy", "-avoid_negative_ts", "make_zero",
                            "-y", str(out)])
                if res.returncode != 0:
                    res = _run([*base, *_encode_args(), "-c:a", "aac",
                                "-b:a", "192k", "-y", str(out)])
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 裁切失败")
            return _ok(out, duration=duration, accurate=bool(accurate))
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 裁切超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    # ---------- 拼接 ----------

    def video_concat(self, paths: Any, out_name: str = "",
                     transition: str = "", transition_duration: float = 0.5) -> Dict[str, Any]:
        try:
            items = self._normalize_paths(paths)
            if len(items) < 2:
                return _fail("至少需要两个片段")
            out = _out(out_name, ".mp4")
            if transition:
                return self._concat_with_transition(
                    items, out, transition, transition_duration)
            return self._concat_direct(items, out)
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 拼接超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    def _normalize_paths(self, paths: Any) -> List[Path]:
        if isinstance(paths, str):
            raw = [x.strip() for x in paths.split(",") if x.strip()]
        else:
            raw = [str(x) for x in (paths or [])]
        items = [_resolve_input(p) for p in raw]
        missing = [p for p in items if p.suffix.lower() not in _VIDEO_EXT]
        if missing:
            raise ValueError(f"不支持的视频格式: {missing[0].suffix}")
        return items

    def _concat_direct(self, items: List[Path], out: Path) -> Dict[str, Any]:
        list_file = _OUT_DIR / f"concat_{int(time.time() * 1000)}.txt"
        list_file.write_text("\n".join(_concat_line(p) for p in items), encoding="utf-8")
        try:
            res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                        "-c", "copy", "-y", str(out)])
            if res.returncode != 0:
                res = _run(["-f", "concat", "-safe", "0", "-i", str(list_file),
                            *_encode_args(), "-c:a", "aac", "-b:a", "192k",
                            "-movflags", "+faststart", "-y", str(out)])
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 拼接失败")
            return _ok(out, clips=len(items), transition="")
        finally:
            try:
                list_file.unlink()
            except Exception:  # noqa: BLE001
                pass

    def _concat_with_transition(self, items: List[Path], out: Path,
                                transition: str, duration: float) -> Dict[str, Any]:
        if transition not in TRANSITIONS:
            return _fail(f"不支持的转场: {transition}；可选: {', '.join(TRANSITIONS)}")
        trans = _to_float(duration, 0.5)
        if trans <= 0:
            return _fail("transition_duration 必须大于 0")

        infos = [_probe_media(p) for p in items]
        width, height = infos[0].get("width", 0), infos[0].get("height", 0)
        fps = infos[0].get("fps", 0) or 30.0
        if not width or not height:
            return _fail("无法读取首个片段的分辨率")
        for i, info in enumerate(infos[1:], start=2):
            if info.get("width") != width or info.get("height") != height:
                return _fail(f"第 {i} 个片段分辨率不一致（需要 {width}x{height}），"
                             f"请先用 video_export 统一规格")
        durations = [float(info.get("duration") or 0) for info in infos]
        if any(d <= trans for d in durations):
            return _fail(f"转场时长 {trans}s 必须小于每个片段时长: {durations}")

        parts: List[str] = []
        for i in range(len(items)):
            parts.append(
                f"[{i}:v]fps={fps:.3f},format=yuv420p[v{i}]")
        has_audio = all(info.get("has_audio") for info in infos)
        if has_audio:
            for i in range(len(items)):
                parts.append(
                    f"[{i}:a]aresample=48000,"
                    f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")

        last_v = "v0"
        offset = 0.0
        for i in range(1, len(items)):
            offset += durations[i - 1] - trans
            label = f"vx{i}"
            parts.append(
                f"[{last_v}][v{i}]xfade=transition={transition}:"
                f"duration={trans:.3f}:offset={offset:.3f}[{label}]")
            last_v = label

        last_a = ""
        if has_audio:
            last_a = "a0"
            for i in range(1, len(items)):
                label = f"ax{i}"
                parts.append(
                    f"[{last_a}][a{i}]acrossfade=d={trans:.3f}:c1=tri:c2=tri[{label}]")
                last_a = label

        args: List[str] = []
        for p in items:
            args += ["-i", str(p)]
        args += ["-filter_complex", ";".join(parts), "-map", f"[{last_v}]"]
        if last_a:
            args += ["-map", f"[{last_a}]"]
        args += [*_encode_args()]
        if last_a:
            args += ["-c:a", "aac", "-b:a", "192k"]
        args += ["-shortest", "-movflags", "+faststart", "-y", str(out)]

        res = _run(args, timeout=600)
        if res.returncode != 0:
            return _fail(res.stderr or "ffmpeg 转场拼接失败")
        return _ok(out, clips=len(items), transition=transition,
                   transition_duration=trans)

    # ---------- 文字 / 字幕 ----------

    def video_text(self, path: str, text: str, out_name: str = "",
                   position: str = "bottom", font: str = "",
                   font_size: int = 64, font_color: str = "white",
                   box: bool = True, box_color: str = "black@0.45",
                   margin: float = 0.08, start: float = 0, end: float = 0) -> Dict[str, Any]:
        tmp: Optional[Path] = None
        try:
            src = _resolve_input(path)
            if not text:
                return _fail("text 不能为空")
            out = _out(out_name, src.suffix or ".mp4")
            vf, tmp = _drawtext_filter(
                text, position=position, font=font, font_size=font_size,
                font_color=font_color, box=bool(box), box_color=box_color,
                margin=margin, start=_to_float(start), end=_to_float(end))
            res = _run(["-i", str(src), "-vf", vf, *_encode_args(),
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        "-y", str(out)])
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 叠加文字失败")
            return _ok(out, position=position)
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 叠加文字超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def video_subtitle(self, path: str, subtitle_path: str, out_name: str = "",
                       font: str = "Microsoft YaHei", font_size: int = 20,
                       margin_v: int = 30) -> Dict[str, Any]:
        try:
            src = _resolve_input(path)
            sub = _resolve_input(subtitle_path)
            if sub.suffix.lower() not in _SUB_EXT:
                return _fail(f"不支持的字幕格式: {sub.suffix}（支持 SRT/ASS/SSA/VTT）")
            out = _out(out_name, ".mp4")
            src_info = _probe_media(src)
            vf = _subtitle_filter(sub, font, font_size, margin_v,
                                  int(src_info.get("height") or 1080))
            res = _run(["-i", str(src), "-vf", vf, *_encode_args(),
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        "-y", str(out)], timeout=600)
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 烧录字幕失败")
            return _ok(out, subtitle=str(sub))
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 烧录字幕超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    # ---------- 配乐 ----------

    def video_audio(self, path: str, bgm: str, out_name: str = "",
                    volume: float = 0.3, mix: bool = True, loop: bool = True,
                    fade_in: float = 0, fade_out: float = 0) -> Dict[str, Any]:
        try:
            src = _resolve_input(path)
            bgm_path = _resolve_input(bgm)
            src_info = _probe_media(src)
            bgm_info = _probe_media(bgm_path)
            if not bgm_info.get("has_audio"):
                return _fail(f"背景音乐没有音轨: {bgm_path.name}")
            if not src_info.get("width"):
                return _fail(f"源文件不是视频: {src.name}")

            vol = max(0.0, min(1.0, _to_float(volume, 0.3)))
            do_mix = bool(mix) and bool(src_info.get("has_audio"))
            mode = "mix" if do_mix else "replace"
            warning = ""
            if mix and not src_info.get("has_audio"):
                warning = "源视频没有音轨，已自动改为替换模式"
            if not loop:
                bgm_dur = _to_float(bgm_info.get("duration"))
                src_dur = _to_float(src_info.get("duration"))
                if bgm_dur and src_dur and bgm_dur < src_dur:
                    warning = (warning + "；" if warning else "") + \
                        "BGM 短于视频且未循环，成片会裁到 BGM 长度"

            bgm_chain = f"[1:a]volume={vol:.3f}"
            fi = max(0.0, _to_float(fade_in))
            fo = max(0.0, _to_float(fade_out))
            if fi > 0:
                bgm_chain += f",afade=t=in:st=0:d={fi:.3f}"
            if fo > 0:
                st = max(0.0, _to_float(src_info.get("duration")) - fo)
                bgm_chain += f",afade=t=out:st={st:.3f}:d={fo:.3f}"
            bgm_chain += "[bgm]"

            if do_mix:
                flt = f"[0:a]anull[orig];{bgm_chain};" \
                      "[orig][bgm]amix=inputs=2:duration=first:" \
                      "dropout_transition=2[aout]"
            else:
                flt = bgm_chain.replace("[bgm]", "[aout]")

            out = _out(out_name, ".mp4")
            inputs: List[str] = ["-i", str(src)]
            if loop:
                inputs += ["-stream_loop", "-1"]
            inputs += ["-i", str(bgm_path)]
            common = [*inputs, "-filter_complex", flt,
                      "-map", "0:v:0", "-map", "[aout]"]
            tail = ["-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", "-y", str(out)]

            res = _run([*common, "-c:v", "copy", *tail])
            if res.returncode != 0:
                res = _run([*common, *_encode_args(), *tail])
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 配乐失败")
            return _ok(out, mode=mode, volume=vol, warning=warning)
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 配乐超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    # ---------- 封面 / 导出 ----------

    def video_cover(self, path: str, at: float = 0, out_name: str = "",
                    preset: str = "", title: str = "", font: str = "",
                    font_size: int = 72, position: str = "center") -> Dict[str, Any]:
        tmp: Optional[Path] = None
        try:
            src = _resolve_input(path)
            suffix = Path(out_name).suffix.lower() if out_name else ".jpg"
            if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                return _fail(f"不支持的封面格式: {suffix}")
            out = _out(out_name, ".jpg")

            width = height = 0
            if preset:
                cfg = PLATFORM_PRESETS.get(preset)
                if not cfg:
                    return _fail(f"未知预设: {preset}")
                width, height = cfg["width"], cfg["height"]

            vf_parts: List[str] = []
            if width > 0 and height > 0:
                vf_parts += [
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease",
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
            vf_parts.append("setsar=1")
            if title:
                text_filter, tmp = _drawtext_filter(
                    title, position=position, font=font, font_size=font_size,
                    font_color="white", box=True, box_color="black@0.45",
                    margin=0.08, start=0, end=0)
                vf_parts.append(text_filter)

            args = ["-ss", f"{max(0.0, _to_float(at)):.3f}", "-i", str(src),
                    "-frames:v", "1", "-vf", ",".join(vf_parts)]
            if suffix in {".jpg", ".jpeg"}:
                args += ["-q:v", "2"]
            args += ["-y", str(out)]
            res = _run(args, timeout=90)
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 生成封面失败")
            return _ok(out, preset=preset or "original")
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 生成封面超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except Exception:  # noqa: BLE001
                    pass

    def video_export(self, path: str, out_name: str = "", preset: str = "bilibili",
                     width: int = 0, height: int = 0, fps: float = 0,
                     crf: int = 20) -> Dict[str, Any]:
        try:
            src = _resolve_input(path)
            cfg = PLATFORM_PRESETS.get(preset)
            if not cfg:
                return _fail(f"未知预设: {preset}；可选: {', '.join(PLATFORM_PRESETS)}")
            w = int(width) if int(width) > 0 else int(cfg["width"])
            h = int(height) if int(height) > 0 else int(cfg["height"])

            vf_parts: List[str] = []
            if w > 0 and h > 0:
                vf_parts += [
                    f"scale={w}:{h}:force_original_aspect_ratio=decrease",
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
                ]
            vf_parts.append("setsar=1")
            fps_val = _to_float(fps)
            if fps_val > 0:
                vf_parts.append(f"fps={fps_val:.3f}")

            out = _out(out_name, ".mp4")
            args = ["-i", str(src), "-vf", ",".join(vf_parts),
                    "-map", "0:v:0", "-map", "0:a?",
                    *_encode_args(int(crf)), "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-y", str(out)]
            res = _run(args, timeout=900)
            if res.returncode != 0:
                return _fail(res.stderr or "ffmpeg 导出失败")
            return _ok(out, preset=preset, width=w or None, height=h or None)
        except subprocess.TimeoutExpired:
            return _fail("ffmpeg 导出超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))


    # ---------- 口播：配音 / 底片 / 一键成片 ----------

    def _normalize_image_paths(self, images: Any) -> List[Path]:
        if isinstance(images, str):
            raw = [x.strip() for x in images.split(",") if x.strip()]
        else:
            raw = [str(x) for x in (images or [])]
        items: List[Path] = []
        for p in raw:
            path = _resolve_input(p)
            if path.suffix.lower() not in _IMAGE_EXT:
                raise ValueError(f"不支持的图片格式: {path.suffix}")
            items.append(path)
        return items

    def _build_slideshow(self, images: List[Path], color: str, duration: float,
                         out: Path, width: int, height: int, fps: float,
                         transition: str, transition_duration: float) -> Dict[str, Any]:
        dur = max(0.1, _to_float(duration, 3.0))
        fps_val = max(1.0, _to_float(fps, 30.0))

        if not images:
            if not re.fullmatch(r"[A-Za-z0-9#@.]+", color or ""):
                return _fail(f"非法纯色值: {color}")
            res = _run([
                "-f", "lavfi",
                "-i", f"color=c={color}:s={width}x{height}:r={fps_val:.3f}:d={dur:.3f}",
                "-vf", "setsar=1", *_encode_args(), "-an", "-y", str(out),
            ], timeout=300)
            if res.returncode != 0:
                return _fail(res.stderr or "纯色底片生成失败")
            return _ok(out, duration=dur, width=width, height=height, images=0)

        if len(images) == 1 and not transition:
            _image_segment(images[0], out, width, height, fps_val, dur)
            return _ok(out, duration=dur, width=width, height=height, images=1)

        work = _work_dir()
        try:
            segments: List[Path] = []
            for i, img in enumerate(images):
                seg = work / f"img_{i:03d}.mp4"
                _image_segment(img, seg, width, height, fps_val, dur)
                segments.append(seg)
            if transition:
                result = self._concat_with_transition(
                    segments, out, transition, transition_duration)
            else:
                result = self._concat_direct(segments, out)
            if not result.get("success"):
                return result
            overlap = _to_float(transition_duration, 0.5) * (len(images) - 1) \
                if transition else 0.0
            total = max(0.0, dur * len(images) - overlap)
            return _ok(out, duration=total, width=width, height=height,
                       images=len(images))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _mux_voice(self, base: Path, voice: Path, bgm: Optional[Path],
                   volume: float, total: float, out: Path) -> Dict[str, Any]:
        if bgm is not None:
            vol = max(0.0, min(1.0, _to_float(volume, 0.15)))
            fade_out_start = max(0.0, float(total) - 1.0)
            flt = (
                "[1:a]volume=1.0[voice];"
                f"[2:a]volume={vol:.3f},afade=t=in:st=0:d=0.5,"
                f"afade=t=out:st={fade_out_start:.3f}:d=1.0[bg];"
                "[voice][bg]amix=inputs=2:duration=first:"
                "dropout_transition=2[aout]"
            )
            args = [
                "-i", str(base), "-i", str(voice),
                "-stream_loop", "-1", "-i", str(bgm),
                "-filter_complex", flt,
                "-map", "0:v:0", "-map", "[aout]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", "-y", str(out),
            ]
        else:
            args = [
                "-i", str(base), "-i", str(voice),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart", "-y", str(out),
            ]
        res = _run(args, timeout=600)
        if res.returncode != 0:
            return _fail(res.stderr or "配音合成到画面失败")
        return _ok(out)

    def _burn_subtitles_to(self, src: Path, sub: Path, out: Path,
                           font_size: int, video_height: int = 0) -> Dict[str, Any]:
        if video_height <= 0:
            try:
                video_height = int(_probe_media(src).get("height") or 1080)
            except Exception:  # noqa: BLE001
                video_height = 1080
        vf = _subtitle_filter(sub, "Microsoft YaHei", int(font_size), 60,
                              video_height)
        res = _run(["-i", str(src), "-vf", vf, *_encode_args(),
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    "-y", str(out)], timeout=600)
        if res.returncode != 0:
            return _fail(res.stderr or "字幕烧录失败")
        return _ok(out)

    def _overlay_title_to(self, src: Path, out: Path, title: str,
                          font_size: int) -> Dict[str, Any]:
        tmp: Optional[Path] = None
        try:
            vf, tmp = _drawtext_filter(
                title, position="top", font="", font_size=int(font_size),
                font_color="white", box=True, box_color="black@0.45",
                margin=0.05, start=0, end=3)
            res = _run(["-i", str(src), "-vf", vf, *_encode_args(),
                        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                        "-y", str(out)], timeout=300)
            if res.returncode != 0:
                return _fail(res.stderr or "片头标题叠加失败")
            return _ok(out)
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except Exception:  # noqa: BLE001
                    pass

    async def video_tts(self, text: str, out_name: str = "", tone: str = "",
                        engine: str = "auto") -> Dict[str, Any]:
        try:
            text = (text or "").strip()
            if not text:
                return _fail("text 不能为空")
            eng = _load_tts_engine(engine)
            out = _out(out_name, ".wav", subdir="voice")
            res = await eng.synthesize(text, tone=tone or "")
            if not res.success or not res.audio_data:
                return _fail(getattr(res, "error", "") or "TTS 合成失败")
            _write_audio_result(res, out)
            info = _probe_media(out)
            return _ok(out, duration=float(info.get("duration") or 0),
                       engine=getattr(eng, "name", ""),
                       sample_rate=int(info.get("sample_rate") or 0))
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    def video_slideshow(self, images: Any = None, color: str = "#101820",
                        duration: float = 3.0, out_name: str = "",
                        preset: str = "douyin", width: int = 0, height: int = 0,
                        fps: float = 30, transition: str = "",
                        transition_duration: float = 0.5) -> Dict[str, Any]:
        try:
            items = self._normalize_image_paths(images)
            if preset not in PLATFORM_PRESETS:
                return _fail(f"未知预设: {preset}")
            w, h = _size_for(preset, width, height)
            out = _out(out_name, ".mp4")
            return self._build_slideshow(
                items, color, duration, out, w, h, fps, transition,
                transition_duration)
        except subprocess.TimeoutExpired:
            return _fail("底片生成超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))

    async def video_narrated(self, text: str, images: Any = None,
                             color: str = "#101820", out_name: str = "",
                             preset: str = "douyin", engine: str = "auto",
                             tone: str = "", bgm: str = "", bgm_volume: float = 0.15,
                             subtitle: bool = True, title: str = "",
                             font_size: int = 56, gap: float = 0.15,
                             segment_max_chars: int = 40, crf: int = 20) -> Dict[str, Any]:
        work: Optional[Path] = None
        try:
            text = (text or "").strip()
            if not text:
                return _fail("text 不能为空")
            if preset not in PLATFORM_PRESETS:
                return _fail(f"未知预设: {preset}")
            segments_text = _split_script(text, segment_max_chars)
            if not segments_text:
                return _fail("文案切句为空")

            eng = _load_tts_engine(engine)
            w, h = _size_for(preset, 0, 0)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
            stem = re.sub(r'[<>:"/\\|?*]+', "_",
                          (Path(out_name).stem if out_name else f"narrated_{stamp}"))
            stem = stem.strip(" .") or f"narrated_{stamp}"

            work = _work_dir()
            seg_wavs: List[Path] = []
            durations: List[float] = []
            rate = 24000
            for i, seg_text in enumerate(segments_text):
                res = await eng.synthesize(seg_text, tone=tone or "")
                if not res.success or not res.audio_data:
                    return _fail(f"第 {i + 1} 句配音失败: "
                                 f"{getattr(res, 'error', '') or '无音频'}")
                wav = work / f"seg_{i:03d}.wav"
                _write_audio_result(res, wav)
                info = _probe_media(wav)
                dur = float(info.get("duration") or 0)
                if dur <= 0:
                    return _fail(f"第 {i + 1} 句配音时长无效")
                rate = int(info.get("sample_rate") or
                           getattr(res, "sample_rate", 0) or 24000)
                seg_wavs.append(wav)
                durations.append(dur)

            gap_s = max(0.0, _to_float(gap, 0.15))
            total = sum(durations) + gap_s * max(0, len(durations) - 1)
            silence: Optional[Path] = None
            if gap_s > 0 and len(seg_wavs) > 1:
                silence = work / "silence.wav"
                _make_silence(silence, gap_s, rate)

            voice_path = _OUT_DIR / f"{stem}_voice.wav"
            voice_items: List[Path] = []
            for i, wav in enumerate(seg_wavs):
                voice_items.append(wav)
                if silence is not None and i < len(seg_wavs) - 1:
                    voice_items.append(silence)
            _concat_wavs(voice_items, voice_path, work)

            images_norm = self._normalize_image_paths(images)
            base_duration = total + 0.2
            per_image = (base_duration / len(images_norm)) if images_norm \
                else base_duration
            base = work / "base.mp4"
            slide = await asyncio.to_thread(
                self._build_slideshow, images_norm, color, per_image, base,
                w, h, 30.0, "", 0.0)
            if not slide.get("success"):
                return slide

            bgm_path: Optional[Path] = None
            if bgm:
                bgm_path = _resolve_input(bgm)
                bgm_info = _probe_media(bgm_path)
                if not bgm_info.get("has_audio"):
                    return _fail("BGM 没有音轨")

            voiced = work / "voiced.mp4"
            mux = await asyncio.to_thread(
                self._mux_voice, base, voice_path, bgm_path,
                max(0.0, min(1.0, _to_float(bgm_volume, 0.15))),
                total, voiced)
            if not mux.get("success"):
                return mux
            current = voiced

            srt_path = _OUT_DIR / f"{stem}.srt"
            srt_entries: List[str] = []
            cursor = 0.0
            for i, (seg_text, dur) in enumerate(zip(segments_text, durations), start=1):
                start = cursor
                end = start + dur
                srt_entries.append(
                    f"{i}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n"
                    f"{seg_text}\n")
                cursor = end + gap_s
            srt_path.write_text("\n".join(srt_entries), encoding="utf-8")

            if subtitle:
                subbed = work / "subbed.mp4"
                sub_res = await asyncio.to_thread(
                    self._burn_subtitles_to, current, srt_path, subbed,
                    int(font_size), int(h))
                if not sub_res.get("success"):
                    return sub_res
                current = subbed

            if title:
                titled = work / "titled.mp4"
                title_res = await asyncio.to_thread(
                    self._overlay_title_to, current, titled, title,
                    int(font_size) + 16)
                if not title_res.get("success"):
                    return title_res
                current = titled

            exported = await asyncio.to_thread(
                self.video_export, str(current), f"{stem}.mp4",
                preset, 0, 0, 0, int(crf))
            if not exported.get("success"):
                return exported
            return {
                "success": True,
                "path": exported["path"],
                "voice": str(voice_path),
                "subtitle": str(srt_path) if subtitle else "",
                "duration": round(total, 3),
                "segments": len(segments_text),
                "engine": getattr(eng, "name", ""),
                "preset": preset,
                "size": exported.get("size", 0),
            }
        except subprocess.TimeoutExpired:
            return _fail("口播成片超时")
        except Exception as e:  # noqa: BLE001
            return _fail(str(e))
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)


def build_video_studio_domain() -> VideoStudioDomain:
    return VideoStudioDomain()


__all__ = ["VideoStudioDomain", "build_video_studio_domain"]
