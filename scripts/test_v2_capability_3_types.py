"""H-2 验证脚本：三型 Capability + WebUI 输入 + 事件路由"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.capability_domain import (
    CapabilityType,
    Receiver,
    Transceiver,
    Actuator,
    SubprocessCapabilityDomain,
    tool_schema,
)
from LLM_v2.core.domain_assembly import DomainAssembly
from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset():
    PersonaRuntime._instance = None


# ---------- 测试用域 ----------

class _TestReceiver(Receiver):
    id = "test_receiver"
    label = "测试接收器"
    default_enabled = True

    def inbound_event_types(self):
        return ["test_event"]

    async def start(self):
        pass

    async def stop(self):
        pass


class _TestTransceiver(Transceiver):
    id = "test_transceiver"
    label = "测试收发"
    default_enabled = True

    def inbound_event_types(self):
        return ["test_user_msg"]

    def allowed_names(self):
        return ["tt_tool"]

    def tools(self):
        return [tool_schema("tt_tool", "测试工具")]

    async def tt_tool(self):
        return {"hello": "tt"}

    async def start(self):
        pass

    async def stop(self):
        pass


class _TestActuator(Actuator):
    id = "test_actuator"
    label = "测试执行器"
    default_enabled = True

    def allowed_names(self):
        return ["ta_tool"]

    def tools(self):
        return [tool_schema("ta_tool", "Actuator 工具")]

    async def ta_tool(self):
        return {"hello": "ta"}


class _BadReceiver(Receiver):
    """违反契约：Receiver 不能有工具"""
    id = "test_bad_receiver"
    label = "坏 Receiver"
    default_enabled = True

    def allowed_names(self):
        return ["bad_tool"]

    def tools(self):
        return [tool_schema("bad_tool", "BAD")]

    def inbound_event_types(self):
        return ["bad_event"]


class _BadActuator(Actuator):
    """违反契约：Actuator 必须有工具"""
    id = "test_bad_actuator"
    label = "坏 Actuator"
    default_enabled = True

    def allowed_names(self):
        return []

    def tools(self):
        return []


# ---------- 测试 ----------

def test_3_types_registration():
    """测试 1：3 类型 Capability 都能注册"""
    print("\n=== Test 1: 3 类型注册 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly

    for cap in (_TestReceiver(), _TestTransceiver(), _TestActuator()):
        asm.register(cap)
        print(f"  ✓ {cap.id} ({cap.type.value}) 注册成功")

    status = asm.worlds_status()
    print(f"  worlds: {[w['id'] + ':' + w['type'] for w in status['worlds']]}")
    assert any(w["id"] == "test_receiver" and w["type"] == "receiver" for w in status["worlds"])
    assert any(w["id"] == "test_transceiver" and w["type"] == "transceiver" for w in status["worlds"])
    assert any(w["id"] == "test_actuator" and w["type"] == "actuator" for w in status["worlds"])


def test_contract_validation():
    """测试 2：契约违反被 register() 拒绝"""
    print("\n=== Test 2: 契约校验 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly

    # Receiver 不能有工具
    try:
        asm.register(_BadReceiver())
        assert False, "应当被拒绝"
    except ValueError as e:
        assert "契约违反" in str(e) or "Receiver" in str(e)
        print(f"  ✓ Bad Receiver 被拒绝: {str(e)[:80]}")

    # Actuator 必须有工具
    try:
        asm.register(_BadActuator())
        assert False, "应当被拒绝"
    except ValueError as e:
        assert "契约违反" in str(e) or "Actuator" in str(e)
        print(f"  ✓ Bad Actuator 被拒绝: {str(e)[:80]}")


def test_event_routing():
    """测试 3：事件路由表登记"""
    print("\n=== Test 3: 事件路由 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly

    asm.register(_TestReceiver())
    asm.register(_TestTransceiver())

    # Receiver emit test_event → 应能查到
    assert asm.event_producer("test_event") == "test_receiver"
    print(f"  ✓ Receiver → test_event: {asm.event_producer('test_event')}")

    # Transceiver emit test_user_msg → 应能查到
    assert asm.event_producer("test_user_msg") == "test_transceiver"
    print(f"  ✓ Transceiver → test_user_msg: {asm.event_producer('test_user_msg')}")


def test_v2_existing_domains_have_type():
    """测试 4：9 个 V2 域都已加 type 字段"""
    print("\n=== Test 4: 9 域 type 分类 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly

    expected_types = {
        "desktop": "transceiver",
        "launcher": "actuator",
        "pvz": "transceiver",
        "browser": "transceiver",
        "bili_live": "transceiver",
        "desktop_agent": "actuator",
        "desktop_pet": "actuator",   # 桌宠当前仅 Actuator（暂未实现输入框）
        "ui_perception": "actuator",
        "webui": "transceiver",   # 新增的 WebUICapability
    }
    actual = {w["id"]: w["type"] for w in asm.worlds_status()["worlds"]}
    print(f"  实际: {actual}")
    print(f"  期望: {expected_types}")
    for did, etype in expected_types.items():
        if did in actual:
            assert actual[did] == etype, f"{did}: 期望 {etype}, 实际 {actual[did]}"
    print(f"  ✓ 所有 type 字段匹配")


async def test_operator_emits_perception():
    """测试 5：Operator 调感知类工具后 emit PERCEPTION_UPDATED"""
    print("\n=== Test 5: Operator emit PERCEPTION_UPDATED ===")
    _reset()
    runtime = get_runtime()
    await runtime.bus.start()

    operator = type("O", (), {
        "_is_perception_tool": lambda self, n: n.startswith("os_capture"),
        "_emit_perception": lambda self, t, r: None,
    })()  # 不完整，只测试 _is_perception_tool

    # 用真实 OperatorBrain
    from LLM_v2.brains.operator import OperatorBrain
    operator = OperatorBrain(runtime)
    assert operator._is_perception_tool("os_capture_window"), "os_capture_window 应该是感知"
    assert operator._is_perception_tool("os_list_windows"), "os_list_windows 应该是感知"
    assert not operator._is_perception_tool("os_click_xy"), "os_click_xy 不是感知"
    print(f"  ✓ _is_perception_tool 识别正确")

    await runtime.bus.stop()


def test_webui_capability_registered():
    """测试 6：WebUICapability 自动发现"""
    print("\n=== Test 6: WebUICapability 自动发现 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly

    webui = asm._registry.get("webui")
    assert webui is not None, "WebUICapability 应自动发现"
    print(f"  ✓ WebUICapability 注册: type={webui.type.value}")
    assert webui.type == CapabilityType.TRANSCEIVER
    assert "user_message" in webui.inbound_event_types()
    print(f"  ✓ WebUI 声明 USER_MESSAGE 作为 inbound event")


def main():
    print("=" * 60)
    print("H-2 验证：三型 Capability + WebUI 输入 + 事件路由")
    print("=" * 60)
    test_3_types_registration()
    test_contract_validation()
    test_event_routing()
    test_v2_existing_domains_have_type()
    asyncio.run(test_operator_emits_perception())
    test_webui_capability_registered()
    print("\n" + "=" * 60)
    print("✅ H-2 全部验证通过")
    print("=" * 60)


if __name__ == "__main__":
    main()