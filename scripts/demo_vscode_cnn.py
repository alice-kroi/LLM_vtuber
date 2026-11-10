"""
P0-minus·真实 Save 演示：完整流程：
  code.cmd 启 VS Code -> Ctrl+N 开 Untitled -> 剪贴板塞 CNN 代码 -> Ctrl+V 粘贴
  -> Ctrl+Shift+S 触发 Save As -> 等 Save As dialog -> 按 label 输入文件名 -> 点 Save 真·点击
  全程：os_activate_window + os_list_elements_json + os_click_id + os_type_by_label
  **不预创建 cnn_demo.py**，确保 Untitled -> Save As 一定会弹 OS dialog。

依赖环境：mcfish（含 paddleocr / uiautomation / pyperclip / pygetwindow）
启动方式（在 LLM_vtuber 项目根目录执行）：
    C:\ProgramData\anaconda3\envs\mcfish\python.exe scripts/demo_vscode_cnn.py
"""
import asyncio
import os
import sys
import time
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.computer_use import (
    os_list_windows,
    os_activate_window,
    os_list_elements_json,
    os_click_id,
    os_click_xy,
    os_type_text,
    os_type_by_label,
    os_press_key,
    os_clipboard_set,
    os_paste_text,
    os_run_command,
)


# ---------- CNN 代码（pyTorch + MNIST，GPU 跑得动） ----------
CNN_CODE = r'''"""CNN 训练 MNIST（GPU），3 epochs 应达 ~99% 准确率。"""
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms


class SimpleCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, 3, 1)
        self.conv2 = nn.Conv2d(32, 64, 3, 1)
        self.fc1 = nn.Linear(9216, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.max_pool2d(x, 2)
        x = torch.flatten(x, 1)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return F.log_softmax(x, dim=1)


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[cnn_demo] device={device}")

    tfm = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)),
    ])
    train_ds = datasets.MNIST(
        "./data", train=True, download=True, transform=tfm)
    test_ds = datasets.MNIST(
        "./data", train=False, download=True, transform=tfm)
    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    test_loader = DataLoader(test_ds, batch_size=256)

    model = SimpleCNN().to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)

    for epoch in range(1, 4):
        model.train()
        for batch_idx, (x, y) in enumerate(train_loader):
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            loss = F.nll_loss(model(x), y)
            loss.backward()
            opt.step()
        # 评估
        model.eval()
        correct = 0
        with torch.no_grad():
            for x, y in test_loader:
                x, y = x.to(device), y.to(device)
                pred = model(x).argmax(dim=1)
                correct += pred.eq(y).sum().item()
        acc = correct / len(test_ds)
        print(f"[cnn_demo] epoch {epoch}: test acc={acc:.4f}")


if __name__ == "__main__":
    main()
'''


# ---------- 调试辅助：打印元素表 ----------
def dump_elements(elements, uia_n=0, ocr_n=0, label=""):
    head = f"--- {label}: 共 {len(elements)} 个元素 (UIA={uia_n}, OCR={ocr_n}) ---"
    print(head)
    for el in elements[:30]:
        bb = el.get("bbox", [])
        bbox = f"({bb[0]:>4},{bb[1]:>4})-({bb[2]:>4},{bb[3]:>4})" if len(bb) == 4 else "(?,?)"
        src = el.get("source", "?")
        conf = el.get("conf")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else "?"
        print(f"  id={el.get('id'):>5}  type={(el.get('type') or '?')[:10]:<10}  "
              f"text={(el.get('text') or '')[:24]!r:<26}  "
              f"name={(el.get('name') or '')[:14]!r:<16}  "
              f"src={src}  conf={conf_str}  {bbox}")
    print("-" * len(head))


def find_elements_by_text(elements, text_kw, exact=False):
    """从元素列表里找含特定 text/name 的元素。exact=True 表示全等匹配。"""
    out = []
    for el in elements:
        t = (el.get("text") or "").strip()
        n = (el.get("name") or "").strip()
        if exact:
            if t == text_kw or n == text_kw:
                out.append(el)
        else:
            hay = (t + " " + n).lower()
            if text_kw.lower() in hay:
                out.append(el)
    return out


async def list_active(win_kw="Visual Studio Code"):
    r = await os_list_elements_json(win_kw)
    if not r.get("success"):
        print(f"!! list_elements_json 失败: {r}")
        return [], 0, 0
    return (r.get("elements", []),
            r.get("uia_count", 0),
            r.get("ocr_count", 0))


