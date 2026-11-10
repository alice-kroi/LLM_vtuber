"""列出可能可用的应用"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.computer_use import os_list_apps


async def main():
    r = await os_list_apps()
    apps = r.get('apps', [])
    print(f'Total apps: {len(apps)}')
    keywords = ['code', 'vscode', 'edge', 'firefox', 'cmd', 'window', '记事本', 'notepad']
    for a in apps:
        name = (a.get('name', '') or '').lower()
        path = (a.get('path', '') or '').lower()
        if any(kw in name or kw in path for kw in keywords):
            print(f'  {a.get("name")}: {a.get("path", "")[:80]}')

asyncio.run(main())