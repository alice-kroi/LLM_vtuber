#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载本地 rerank cross-encoder 模型到 models/ 目录。

用法：
    python scripts/download_reranker.py
    python scripts/download_reranker.py --model BAAI/bge-reranker-base \
        --dest models/bge-reranker-base

国内网络默认走 hf-mirror.com；可用 HF_ENDPOINT 环境变量覆盖。
只拉运行必需文件（权重/配置/tokenizer），跳过 README、图片与 onnx。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ALLOW_PATTERNS = [
    "*.json", "*.safetensors", "*.bin", "*.model", "*.txt", "*.py",
]
IGNORE_PATTERNS = ["assets/*", "*.png", "*.md", "onnx/*", "*.gguf", "*.h5"]


def main() -> int:
    parser = argparse.ArgumentParser(description="下载 rerank 模型")
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3",
                        help="HuggingFace 模型名")
    parser.add_argument("--dest", default="models/bge-reranker-v2-m3",
                        help="本地保存目录（相对项目根）")
    parser.add_argument("--endpoint", default="",
                        help="镜像地址，默认 hf-mirror.com（会覆盖已有 HF_ENDPOINT）")
    args = parser.parse_args()

    # 必须在 import huggingface_hub 之前设置：HF_ENDPOINT 在导入时读取
    endpoint = args.endpoint or "https://hf-mirror.com"
    os.environ["HF_ENDPOINT"] = endpoint
    host = endpoint.split("//", 1)[-1].split("/", 1)[0]
    no_proxy = {p.strip() for p in os.getenv("NO_PROXY", "").split(",") if p.strip()}
    no_proxy.update({host, "huggingface.co", "hf-mirror.com", "localhost", "127.0.0.1"})
    os.environ["NO_PROXY"] = ",".join(sorted(no_proxy))
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    # 镜像走 Xet 时容易卡住，强制回退普通 HTTP 下载
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    dest = Path(args.dest)
    if not dest.is_absolute():
        dest = PROJECT_ROOT / dest
    dest.mkdir(parents=True, exist_ok=True)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少 huggingface_hub，请先 pip install huggingface_hub")
        return 1

    print(f"下载 {args.model} → {dest}")
    print(f"镜像: {os.environ['HF_ENDPOINT']}  NO_PROXY={os.environ['NO_PROXY']}")
    snapshot_download(
        repo_id=args.model,
        local_dir=str(dest),
        allow_patterns=ALLOW_PATTERNS,
        ignore_patterns=IGNORE_PATTERNS,
    )
    total = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"完成：{dest}（{total / 1024 / 1024:.0f} MB）")
    print("把 config.ini [rag] rerank_model_path 指到该目录即可")
    return 0


if __name__ == "__main__":
    sys.exit(main())
