# ============================================================
# start_v2.ps1 — LLM_vtuber V2 一键启动脚本
#
# 每次启动前自动清理：
#   1. 占用 8081 端口的旧 WebUI 进程
#   2. 残留的 LLM_v2.main python 进程
#   3. 遗留的子进程引擎（pvz/browser/launcher/desktop_agent/ui_perception 等）
# 然后以项目根目录启动新的 LLM_v2.main 实例。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File .\start_v2.ps1
#   powershell -ExecutionPolicy Bypass -File .\start_v2.ps1 --no-bilibili --no-tts --no-live2d
# ============================================================

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

# 中文输出兼容（PowerShell 5.1 默认 GBK，切到 UTF-8）
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8

$ErrorActionPreference = "SilentlyContinue"
$PORT = 8081

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  LLM_vtuber V2 启动（先清理旧进程）" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---------- 1. 清理占用 8081 端口的进程 ----------
$listeners = Get-NetTCPConnection -LocalPort $PORT -State Listen
if ($listeners) {
    $pids = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "  [kill] 8081 端口占用进程 PID=$procId ($($proc.ProcessName))" -ForegroundColor Yellow
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
} else {
    Write-Host "  [ok] 8081 端口空闲" -ForegroundColor Green
}

# ---------- 2. 清理残留的 LLM_v2.main python 进程 ----------
$stale = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -match 'LLM_v2[\\/\.]main|LLM_v2' }
foreach ($p in $stale) {
    Write-Host "  [kill] 残留 LLM_v2 进程 PID=$($p.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

# ---------- 3. 清理遗留的子进程引擎 ----------
$engines = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
    Where-Object { $_.CommandLine -match 'domains[\\/].*_engine\.py' }
foreach ($e in $engines) {
    Write-Host "  [kill] 残留子进程引擎 PID=$($e.ProcessId)" -ForegroundColor Yellow
    Stop-Process -Id $e.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 300

# ---------- 4. 启动新实例（保持前台，Ctrl+C 可优雅关闭）----------
$cmdArgs = @("-m", "LLM_v2.main")
if ($AppArgs) {
    $cmdArgs += $AppArgs
}
Write-Host ""
Write-Host "  [start] python $($cmdArgs -join ' ')" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
python @cmdArgs
