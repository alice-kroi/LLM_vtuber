"""
P0-minus 真实场景演示：用 os_list_elements_json/os_click_id/os_type_by_label
对当前活跃窗口做"看结构 → 按 id 点 → 按 label 输"全流程。

先把当前活跃窗口置到一个有可识别控件的地方（VS Code / 文件管理器都行），
然后本脚本会：截屏 → 列元素 → 展示前 10 → 试着按"text/name"匹配某个按钮点击。

输出格式：标到 stdout 的就是 os_list_elements_json 的真实返回 JSON 节选。
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.computer_use import (
    os_list_windows,
    os_list_elements_json,
    os_click_id,
    os_type_by_label,
)


async def main(window_keyword: str = "", target_text: str = "",
                target_label: str = "") -> int:
    # 1) 列窗口，让用户挑选
    ws = await os_list_windows()
    cands = []
    for w in (ws.get("windows", []) if ws.get("success") else []):
        t = w.get("title", "") or ""
        if t and t.strip():
            cands.append({"pid": w.get("pid"), "title": t.strip()[:80],
                          "left": w.get("left"), "top": w.get("top"),
                          "width": w.get("width"), "height": w.get("height")})
    print(f"=== 可用窗口（{len(cands)}） ===")
    for i, c in enumerate(cands[:15]):
        print(f"  [{i}] {c['title'][:60]}  ({c['width']}x{c['height']})")

    # 2) 选窗口
    if window_keyword:
        picked = next((c for c in cands
                       if window_keyword.lower() in c["title"].lower()), None)
        if not picked:
            print(f"!! 没找到关键字 {window_keyword!r}，将使用第一个非空标题窗口")
            picked = cands[0] if cands else None
    else:
        # 优先挑包含 "Visual Studio Code" 或 "Visual Studio" 的真正编辑器窗口
        # 排除 "TRAE CN" / 其它 IDE 编辑器截图
        for kw in ["Visual Studio Code", "llmvt_demo", "csv2md",
                   "Visual Studio"]:
            picked = next((c for c in cands
                           if kw.lower() in c["title"].lower()), None)
            if picked:
                break
        if not picked:
            # 退路：找最大尺寸的窗口
            picked = max(cands, key=lambda c: c["width"] * c["height"]) if cands else None

    if not picked:
        print("无可用窗口")
        return 1
    print(f"\n--- 已选窗口: {picked['title']} ---")

    # 3) 跑结构化元素表
    print("\n[1/3] 跑 os_list_elements_json ...")
    r = await os_list_elements_json(picked["title"])
    if not r.get("success"):
        print(f"!! 失败: {r}")
        return 1
    elements = r.get("elements", [])
    print(f"    success=True, {len(elements)} 个元素  "
          f"(UIA={r.get('uia_count')}, OCR={r.get('ocr_count')})")
    print(f"    uia_error={r.get('uia_error','')[:100]!r}")
    print(f"    ocr_error={r.get('ocr_error','')[:100]!r}")
    # 元素表前 12 项
    print("\n--- 前 12 个元素 (id, type, name, text, bbox, source, conf) ---")
    for el in elements[:12]:
        bb = el.get("bbox", [])
        print(f"  {el.get('id'):>5}  {el.get('type','?'):<12} "
              f"name={el.get('name','')[:18]!r:<20} "
              f"text={el.get('text','')[:24]!r:<26} "
              f"bbox=({bb[0]},{bb[1]})-({bb[2]},{bb[3]}) "
              f"src={el.get('source')} conf={el.get('conf'):.2f}")

    # 4) 按 text 找目标点一下（演示 os_click_id）
    if target_text:
        target_id = None
        target_el_full = None
        for el in elements:
            t = (el.get("text") or "").strip()
            n = (el.get("name") or "").strip()
            if t == target_text or n == target_text or \
                    (target_text and target_text in (t + n)):
                target_id = el["id"]
                target_el_full = el
                break
        if not target_id:
            # 退路：找首个含 target_text 的元素
            print(f"!! 未精确匹配 text={target_text!r}，跳过点击步骤")
        else:
            print(f"\n[2/3] 点 text={target_text!r} 的元素 id={target_id} ...")
            cr = await os_click_id(picked["title"], target_id)
            print(f"    {cr}")
    else:
        print("\n[2/3] (跳过点击；未指定 target_text)")

    # 5) 按 label 输
    if target_label:
        print(f"\n[3/3] 按 label={target_label!r} 找输入框并输入 ...")
        tr = await os_type_by_label(
            picked["title"], target_label, "P0-minus test input")
        print(f"    {json.dumps(tr, ensure_ascii=False, indent=2)[:1000]}")
    else:
        print("\n[3/3] (跳过输入；未指定 target_label)")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="", help="窗口标题关键字（省略自动选）")
    ap.add_argument("--click-text", default="", help="想点击的按钮/元素 text/name")
    ap.add_argument("--type-label", default="",
                    help="想输入位置的标签文字（OCR/UIA 名称）")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.window, a.click_text, a.type_label)))
