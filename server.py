import os
import time
import uuid
import ctypes
import signal
import logging
import threading
import socket as _socket
from collections import deque

import mss
import cv2
import numpy as np
import eventlet
import pyautogui
import qrcode
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, disconnect
from zeroconf import ServiceInfo, Zeroconf

# Mandatory for eventlet background tasks
eventlet.monkey_patch()

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger(__name__)

FPS            = int(os.environ.get('FPS', 30))
JPEG_QUALITY   = int(os.environ.get('JPEG_QUALITY', 65))
SECRET_TOKEN   = os.environ.get('SECRET_TOKEN', str(uuid.uuid4()))
MAX_CLIENTS    = int(os.environ.get('MAX_CLIENTS', 4))
CORS_ORIGINS   = os.environ.get('CORS_ORIGINS', '*')
# E2: which Mac-monitor edge triggers cursor handoff to Windows (left/right/top/bottom)
HANDOFF_EDGE   = os.environ.get('HANDOFF_EDGE', 'right')

app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins=CORS_ORIGINS)

# --- Runtime state ---
rotation_state = "landscape"
rotation_lock  = threading.Lock()

active_clients = 0
clients_lock   = threading.Lock()

# Shared monitor bounds — written by _capture_loop, read by input handlers
monitor_bounds      = {'left': 0, 'top': 0, 'width': 0, 'height': 0}
monitor_bounds_lock = threading.Lock()

stop_flag = threading.Event()

# Phase D state
_start_time        = time.monotonic()
_current_fps       = FPS           # may be halved by adaptive FPS logic
_virtual_display_id = None         # None — Phase B integration out of scope for Phase D
_emit_times: deque = deque(maxlen=10)  # rolling emit-duration window (ms)

# mDNS handles — set in __main__, torn down in _shutdown_handler
_zeroconf: Zeroconf | None    = None
_zc_info:  ServiceInfo | None = None

# E2: Windows client session state
_windows_sid:    str | None = None   # SID of the registered Windows pygame client
_cursor_grabbed: bool       = False  # True while cursor is handed off to Windows
_windows_lock   = threading.Lock()


def _shutdown_handler(signum, frame):
    global _zeroconf, _zc_info
    log.info("Shutdown signal received, stopping...")
    stop_flag.set()
    if _zc_info and _zeroconf:
        try:
            _zeroconf.unregister_service(_zc_info)
            _zeroconf.close()
            log.info("mDNS service unregistered")
        except Exception as e:
            log.warning("mDNS shutdown error: %s", e)
        _zeroconf = None
        _zc_info  = None
    # eventlet's WSGI server doesn't respond to stop_flag — force exit
    os._exit(0)

signal.signal(signal.SIGINT,  _shutdown_handler)
signal.signal(signal.SIGTERM, _shutdown_handler)


def get_local_ip() -> str:
    """Return the LAN IP by routing a UDP socket (no packets are sent)."""
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'


# --- WebSocket handlers ---

@socketio.on('connect')
def handle_connect():
    global active_clients
    token = request.args.get('token')
    if token != SECRET_TOKEN:
        log.warning("Rejected connection: bad token from %s", request.remote_addr)
        disconnect()
        return
    with clients_lock:
        if active_clients >= MAX_CLIENTS:
            log.warning("Rejected connection: max clients (%d) reached", MAX_CLIENTS)
            disconnect()
            return
        active_clients += 1
    log.info("Client connected (%d/%d)", active_clients, MAX_CLIENTS)


@socketio.on('disconnect')
def handle_disconnect():
    global active_clients, _windows_sid, _cursor_grabbed
    with clients_lock:
        active_clients = max(0, active_clients - 1)
    with _windows_lock:
        if _windows_sid == request.sid:
            _windows_sid    = None
            _cursor_grabbed = False
            log.info("Windows client disconnected — cursor handoff cleared")
    log.info("Client disconnected (%d/%d)", active_clients, MAX_CLIENTS)


# E2 ── Windows client registration ─────────────────────────────────────────

@socketio.on('register')
def handle_register(data):
    global _windows_sid
    if isinstance(data, dict) and data.get('client_type') == 'windows':
        with _windows_lock:
            _windows_sid = request.sid
        log.info("Windows client registered (sid=%s)", request.sid)


# E2 ── Relative mouse movement (cursor-grabbed mode) ───────────────────────

