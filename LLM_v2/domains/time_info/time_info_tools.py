import datetime
from LLM_v2.core.capability_domain import tool_schema


TOOL_NAMES = ["get_current_time"]

TOOLS_SCHEMA = [
    tool_schema(
        "get_current_time",
        "获取当前日期和时间",
        {},
    )
]


def get_current_time() -> dict:
    """获取当前日期和时间"""
    try:
        now = datetime.datetime.now()
        return {
            "success": True,
            "time": now.strftime("%H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now.timestamp()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
