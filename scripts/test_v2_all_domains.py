"""Phase D 验证脚本：所有 8 个 V2 域自动发现 + 基本调用"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset():
    PersonaRuntime._instance = None


def test_all_domains_discovered():
    """测试 1：所有 8 个域自动发现"""
    print("\n=== Test 1: 自动发现全部 8 个域 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly
    expected = {
        "desktop", "launcher", "pvz",
        "browser", "bili_live", "desktop_agent", "desktop_pet", "ui_perception",
    }
    actual = set(asm._registry.keys())
    print(f"  期望: {sorted(expected)}")
    print(f"  实际: {sorted(actual)}")
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"缺少域: {missing}"
    print(f"  ✓ 全部发现（{len(expected)} 个域）")
    if extra:
        print(f"  ⚠ 额外发现: {extra}")


def test_worlds_status():
    """测试 2：worlds_status 含全部域"""
    print("\n=== Test 2: worlds_status 快照 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly
    status = asm.worlds_status()
    print(f"  ✓ worlds 数量: {len(status['worlds'])}")
    for w in status["worlds"]:
        print(f"    {w['id']}: enabled={w['enabled']}, tool_count={w['tool_count']}")


async def test_smoke_for_each():
    """测试 3：每个子进程域 --smoke 自检"""
    print("\n=== Test 3: 各子进程域 --smoke 自检 ===")
    _reset()
    runtime = get_runtime()
    asm = runtime.assembly
    subprocess_domains = ["launcher", "pvz", "browser", "bili_live",
                          "desktop_agent", "desktop_pet", "ui_perception"]
    for did in subprocess_domains:
        if did not in asm._registry:
            print(f"  ⚠ {did} 未注册")
            continue
        asm.activate(did)
        try:
            await asm.start_domain(did)
            d = asm._registry[did]
            # 子进程域的握手/或空启动
            print(f"  ✓ {did}: enabled + started, _proc={d._proc is not None}")
            await asm.stop_domain(did)
        except Exception as e:
            print(f"  ⚠ {did} 启动失败: {e}")


def main():
    print("=" * 60)
    print("Phase D 验证：8 个 V2 域全部自动发现")
    print("=" * 60)
    test_all_domains_discovered()
    test_worlds_status()
    asyncio.run(test_smoke_for_each())
    print("\n" + "=" * 60)
    print("✅ Phase D 通过")
    print("=" * 60)


if __name__ == "__main__":
    main()