#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "          Pi Agent Connector - Bridge             "
echo "=================================================="

# Check python3
if ! command -v python3 &>/dev/null; then
    echo "[!] Error: python3 is required but not found in PATH."
    exit 1
fi

# Check websockets library
if ! python3 -c "import websockets" &>/dev/null; then
    echo "[*] Python package 'websockets' not found. Installing..."
    python3 -m pip install websockets || {
        echo "[!] Failed to install websockets. Please run: pip install websockets"
        exit 1
    }
fi

# Kill old process on port 17613 if running
OLDPID=$(lsof -ti :17613 2>/dev/null || true)
if [ -n "$OLDPID" ]; then
    echo "[*] Releasing port 17613 (PID: $OLDPID)..."
    kill -9 $OLDPID 2>/dev/null || true
    sleep 1
fi

echo "[*] Starting Pi Bridge..."
echo "    - WebSocket Bridge: ws://127.0.0.1:17613"
echo "    - HTTP REST API   : http://127.0.0.1:17614"
echo "    - Tools Server    : pi_tools_mcp.py (bash, read, write, edit, grep, find, ls)"
echo ""
echo "Keep this window open while using the browser extension."
echo "Press Ctrl+C to stop."
echo "=================================================="

exec python3 "$SCRIPT_DIR/pi_bridge.py"