async def clipboard_set_retry(text, attempts=4):
    """pyperclip 偶发 OpenClipboard 失败，重试 + sleep"""
    last_err = None
    for i in range(attempts):
        r = await os_clipboard_set(text)
        if r.get("success"):
            return r
        last_err = r.get("error")
        wait = 0.4 * (2 ** i)
        print(f"   ! clipboard 第 {i+1} 次失败: {last_err[:80]!r}，sleep {wait:.1f}s 重试")
        await asyncio.sleep(wait)
    return {"success": False, "error": f"重试 {attempts} 次仍失败: {last_err}"}


async def get_window_rect(win_kw):
    """从 os_list_windows 里查窗口矩形。"""
    ws = await os_list_windows()
    for w in ws.get("windows", []):
        t = w.get("title", "") or ""
        if win_kw.lower() in t.lower():
            return {
                "title": t,
                "left": w.get("left"),
                "top": w.get("top"),
                "width": w.get("width"),
                "height": w.get("height"),
            }
    return None


async def find_saveas_dialog_window():
    """找当前打开的 Save As dialog（标题可能为 '另存为' / 'Save As'）"""
    ws = await os_list_windows()
    for w in (ws.get("windows", []) if ws.get("success") else []):
        t = (w.get("title") or "").strip()
        if t in ("另存为", "Save As") or "另存为" in t or t.startswith("Save As"):
            return {
                "title": t,
                "left": w.get("left"),
                "top": w.get("top"),
                "width": w.get("width"),
                "height": w.get("height"),
            }
    return None


async def fill_filename_and_save(dialog_title: str, filename: str) -> dict:
    """在 Save As dialog 里：定位文件名输入框 → 清空 → 输入 → 点保存按钮"""
    # 1) OCR dialog
    er = await os_list_elements_json(dialog_title)
    if not er.get("success"):
        return {"success": False, "error": f"OCR dialog 失败: {er}"}
    elements = er.get("elements", [])

    # 2) 找文件名输入框：UIA type=Edit 且 text 形如 *.py / Untitled-*
    filename_input = None
    for el in elements:
        if (el.get("type") or "").lower() in ("edit", "editcontrol", "text"):
            t = (el.get("text") or "").strip()
            if t and (t.endswith(".py") or "Untitled" in t or "untitled" in t.lower()):
                filename_input = el
                break
    # 兜路：找含 "Untitled" 的任何元素
    if not filename_input:
        for el in elements:
            t = (el.get("text") or "").strip()
            if "Untitled" in t or ".py" in t:
                filename_input = el
                break

    # 3) 找保存按钮：text 含 "保存" / "Save"
    save_btn = None
    for el in elements:
        t = (el.get("text") or "").strip()
        if t in ("保存(S)", "保存", "Save", "Save As") or \
           (el.get("name") or "").strip() in ("保存(S)", "Save"):
            save_btn = el
            break
    # 兜路：含 save/保存 子串
    if not save_btn:
        for el in elements:
            t = (el.get("text") or "").strip()
            n = (el.get("name") or "").strip()
            if "保存" in t or t.lower() == "save" or "save" in n.lower():
                save_btn = el
                break

    print(f"      filename_input: {filename_input}")
    print(f"      save_btn: {save_btn}")
    if not filename_input:
        return {"success": False, "error": "没找到文件名输入框"}
    if not save_btn:
        return {"success": False, "error": "没找到保存按钮"}

    # 4) 点击文件名输入框
    bb = filename_input["bbox"]
    cx = (bb[0] + bb[2]) // 2
    cy = (bb[1] + bb[3]) // 2
    print(f"      click filename input center=({cx},{cy})")
    await os_click_xy(cx, cy)
    await asyncio.sleep(0.4)

    # 5) Ctrl+A 全选 + Delete 清空
    await os_press_key("ctrl+a")
    await asyncio.sleep(0.2)
    await os_press_key("delete")
    await asyncio.sleep(0.3)

    # 6) 输入新文件名（纯 ASCII 走 pyautogui.write）
    tr = await os_type_text(filename)
    print(f"      type_text({filename!r}): {tr}")
    await asyncio.sleep(0.6)

    # 7) 点保存按钮（直接用坐标，避免 os_click_id 内部重新 OCR 导致 id 不稳定）
    bb = save_btn["bbox"]
    cx = (bb[0] + bb[2]) // 2
    cy = (bb[1] + bb[3]) // 2
    print(f"      click Save button center=({cx},{cy}) (text={save_btn.get('text')!r})")
    await os_click_xy(cx, cy)
    await asyncio.sleep(2.0)
    return {"success": True, "filename_input": filename_input, "save_btn": save_btn,
            "typed": filename, "clicked_xy": (cx, cy)}