@socketio.on('mouse_move_delta')
def handle_mouse_move_delta(data):
    if not isinstance(data, dict):
        return
    dx = data.get('dx', 0)
    dy = data.get('dy', 0)
    if not (isinstance(dx, (int, float)) and isinstance(dy, (int, float))):
        return
    pyautogui.moveRel(int(dx), int(dy), _pause=False)


# E2 ── Windows client releases cursor back to Mac ───────────────────────────

@socketio.on('cursor_release_request')
def handle_cursor_release_request():
    global _cursor_grabbed
    with _windows_lock:
        _cursor_grabbed = False
        wsid = _windows_sid
    if wsid:
        socketio.emit('cursor_release', {}, to=wsid)
        log.debug("cursor_release → %s", wsid)


@socketio.on('rotate_display')
def handle_rotation(data):
    global rotation_state
    orientation = data.get('orientation') if isinstance(data, dict) else None
    if orientation not in ('portrait', 'landscape'):
        log.warning("Invalid orientation ignored: %r", orientation)
        return
    with rotation_lock:
        rotation_state = orientation
    log.info("Rotation -> %s", rotation_state)


def _check_accessibility():
    """Warn at startup if macOS Accessibility permission is missing — pyautogui silently fails without it."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
        )
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        if not lib.AXIsProcessTrusted():
            log.warning("=" * 60)
            log.warning("Accessibility permission NOT granted.")
            log.warning("Input forwarding will be silently ignored by macOS.")
            log.warning("Fix: System Settings > Privacy & Security > Accessibility")
            log.warning("     Enable 'Terminal' (or whichever app runs this script).")
            log.warning("=" * 60)
            return False
        log.info("Accessibility permission: granted")
        return True
    except Exception as e:
        log.warning("Could not check Accessibility permission: %s", e)
        return True


def _get_bounds():
    with monitor_bounds_lock:
        return dict(monitor_bounds)


def _valid_coords(x, y, bounds):
    return (isinstance(x, (int, float)) and isinstance(y, (int, float))
            and 0 <= x <= bounds['width'] and 0 <= y <= bounds['height'])


@socketio.on('mouse_move')
def handle_mouse_move(data):
    if not isinstance(data, dict):
        return
    x, y = data.get('x'), data.get('y')
    bounds = _get_bounds()
    if not _valid_coords(x, y, bounds):
        return
    pyautogui.moveTo(bounds['left'] + x, bounds['top'] + y, _pause=False)


@socketio.on('mouse_click')
def handle_mouse_click(data):
    if not isinstance(data, dict):
        return
    x, y = data.get('x'), data.get('y')
    button = data.get('button', 'left')
    bounds = _get_bounds()
    if not _valid_coords(x, y, bounds):
        return
    if button not in ('left', 'right', 'middle'):
        button = 'left'
    pyautogui.click(bounds['left'] + x, bounds['top'] + y, button=button, _pause=False)


@socketio.on('scroll')
def handle_scroll(data):
    if not isinstance(data, dict):
        return
    x, y = data.get('x'), data.get('y')
    delta = data.get('delta', 0)
    bounds = _get_bounds()
    if not _valid_coords(x, y, bounds):
        return
    clicks = -round(delta / 100)
    if clicks == 0:
        return
    pyautogui.scroll(clicks, x=bounds['left'] + x, y=bounds['top'] + y, _pause=False)


@socketio.on('key_press')
def handle_key_press(data):
    if not isinstance(data, dict):
        return
    key = data.get('key', '')
    if not isinstance(key, str) or len(key) > 20:
        return
    special = {
        'Backspace': 'backspace', 'Enter': 'enter', 'Tab': 'tab',
        'Escape': 'escape', 'Delete': 'delete', ' ': 'space',
        'ArrowUp': 'up', 'ArrowDown': 'down',
        'ArrowLeft': 'left', 'ArrowRight': 'right',
        'Home': 'home', 'End': 'end',
        'PageUp': 'pageup', 'PageDown': 'pagedown',
    }
    if key in special:
        pyautogui.press(special[key], _pause=False)
    elif len(key) == 1 and key.isprintable():
        pyautogui.typewrite(key, interval=0, _pause=False)
    else:
        log.debug("Unhandled key: %r", key)


# --- Capture loop ---

def capture_and_stream():
    while not stop_flag.is_set():
        try:
            _capture_loop()
        except Exception as e:
            log.error("Capture task crashed: %s. Restarting in 2s...", e)
            socketio.sleep(2)
    log.info("Capture task stopped cleanly.")


def _capture_loop():
    global rotation_state, _current_fps, _cursor_grabbed

    # Synthetic frame path — no real screen/mouse access, safe for CI/tests
    if os.environ.get('DISABLE_CAPTURE'):
        synthetic = np.zeros((180, 320, 3), dtype=np.uint8)
        _, buf = cv2.imencode('.jpg', synthetic)
        payload = buf.tobytes()
        while not stop_flag.is_set():
            socketio.emit('screen_frame', payload)
            socketio.sleep(1 / _current_fps)
        return

    with mss.mss() as sct:
        monitor_index = int(os.environ.get('MONITOR_INDEX', len(sct.monitors) - 1))
        if monitor_index >= len(sct.monitors):
            log.warning("Monitor %d not found, falling back to primary (index 1)", monitor_index)
            monitor_index = 1
        monitor = sct.monitors[monitor_index]
        log.info("Capturing monitor %d: %dx%d", monitor_index, monitor['width'], monitor['height'])
        with monitor_bounds_lock:
            monitor_bounds.update({
                'left':   monitor['left'],
                'top':    monitor['top'],
                'width':  monitor['width'],
                'height': monitor['height'],
            })

        while not stop_flag.is_set():
            try:
                # 1. Capture screen
                sct_img = sct.grab(monitor)
                frame   = np.array(sct_img)
                frame   = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                # 2. Draw cursor (must happen before compression)
                mx, my = pyautogui.position()
                rx, ry = mx - monitor["left"], my - monitor["top"]
                if 0 <= rx < monitor["width"] and 0 <= ry < monitor["height"]:
                    cv2.circle(frame, (rx, ry), 8, (255, 255, 255), -1)
                    cv2.circle(frame, (rx, ry), 9, (0, 0, 0), 2)

                # E2: cursor handoff — emit cursor_grab when Mac cursor hits HANDOFF_EDGE
                with _windows_lock:
                    wsid    = _windows_sid
                    grabbed = _cursor_grabbed
                if wsid and not grabbed:
                    hit = False
                    e   = HANDOFF_EDGE
                    w, h = monitor["width"], monitor["height"]
                    if   e == 'right'  and rx >= w - 2: hit = True
                    elif e == 'left'   and rx <= 1:     hit = True
                    elif e == 'bottom' and ry >= h - 2: hit = True
                    elif e == 'top'    and ry <= 1:     hit = True
                    if hit:
                        with _windows_lock:
                            _cursor_grabbed = True
                        socketio.emit('cursor_grab', {}, to=wsid)
                        log.debug("cursor_grab → %s", wsid)

                # 3. Handle rotation
                with rotation_lock:
                    current_rotation = rotation_state
                if current_rotation == "portrait":
                    frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

                # 4. JPEG compression
                success, buffer = cv2.imencode(
                    '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
                )
                if not success:
                    log.warning("Frame encode failed, skipping")
                    socketio.sleep(1 / _current_fps)
                    continue

                # 5. Emit and measure duration for adaptive FPS
                t0 = time.monotonic()
                socketio.emit('screen_frame', buffer.tobytes())
                emit_ms = (time.monotonic() - t0) * 1000
                _emit_times.append(emit_ms)

                # Adapt FPS based on rolling average emit time
                if len(_emit_times) == _emit_times.maxlen:
                    avg = sum(_emit_times) / len(_emit_times)
                    budget_ms = 1000.0 / FPS
                    if avg > budget_ms * 1.5 and _current_fps > FPS // 2:
                        _current_fps = max(FPS // 2, 5)
                        log.info("Adaptive FPS: throttled to %d (avg emit %.1fms)", _current_fps, avg)
                    elif avg < budget_ms * 0.5 and _current_fps < FPS:
                        _current_fps = FPS
                        log.info("Adaptive FPS: restored to %d (avg emit %.1fms)", _current_fps, avg)

            except Exception as e:
                log.error("Capture error: %s", e)

            socketio.sleep(1 / _current_fps)


# --- HTTP routes ---

@app.route('/')
def index():
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>StreamDisplay</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #000; overflow: hidden; width: 100vw; height: 100vh; touch-action: none; user-select: none; }}
    #screenCanvas {{ display: block; width: 100vw; height: 100vh; object-fit: contain; }}
    #cursor {{
      position: fixed; width: 14px; height: 14px; border-radius: 50%;
      background: rgba(255,255,255,0.85); border: 2px solid #000;
      pointer-events: none; transform: translate(-50%,-50%);
      display: none; z-index: 100; transition: opacity 0.1s;
    }}
    #ui {{
      position: fixed; top: 12px; right: 12px;
      display: flex; gap: 8px; z-index: 200;
    }}
    .btn {{
      background: rgba(0,0,0,0.55); color: #fff;
      border: 1px solid rgba(255,255,255,0.25); border-radius: 8px;
      padding: 8px 14px; font-size: 15px; cursor: pointer;
      backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px);
      touch-action: manipulation;
    }}
    .btn:active {{ background: rgba(255,255,255,0.18); }}
    #kbInput {{
      position: fixed; bottom: 0; left: 0;
      opacity: 0; width: 1px; height: 1px;
      border: none; outline: none;
    }}
  </style>
</head>
<body>
  <canvas id="screenCanvas"></canvas>
  <div id="cursor"></div>
  <div id="ui">
    <button class="btn" id="rotateBtn">&#8635; Rotate</button>
    <button class="btn" id="kbBtn">&#9000;</button>
    <button class="btn" id="fsBtn">&#x26F6;</button>
  </div>
  <input id="kbInput" type="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">

  <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
  <script>
    const socket = io({{ query: {{ token: '{SECRET_TOKEN}' }} }});
    const canvas  = document.getElementById('screenCanvas');
    const ctx     = canvas.getContext('2d');
    const cursor  = document.getElementById('cursor');
    const kbInput = document.getElementById('kbInput');

    let currentRotation = 'landscape';

    socket.on('screen_frame', (data) => {{
      const blob = new Blob([data], {{ type: 'image/jpeg' }});
      const url  = URL.createObjectURL(blob);
      const img  = new Image();
      img.onload = () => {{
        canvas.width  = img.width;
        canvas.height = img.height;
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
      }};
      img.onerror = () => URL.revokeObjectURL(url);
      img.src = url;
    }});

    function getImageRect() {{
      const rect  = canvas.getBoundingClientRect();
      const cw    = canvas.width  || 1;
      const ch    = canvas.height || 1;
      const cAR   = cw / ch;
      const eAR   = rect.width / rect.height;
      let iW, iH, iLeft, iTop;
      if (cAR > eAR) {{
        iW    = rect.width;
        iH    = rect.width / cAR;
        iLeft = 0;
        iTop  = (rect.height - iH) / 2;
      }} else {{
        iH    = rect.height;
        iW    = rect.height * cAR;
        iTop  = 0;
        iLeft = (rect.width - iW) / 2;
      }}
      return {{ left: rect.left + iLeft, top: rect.top + iTop, width: iW, height: iH }};
    }}

    function toFrameCoords(vx, vy) {{
      const ir = getImageRect();
      const rx = vx - ir.left;
      const ry = vy - ir.top;
      if (rx < 0 || rx > ir.width || ry < 0 || ry > ir.height) return null;
      let fx = Math.round((rx / ir.width)  * canvas.width);
      let fy = Math.round((ry / ir.height) * canvas.height);
      if (currentRotation === 'portrait') {{
        const origX = fy;
        const origY = canvas.width - fx;
        return {{ x: origX, y: origY }};
      }}
      return {{ x: fx, y: fy }};
    }}

    let lastMove = 0;
    const MOVE_THROTTLE_MS = 50;

    canvas.addEventListener('mousemove', (e) => {{
      const now = Date.now();
      if (now - lastMove < MOVE_THROTTLE_MS) return;
      lastMove = now;
      const pt = toFrameCoords(e.clientX, e.clientY);
      if (!pt) return;
      cursor.style.display = 'block';
      cursor.style.left = e.clientX + 'px';
      cursor.style.top  = e.clientY + 'px';
      socket.emit('mouse_move', pt);
    }});

    canvas.addEventListener('mouseleave', () => {{ cursor.style.display = 'none'; }});

    canvas.addEventListener('click', (e) => {{
      const pt = toFrameCoords(e.clientX, e.clientY);
      if (!pt) return;
      socket.emit('mouse_click', {{ ...pt, button: 'left' }});
    }});

    canvas.addEventListener('contextmenu', (e) => {{
      e.preventDefault();
      const pt = toFrameCoords(e.clientX, e.clientY);
      if (!pt) return;
      socket.emit('mouse_click', {{ ...pt, button: 'right' }});
    }});

    canvas.addEventListener('wheel', (e) => {{
      e.preventDefault();
      const pt = toFrameCoords(e.clientX, e.clientY);
      if (!pt) return;
      socket.emit('scroll', {{ ...pt, delta: e.deltaY }});
    }}, {{ passive: false }});

    let touchStartTime = 0;
    let touchStartPt   = null;
    let longPressTimer = null;

    canvas.addEventListener('touchstart', (e) => {{
      e.preventDefault();
      const t = e.touches[0];
      const pt = toFrameCoords(t.clientX, t.clientY);
      if (!pt) return;
      touchStartTime = Date.now();
      touchStartPt   = pt;
      longPressTimer = setTimeout(() => {{
        socket.emit('mouse_click', {{ ...pt, button: 'right' }});
        touchStartPt = null;
      }}, 500);
    }}, {{ passive: false }});

    canvas.addEventListener('touchmove', (e) => {{
      e.preventDefault();
      clearTimeout(longPressTimer);
      const now = Date.now();
      if (now - lastMove < MOVE_THROTTLE_MS) return;
      lastMove = now;
      const t  = e.touches[0];
      const pt = toFrameCoords(t.clientX, t.clientY);
      if (!pt) return;
      cursor.style.display = 'block';
      cursor.style.left = t.clientX + 'px';
      cursor.style.top  = t.clientY + 'px';
      socket.emit('mouse_move', pt);
    }}, {{ passive: false }});

    canvas.addEventListener('touchend', (e) => {{
      e.preventDefault();
      clearTimeout(longPressTimer);
      if (touchStartPt && Date.now() - touchStartTime < 500) {{
        socket.emit('mouse_click', {{ ...touchStartPt, button: 'left' }});
      }}
      touchStartPt = null;
      cursor.style.display = 'none';
    }}, {{ passive: false }});

    document.addEventListener('keydown', (e) => {{
      if (e.metaKey || e.ctrlKey) return;
      if (e.repeat) return;                                        // ignore key-hold repeats
      if (document.activeElement === kbInput && e.key.length === 1) return; // printable chars handled by input event
      socket.emit('key_press', {{ key: e.key }});
    }});

    kbInput.addEventListener('input', (e) => {{
      if (e.data) socket.emit('key_press', {{ key: e.data }});
      kbInput.value = '';
    }});

    document.getElementById('rotateBtn').addEventListener('click', () => {{
      currentRotation = currentRotation === 'landscape' ? 'portrait' : 'landscape';
      socket.emit('rotate_display', {{ orientation: currentRotation }});
    }});

    document.getElementById('kbBtn').addEventListener('click', () => {{
      kbInput.focus();
    }});

    document.getElementById('fsBtn').addEventListener('click', () => {{
      if (!document.fullscreenElement) {{
        document.documentElement.requestFullscreen().catch(() => {{}});
      }} else {{
        document.exitFullscreen().catch(() => {{}});
      }}
    }});
  </script>
</body>
</html>"""


