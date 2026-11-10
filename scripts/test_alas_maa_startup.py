"""ALAS + MAA + MuMu 启动测试（修正版）"""
import asyncio
import sys
import time
import subprocess
import os
from pathlib import Path

sys.path.insert(0, '.')


def check_path(path: str) -> bool:
    return Path(path).exists()


def list_processes(names: list) -> dict:
    try:
        import psutil
        result = {n: [] for n in names}
        for proc in psutil.process_iter(['name', 'pid']):
            for n in names:
                if n.lower() in proc.info['name'].lower():
                    result[n].append(proc.info)
        return result
    except ImportError:
        return {}


async def start_mumu_via_manager(instance_id: int = 0):
    """用正确的 MuMuManager CLI 启动"""
    print(f'\n=== [1] 启动 MuMu 模拟器 (实例 {instance_id}) ===')
    mumu_path = r"F:\MuMu Player 12\nx_main\MuMuManager.exe"
    if not check_path(mumu_path):
        print(f'  ✗ MuMuManager 不存在')
        return False

    # 正确的命令格式: MuMuManager control -v <vm> launch
    vm_index = str(instance_id)
    try:
        result = subprocess.run(
            [mumu_path, "control", "-v", vm_index, "launch"],
            timeout=30, capture_output=True, text=True
        )
        print(f'  命令: MuMuManager control -v {vm_index} launch')
        print(f'  returncode: {result.returncode}')
        if result.stdout:
            print(f'  stdout: {result.stdout[:200]}')
        if result.stderr:
            print(f'  stderr: {result.stderr[:200]}')

        await asyncio.sleep(15)  # 等启动
        print(f'  等 15 秒启动')
        return True
    except subprocess.TimeoutExpired:
        print(f'  超时')
        return False
    except Exception as e:
        print(f'  ✗ 失败: {e}')
        return False


async def start_alas():
    print('\n=== [2] 启动 ALAS ===')
    alas_path = r"F:\download\AzurLaneAutoScript\alas.exe"
    if not check_path(alas_path):
        return False
    try:
        proc = subprocess.Popen([alas_path],
                                cwd=os.path.dirname(alas_path),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        print(f'  PID: {proc.pid}')
        await asyncio.sleep(3)
        if proc.poll() is None:
            print(f'  ✓ ALAS 启动成功')
            return True
        print(f'  ✗ ALAS 退出')
        return False
    except Exception as e:
        print(f'  ✗ {e}')
        return False


async def start_maa():
    print('\n=== [3] 启动 MAA ===')
    maa_path = r"F:\maa\maa.exe"
    if not check_path(maa_path):
        return False
    try:
        proc = subprocess.Popen([maa_path],
                                cwd=os.path.dirname(maa_path),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        print(f'  PID: {proc.pid}')
        await asyncio.sleep(3)
        if proc.poll() is None:
            print(f'  ✓ MAA 启动成功')
            return True
        print(f'  ✗ MAA 退出')
        return False
    except Exception as e:
        print(f'  ✗ {e}')
        return False


def list_windows():
    """列出当前窗口"""
    print('\n=== [4] 当前窗口列表 ===')
    try:
        import pygetwindow as gw
        wsz = gw.getAllWindows()
        # 过滤有标题且包含关键名字的窗口
        keywords = ['MuMu', 'Alas', 'MAA', 'mumu', 'alas', 'maa']
        targets = []
        for w in wsz:
            if w.title and any(k.lower() in w.title.lower() for k in keywords):
                targets.append(w.title)
        if targets:
            for t in targets:
                print(f'  ✓ {t}')
        else:
            print('  无相关窗口')
            # 显示前 10 个非空窗口
            print('\n  前 10 个窗口（参考）:')
            for w in wsz[:20]:
                if w.title:
                    print(f'    - {w.title[:60]}')
        return targets
    except ImportError:
        print('  pygetwindow 未装')
        return []
    except Exception as e:
        print(f'  ✗ {e}')
        return []


def list_processes_full():
    print('\n=== [5] 进程检查 ===')
    targets = ['MuMu', 'Alas', 'MAA', 'MuMuPlayer', 'MuMuNxMain']
    procs = list_processes(targets)
    for name, p_list in procs.items():
        if p_list:
            print(f'  {name}: {len(p_list)} 个进程')
            for p in p_list[:3]:
                print(f'    - PID {p["pid"]}: {p["name"]}')


async def main():
    print('=' * 70)
    print('  ALAS + MAA + MuMu 启动测试（修正版）')
    print('=' * 70)

    # 1. MuMu
    await start_mumu_via_manager(0)

    # 2. ALAS
    await start_alas()

    # 3. MAA
    await start_maa()

    # 4. 等稳定
    print('\n=== 等 5 秒稳定 ===')
    await asyncio.sleep(5)

    # 5. 列出窗口
    list_windows()

    # 6. 列出进程
    list_processes_full()

    print('\n' + '=' * 70)
    print('  测试完成（启动的进程未关闭）')
    print('=' * 70)


asyncio.run(main())
