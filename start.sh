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

# Check websockets library. Do not mutate the system Python environment from a
# launcher; install through the OS package manager or a project virtualenv.
if ! python3 -c "import websockets" >/dev/null 2>&1; then
    echo "[!] Python package 'websockets' is required but not installed."
    echo "    Arch Linux: sudo pacman -S python-websockets"
    echo "    Or install it in a virtualenv and run this launcher with that python3 on PATH."
    exit 1
fi

# Do not kill arbitrary processes to reclaim a port. If a previous instance is
# still running, fail with an explicit diagnostic and let its owner stop it.
for PORT in 17613 17614; do
    if command -v ss >/dev/null 2>&1; then
        if ss -H -ltn "sport = :$PORT" | grep -q .; then
            echo "[!] Port $PORT is already in use. Stop the owning process before starting another bridge."
            exit 1
        fi
    elif command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
            echo "[!] Port $PORT is already in use. Stop the owning process before starting another bridge."
            exit 1
        fi
    else
        echo "[!] Cannot check whether port $PORT is in use (install iproute2 or lsof)."
        exit 1
    fi
done

echo "[*] Starting Pi Bridge..."
echo "    - WebSocket Bridge: ws://127.0.0.1:17613"
echo "    - HTTP REST API   : http://127.0.0.1:17614"
echo "    - Tools Server    : pi_tools_mcp.py (bash, read, write, edit, grep, find, ls)"
echo ""
echo "Keep this window open while using the browser extension."
echo "Press Ctrl+C to stop."
echo "=================================================="

exec python3 "$SCRIPT_DIR/pi_bridge.py"