# ---------- 主流程 ----------
async def main() -> int:
    work = Path.home() / "Documents" / "llmvt_demo_cnn"
    print(f"=== P0-minus CNN 真实 Save 演示 ===")
    print(f"目标 workspace: {work}\n")

    # 1) 清空旧 workspace（保留 .bak 如有）
    if work.exists():
        bak = work.with_suffix(work.suffix + ".bak")
        if bak.exists():
            shutil.rmtree(bak, ignore_errors=True)
        shutil.move(str(work), str(bak))
        print(f"[1/10] 旧 workspace 已备份 -> {bak.name}")
    else:
        print(f"[1/10] workspace 不存在，无需清理")

    # 2) 重建空 workspace（不预创建 cnn_demo.py）
    work.mkdir(parents=True, exist_ok=True)
    print(f"[2/10] 已创建空 workspace: {work}")
    # 确认确实空
    contents = list(work.iterdir())
    print(f"      workspace 内容: {contents}")

    # 3) 用 code.cmd 启动 VS Code（强制用字符串形式，避免 .cmd WinError 193）
    print(f"[3/10] 启动 VS Code ...")
    code_cli = r"D:\Microsoft VS Code\bin\code.cmd"
    if not os.path.exists(code_cli):
        # 兜底：where code 查
        probe = await os_run_command("where code", timeout=5)
        for line in (probe.get("stdout") or "").splitlines():
            cand = line.strip()
            if cand.lower().endswith((".cmd", ".exe", ".bat")) and os.path.isfile(cand):
                code_cli = cand
                break
    print(f"      code_cli = {code_cli}")
    r = await os_run_command(f'"{code_cli}" "{work}"', timeout=20)
    print(f"      rc={r.get('returncode')} success={r.get('success')}")

    # 4) 等 VS Code 启动，激活窗口
    print(f"[4/10] 激活 VS Code 窗口 ...")
    await asyncio.sleep(4)
    # 先列窗口看真实标题
    ws = await os_list_windows()
    titles = [w["title"] for w in ws.get("windows", []) if w.get("title")]
    print(f"      当前窗口（前 10）: {titles[:10]}")
    # 选含有 work 目录名的窗口
    work_name = work.name
    win_kw = work_name
    rect = await get_window_rect(win_kw)
    if not rect:
        win_kw = "Visual Studio Code"
        rect = await get_window_rect(win_kw)
    print(f"      匹配窗口: {rect}")
    ar = await os_activate_window(win_kw)
    print(f"      activate('{win_kw}'): {ar}")
    await asyncio.sleep(1.5)

    # 5) 先点 editor 区中央一下（VS Code 启动后焦点可能停在 Explorer 面板）
    print(f"[5/10] 先点 editor 区中央确保焦点进编辑器 ...")
    if rect:
        ex = rect["left"] + int(rect["width"] * 0.7)
        ey = rect["top"] + int(rect["height"] * 0.5)
        print(f"      click_xy({ex}, {ey})")
        await os_click_xy(ex, ey)
        await asyncio.sleep(0.8)

    # 6) Ctrl+N 新建 Untitled
    print(f"[6/10] Ctrl+N 新建 Untitled ...")
    await os_press_key("ctrl+n")
    await asyncio.sleep(1.5)

    # 7) 把 CNN 代码写入编辑器（用新智能粘贴 API，自动降级）
    print(f"[7/10] os_paste_text 写入 CNN 代码到编辑器（{len(CNN_CODE)} chars） ...")
    cr = await os_paste_text(CNN_CODE)
    print(f"      paste: method={cr.get('method')!r} success={cr.get('success')}")
    await asyncio.sleep(2.0)

    # 8) 截图 OCR 看现在 VS Code 是什么状态
    print(f"\n[8/10] 当前 VS Code 元素表（粘贴后）:")
    elements, uia_n, ocr_n = await list_active(win_kw)
    dump_elements(elements, uia_n=uia_n, ocr_n=ocr_n, label="after paste")
    # 验证：元素表里有没有 "import" / "SimpleCNN" / "class" 这些代码标识
    code_keywords = ["import torch", "SimpleCNN", "torchvision", "DataLoader", "class SimpleCNN"]
    found_kws = []
    for el in elements:
        blob = (el.get("text") or "") + " " + (el.get("name") or "")
        for kw in code_keywords:
            if kw in blob:
                found_kws.append(kw)
    if found_kws:
        print(f"   ✓ 元素表里发现代码关键词: {found_kws} → 粘贴成功")
    else:
        print(f"   ✗ 元素表里没发现代码关键词 → 粘贴失败，需要重新点 editor 再 Ctrl+V")
        # 兜底：再点一次 editor 中央
        if rect:
            ex = rect["left"] + int(rect["width"] * 0.7)
            ey = rect["top"] + int(rect["height"] * 0.5)
            await os_click_xy(ex, ey)
            await asyncio.sleep(0.6)
            await os_press_key("ctrl+v")
            await asyncio.sleep(1.5)
            elements, uia_n, ocr_n = await list_active(win_kw)
            dump_elements(elements, uia_n=uia_n, ocr_n=ocr_n, label="after retry paste")

    # 9) Ctrl+Shift+S 触发 Save As
    print(f"\n[9/10] Ctrl+Shift+S 触发 Save As ...")
    await os_press_key("ctrl+shift+s")
    await asyncio.sleep(2.0)

    # 10) Loop OCR 等 Save As dialog 出现（最多 15 秒，找独立的 dialog 窗口）
    print(f"\n[10/12] 等待 Save As dialog ...")
    dialog_appeared = False
    dialog_title = None
    deadline = time.time() + 15
    while time.time() < deadline:
        dlg = await find_saveas_dialog_window()
        if dlg:
            dialog_appeared = True
            dialog_title = dlg["title"]
            print(f"   ✓ Save As dialog 出现: title={dialog_title!r}, rect={dlg}")
            break
        await asyncio.sleep(0.5)
    if not dialog_appeared:
        print(f"   ✗ 15 秒内未出现 Save As dialog。当前所有窗口标题：")
        ws = await os_list_windows()
        for w in (ws.get("windows", []) if ws.get("success") else []):
            print(f"      - {w.get('title')!r}")

    # 11) 如果 dialog 出现：真·点击输入文件名 + 真·点击保存按钮（用新中英双语 label API）
    print(f"\n[11/12] 执行保存 ...")
    if dialog_appeared:
        await os_activate_window(dialog_title)
        await asyncio.sleep(0.5)
        # 用中英双语 label 候选（os_type_by_label 新功能）
        sr = await os_type_by_label(
            dialog_title, "文件名(N):|File name:|文件名|File name", "cnn_demo.py")
        print(f"      type_by_label (中英双语候选): "
              f"success={sr.get('success')} matched={sr.get('matched_label')!r}")
        await asyncio.sleep(0.6)
        # 再 OCR 找 "保存(S)" 按钮（id 可能不稳，直接锁坐标）
        er = await os_list_elements_json(dialog_title)
        elements = er.get("elements", [])
        save_btn = None
        for el in elements:
            t = (el.get("text") or "").strip()
            if t in ("保存(S)", "保存", "Save") or \
               (el.get("name") or "").strip() in ("保存(S)", "Save"):
                save_btn = el
                break
        if not save_btn:
            for el in elements:
                t = (el.get("text") or "").strip()
                if "保存" in t or t.lower() == "save":
                    save_btn = el
                    break
        if save_btn:
            # 用新 os_click_id 传 bbox 锁定坐标（避免 OCR 重跑 id 漂移）
            cr = await os_click_id(dialog_title, str(save_btn.get("id")),
                                   bbox=save_btn["bbox"])
            print(f"      click_id+bbox: {cr.get('success')} "
                  f"clicked={cr.get('clicked')}")
            await asyncio.sleep(2.0)
        else:
            print(f"   ✗ 没找到保存按钮候选，尝试用 Enter 兜底")
            await os_press_key("enter")
            await asyncio.sleep(1.5)
    else:
        print(f"   ✗ Save As dialog 未出现，跳过 Save 步骤")

    # 12) 验证：文件是否真的保存到磁盘
    print(f"\n[12/12] 验证 ...")
    target = work / "cnn_demo.py"
    if target.exists():
        sz = target.stat().st_size
        head = target.read_text(encoding="utf-8", errors="replace")[:300]
        print(f"   ✓ 文件存在: {target} ({sz} bytes)")
        print(f"   --- 头 300 字符 ---")
        print(f"   {head}")
        print(f"   --- 结束 ---")
    else:
        print(f"   ✗ 文件不存在: {target}")
        # 兜底：看看是不是另存为其它文件名
        for f in work.iterdir():
            print(f"      实际文件: {f.name} ({f.stat().st_size} bytes)")

    print(f"\n=== 演示结束 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
