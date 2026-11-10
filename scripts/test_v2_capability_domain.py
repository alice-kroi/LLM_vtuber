"""Phase A 验证脚本：CapabilityDomain + DomainAssembly 单元测试

测试目标：
  1. CapabilityDomain ABC 契约（allowed_names/tools/preflight/start/stop）
  2. SubprocessCapabilityDomain 协议握手 + id 兼容
  3. DomainAssembly register / activate / 冲突检测 / 保留字
  4. DomainAssembly.start_all 单域失败不影响其它
  5. DomainAssembly.call_tool 统一调度 + 反向索引
  6. 域配置加载/持久化
  7. 自动发现（无域时返回空列表）
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.capability_domain import (
    CapabilityDomain,
    CapabilityType,
    SubprocessCapabilityDomain,
    tool_schema,
)
from LLM_v2.core.domain_assembly import DomainAssembly
from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


# ---------- 测试用域 ----------

class _GoodDomain(CapabilityDomain):
    id = "test_good"
    label = "测试域（正常）"
    prefix = "tg_"
    default_enabled = True
    type = CapabilityType.ACTUATOR   # H-2: 纯工具域

    def allowed_names(self):
        return ["tg_tool_a", "tg_tool_b"]

    def tools(self):
        return [
            tool_schema("tg_tool_a", "测试工具 A", {"x": {"type": "integer"}}, required=["x"]),
            tool_schema("tg_tool_b", "测试工具 B"),
        ]

    def system_prompt_fragment(self):
        return "[test_good fragment]"

    async def tg_tool_a(self, x: int) -> dict:
        return {"x": x, "doubled": x * 2}

    def tg_tool_b(self) -> dict:
        return {"hello": "world"}


class _BadPreflightDomain(CapabilityDomain):
    id = "test_preflight_fail"
    label = "测试域（preflight 失败）"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    def allowed_names(self):
        return ["tp_tool"]

    def tools(self):
        return [tool_schema("tp_tool", "前置检查失败工具")]

    def preflight(self):
        return "依赖未就绪：mock failure"

    async def start(self):
        raise RuntimeError("不应被 start")


class _ClashDomainA(CapabilityDomain):
    id = "test_clash_a"
    label = "测试域 A"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    def allowed_names(self):
        return ["shared_name"]
    def tools(self):
        return [tool_schema("shared_name", "共名工具")]


class _ClashDomainB(CapabilityDomain):
    id = "test_clash_b"
    label = "测试域 B"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    def allowed_names(self):
        return ["shared_name"]   # 与 A 撞名
    def tools(self):
        return [tool_schema("shared_name", "共名工具")]


class _ReservedDomain(CapabilityDomain):
    id = "test_reserved"
    label = "测试域（占保留字）"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    def allowed_names(self):
        return ["world_list"]   # reserved
    def tools(self):
        return [tool_schema("world_list", "占保留字")]


class _BrokenInitDomain(CapabilityDomain):
    id = "test_broken_init"
    label = "测试域（构造失败）"
    default_enabled = True
    type = CapabilityType.ACTUATOR
    def allowed_names(self):
        raise RuntimeError("构造失败")
    def tools(self):
        return []


def _reset_runtime():
    """重置单例（测试用）"""
    PersonaRuntime._instance = None


# ---------- 测试用例 ----------

def test_1_basic_register():
    """测试 1：基本 register / activate / 聚合"""
    print("\n=== Test 1: 基本 register / activate ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    good = _GoodDomain()
    asm.register(good)
    assert good.id in asm._registry
    assert good.id in asm._active
    names = asm.active_tool_names()
    assert "tg_tool_a" in names
    assert "tg_tool_b" in names
    schemas = asm.active_tool_schemas()
    assert len(schemas) == 2
    print(f"  ✓ register OK: {asm._registry}")
    print(f"  ✓ active names: {names}")
    return asm


def test_2_tool_clash():
    """测试 2：toolClash 检测"""
    print("\n=== Test 2: toolClash 检测 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    asm.register(_ClashDomainA())
    try:
        asm.register(_ClashDomainB())
        assert False, "应当抛出 ValueError"
    except ValueError as e:
        assert "冲突" in str(e), f"异常信息不符: {e}"
        print(f"  ✓ clash 检测通过: {e}")
    # missing 表应有记录
    assert any(m["stage"] == "tool_clash" for m in asm._missing)
    print(f"  ✓ missing 记录: {[m for m in asm._missing if m['stage']=='tool_clash']}")


def test_3_reserved_name():
    """测试 3：保留字占用拒绝"""
    print("\n=== Test 3: 保留字占用 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=["world_list"])
    try:
        asm.register(_ReservedDomain())
        assert False, "应当抛出 ValueError"
    except ValueError as e:
        assert "保留" in str(e) or "reserved" in str(e).lower()
        print(f"  ✓ reserved 拒绝: {e}")
    assert any(m["stage"] == "reserved" for m in asm._missing)
    print(f"  ✓ missing 记录: {[m for m in asm._missing if m['stage']=='reserved']}")


def test_4_preflight_rejection():
    """测试 4：preflight 失败拒绝激活"""
    print("\n=== Test 4: preflight 拒绝 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    bad = _BadPreflightDomain()
    # 先 register（preflight 不影响 register）
    asm.register(bad)
    assert bad.id in asm._registry
    assert bad.id in asm._active
    # start_domain 时 preflight 应拒绝
    async def _try_start():
        try:
            await asm.start_domain(bad.id)
            return False
        except ValueError as e:
            return str(e)
    err = asyncio.run(_try_start())
    assert err and "前置检查" in err, f"preflight 错误不符: {err}"
    print(f"  ✓ preflight 拒绝: {err}")
    # start_domain 失败后该域应被 deactivate
    assert bad.id not in asm._active
    print(f"  ✓ 失败域已 deactivate")


async def test_5_call_tool_async():
    """测试 5：call_tool 统一调度（异步域）"""
    print("\n=== Test 5: call_tool 调度 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    good = _GoodDomain()
    asm.register(good)
    await asm.start_all()

    # 测试异步工具 tg_tool_a
    result = await asm.call_tool("tg_tool_a", {"x": 21}, timeout=5.0)
    assert result["success"], f"call_tool 失败: {result}"
    assert result["result"]["doubled"] == 42
    print(f"  ✓ tg_tool_a: {result}")

    # 测试同步工具 tg_tool_b
    result = await asm.call_tool("tg_tool_b", {}, timeout=5.0)
    assert result["success"], f"call_tool 失败: {result}"
    print(f"  ✓ tg_tool_b: {result}")

    # 测试未注册工具
    result = await asm.call_tool("unknown_tool", {}, timeout=5.0)
    assert not result["success"]
    assert "tool_not_found" in result["error"]
    print(f"  ✓ 未注册工具: {result}")

    await asm.stop_all()


def test_6_start_all_isolation():
    """测试 6：start_all 单域失败不影响其它"""
    print("\n=== Test 6: start_all 单域隔离 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    good = _GoodDomain()
    bad = _BadPreflightDomain()
    asm.register(good)
    asm.register(bad)
    asyncio.run(asm.start_all())
    # good 应已 start，bad 应被 deactivate（preflight 拒绝）
    assert good.id in asm._started, "good 应当 start 成功"
    assert bad.id not in asm._active, "bad 应被 preflight 拒绝"
    print(f"  ✓ good.started={good.id in asm._started}, "
          f"bad.active={bad.id in asm._active}")
    asyncio.run(asm.stop_all())


def test_7_worlds_status():
    """测试 7：worlds_status 快照"""
    print("\n=== Test 7: worlds_status ===")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(runtime=runtime, reserved=[])
    asm.register(_GoodDomain())
    # _BrokenInitDomain.allowed_names() 抛错 → register 记录 missing 后 re-raises（对齐 V1）
    try:
        asm.register(_BrokenInitDomain())
    except RuntimeError as e:
        assert "构造失败" in str(e)
        print(f"  ✓ broken domain register 抛错（预期）: {e}")
    status = asm.worlds_status()
    assert "worlds" in status
    assert "missing" in status
    assert any(m["stage"] == "allowed_names" for m in status["missing"])
    print(f"  ✓ worlds: {len(status['worlds'])}, missing: {len(status['missing'])}")
    print(f"  ✓ status: {status}")


def test_8_persona_runtime_assembly():
    """测试 8：runtime.assembly 懒创建"""
    print("\n=== Test 8: runtime.assembly 懒创建 ===")
    _reset_runtime()
    runtime = get_runtime()
    asm1 = runtime.assembly
    asm2 = runtime.assembly
    assert asm1 is asm2, "应返回同一实例"
    assert hasattr(runtime, "_assembly")
    print(f"  ✓ 同一实例: id={id(asm1)} == id={id(asm2)}")
    # 自动发现已生效（V2 阶段所有 LLM_v2/domains/<id>/ 自动挂载 + webui 新增）
    assert len(asm1._registry) == 9, f"应自动发现 9 个域，实际: {len(asm1._registry)}"
    print(f"  ✓ 自动发现: registry={list(asm1._registry.keys())}")


def test_9_subprocess_lazy_start():
    """Test 9: 子进程域懒启动路径（仅验证 lazy_start 标记，未真起子进程）"""
    print("\n=== Test 9: SubprocessCapabilityDomain lazy_start ===")
    class _StubSub(SubprocessCapabilityDomain):
        id = "test_stub_sub"
        label = "测试子进程域"
        default_enabled = True
        lazy_start = True
        def allowed_names(self):
            return ["stub_method"]
        def tools(self):
            return [tool_schema("stub_method", "stub")]
        def launch_args(self):
            return ["-c", "print('stub')"]

    sub = _StubSub()
    # 不应主动 spawn（lazy_start=True）
    asyncio.run(sub.start())
    assert sub._proc is None, "lazy_start=True 时 start 不应 spawn"
    print(f"  ✓ lazy_start 不 spawn: _proc={sub._proc}")
    # 异步 stop 也不应抛错
    asyncio.run(sub.stop())
    print(f"  ✓ stop 无抛错")


def test_10_config_persistence(tmp_path):
    """Test 10: 配置加载/持久化"""
    print("\n=== Test 10: config 持久化 ===")
    cfg_path = tmp_path / "domains.json"
    cfg_path.write_text('{"test_good": {"enabled": false}}', encoding="utf-8")
    _reset_runtime()
    runtime = get_runtime()
    asm = DomainAssembly(
        runtime=runtime, reserved=[],
        config_path=str(cfg_path),
    )
    good = _GoodDomain()
    asm.register(good)
    assert good.id not in asm._active, "配置文件 enabled=false 应禁用域"
    print(f"  ✓ config 加载: good.active={good.id in asm._active}")

    # persist
    res = asm.persist_world_config("test_good", {"enabled": True})
    assert res["success"]
    # 重新加载验证
    cfg2 = tmp_path / "domains.json"
    assert cfg2.read_text(encoding="utf-8").find('"enabled": true') >= 0
    print(f"  ✓ config 持久化: {cfg2.read_text(encoding='utf-8')}")


# ---------- main ----------

def main():
    """运行所有测试"""
    print("=" * 60)
    print("Phase A 单元测试：CapabilityDomain + DomainAssembly")
    print("=" * 60)
    import tempfile
    tmp = Path(tempfile.mkdtemp())

    test_1_basic_register()
    test_2_tool_clash()
    test_3_reserved_name()
    test_4_preflight_rejection()
    asyncio.run(test_5_call_tool_async())
    test_6_start_all_isolation()
    test_7_worlds_status()
    test_8_persona_runtime_assembly()
    test_9_subprocess_lazy_start()
    test_10_config_persistence(tmp)

    print("\n" + "=" * 60)
    print("✅ 全部测试通过（10 个）")
    print("=" * 60)


if __name__ == "__main__":
    main()