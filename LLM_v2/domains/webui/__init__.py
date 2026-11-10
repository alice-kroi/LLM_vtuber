# ============================================================
# LLM_v2/domains/webui/__init__.py
# ============================================================
from LLM_v2.domains.webui.webui import (
    WebUICapability,
    build_webui_domain,
)

__all__ = ['WebUICapability', 'build_webui_domain']