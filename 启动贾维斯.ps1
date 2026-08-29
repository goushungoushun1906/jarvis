# JARVIS Launcher - PowerShell
# 双击运行此文件即可启动贾维斯

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   JARVIS AI Assistant - Launcher" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Kill existing processes
Get-Process electron -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# Check and start backend
$backendPort = netstat -ano | Select-String ":18200.*LISTENING"
if (-not $backendPort) {
    Write-Host "[START] Backend on port 18200..." -ForegroundColor Yellow
    $backend = Start-Process -FilePath "cmd.exe" -ArgumentList "/c cd /d `"$scriptDir\server`" ^& .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 18200" -WindowStyle Normal -PassThru
    Start-Sleep -Seconds 3
} else {
    Write-Host "[OK] Backend already running on port 18200" -ForegroundColor Green
}

# Check and start frontend
$frontendPort = netstat -ano | Select-String ":5173.*LISTENING"
if (-not $frontendPort) {
    Write-Host "[START] Frontend on port 5173..." -ForegroundColor Yellow
    $frontend = Start-Process -FilePath "cmd.exe" -ArgumentList "/c cd /d `"$scriptDir`" ^& npm run dev" -WindowStyle Normal -PassThru
    Start-Sleep -Seconds 5
} else {
    Write-Host "[OK] Frontend already running on port 5173" -ForegroundColor Green
}

# Launch Electron
Write-Host "[START] JARVIS Desktop App..." -ForegroundColor Yellow
$electron = Start-Process -FilePath "npx" -ArgumentList "electron ." -WindowStyle Normal -PassThru

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   JARVIS is ready!" -ForegroundColor Green
Write-Host "   Backend:  http://127.0.0.1:18200" -ForegroundColor White
Write-Host "   Frontend: http://localhost:5173" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "All terminals will stay open. Close them to stop JARVIS." -ForegroundColor Gray
Write-Host ""
pause
