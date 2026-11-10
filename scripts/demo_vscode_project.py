"""
P0-1 真实场景演示：AI 用文件/Shell 工具一键生成一个 Python CLI 项目，并用 VS Code 打开。

场景：csv2md —— 把 CSV 转成 Markdown 表格的小工具。
- 6 个文件：README / .gitignore / requirements / src/csv2md / src/__main__ / tests/test_csv2md / examples
- 单元测试 + 实际运行 + 在 VS Code 中打开
- 全程不键入 UI，全部走 file/Shell 工具

用法（在你的 mcfish 环境运行）：
    python scripts/demo_vscode_project.py
"""
import asyncio
import os
import sys
from pathlib import Path

# 让脚本能 import tool/ 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.computer_use import (
    os_mkdir, os_create_file, os_run_command, os_read_file, os_edit_file,
)


# ---------- 项目结构（6 个文件） ----------

README_MD = """# csv2md

把 CSV 转成 Markdown 表格的极简 CLI 工具。

## 安装

```bash
pip install -r requirements.txt
```

## 用法

```bash
# 标准输出
python -m src examples/people.csv

# 写到文件
python -m src examples/people.csv -o people.md
```

## 开发

```bash
python -m pytest tests/ -v
```
"""

GITIGNORE = """# Python
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.mypy_cache/

# IDE
.vscode/
.idea/

# 输出
*.md.bak
"""

REQUIREMENTS_TXT = ""  # 零依赖，纯标准库

SRC_INIT = '"""csv2md: CSV -> Markdown table converter."""\n__version__ = "0.1.0"\n'

CSV2MD_PY = '''"""核心转换逻辑（无 I/O 副作用，便于测试）。"""
import csv
import io
from typing import List


def _escape_cell(s: str) -> str:
    """Markdown 表格单元转义：替换 | 与换行。"""
    if s is None:
        return ""
    return s.replace("|", "\\\\|").replace("\\n", " ").replace("\\r", " ").strip()


def convert(csv_text: str, delimiter: str = ",") -> str:
    """把 CSV 文本转成 Markdown 表格字符串。

    Args:
        csv_text: CSV 文本（含表头）。
        delimiter: 字段分隔符，默认 ","。

    Returns:
        Markdown 表格字符串。
    """
    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
    rows: List[List[str]] = [r for r in reader if r]
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    head_line = "| " + " | ".join(_escape_cell(c) for c in header) + " |"
    sep_line = "| " + " | ".join("---" for _ in header) + " |"
    body_lines = [
        "| " + " | ".join(_escape_cell(c) for c in r) + " |" for r in body
    ]
    return "\\n".join([head_line, sep_line, *body_lines]) + "\\n"
'''

MAIN_PY = '''"""CLI 入口。"""
import argparse
import sys
from pathlib import Path

from src.csv2md import convert


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="csv2md", description="CSV -> Markdown table")
    p.add_argument("input", type=Path, help="输入 CSV 文件")
    p.add_argument("-o", "--output", type=Path, help="输出 Markdown 文件（省略则打印到 stdout）")
    p.add_argument("-d", "--delimiter", default=",", help="字段分隔符，默认 ','")
    args = p.parse_args(argv)

    if not args.input.exists():
        print(f"输入文件不存在: {args.input}", file=sys.stderr)
        return 1

    csv_text = args.input.read_text(encoding="utf-8")
    md = convert(csv_text, delimiter=args.delimiter)

    if args.output:
        args.output.write_text(md, encoding="utf-8")
        print(f"已写入 {args.output} ({len(md)} chars)")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TESTS_INIT = ""

TEST_PY = '''"""单元测试（pytest / unittest 双兼容）。"""
import unittest

from src.csv2md import convert


class TestConvert(unittest.TestCase):
    def test_basic(self):
        md = convert("name,age\\nAlice,30\\nBob,25\\n")
        lines = md.strip().split("\\n")
        self.assertEqual(lines[0], "| name | age |")
        self.assertEqual(lines[1], "| --- | --- |")
        self.assertEqual(lines[2], "| Alice | 30 |")
        self.assertEqual(lines[3], "| Bob | 25 |")
        self.assertEqual(len(lines), 4)

    def test_empty(self):
        self.assertEqual(convert(""), "")
        self.assertEqual(convert("\\n\\n"), "")

    def test_escape_pipe(self):
        # | 必须被转义为 \\|，否则破坏表格
        md = convert("a|b,c\\n1|2,3\\n")
        self.assertIn("a\\\\|b", md)  # header "a|b" -> "a\\|b"
        self.assertIn("1\\\\|2", md)  # row "1|2" -> "1\\|2"
        # 末列 "3" 无 |，保持原样
        self.assertIn("| 3 |", md)

    def test_custom_delimiter(self):
        md = convert("a;b\\n1;2\\n", delimiter=";")
        self.assertIn("| a | b |", md)
        self.assertIn("| 1 | 2 |", md)

    def test_single_column(self):
        md = convert("name\\nAlice\\nBob\\n")
        lines = md.strip().split("\\n")
        self.assertEqual(lines[0], "| name |")
        self.assertEqual(lines[1], "| --- |")
        self.assertEqual(lines[2], "| Alice |")
        self.assertEqual(lines[3], "| Bob |")
        self.assertEqual(len(lines), 4)


