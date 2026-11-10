# ============================================================
# LLM_v2/domains/launcher/__init__.py — launcher 域包标识
# ============================================================
from LLM_v2.domains.launcher.launcher import (
    LauncherDomain,
    build_launcher_domain,
    launch_mumu, launch_alas, launch_maa, start_all, status,
    MUMU_MANAGER, ALAS_EXE, MAA_EXE,
    ALAS_MAIN_TITLE, MAA_MAIN_TITLE, ALAS_START_REL, MAA_START_REL,
)
from LLM_v2.domains.launcher.launcher_tools import (
    LAUNCHER_TOOL_NAMES, LAUNCHER_TOOLS_SCHEMA, tools_schema_for,
)

__all__ = [
    'LauncherDomain', 'build_launcher_domain',
    'launch_mumu', 'launch_alas', 'launch_maa', 'start_all', 'status',
    'MUMU_MANAGER', 'ALAS_EXE', 'MAA_EXE',
    'ALAS_MAIN_TITLE', 'MAA_MAIN_TITLE', 'ALAS_START_REL', 'MAA_START_REL',
    'LAUNCHER_TOOL_NAMES', 'LAUNCHER_TOOLS_SCHEMA', 'tools_schema_for',
]