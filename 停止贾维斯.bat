@echo off
setlocal EnableDelayedExpansion
chcp 936 >nul 2>&1
title JARVIS 停止器
cd /d "%~dp0"

rem ============================================================
rem  J.A.R.V.I.S. 唯一停止入口
rem ------------------------------------------------------------
rem  关闭桌面端（Electron），并释放后端 18200 / 前端 5173 端口。
rem  只杀占用这两个端口的进程，不会误杀其他 python/node 进程。
rem ============================================================

set "BACKEND_PORT=18200"
set "FRONTEND_PORT=5173"
set "STOPPED=0"

echo.
echo ============================================================
echo    正在停止 J.A.R.V.I.S. ...
echo ============================================================
echo.

rem ---------- 1. 桌面端 ----------
tasklist /FI "IMAGENAME eq electron.exe" 2>nul | findstr /i "electron.exe" >nul 2>&1
if not errorlevel 1 (
    echo [停止] Electron 桌面端 ...
    taskkill /F /IM electron.exe >nul 2>&1
    set /a "STOPPED+=1"
) else (
    echo [跳过] 没有正在运行的 Electron 桌面端
)

rem ---------- 2. 后端 / 前端端口 ----------
call :kill_port %BACKEND_PORT% 后端
call :kill_port %FRONTEND_PORT% 前端

echo.
if "%STOPPED%"=="0" (
    echo [完成] 没有正在运行的 JARVIS 服务。
) else (
    echo [完成] 已停止 %STOPPED% 个 JARVIS 进程。
)
echo.
endlocal
exit /b 0

rem 杀掉占用指定端口的进程，参数：端口 显示名
:kill_port
set "FOUND=0"
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%~1 " ^| findstr "LISTENING"') do (
    echo [停止] %~2 进程 PID %%a（端口 %~1）
    taskkill /PID %%a /F >nul 2>&1
    set "FOUND=1"
    set /a "STOPPED+=1"
)
if "%FOUND%"=="0" echo [跳过] %~2 端口 %~1 未被占用
exit /b 0
