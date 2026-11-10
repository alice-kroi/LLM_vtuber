from typing import Any, Dict, List
from LLM_v2.core.capability_domain import Actuator, CapabilityType
from . import time_info_tools
from .time_info_tools import TOOL_NAMES, TOOLS_SCHEMA


class TimeInfoDomain(Actuator):
    id = "time_info"
    label = "时间信息"
    prefix = "time_info"
    default_enabled = True
    type = CapabilityType.ACTUATOR

    def allowed_names(self) -> List[str]:
        return TOOL_NAMES

    def tools(self) -> List[Dict[str, Any]]:
        return TOOLS_SCHEMA

    def get_current_time(self):
        return time_info_tools.get_current_time()


def build_time_info_domain() -> TimeInfoDomain:
    """构建时间信息能力域"""
    return TimeInfoDomain()
