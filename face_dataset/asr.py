#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""录音转文字（ASR）接口。

当前为"后端可插拔"骨架：本机无法验证录音时，转写也不强依赖实测，
只需接入任一后端即可。输出统一 JSON 供 align.py 使用：

    {"segments": [{"start": 0.0, "end": 1.2, "text": "大家好"}, ...]}

start/end 为相对 wav 起点秒 —— align.py 用 meta.t0_audio_mono 换算到单调时间轴。

接入后端（任选其一，实现 _transcribe_<backend>(wav, sr) -> segments）：
- whisper 本地：pip install faster-whisper，加载 small/base 模型
- 豆包/火山 ASR：使用方舟语音识别接口（需 API key）
- 其它：按 segments 格式返回即可

用法：
    python face_dataset/asr.py session1.wav --out session1_asr.json
"""

import argparse
import json
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ---------------------------------------------------------------------------
# 后端实现（按需启用）
# ---------------------------------------------------------------------------

def _transcribe_faster_whisper(wav_path: str, model_size: str = "small") -> list:
    """本地 faster-whisper：精度好、无需联网。需 pip install faster-whisper。"""
    from faster_whisper import WhisperModel  # 延迟导入，避免无依赖时 import 失败
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, _info = model.transcribe(wav_path, language="zh", vad_filter=True)
    return [{"start": float(s.start), "end": float(s.end),
             "text": s.text.strip()} for s in segs if s.text.strip()]


def _transcribe_doubao(wav_path: str, api_key: str | None = None) -> list:
    """火山引擎/豆包 ASR（需在方舟开通语音识别，配置 Doubao_API_KEY）。"""
    raise NotImplementedError(
        "豆包 ASR 后端待接入：需开通方舟语音识别并填 Doubao_API_KEY 后在此实现")


BACKENDS = {"whisper": _transcribe_faster_whisper, "doubao": _transcribe_doubao}


# ---------------------------------------------------------------------------

def transcribe(wav_path: str, backend: str = "whisper", **kw) -> dict:
    if backend not in BACKENDS:
        raise ValueError(f"未知后端 {backend}，可用: {list(BACKENDS)}")
    segs = BACKENDS[backend](wav_path, **kw)
    return {"backend": backend, "segments": segs}


def main(argv=None):
    ap = argparse.ArgumentParser(description="录音转文字（ASR），输出 align.py 用的 JSON")
    ap.add_argument("wav", help="sync_recorder 输出的 .wav")
    ap.add_argument("--out", default=None, help="输出 JSON（默认 <wav 同名>_asr.json）")
    ap.add_argument("--backend", default="whisper", choices=list(BACKENDS),
                    help="转写后端（默认 whisper，需 pip install faster-whisper）")
    ap.add_argument("--model", default="small", help="whisper 模型大小（默认 small）")
    args = ap.parse_args(argv)

    out_path = args.out or (os.path.splitext(args.wav)[0] + "_asr.json")
    result = transcribe(args.wav, backend=args.backend, model_size=args.model)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"转写完成：{len(result['segments'])} 段 → {out_path}")
    for s in result["segments"][:5]:
        print(f"  [{s['start']:6.2f}~{s['end']:6.2f}s] {s['text'][:30]}")


if __name__ == "__main__":
    main()
