"""调试 WillEngine 路由逻辑"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from LLM_v2.core.persona_runtime import PersonaRuntime
PersonaRuntime._instance = None
runtime = PersonaRuntime()
from LLM_v2.brains.will_engine import WillEngineBrain
we = WillEngineBrain(runtime, decision_mode='rule')

text = '帮我打开 Chrome 浏览器'
print(f'Input: {repr(text)}')
print(f'  contains 打开: {"打开" in text}')

matched = [kw for kw in ['打开','关闭','启动','执行','查','截屏','写文件','读文件','列出','搜索','运行','调用','调'] if kw in text]
print(f'Matched: {matched}')

tool, args = we._route_by_keywords(text, 'webui')
print(f'Route result: tool={tool}, args={args}')