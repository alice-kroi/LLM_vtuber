# -*- coding: utf-8 -*-
"""把本地书籍/文档分块写入 knowledge_chunks。

用法：
    python scripts/ingest_knowledge.py -file book.pdf
    python scripts/ingest_knowledge.py -dir D:\books --source-type book --tags 小说,设定
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def iter_files(path: Path):
    if path.is_file():
        yield path
        return
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                yield item


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库分块入库")
    parser.add_argument("-file", "--file", action="append", default=[],
                        help="文件路径，可重复")
    parser.add_argument("-dir", "--dir", action="append", default=[],
                        help="目录路径，递归入库，可重复")
    parser.add_argument("--source-type", default="book")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    targets = [Path(p) for p in (args.file + args.dir)]
    if not targets:
        parser.print_help()
        return 2

    from RAG.knowledge_chunks import get_knowledge_chunk_manager

    manager = get_knowledge_chunk_manager()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    total = 0
    failed = 0
    for target in targets:
        for path in iter_files(target):
            try:
                result = manager.add_document(
                    file_data=path.read_bytes(),
                    filename=path.name,
                    title=args.title or path.stem,
                    tags=tags,
                    source_url=args.source_url,
                    source_type=args.source_type,
                )
            except Exception as e:  # noqa: BLE001
                result = {"success": False, "error": str(e)}
            print(json.dumps({"file": str(path), **result},
                             ensure_ascii=False))
            if result.get("success"):
                total += 1
            else:
                failed += 1
    print(json.dumps({"indexed": total, "failed": failed},
                     ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
