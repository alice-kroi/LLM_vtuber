"""校验 RAG 评估检查清单：四要素齐全、检索指标与阈值缺口均标注 to_measure。"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "rag_eval" / "CHECKLIST.md"
JSON_PATH = ROOT / "docs" / "rag_eval" / "checklist.json"

REQUIRED_FIELDS = ("id", "item", "standard", "verify", "threshold", "status")
VALID_STATUS = {"fixed", "to_measure"}
RETRIEVAL_GROUP = "retrieval"


def check_json(errors):
    if not JSON_PATH.exists():
        errors.append(f"缺少文件: {JSON_PATH}")
        return None
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not items:
        errors.append("checklist.json 中 items 为空")
        return data

    ids = set()
    for it in items:
        missing = [f for f in REQUIRED_FIELDS if f not in it]
        if missing:
            errors.append(f"{it.get('id', '?')} 缺字段: {missing}")
            continue
        if it["status"] not in VALID_STATUS:
            errors.append(f"{it['id']} status 非法: {it['status']}")
        if it["id"] in ids:
            errors.append(f"重复 id: {it['id']}")
        ids.add(it["id"])
        if it["status"] == "to_measure" and it["threshold"] != "to_measure":
            errors.append(f"{it['id']} 标为待标定但 threshold 已给值: {it['threshold']}")
        if it["status"] == "fixed" and it["threshold"] == "to_measure":
            errors.append(f"{it['id']} 标为已定但 threshold 是 to_measure")

    # 检索指标必须全部待实测标定
    for it in items:
        if it.get("group") == RETRIEVAL_GROUP and it.get("status") != "to_measure":
            errors.append(f"检索指标 {it['id']} 必须为 to_measure")
    return data


def check_markdown(data, errors):
    if not MD.exists():
        errors.append(f"缺少文件: {MD}")
        return
    text = MD.read_text(encoding="utf-8")
    for section in ("检查项", "判定标准", "验证方式", "建议阈值"):
        if section not in text:
            errors.append(f"CHECKLIST.md 缺少列: {section}")
    if "待实测标定" not in text:
        errors.append("CHECKLIST.md 未出现“待实测标定”标记")
    if data:
        # 每个 JSON 条目都应在 markdown 表里出现
        for it in data.get("items", []):
            if not re.search(rf"\| {re.escape(it['id'])} \|", text):
                errors.append(f"CHECKLIST.md 表格缺条目: {it['id']}")


def main():
    errors = []
    data = check_json(errors)
    check_markdown(data, errors)
    if errors:
        print("FAIL")
        for e in errors:
            print(" -", e)
        return 1
    counts = {}
    for it in data["items"]:
        counts[it["group"]] = counts.get(it["group"], 0) + 1
    print(f"OK: {len(data['items'])} 条检查项通过校验 {counts}")
    to_measure = sum(1 for it in data["items"] if it["status"] == "to_measure")
    print(f"待实测标定: {to_measure} 条 / 已定: {len(data['items']) - to_measure} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
