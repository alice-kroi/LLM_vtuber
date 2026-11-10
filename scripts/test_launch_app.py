"""测试 os_launch_app 实际行为"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tool.computer_use import os_launch_app, os_open_path, os_list_apps


async def main():
    print('=== List apps ===')
    r = await os_list_apps()
    apps = r.get('apps', [])
    print(f'  Total apps: {len(apps)}')
    chrome_found = False
    for a in apps:
        name = a.get('name', '?')
        path = a.get('path', '')[:60]
        if 'chrome' in name.lower() or 'chrome' in path.lower():
            chrome_found = True
            print(f'  [CHROME] {name}: {path}')
        elif len(apps) < 10:
            print(f'    - {name}: {path}')

    print(f'\nChrome found: {chrome_found}')

    print('\n=== Test launch "chrome" ===')
    r = await os_launch_app('chrome')
    print(f'  Result: {r}')

    print('\n=== Test launch via shell ===')
    r = await os_open_path('chrome')
    print(f'  Result: {r}')


asyncio.run(main())