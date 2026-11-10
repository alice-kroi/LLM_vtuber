# ============================================================
# LLM_v2/domains/tts/__init__.py
# ============================================================
from LLM_v2.domains.tts.tts import (
    TTSDomain, build_tts_domain,
    TTS_TOOL_NAMES, TTS_TOOLS_SCHEMA,
)

__all__ = [
    'TTSDomain', 'build_tts_domain',
    'TTS_TOOL_NAMES', 'TTS_TOOLS_SCHEMA',
]
