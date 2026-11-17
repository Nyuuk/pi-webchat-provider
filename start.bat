@echo off
setlocal enabledelayedexpansion
title Pi Agent Bridge
cd /d "%~dp0"

echo ==================================================
echo           Pi Agent Connector - Bridge             
echo ==================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [!] Error: python is not found in PATH.
    pause
    exit /b 1
)

python -c "import websockets" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing websockets...
    python -m pip install websockets
)

echo [*] Starting Pi Bridge on ws://127.0.0.1:17613 and http://127.0.0.1:17614
python "%~dp0pi_bridge.py"

pause
