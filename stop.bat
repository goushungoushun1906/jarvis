@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title JARVIS Stop

echo Stopping JARVIS services...

set "FOUND=0"

:: Kill backend (port 18200)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":18200 " ^| findstr "LISTENING"') do (
    echo   Stopping backend (PID %%a)...
    taskkill /PID %%a /F >nul 2>&1
    set "FOUND=1"
)

:: Kill frontend (port 5173)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    echo   Stopping frontend (PID %%a)...
    taskkill /PID %%a /F >nul 2>&1
    set "FOUND=1"
)

if !FOUND!==0 (
    echo [OK] All JARVIS services stopped.
) else (
    echo [OK] No JARVIS services running.
)

timeout /t 2 /nobreak >nul
endlocal
