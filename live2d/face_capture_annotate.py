#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
面部捕捉录制 → 句子时间段标注器

把朗读稿的每个句子映射到录制 CSV 的时间段，输出标注表，用于制作
「文本 ↔ 表情参数」数据集标签。

判定原理（录制时 VTS 音频输入未开时同样适用）：
- VTS 的 VoiceSilence/VoiceVolume 需麦克风音频输入，未开则恒无效；
- 这里用摄像头捕捉的 MouthOpen（嘴开合）判定说话帧，天然可用；
- 说话帧 → 说话段（合并短毛刺）→ 句间停顿 >= 阈值视为句边界 → 聚合成"句"。

用法:
    python live2d/face_capture_annotate.py \
        --csv logs/face_capture_session1.csv \
        --script logs/face_capture_script1.txt \
        --out logs/face_capture_session1_sentences.csv

参数:
    --csv     录制 CSV（face_capture_recorder.py 的输出）
    --script  朗读稿 txt（【第X段】为段标题，其余非空行为句子，行尾（停顿）会被去掉）
    --out     标注表输出路径（默认 <csv 同名>_sentences.csv）
    --pause   句间停顿阈值秒（默认 1.0）
    --mouth   说话判定 MouthOpen 阈值（默认 0.04）
"""

import argparse
import csv
import os
import re
import sys


def parse_script(path: str):
    """解析朗读稿：段号 + 句子列表。段标题行以【 开头。"""
    sentences = []  # (段号, 文本)
    with open(path, encoding="utf-8") as f:
        seg_no, started = 0, False
        for line in f:
            line = line.strip()
            if not line or line.startswith("==="):
                continue
            if line.startswith("【"):
                started = True
                seg_no += 1
                continue
            if not started:
                continue  # 跳过第一段之前的使用说明
            text = re.sub(r"（(停顿|长停顿)）\s*$", "", line).strip()
            if text:
                sentences.append((seg_no, text))
    return sentences


def load_frames(csv_path: str):
    """读录制 CSV 的 elapsed_ms 与 MouthOpen（列索引固定：1 和 10）。"""
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        r = csv.reader(f)
        header = next(r)
        # 按表头名定位列，不依赖固定索引
        try:
            i_t = header.index("elapsed_ms")
            i_mouth = header.index("MouthOpen")
        except ValueError as e:
            print(f"CSV 缺少必要列: {e}")
            sys.exit(1)
        for line in r:
            if len(line) <= max(i_t, i_mouth):
                continue
            rows.append({"t": float(line[i_t]), "mouth": float(line[i_mouth])})
    return rows


def merge_short(segs, rows, min_seg):
    """合并时长 < min_seg 的毛刺段到相邻段（优先并入同标签段）。"""
    changed = True
    while changed:
        changed = False
        for i in range(len(segs)):
            s = segs[i]
            dur = (rows[s[1] - 1]["t"] - rows[s[0]]["t"]) / 1000.0
            if dur >= min_seg or len(segs) == 1:
                continue
            cands = []
            if i > 0:
                cands.append((i - 1, abs(rows[segs[i - 1][1] - 1]["t"] - rows[segs[i - 1][0]]["t"])))
            if i < len(segs) - 1:
                cands.append((i + 1, abs(rows[segs[i + 1][1] - 1]["t"] - rows[segs[i + 1][0]]["t"])))
            if not cands:
                continue
            same = [c for c in cands if segs[c[0]][2] == s[2]]
            pick = same[0] if same else max(cands, key=lambda c: c[1])
            j = pick[0]
            if j == i - 1:
                segs[i - 1][1] = s[1]
            else:
                segs[i + 1][0] = s[0]
            del segs[i]
            changed = True
            break
    return segs


def annotate(csv_path: str, script_path: str, out_path: str,
             pause_th: float, mouth_th: float, min_seg: float = 0.35):
    sentences = parse_script(script_path)
    rows = load_frames(csv_path)
    n = len(rows)
    ts0 = rows[0]["t"]
    print(f"朗读稿句子数: {len(sentences)} 段")

    # 滑动窗口平滑 MouthOpen（半窗 3 帧 ≈ 0.2s）
    W = 3
    sm = []
    for i in range(n):
        lo, hi = max(0, i - W), min(n, i + W + 1)
        sm.append(sum(rows[j]["mouth"] for j in range(lo, hi)) / (hi - lo))
    raw = [1 if v >= mouth_th else 0 for v in sm]

    # 原始连续段
    segs = []
    if raw:
        start, cur = 0, raw[0]
        for i in range(1, n):
            if raw[i] != cur:
                segs.append([start, i, cur])
                start, cur = i, raw[i]
        segs.append([start, n, cur])
    segs = merge_short(segs, rows, min_seg)

    # 句边界聚合
    sentence_segs = []
    cur_start = cur_end = None
    for s in segs:
        if s[2] == 0:
            dur = (rows[s[1] - 1]["t"] - rows[s[0]]["t"]) / 1000.0
            if cur_start is not None and dur >= pause_th:
                sentence_segs.append((cur_start, cur_end))
                cur_start = cur_end = None
            continue
        if cur_start is None:
            cur_start = s[0]
        cur_end = s[1]
    if cur_start is not None:
        sentence_segs.append((cur_start, cur_end))

    print(f"聚类句子段: {len(sentence_segs)} 个（句间停顿阈值 {pause_th}s）")

    # 输出标注表
    out = []
    for i, (a, b) in enumerate(sentence_segs, 1):
        st = (rows[a]["t"] - ts0) / 1000.0
        en = (rows[b - 1]["t"] - ts0) / 1000.0
        seg_no = sentences[i - 1][0] if i - 1 < len(sentences) else ""
        text = sentences[i - 1][1] if i - 1 < len(sentences) else ""
        out.append([i, seg_no, text, round(st, 2), round(en, 2), round(en - st, 2)])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["sentence_no", "segment", "text", "start_s", "end_s", "dur_s"])
        w.writerows(out)

    for r_ in out:
        print(f"#{r_[0]:>3} [段{r_[1]}] {r_[3]:7.2f}~{r_[4]:7.2f}s ({r_[5]:4.2f}s) {r_[2][:24]}")
    print(f"\n标注表已存: {out_path}")
    if len(out) < len(sentences):
        print(f"提示：{len(sentences) - len(out)} 句未匹配到说话段"
              f"（可能没念到/念得太轻没触发 MouthOpen 阈值）")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="面部捕捉录制 → 句子时间段标注器")
    ap.add_argument("--csv", required=True, help="录制 CSV 路径")
    ap.add_argument("--script", required=True, help="朗读稿 txt 路径")
    ap.add_argument("--out", default=None, help="标注表输出路径（默认 <csv 同名>_sentences.csv）")
    ap.add_argument("--pause", type=float, default=1.0, help="句间停顿阈值秒（默认 1.0）")
    ap.add_argument("--mouth", type=float, default=0.04, help="MouthOpen 说话阈值（默认 0.04）")
    args = ap.parse_args(argv)

    out = args.out or (os.path.splitext(args.csv)[0] + "_sentences.csv")
    annotate(args.csv, args.script, out, args.pause, args.mouth)


if __name__ == "__main__":
    main()
