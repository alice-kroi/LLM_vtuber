"""Phase B 验证脚本：desktop 内建域

测试目标：
  1. DomainAssembly 自动发现 LLM_v2/domains/desktop/desktop.py
  2. desktop 域注册成功
  3. tools() 返回 31 个 os_* 工具 schema
  4. call_tool 调用 os_list_windows / os_clipboard_set / os_clipboard_get
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


def test_desktop_discovery():
    """测试 1：自动发现 desktop 域"""
    print("\n=== Test 1: 自动发现 desktop 域 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly  # 触发懒创建 + 自动发现
    assert "desktop" in asm._registry, f"desktop 域未发现: {list(asm._registry.keys())}"
    desktop = asm._registry["desktop"]
    print(f"  ✓ 发现: {desktop}")
    names = asm.active_tool_names()
    print(f"  ✓ 工具数: {len(names)}")
    print(f"  ✓ 前 5: {names[:5]}")


def test_desktop_schemas():
    """测试 2：tools() 返回 schema"""
    print("\n=== Test 2: tools() schema ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    schemas = asm.active_tool_schemas()
    assert len(schemas) >= 30, f"schema 数量过少: {len(schemas)}"
    tool_names = {s["function"]["name"] for s in schemas}
    expected = {"os_click_xy", "os_open_path", "os_list_windows",
                "os_create_file", "os_run_command", "os_list_elements_json"}
    assert expected.issubset(tool_names), f"缺少关键工具: {expected - tool_names}"
    print(f"  ✓ schema 数: {len(schemas)}")
    print(f"  ✓ 关键工具齐全: {expected.issubset(tool_names)}")


async def test_desktop_call_tool():
    """测试 3：call_tool 调用真实工具"""
    print("\n=== Test 3: call_tool 真实调用 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    await asm.start_all()

    # 1) os_list_windows（不依赖具体窗口，只验证路径可走通）
    result = await asm.call_tool("os_list_windows", {}, timeout=10.0)
    print(f"  os_list_windows: success={result.get('success')}, "
          f"error={result.get('error', '')[:80]}")
    assert result["success"], f"os_list_windows 失败: {result}"

    # 2) os_clipboard_set + os_clipboard_get（验证两个工具串联）
    await asm.call_tool("os_clipboard_set", {"text": "v2 desktop test"}, timeout=5.0)
    result = await asm.call_tool("os_clipboard_get", {}, timeout=5.0)
    print(f"  os_clipboard_get: {result}")
    assert result["success"]
    # os_clipboard_get 返回结构是 {'success': True, 'clipboard': '...', 'method': '...'}
    inner = result["result"]
    assert inner.get("clipboard") == "v2 desktop test", f"剪贴板内容不符: {inner}"
    print(f"  ✓ 剪贴板读写一致 (method={inner.get('method')})")

    # 4) 系统 prompt fragment
    prompt = asm.build_system_prompt("[base]")
    assert "os_open_path" in prompt
    print(f"  ✓ prompt 含工具说明: 长度={len(prompt)}")

    await asm.stop_all()


def test_desktop_worlds_status():
    """测试 4：worlds_status 含 desktop"""
    print("\n=== Test 4: worlds_status ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    status = asm.worlds_status()
    desktop = next(w for w in status["worlds"] if w["id"] == "desktop")
    assert desktop["enabled"] is True
    assert desktop["tool_count"] >= 30
    print(f"  ✓ desktop: enabled={desktop['enabled']}, "
          f"tool_count={desktop['tool_count']}")


def main():
    print("=" * 60)
    print("Phase B 验证：desktop 内建域")
    print("=" * 60)
    test_desktop_discovery()
    test_desktop_schemas()
    asyncio.run(test_desktop_call_tool())
    test_desktop_worlds_status()
    print("\n" + "=" * 60)
    print("✅ Phase B 通过")
    print("=" * 60)


if __name__ == "__main__":
    main()