@app.route('/health')
def health():
    return jsonify({
        'uptime':            round(time.monotonic() - _start_time, 1),
        'connected_clients': active_clients,
        'current_fps':       _current_fps,
        'virtual_display_id': _virtual_display_id,
        'monitor_res':       f"{monitor_bounds['width']}x{monitor_bounds['height']}",
    })


# --- Startup helpers ---

def _print_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    log.info("Scan the QR code above to connect")


def _register_mdns(ip: str, port: int) -> None:
    global _zeroconf, _zc_info
    try:
        _zc_info = ServiceInfo(
            "_streamdisplay._tcp.local.",
            "StreamDisplay._streamdisplay._tcp.local.",
            addresses=[_socket.inet_aton(ip)],
            port=port,
            properties={"token": SECRET_TOKEN},
            server="streamdisplay.local.",
        )
        _zeroconf = Zeroconf()
        _zeroconf.register_service(_zc_info)
        log.info("mDNS: registered _streamdisplay._tcp.local. on %s:%d", ip, port)
    except Exception as e:
        log.warning("mDNS registration failed (non-fatal): %s", e)


if __name__ == '__main__':
    HOST      = os.environ.get('HOST', '0.0.0.0')
    PORT      = int(os.environ.get('PORT', 5001))
    local_ip  = get_local_ip()
    url       = f"http://{local_ip}:{PORT}/?token={SECRET_TOKEN}"

    log.info("=== Display Streaming Server ===")
    log.info("Token     : %s", SECRET_TOKEN)
    log.info("URL       : %s", url)
    log.info("FPS       : %d  |  Quality: %d  |  Max clients: %d", FPS, JPEG_QUALITY, MAX_CLIENTS)

    _check_accessibility()
    _print_qr(url)
    _register_mdns(local_ip, PORT)

    socketio.start_background_task(capture_and_stream)
    socketio.run(app, host=HOST, port=PORT)
