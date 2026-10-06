@echo off
setlocal EnableDelayedExpansion
chcp 936 >nul 2>&1
title JARVIS 启动器
cd /d "%~dp0"

rem ============================================================
rem  J.A.R.V.I.S. 唯一启动入口
rem ------------------------------------------------------------
rem  双击即可，等价默认启动。可选参数：
rem    启动贾维斯.bat                启动后端 + 前端 + 桌面端
rem    启动贾维斯.bat --no-electron  只启动后端 + 前端（浏览器访问）
rem    启动贾维斯.bat --help         查看帮助
rem
rem  停止请运行同目录：停止贾维斯.bat
rem  日志：server\backend.log   frontend.log
rem ============================================================

set "PROJECT_DIR=%~dp0"
set "SERVER_DIR=%PROJECT_DIR%server"
set "BACKEND_PORT=18200"
set "FRONTEND_PORT=5173"
set "START_ELECTRON=1"

if /i "%~1"=="--no-electron" set "START_ELECTRON=0"
if /i "%~1"=="--services-only" set "START_ELECTRON=0"
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h" goto :usage

echo.
echo ============================================================
echo    J.A.R.V.I.S.  启动器
echo ============================================================
echo.

rem ---------- 1. 后端（FastAPI / uvicorn）----------
call :port_in_use %BACKEND_PORT%
if "!PORT_IN_USE!"=="1" (
    echo [跳过] 后端已在 %BACKEND_PORT% 运行
) else (
    echo [启动] 后端 http://127.0.0.1:%BACKEND_PORT% ...
    rem 必须用项目 venv 的解释器：Electron 用的也是它。
    rem 裸 `python` 可能解析到另一个缺少依赖的环境，
    rem 浏览器工具曾因此静默失效过一次。
    set "PYTHON_EXE=python"
    if exist "%SERVER_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%SERVER_DIR%\.venv\Scripts\python.exe"
    start "JARVIS-Backend" /min cmd /c "cd /d "%SERVER_DIR%" && "!PYTHON_EXE!" -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% >>"%SERVER_DIR%\backend.log" 2>&1"
    call :wait_port %BACKEND_PORT% 90
    if "!PORT_IN_USE!"=="1" (
        echo [完成] 后端已就绪
    ) else (
        echo [失败] 后端启动超时，请查看 server\backend.log
    )
)
echo.

rem ---------- 2. 前端（Vite dev server）----------
call :port_in_use %FRONTEND_PORT%
if "!PORT_IN_USE!"=="1" (
    echo [跳过] 前端已在 %FRONTEND_PORT% 运行
) else (
    echo [启动] 前端 http://localhost:%FRONTEND_PORT% ...
    if not exist "%PROJECT_DIR%node_modules\.bin\vite.cmd" (
        echo [失败] 未找到 node_modules\.bin\vite.cmd，请先执行 pnpm install
        goto :done
    )
    start "JARVIS-Frontend" /min cmd /c "cd /d "%PROJECT_DIR%" && "%PROJECT_DIR%node_modules\.bin\vite.cmd" >>"%PROJECT_DIR%frontend.log" 2>&1"
    call :wait_port %FRONTEND_PORT% 60
    if "!PORT_IN_USE!"=="1" (
        echo [完成] 前端已就绪
    ) else (
        echo [失败] 前端启动超时，请查看 frontend.log
    )
)
echo.

rem ---------- 3. 桌面端（Electron）----------
if "%START_ELECTRON%"=="0" goto :done
echo [启动] JARVIS 桌面端 ...
if not exist "%PROJECT_DIR%node_modules\.bin\electron.cmd" (
    echo [失败] 未找到 node_modules\.bin\electron.cmd，请先执行 pnpm install
    goto :done
)
rem 桌面端会自行拉起后端（端口被占用时跳过），窗口则连接 Vite dev server。
start "JARVIS-Desktop" cmd /c "cd /d "%PROJECT_DIR%" && "%PROJECT_DIR%node_modules\.bin\electron.cmd" ."

:done
echo.
echo ============================================================
echo    启动完成
echo    前端：http://localhost:%FRONTEND_PORT%
echo    后端：http://127.0.0.1:%BACKEND_PORT%
echo ------------------------------------------------------------
echo    停止所有服务请运行：停止贾维斯.bat
echo    本窗口可关闭，服务在后台继续运行。
echo ============================================================
echo.
endlocal
exit /b 0

:usage
echo.
echo J.A.R.V.I.S. 启动器
echo.
echo   启动贾维斯.bat                启动后端 + 前端 + 桌面端
echo   启动贾维斯.bat --no-electron  只启动后端 + 前端
echo   启动贾维斯.bat --help         显示本帮助
echo.
echo   停止：停止贾维斯.bat
echo.
endlocal
exit /b 0

rem ===================== 子过程 =====================

rem 检测端口是否处于 LISTENING，结果写入 PORT_IN_USE(1/0)
:port_in_use
set "PORT_IN_USE=0"
netstat -ano 2>nul | findstr ":%~1 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT_IN_USE=1"
exit /b 0

rem 轮询等待端口就绪，参数：端口 最大秒数；结果写入 PORT_IN_USE(1/0)
:wait_port
set "PORT_IN_USE=0"
for /l %%i in (1,1,%~2) do (
    netstat -ano 2>nul | findstr ":%~1 " | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        set "PORT_IN_USE=1"
        exit /b 0
    )
    ping -n 2 127.0.0.1 >nul 2>&1
)
exit /b 0
