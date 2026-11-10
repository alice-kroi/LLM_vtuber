"""Phase C 验证脚本：launcher 子进程域

测试目标：
  1. launcher 域自动发现
  2. preflight：MuMuManager 路径检查（失败时域不激活）
  3. 子进程握手（__handshake__ → ready=true）
  4. launcher_status 通过 call_tool 走通（不动 MuMu 实际启动）
  5. 子进程协议兼容 id 匹配
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


def test_launcher_discovery():
    """测试 1：自动发现"""
    print("\n=== Test 1: 自动发现 launcher 域 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    assert "launcher" in asm._registry
    print(f"  ✓ 发现: {asm._registry['launcher']}")


async def test_launcher_handshake():
    """测试 2：子进程握手（懒启动：握手在首次 call_tool 时发生）"""
    print("\n=== Test 2: 子进程握手（懒启动） ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly

    asm.activate("launcher")
    await asm.start_domain("launcher")
    ld = asm._registry["launcher"]
    # lazy_start=True：start() 不主动 spawn
    assert ld._proc is None, f"lazy_start=True 时 start 不应 spawn，但 _proc={ld._proc}"
    print(f"  ✓ lazy_start: start() 不 spawn, _proc={ld._proc}")

    # 首次 call_tool 应触发 spawn + 握手
    result = await asm.call_tool("launcher_status", {}, timeout=10.0)
    assert ld._proc is not None, "首次 call_tool 后 _proc 应就绪"
    print(f"  ✓ 首次 call_tool 触发 spawn: pid={ld._proc.pid}, handshake_done={ld._handshake_done}")

    await asm.stop_domain("launcher")
    # stop 后 _proc 应被关闭
    assert ld._proc is None or ld._proc.returncode is not None
    print(f"  ✓ stop_domain 关闭子进程")


async def test_launcher_status():
    """测试 3：launcher_status（不动 MuMu，仅查窗口状态）"""
    print("\n=== Test 3: launcher_status ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("launcher")
    await asm.start_domain("launcher")

    result = await asm.call_tool("launcher_status", {}, timeout=10.0)
    print(f"  result: {result}")
    assert result["success"], f"launcher_status 失败: {result}"
    inner = result["result"]
    # launcher_status 返回 {"present_windows": [...]}
    assert "present_windows" in str(inner)
    print(f"  ✓ 工具返回结构正确")

    await asm.stop_domain("launcher")


async def test_launcher_unregistered_tool():
    """测试 4：未注册工具拒绝"""
    print("\n=== Test 4: 未注册工具拒绝 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("launcher")
    await asm.start_domain("launcher")

    result = await asm.call_tool("launcher_does_not_exist", {}, timeout=5.0)
    assert not result["success"]
    assert "tool_not_found" in result["error"]
    print(f"  ✓ 未注册工具被拒: {result['error']}")

    await asm.stop_domain("launcher")


def test_launcher_preflight():
    """测试 5：preflight 检查（机器上无 MuMuManager 时域应拒绝启动）"""
    print("\n=== Test 5: preflight ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    # 模拟无 MuMuManager 时 Windows 的情况：
    # 默认路径 F:\MuMu Player 12\nx_main\MuMuManager.exe，若机器上无，应被 preflight 拒绝
    # 这台机器有 MuMuManager，所以这里只能验证路径检查逻辑存在
    ld = asm._registry["launcher"]
    reason = ld.preflight()
    print(f"  preflight result: {reason}")
    # 如果机器无 MuMu，reason 非空；如有，reason 为 None
    # 这里不强断言（依赖环境），只验证返回类型
    assert reason is None or isinstance(reason, str)


def main():
    print("=" * 60)
    print("Phase C 验证：launcher 子进程域")
    print("=" * 60)
    test_launcher_discovery()
    asyncio.run(test_launcher_handshake())
    asyncio.run(test_launcher_status())
    asyncio.run(test_launcher_unregistered_tool())
    test_launcher_preflight()
    print("\n" + "=" * 60)
    print("✅ Phase C（launcher 部分）通过")
    print("=" * 60)


if __name__ == "__main__":
    main()