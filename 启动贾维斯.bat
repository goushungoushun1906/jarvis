@echo off
setlocal EnableDelayedExpansion
chcp 936 >nul 2>&1
title JARVIS ������
cd /d "%~dp0"

rem ============================================================
rem  J.A.R.V.I.S. ͳһ������
rem ------------------------------------------------------------
rem  ˫�����ɣ���� + ǰ�� + �����
rem    ������ά˹.bat                ��� + ǰ�� + �����
rem    ������ά˹.bat --no-electron  ֻ���� + ǰ�ˣ������������
rem    ������ά˹.bat --help         �鿴����
rem
rem  ����ǰ��������һ�ֲ�����Electron �ص�����Ĭ�����������̡���˺�
rem  ǰ�˶����ں�̨�ܡ���ǰ����ʱ�����˿ڱ�ռ��ֱ���������ã������
rem  �û����������´��롢����ͽӿ�ȴ���Ǿɵ� ���� ������ȫ������ԭ��
rem  ����һ������ɾ������µġ�
rem
rem  ֹͣ��ͬĿ¼�� ֹͣ��ά˹.bat
rem  ��־��server\backend.log   frontend.log
rem ============================================================

set "PROJECT_DIR=%~dp0"
set "SERVER_DIR=%PROJECT_DIR%server"
set "BACKEND_PORT=18200"
set "FRONTEND_PORT=5173"
set "START_ELECTRON=1"

rem 让 Python 用 UTF-8 输出。中文 Windows 默认 GBK，而麦克风设备名里带 ®
rem （"英特尔® 智音"），写日志时会抛 UnicodeEncodeError，整条日志直接丢失。
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if /i "%~1"=="--no-electron" set "START_ELECTRON=0"
if /i "%~1"=="--services-only" set "START_ELECTRON=0"
if /i "%~1"=="--help" goto :usage
if /i "%~1"=="-h" goto :usage

echo.
echo ============================================================
echo    J.A.R.V.I.S.  ��������
echo ============================================================
echo.

rem ---------- 0. ������һ�ֲ������� ----------
echo [0/3] ������һ�ֲ��� ...
taskkill /F /IM electron.exe >nul 2>&1
call :kill_port %BACKEND_PORT% ���
call :kill_port %FRONTEND_PORT% ǰ��
rem ���ں�һ��ʱ�������ͷŶ˿ڣ������½�������ʱ��ײ�϶˿�ռ��
ping -n 3 127.0.0.1 >nul 2>&1
echo.

rem ---------- 1. ��ˣ�FastAPI / uvicorn ----------
echo [1/3] ������� http://127.0.0.1:%BACKEND_PORT% ...
rem ����Ŀ venv �Ľ������������ electron �õ�Ҳ��ͬһ��������
rem `python` ��������һ��ȱ�����Ļ������� Wake ��������
set "PYTHON_EXE=python"
if exist "%SERVER_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%SERVER_DIR%\.venv\Scripts\python.exe"
start "JARVIS-Backend" /min cmd /c "cd /d "%SERVER_DIR%" && "!PYTHON_EXE!" -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% >>"%SERVER_DIR%\backend.log" 2>&1"
call :wait_port %BACKEND_PORT% 90
if "!PORT_IN_USE!"=="1" (
    echo [����] ���������
) else (
    echo [ʧ��] ���������ʱ���뿴 server\backend.log
)
echo.

rem ---------- 2. ǰ�ˣ�Vite dev server ----------
echo [2/3] ����ǰ�� http://localhost:%FRONTEND_PORT% ...
if not exist "%PROJECT_DIR%node_modules\.bin\vite.cmd" (
    echo [ʧ��] δ�ҵ� node_modules\.bin\vite.cmd������ִ�� pnpm install
    goto :done
)
start "JARVIS-Frontend" /min cmd /c "cd /d "%PROJECT_DIR%" && "%PROJECT_DIR%node_modules\.bin\vite.cmd" >>"%PROJECT_DIR%frontend.log" 2>&1"
call :wait_port %FRONTEND_PORT% 60
if "!PORT_IN_USE!"=="1" (
    echo [����] ǰ��������
) else (
    echo [ʧ��] ǰ��������ʱ���뿴 frontend.log
)
echo.

rem ---------- 3. ����ˣ�Electron ----------
if "%START_ELECTRON%"=="0" goto :done
echo [3/3] ���� JARVIS ����� ...
if not exist "%PROJECT_DIR%node_modules\.bin\electron.cmd" (
    echo [ʧ��] δ�ҵ� node_modules\.bin\electron.cmd������ִ�� pnpm install
    goto :done
)
start "JARVIS-Desktop" cmd /c "cd /d "%PROJECT_DIR%" && "%PROJECT_DIR%node_modules\.bin\electron.cmd" ."

:done
echo.
echo ============================================================
echo    �������
echo    ǰ�ˣ�http://localhost:%FRONTEND_PORT%
echo    ��ˣ�http://127.0.0.1:%BACKEND_PORT%
echo ------------------------------------------------------------
echo    ֱ�Ӷ��ŵ���˵����ü�ά˹�����ɣ�����Ҫ����˷簴ť��
echo    ֹͣ���з��������У�ֹͣ��ά˹.bat
echo ============================================================
echo.
endlocal
exit /b 0

:usage
echo.
echo J.A.R.V.I.S. ������
echo.
echo   ������ά˹.bat                ��� + ǰ�� + �����
echo   ������ά˹.bat --no-electron  ֻ���� + ǰ��
echo   ������ά˹.bat --help         ��ʾ������
echo.
echo   ֹͣ�����У�ֹͣ��ά˹.bat
echo.
endlocal
exit /b 0

rem ===================== �ӹ��� =====================

rem ��ѯ�˿��Ƿ��� LISTENING�����д�� PORT_IN_USE(1/0)
:port_in_use
set "PORT_IN_USE=0"
netstat -ano 2>nul | findstr ":%~1 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 set "PORT_IN_USE=1"
exit /b 0

rem ��ѯ�ȴ��˿ھ��������д�� PORT_IN_USE(1/0)
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

rem ɱ��ռ��ָ���˿ڵĽ��̣�%~1 = �˿ڣ�%~2 = ��ʾ��
:kill_port
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%~1 " ^| findstr "LISTENING"') do (
    echo [����] %~2 �˿� %~1 �� PID %%a ռ�ã�������
    taskkill /PID %%a /F >nul 2>&1
)
exit /b 0
