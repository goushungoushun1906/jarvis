@echo off
chcp 65001 >nul
title JARVIS Launcher
cd /d "%~dp0"

echo.
echo ==========================================
echo    JARVIS AI Assistant - Launcher
echo ==========================================
echo.

:: Kill any existing JARVIS processes
taskkill /F /IM electron.exe 2>nul
taskkill /F /IM python.exe 2>nul
ping 127.0.0.1 -n 2 >nul

:: Check backend port
netstat -ano | findstr ":18200" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [OK] Backend port 18200 already in use, skipping backend launch.
) else (
    echo [START] Launching backend on port 18200...
    start "JARVIS Backend" cmd /c "cd server ^&^& .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 18200"
    timeout /t 3 /nobreak >nul
)

:: Check frontend port
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [OK] Frontend port 5173 already in use, skipping frontend launch.
) else (
    echo [START] Launching frontend on port 5173...
    start "JARVIS Frontend" cmd /c "npm run dev"
    timeout /t 5 /nobreak >nul
)

:: Launch Electron
echo [START] Launching JARVIS desktop app...
start "" cmd /c "npx electron ."

echo.
echo ==========================================
echo    JARVIS is starting up!
echo    Backend:  http://127.0.0.1:18200
echo    Frontend: http://localhost:5173
echo ==========================================
echo.
echo Close this window to keep services running.
echo To stop JARVIS, close all opened terminal windows.
echo.
pause
