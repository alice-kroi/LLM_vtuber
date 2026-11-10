"""验证 tts / live2d 能力域架构接入

验证点：
  1. 自动发现：tts / live2d 域被 _discover_domains 挂载
  2. 契约校验：Actuator 类型（有工具、无 inbound 事件）通过 register
  3. 默认不启动：default_enabled=False → 不在 _active
  4. start_domain：注入 bus + 注册事件监听器（TTS_SPEAK / LIVE2D_EXPRESSION / LIVE2D_MOTION）
  5. 工具可发现：tts_status / live2d_status 等 schema 聚合
  6. 热拔插：activate → start → stop → deactivate 全流程
  7. 事件路由：outbound_event_types 声明的消费关系
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime
from LLM_v2.events import EVENTS


def _reset_runtime():
    PersonaRuntime._instance = None


def test_discovery():
    print("\n=== Test 1: 自动发现 tts / live2d 域 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    assert "tts" in asm._registry, f"tts 域未发现: {list(asm._registry.keys())}"
    assert "live2d" in asm._registry, f"live2d 域未发现: {list(asm._registry.keys())}"
    print(f"  ✓ tts: {asm._registry['tts']}")
    print(f"  ✓ live2d: {asm._registry['live2d']}")
    # 默认不启动
    assert "tts" not in asm._active
    assert "live2d" not in asm._active
    print("  ✓ 默认 default_enabled=False，未激活")


def test_tools_schema():
    print("\n=== Test 2: 工具 schema 聚合 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    asm.activate("tts")
    asm.activate("live2d")
    schemas = asm.active_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "tts_status" in names and "tts_speak" in names and "tts_stop" in names
    assert "live2d_status" in names and "live2d_set_expression" in names
    assert "live2d_trigger_motion" in names
    print(f"  ✓ tts 工具: {[n for n in names if n.startswith('tts_')]}")
    print(f"  ✓ live2d 工具: {[n for n in names if n.startswith('live2d_')]}")
    asm.deactivate("tts")
    asm.deactivate("live2d")


async def test_start_event_listeners():
    print("\n=== Test 3: start_domain 注册事件监听器 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    await runtime.bus.start()

    # tts 域
    await asm.start_domain("tts")
    tts = asm._registry["tts"]
    assert getattr(tts, "bus", None) is not None, "tts 域未注入 bus"
    assert runtime.bus.listener_count(EVENTS.TTS_SPEAK) >= 1
    print(f"  ✓ tts 域注入 bus + 注册 TTS_SPEAK 监听 (count={runtime.bus.listener_count(EVENTS.TTS_SPEAK)})")
    assert "tts" in asm._started
    await asm.stop_domain("tts")

    # live2d 域
    await asm.start_domain("live2d")
    l2d = asm._registry["live2d"]
    assert getattr(l2d, "bus", None) is not None
    assert runtime.bus.listener_count(EVENTS.LIVE2D_EXPRESSION) >= 1
    assert runtime.bus.listener_count(EVENTS.LIVE2D_MOTION) >= 1
    print(f"  ✓ live2d 域注入 bus + 注册 LIVE2D_EXPRESSION/MOTION 监听 "
          f"(count={runtime.bus.listener_count(EVENTS.LIVE2D_EXPRESSION)}/{runtime.bus.listener_count(EVENTS.LIVE2D_MOTION)})")
    await asm.stop_domain("live2d")

    # stop 后监听器应注销
    assert runtime.bus.listener_count(EVENTS.TTS_SPEAK) == 0, "tts 监听未注销"
    assert runtime.bus.listener_count(EVENTS.LIVE2D_EXPRESSION) == 0
    print("  ✓ stop 后监听器已注销")

    await runtime.bus.stop()


async def test_tool_call_tts_status():
    """tts_status 走 call_tool（纯本地状态查询，无网络副作用）"""
    print("\n=== Test 4: call_tool tts_status ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    await asm.start_domain("tts")
    result = await asm.call_tool("tts_status", {}, timeout=10.0)
    print(f"  result: {result}")
    assert result["success"], f"tts_status 调用失败: {result}"
    await asm.stop_domain("tts")


def test_event_route_decl():
    print("\n=== Test 5: 事件路由声明 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly
    tts = asm._registry["tts"]
    l2d = asm._registry["live2d"]
    assert EVENTS.TTS_SPEAK in tts.outbound_event_types()
    assert EVENTS.LIVE2D_EXPRESSION in l2d.outbound_event_types()
    assert EVENTS.LIVE2D_MOTION in l2d.outbound_event_types()
    print(f"  ✓ tts outbound: {tts.outbound_event_types()}")
    print(f"  ✓ live2d outbound: {l2d.outbound_event_types()}")


def main():
    print("=" * 60)
    print("  V2 能力域接入验证：tts / live2d")
    print("=" * 60)
    test_discovery()
    test_tools_schema()
    asyncio.run(test_start_event_listeners())
    asyncio.run(test_tool_call_tts_status())
    test_event_route_decl()
    print("\n" + "=" * 60)
    print("✅ tts / live2d 能力域接入验证通过")
    print("=" * 60)


if __name__ == "__main__":
    main()
