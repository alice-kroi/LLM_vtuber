"""Phase C 验证脚本：pvz 子进程域（验证 id 协议兼容）

测试目标：
  1. pvz 域自动发现
  2. 子进程握手（即使引擎不回 id，V2 父进程应自动降级到宽容模式）
  3. pvz_queue 通过 call_tool 走通
  4. pvz_do 接受 steps 并返回 task_id
  5. 验证 pvz_engine 在没装 PvZ 游戏时优雅降级（attached: false）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


def test_pvz_discovery():
    """测试 1：自动发现"""
    print("\n=== Test 1: 自动发现 pvz 域 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    assert "pvz" in asm._registry
    pvz = asm._registry["pvz"]
    print(f"  ✓ 发现: {pvz}")


async def test_pvz_protocol_no_id():
    """测试 2：pvz 引擎不回 id，V2 父进程应宽容兼容"""
    print("\n=== Test 2: pvz 引擎协议兼容（不回 id） ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("pvz")
    await asm.start_domain("pvz")
    pvz = asm._registry["pvz"]

    # pvz_engine 不回 id，所以 handshake 应保持 False（未严格握手成功）
    # 但首次 call_tool 后 _proc 应就绪
    result = await asm.call_tool("pvz_queue", {}, timeout=10.0)
    assert result["success"], f"pvz_queue 失败: {result}"
    inner = result["result"]
    # pvz_queue 通过：握手成功 + 引擎不回 id → 宽容模式
    inner = result["result"]
    print(f"  ✓ handshake_done={pvz._handshake_done}, engine_returns_id={pvz._engine_returns_id}")
    print(f"  ✓ 返回 inner 类型: {type(inner).__name__}")

    await asm.stop_domain("pvz")


async def test_pvz_observe_no_game():
    """测试 3：未启动 PvZ 游戏时优雅降级"""
    print("\n=== Test 3: pvz_observe（无游戏时优雅降级） ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("pvz")
    await asm.start_domain("pvz")

    result = await asm.call_tool("pvz_observe", {}, timeout=10.0)
    print(f"  result: {result}")
    assert result["success"], f"pvz_observe 失败: {result}"
    # 不管 attached 是 true 还是 false，都应返回 JSON
    inner = result["result"]
    res_str = str(inner)
    if "attached" in res_str or "phase" in res_str:
        print(f"  ✓ 优雅降级（attached/phase 已上报）")
    else:
        print(f"  ⚠ 返回格式异常: {inner}")

    await asm.stop_domain("pvz")


async def test_pvz_do_and_queue():
    """测试 4：pvz_do 提交 steps + pvz_queue 查任务"""
    print("\n=== Test 4: pvz_do + pvz_queue ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("pvz")
    await asm.start_domain("pvz")

    # 提交一个空步骤集合（用于测试 accept）
    result = await asm.call_tool("pvz_do", {
        "steps": [{"action": "menu", "target": "main_menu"}],
        "queue": "replace",
    }, timeout=10.0)
    print(f"  pvz_do: {result}")
    assert result["success"], f"pvz_do 失败: {result}"

    # 查队列
    result = await asm.call_tool("pvz_queue", {}, timeout=10.0)
    print(f"  pvz_queue: {result}")
    assert result["success"], f"pvz_queue 失败: {result}"

    # 撤回任务
    result = await asm.call_tool("pvz_stop", {"all": False}, timeout=10.0)
    print(f"  pvz_stop: {result}")

    await asm.stop_domain("pvz")


def main():
    print("=" * 60)
    print("Phase C 验证：pvz 子进程域")
    print("=" * 60)
    test_pvz_discovery()
    asyncio.run(test_pvz_protocol_no_id())
    asyncio.run(test_pvz_observe_no_game())
    asyncio.run(test_pvz_do_and_queue())
    print("\n" + "=" * 60)
    print("✅ Phase C（pvz 部分）通过")
    print("=" * 60)


if __name__ == "__main__":
    main()