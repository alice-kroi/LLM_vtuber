#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描版 PDF → Markdown（火山引擎 OCR）

用法：
    python scripts/ocr_pdf.py "D:\\books\\教材.pdf"
    python scripts/ocr_pdf.py 教材.pdf --max-pages 20 --dpi 200 -o out.md

凭证（环境变量，不是 Doubao_API_KEY）：
    VOLC_ACCESSKEY / VOLC_SECRETKEY
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tool import ocr_cloud  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="扫描版 PDF 云端 OCR → Markdown")
    parser.add_argument("pdf", help="PDF 路径")
    parser.add_argument("-o", "--out", default="", help="输出 md 路径（默认同名 .ocr.md）")
    parser.add_argument("--max-pages", type=int, default=0, help="最多处理多少页（0=配置值）")
    parser.add_argument("--dpi", type=int, default=0, help="渲染 DPI（0=配置值，默认 200）")
    args = parser.parse_args()

    if not ocr_cloud.available():
        print(ocr_cloud.credentials_hint())
        return 2

    def on_page(index: int, total: int, result: dict) -> None:
        mark = "ok" if result.get("success") else f"失败：{result.get('error')}"
        count = len(result.get("lines") or [])
        print(f"[{index}/{total}] {mark}（{count} 行）", flush=True)

    result = ocr_cloud.ocr_pdf_pages(
        args.pdf,
        max_pages=args.max_pages or None,
        dpi=args.dpi or None,
        on_page=on_page,
    )
    out = Path(args.out) if args.out else Path(args.pdf).with_suffix(".ocr.md")
    out.write_text(result.get("text") or "", encoding="utf-8")
    print(f"完成：{result.get('page_count')} 页 → {out}")
    if result.get("error"):
        print(f"部分页失败：{result['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
