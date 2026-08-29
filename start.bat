@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title JARVIS Launcher

set "PROJECT_DIR=%~dp0"
set "SERVER_DIR=%PROJECT_DIR%server"
set "FRONTEND_DIR=%PROJECT_DIR%"
set "BACKEND_PORT=18200"
set "FRONTEND_PORT=5173"

echo ====================================================
echo   J.A.R.V.I.S.  Launcher
echo ====================================================
echo.

:: --- Backend ---
netstat -ano 2>nul | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 goto :start_backend
echo [OK] Backend already running on port %BACKEND_PORT%
goto :check_frontend

:start_backend
echo [..] Starting backend on port %BACKEND_PORT%...
start "JARVIS-Backend" /min cmd /c "cd /d "%SERVER_DIR%" && python -m uvicorn main:app --host 127.0.0.1 --port %BACKEND_PORT% >>"%PROJECT_DIR%server\backend.log" 2>&1"
timeout /t 4 /nobreak >nul
netstat -ano 2>nul | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 (
    echo [!!] Backend failed to start — check server\backend.log
) else (
    echo [OK] Backend started on http://127.0.0.1:%BACKEND_PORT%
)

:check_frontend
echo.

:: --- Frontend ---
netstat -ano 2>nul | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 goto :start_frontend
echo [OK] Frontend already running on port %FRONTEND_PORT%
goto :done

:start_frontend
echo [..] Starting frontend on port %FRONTEND_PORT%...
start "JARVIS-Frontend" /min cmd /c "cd /d "%FRONTEND_DIR%" && "%FRONTEND_DIR%node_modules\.bin\vite.cmd" --host 2>"%PROJECT_DIR%frontend.log""
timeout /t 4 /nobreak >nul
netstat -ano 2>nul | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul 2>&1
if !errorlevel!==0 (
    echo [!!] Frontend failed to start — check frontend.log
) else (
    echo [OK] Frontend started on http://localhost:%FRONTEND_PORT%
)

:done
echo.
echo ====================================================
echo   JARVIS is ready!
echo   Frontend:  http://localhost:%FRONTEND_PORT%
echo   Backend:   http://127.0.0.1:%BACKEND_PORT%
echo.
echo   Close this window to exit launcher.
echo   Services will keep running in background.
echo ====================================================
echo.
pause >nul
endlocal
