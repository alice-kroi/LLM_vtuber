@echo off
rem ============================================================
rem start_v2.bat — LLM_vtuber V2 一键启动（BAT 包装）
rem 调用 start_v2.ps1；启动前自动清理旧进程（8081 端口 + 残留实例）
rem 用法：双击运行 或 start_v2.bat --no-bilibili --no-tts --no-live2d
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_v2.ps1" %*