if __name__ == "__main__":
    unittest.main()
'''

EXAMPLES_CSV = """name,role,city
Alice,Engineer,Beijing
Bob,Designer,Shanghai
Carol,PM,Shenzhen
Dave,QA,Hangzhou
"""


# ---------- 演示流程 ----------

async def main() -> int:
    # 演示项目放在用户主目录下的桌面/工作目录，不污染 LLM_vtuber 仓库本身
    work = Path.home() / "Documents" / "llmvt_demo_csv2md"
    if work.exists():
        # 演示项目用完可保留也可清理；这里强制重建保证一致
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    print(f"=== P0-1 真实场景演示：csv2md 项目 ===")
    print(f"目标目录: {work}\\n")

    # 1) 创建目录骨架
    print("[1/8] 创建项目目录骨架 ...")
    r = await os_mkdir(str(work / "src"))
    assert r["success"]
    r = await os_mkdir(str(work / "tests"))
    assert r["success"]
    r = await os_mkdir(str(work / "examples"))
    assert r["success"]
    print(f"      OK: {work}")

    # 2) 用 os_create_file 一气写完 7 个文件
    print("[2/8] 写入 7 个文件 ...")
    files = [
        ("README.md", README_MD),
        (".gitignore", GITIGNORE),
        ("requirements.txt", REQUIREMENTS_TXT),
        ("src/__init__.py", SRC_INIT),
        ("src/csv2md.py", CSV2MD_PY),
        ("src/__main__.py", MAIN_PY),
        ("tests/__init__.py", TESTS_INIT),
        ("tests/test_csv2md.py", TEST_PY),
        ("examples/people.csv", EXAMPLES_CSV),
    ]
    for rel, content in files:
        r = await os_create_file(str(work / rel), content)
        assert r["success"], f"create {rel} failed: {r}"
        print(f"      + {rel} ({r['size']} bytes)")

    # 3) 初始化 git
    print("[3/8] git init ...")
    r = await os_run_command("git init", cwd=str(work), timeout=15)
    print(f"      rc={r['returncode']} stderr={r.get('stderr','').strip()[:80]!r}")

    # 4) 跑一次：把 people.csv 转成 md 打印
    print("[4/8] 运行：python -m src examples/people.csv")
    r = await os_run_command(
        ["python", "-m", "src", "examples/people.csv"],
        cwd=str(work), timeout=10,
    )
    print(f"      rc={r['returncode']} success={r['success']}")
    print("      --- 输出 ---")
    for line in r["stdout"].splitlines():
        print(f"      {line}")
    print("      --- 结束 ---")
    assert r["success"] and r["returncode"] == 0

    # 5) 写到文件
    print("[5/8] 写到文件：python -m src examples/people.csv -o examples/people.md")
    r = await os_run_command(
        ["python", "-m", "src", "examples/people.csv", "-o", "examples/people.md"],
        cwd=str(work), timeout=10,
    )
    print(f"      {r['stdout'].strip()}")
    assert r["success"] and r["returncode"] == 0

    # 6) 跑单元测试（pytest 缺失则用 unittest 兜底；保证演示可跑通）
    print("[6/8] 单元测试：python -m pytest tests/ -v (fallback: unittest)")
    r = await os_run_command(
        ["python", "-m", "pytest", "tests/", "-v"],
        cwd=str(work), timeout=30,
    )
    if r["returncode"] != 0 and "No module named pytest" in r.get("stderr", ""):
        print("      pytest 缺失，回退到 unittest 发现模式")
        r = await os_run_command(
            ["python", "-m", "unittest", "discover", "tests/", "-v"],
            cwd=str(work), timeout=30,
        )
    print(f"      rc={r['returncode']}")
    for line in r["stdout"].splitlines()[-15:]:
        print(f"      {line}")
    assert r["success"] and r["returncode"] == 0, f"tests failed: {r.get('stderr','')[:500]}"

    # 7) 用 os_edit_file 做一次"AI 改需求"演示：把分隔符支持加上 tab
    print("[7/8] 演示 AI 改需求：用 os_edit_file 加 'tab 自动嗅探' 分隔符")

    needle_a = (
        'def convert(csv_text: str, delimiter: str = ",") -> str:\n'
    )
    new_block_a = (
        'def _auto_delimiter(text: str) -> str:\n'
        '    """Auto sniff: tab > comma"""\n'
        '    return "\\t" if "\\t" in text.split("\\n")[0] else ","\n'
        '\n'
        '\n'
        'def convert(csv_text: str, delimiter: str = ",") -> str:\n'
    )
    r = await os_edit_file(
        str(work / "src" / "csv2md.py"),
        needle_a, new_block_a,
    )
    print(f"      edit csv2md.py: success={r.get('success')} replaced={r.get('replaced')} error={r.get('error','')[:120]}")
    assert r["success"] and r["replaced"] == 1, f"edit csv2md.py failed: {r}"

    needle_b = (
        '    csv_text = args.input.read_text(encoding="utf-8")\n'
        '    md = convert(csv_text, delimiter=args.delimiter)\n'
    )
    new_block_b = (
        '    csv_text = args.input.read_text(encoding="utf-8")\n'
        '    if args.delimiter == "," and "\\t" in csv_text.split("\\n")[0]:\n'
        '        args.delimiter = "\\t"\n'
        '        sys.stderr.write("[csv2md] auto-sniffed tab delimiter\\n")\n'
        '    md = convert(csv_text, delimiter=args.delimiter)\n'
    )
    r = await os_edit_file(
        str(work / "src" / "__main__.py"),
        needle_b, new_block_b,
    )
    print(f"      edit __main__.py: success={r.get('success')} replaced={r.get('replaced')} error={r.get('error','')[:120]}")
    assert r["success"] and r["replaced"] == 1, f"edit __main__.py failed: {r}"

    # 再跑测试 + 跑一次 tab 分隔
    r = await os_run_command(
        ["python", "-m", "pytest", "tests/", "-q"],
        cwd=str(work), timeout=30,
    )
    print(f"      改后测试 rc={r['returncode']}: {r['stdout'].strip().splitlines()[-1] if r['stdout'].strip() else ''}")

    # 8) 在 VS Code 中打开项目
    print("[8/8] 在 VS Code 中打开项目 ...")
    # PATH 不一定带 code，先用 where 探测；必须挑出 .cmd/.exe/.bat 实文件，
    # 否则可能命中 "D:\Microsoft VS Code\bin\code" 这个 WSL bash shim —— 它没有
    # .exe/.cmd 后缀，从 cmd.exe 调用时 $0 解析异常，会把 cwd 当 $0 拼出
    # "cwd\..\Code.exe"，从而得到 rc=9009。
    probe = await os_run_command("where code", timeout=5)
    code_cli = ""
    if probe.get("success") and probe.get("returncode") == 0:
        for line in (probe.get("stdout") or "").splitlines():
            cand = line.strip()
            low = cand.lower()
            # 跳过 WSL shim：没有 .exe/.cmd/.bat 后缀且不是 directory
            if low.endswith((".cmd", ".bat", ".exe")) and os.path.isfile(cand):
                code_cli = cand
                break
    if not code_cli:
        fallback = r"D:\Microsoft VS Code\bin\code.cmd"
        if os.path.exists(fallback):
            code_cli = fallback
            print(f"      where code 无可用实文件，回退到 {code_cli}")
    if not code_cli:
        print("      ✗ 找不到 code.cmd/code.exe，请安装 VS Code")
        return 0
    print(f"      使用: {code_cli}")
    # 强制用 shell 字符串形式（list 形式调用 .cmd 会触发 WinError 193）
    r = await os_run_command(f'"{code_cli}" .', cwd=str(work), timeout=20)
    if not r.get("success") and "WinError 193" in r.get("error", ""):
        # 退化兜底：cmd /c 包一层
        r = await os_run_command(f'cmd /c ""{code_cli}" ."', cwd=str(work), timeout=20)
    if r.get("success") and r.get("returncode") == 0:
        print(f"      code . rc=0 success=True  ✓ VS Code 已启动并打开项目")
    else:
        print(f"      code . rc={r.get('returncode')} success={r.get('success')} error={r.get('error','')[:200]}")
        if r.get("stderr"):
            print(f"      stderr: {r['stderr'].strip()[:200]}")

    print(f"\\n=== 演示完成 ===")
    print(f"项目位置: {work}")
    print(f"在 VS Code 中可看到：7 个文件 + 单元测试 + 实际运行输出")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
