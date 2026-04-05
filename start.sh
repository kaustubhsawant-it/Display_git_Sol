#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check Python 3
if ! python3 --version >/dev/null 2>&1; then
    echo "Error: python3 not found. Install Python 3.11+." >&2
    exit 1
fi

# Check required packages
MISSING=()
for pkg in flask flask_socketio eventlet mss cv2 numpy pyautogui qrcode zeroconf; do
    python3 -c "import $pkg" 2>/dev/null || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Error: missing packages: ${MISSING[*]}" >&2
    echo "Run: pip install -r requirements.txt" >&2
    exit 1
fi

# Generate token if not already set
export SECRET_TOKEN="${SECRET_TOKEN:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"

# Server owns QR printing and mDNS — just exec python
exec python3 server.py
