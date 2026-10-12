#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三模态对齐工具：把 ASR 分段时间文本 ↔ VTS 参数帧 ↔ 录音，统一到单调时间轴。

用法：
    # 先采集：face_dataset/sync_recorder.py --duration 60 --out session1
    # 再转写：face_dataset/asr.py session1.wav --out session1_asr.json
    # 后对齐：
    python face_dataset/align.py \
        --params session1_params.csv --meta session1_meta.json \
        --asr session1_asr.json --out session1_aligned.csv

对齐核心（见 sync_recorder 的 meta.align_note）：
    mono = t0_audio_mono + t_audio
- t_audio：ASR 分段的 start/end（相对 wav 起点秒）
- t_mono ：VTS 参数 CSV 的 t_mono 列（同一进程单调时钟）

输出 CSV 每行一个转写分段：
    text, t_audio_s, t_audio_e, t_mono_s, t_mono_e,
    该时段内 VTS 参数的均值（仅列出现过的参数）
"""

import argparse
import csv
import json
import os
import sys
from collections import defaultdict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def load_meta(meta_path: str) -> dict:
    with open(meta_path, encoding="utf-8") as f:
        return json.load(f)


def load_params(params_csv: str) -> list:
    """读 params.csv → [(t_mono, {param: value}), ...]，t 升序。"""
    rows = []
    with open(params_csv, encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r)
        t_idx = header.index("t_mono")
        names = header[t_idx + 2:]  # 跳过 t_mono, face_found
        for line in r:
            if len(line) <= t_idx:
                continue
            t = float(line[t_idx])
            d = {"face_found": int(line[t_idx + 1])}
            for i, n in enumerate(names):
                if t_idx + 2 + i < len(line) and line[t_idx + 2 + i] != "":
                    d[n] = float(line[t_idx + 2 + i])
            rows.append((t, d))
    return rows


def load_asr(asr_path: str) -> list:
    """ASR 结果 → [{start, end, text}, ...]（start/end 为相对 wav 起点秒）。"""
    with open(asr_path, encoding="utf-8") as f:
        data = json.load(f)
    segs = data.get("segments") if isinstance(data, dict) else data
    out = []
    for s in segs or []:
        out.append({"start": float(s["start"]), "end": float(s["end"]),
                    "text": s.get("text", "").strip()})
    return out


def mean_params(rows: list, names: set) -> dict:
    """rows 内参数均值（空时段返回 {}）。"""
    sums = defaultdict(float)
    cnt = defaultdict(int)
    for _, d in rows:
        for n in names:
            if n in d:
                sums[n] += d[n]
                cnt[n] += 1
    return {n: round(sums[n] / cnt[n], 4) for n in names if cnt[n]}


def align(params_rows: list, asr_segs: list, t0_audio_mono: float,
          param_names: set) -> list:
    out = []
    for s in asr_segs:
        t_audio_s, t_audio_e = s["start"], s["end"]
        t_mono_s = t0_audio_mono + t_audio_s
        t_mono_e = t0_audio_mono + t_audio_e
        # 该时段内的参数帧（t_mono_s <= t <= t_mono_e）
        window = [r for r in params_rows if t_mono_s <= r[0] <= t_mono_e]
        mp = mean_params(window, param_names)
        out.append({
            "text": s["text"],
            "t_audio_s": round(t_audio_s, 3),
            "t_audio_e": round(t_audio_e, 3),
            "t_mono_s": round(t_mono_s, 4),
            "t_mono_e": round(t_mono_e, 4),
            "param_frames": len(window),
            "params_mean": mp,
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="三模态对齐：ASR 文本 ↔ VTS 参数帧")
    ap.add_argument("--params", required=True, help="sync_recorder 输出的 *_params.csv")
    ap.add_argument("--meta", required=True, help="sync_recorder 输出的 *_meta.json")
    ap.add_argument("--asr", required=True, help="asr.py 输出的转写 JSON")
    ap.add_argument("--out", default=None, help="对齐 CSV 输出（默认 <asr 同名>_aligned.csv）")
    args = ap.parse_args(argv)

    meta = load_meta(args.meta)
    t0 = meta["t0_audio_mono"]
    rows = load_params(args.params)
    segs = load_asr(args.asr)
    param_names = set()
    for _, d in rows:
        param_names.update(d.keys())
    param_names.discard("face_found")

    out = align(rows, segs, t0, param_names)
    out_path = args.out or (os.path.splitext(args.asr)[0] + "_aligned.csv")

    # 写 CSV：text + 时间段 + 参数均值列
    pnames = sorted(param_names)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["text", "t_audio_s", "t_audio_e", "t_mono_s", "t_mono_e",
                    "param_frames"] + pnames)
        for o in out:
            row = [o["text"], o["t_audio_s"], o["t_audio_e"],
                   o["t_mono_s"], o["t_mono_e"], o["param_frames"]]
            row += [o["params_mean"].get(n, "") for n in pnames]
            w.writerow(row)

    print(f"对齐完成：{len(out)} 个转写分段 → {out_path}")
    print("示例（前 5 段，取 MouthOpen/眉毛/角度）：")
    for o in out[:5]:
        m = o["params_mean"]
        keys = [k for k in ("MouthOpen", "MouthSmile", "BrowLeftY", "FaceAngleX") if k in m]
        kv = ", ".join(f"{k}={m[k]}" for k in keys)
        print(f"  [{o['t_audio_s']:6.2f}~{o['t_audio_e']:6.2f}s] {o['text'][:20]:<22} {kv}")


if __name__ == "__main__":
    main()
