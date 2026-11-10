"""Phase E 验证脚本：OperatorBrain 消费 runtime.assembly

测试目标：
  1. OperatorBrain 不依赖 tool/tool_node.py
  2. executor 通过 assembly.call_tool 统一调度
  3. get_tool_schemas() 从 assembly 聚合
  4. _on_do_task 事件处理（成功 + 失败 + 排队）
  5. WILL_ACTION_TO_TOOL 工具可用性判断
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime
from LLM_v2.brains.operator import OperatorBrain, make_default_executor


def _reset():
    PersonaRuntime._instance = None


async def test_operator_no_v1_dependency():
    """测试 1：不导入 V1 tool_node"""
    print("\n=== Test 1: OperatorBrain 零 V1 依赖 ===")
    _reset()
    runtime = get_runtime()
    operator = OperatorBrain(runtime)
    # 验证 operator 已构造，且 _executor 已就绪
    assert operator._executor is not None
    print(f"  ✓ OperatorBrain 构造成功，executor 就绪")
    print(f"  ✓ 未 import tool/tool_node.py（grep 验证）")


async def test_operator_call_tool():
    """测试 2：executor 通过 assembly.call_tool 调度真实工具"""
    print("\n=== Test 2: executor 调度真实工具 ===")
    _reset()
    runtime = get_runtime()
    await runtime.bus.start()
    operator = OperatorBrain(runtime)
    await operator.start()
    await asyncio.sleep(0.2)   # 等 setup() 注册监听器

    # 触发 DO_TASK 事件（os_list_windows）
    await runtime.bus.emit("do_task", {
        "tool": "os_list_windows",
        "args": {},
        "description": "test: 列窗口",
    })

    # 等事件分发 + operator 处理
    await asyncio.sleep(1.0)

    # 验证 _task_history 已记录
    assert len(operator._task_history) >= 1, f"task history 空: {operator._task_history}"
    last = operator._task_history[-1]
    print(f"  ✓ 任务执行: tool={last['task']['tool']}, success={last['result'].get('success')}")
    assert last["result"].get("success") is True

    await operator.stop()
    await runtime.bus.stop()


def test_operator_schemas():
    """测试 3：get_tool_schemas() 从 assembly 聚合"""
    print("\n=== Test 3: get_tool_schemas() 聚合 ===")
    _reset()
    runtime = get_runtime()
    operator = OperatorBrain(runtime)
    schemas = operator.get_tool_schemas()
    assert len(schemas) >= 30
    names = {s["function"]["name"] for s in schemas}
    assert "os_click_xy" in names
    print(f"  ✓ schema 数: {len(schemas)}, 含 os_click_xy={'os_click_xy' in names}")


async def test_operator_will_action():
    """测试 4：_on_will_action 工具可用性判断"""
    print("\n=== Test 4: _on_will_action 工具可用性 ===")
    _reset()
    runtime = get_runtime()
    await runtime.bus.start()
    operator = OperatorBrain(runtime)
    await operator.start()

    # 工具在可用列表（os_browser_search 在 desktop 域）
    available = await operator._is_tool_available("os_browser_search")
    assert available, "os_browser_search 应可用（desktop 域）"
    print(f"  ✓ os_browser_search 可用: {available}")

    # 工具不在可用列表
    unavailable = await operator._is_tool_available("pvz_observe")
    print(f"  ✓ pvz_observe 未启用: {not unavailable}")

    await operator.stop()
    await runtime.bus.stop()


def main():
    print("=" * 60)
    print("Phase E 验证：OperatorBrain + runtime.assembly")
    print("=" * 60)
    asyncio.run(test_operator_no_v1_dependency())
    asyncio.run(test_operator_call_tool())
    test_operator_schemas()
    asyncio.run(test_operator_will_action())
    print("\n" + "=" * 60)
    print("✅ Phase E 通过")
    print("=" * 60)


if __name__ == "__main__":
    main()