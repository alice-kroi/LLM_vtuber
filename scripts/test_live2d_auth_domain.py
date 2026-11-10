"""Live2D 能力域认证链路测试：确认插件请求真实发到 VTS

链路：DomainAssembly.start_domain("live2d") → Live2DDomain
     → _ensure_connected() → VTubeStudioAPI
       → connect() → request_auth_token()（VTS 弹窗授权）→ authenticate()

注意：request_auth_token 需要用户在 VTS 弹出的授权框中点击"允许"，
脚本会等待至多 40s 让用户操作。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


async def test_live2d_auth():
    print("=" * 70)
    print("  Live2D 能力域认证链路测试（VTS 插件请求）")
    print("=" * 70)
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly

    await asm.start_domain("live2d")
    l2d = asm._registry["live2d"]
    print(f"\n[1] live2d 域已激活: {l2d}")
    print("    开始连接 VTS:8001 ...")

    # 直接调用认证（会走 connect → request_auth_token → authenticate）
    connected = await l2d._ensure_connected()
    print(f"\n[2] _ensure_connected = {connected}")

    if not connected:
        print("\n[FAIL] 认证未完成。可能原因：")
        print("  - VTS 授权弹窗被忽略/拒绝")
        print("  - 之前已授权过但 token 未保存（需重启 VTS 或手动重新授权）")
    else:
        print("[OK] 认证成功，插件请求已真实发到 VTS")

        # 查询状态
        status = await l2d.live2d_status()
        print(f"\n[3] live2d_status: {status}")

        # call_tool 走统一入口
        print("\n[4] call_tool live2d_status:")
        result = await asm.call_tool("live2d_status", {}, timeout=10.0)
        print(f"    {result}")

    await asm.stop_domain("live2d")
    print("\n[完成] live2d 认证链路测试结束")


asyncio.run(test_live2d_auth())
