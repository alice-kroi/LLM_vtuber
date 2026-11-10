"""TTS API 生成测试：通过 tts 能力域 → doubao 引擎（火山 V3 API）实际合成语音

链路：DomainAssembly.call_tool("tts_speak") → TTSDomain → resolve_engine("auto")
→ DoubaoEngine.astream()（火山 V3 单向流式）→ stream_player 播放 + 保存 wav

本脚本：
  1. 激活 tts 域
  2. call_tool 查 tts_status（确认引擎）
  3. 直接调用引擎 synthesize 保存 wav（避免真实声卡播放，适合验证）
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import get_runtime, PersonaRuntime


def _reset_runtime():
    PersonaRuntime._instance = None


async def test_tts_api():
    print("=" * 70)
    print("  TTS API 生成测试（doubao 火山 V3）")
    print("=" * 70)
    _reset_runtime()
    runtime = get_runtime()
    asm = runtime.assembly

    # 激活 tts 域
    await asm.start_domain("tts")
    tts = asm._registry["tts"]
    print(f"\n[1] tts 域已激活: {tts}")

    # 引擎探测
    tts._load_engine()
    eng = tts._engine
    if eng is None:
        print("[FAIL] 无可用 TTS 引擎（检查 .env：TTS_KEY / DOUBAO_API_KEY）")
        return
    print(f"[2] 引擎: {eng.name} / {eng.display_name} / available={eng.available()}")

    # 用引擎 synthesize 直接合成（保存 wav，不走声卡）
    text = "你好，我是小鲤，正在测试语音合成能力。"
    print(f"\n[3] 合成文本: {text}")
    result = await eng.synthesize(text, tone="happy")
    if not result.success:
        print(f"[FAIL] 合成失败: {result.error}")
        return

    print(f"    success={result.success} format={result.format} "
          f"rate={result.sample_rate} bytes={len(result.audio_data)} "
          f"duration={result.duration:.1f}s")

    # 保存 wav（PCM → WAV 封装）
    out = Path("scripts/out_tts_api_test.wav")
    if result.format == "pcm":
        import struct
        data = result.audio_data
        # 写 WAV 头（16bit mono）
        hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        hdr += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, result.sample_rate,
                                     result.sample_rate * 2, 2, 16)
        hdr += b"data" + struct.pack("<I", len(data))
        out.write_bytes(hdr + data)
    else:
        out.write_bytes(result.audio_data)
    print(f"[4] 已保存: {out} ({out.stat().st_size} bytes)")

    # call_tool 走统一入口验证 tts_status
    print("\n[5] call_tool tts_status:")
    status = await asm.call_tool("tts_status", {}, timeout=10.0)
    print(f"    {status}")

    await asm.stop_domain("tts")
    print("\n[完成] TTS API 生成测试通过")


asyncio.run(test_tts_api())
