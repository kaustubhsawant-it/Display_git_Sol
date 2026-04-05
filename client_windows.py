"""
StreamDisplay — Windows pygame client (Phase E1 + E2).

Usage:
    python client_windows.py --url http://192.168.x.x:5001 --token TOKEN

Keys:
    F11   toggle fullscreen / windowed
    Esc   quit

Cursor handoff (E2 — seamless KVM):
    When the Mac cursor hits HANDOFF_EDGE the server emits cursor_grab.
    This client then captures the Windows mouse (pygame.event.set_grab).
    Moving the Windows mouse to RELEASE_EDGE sends cursor_release_request back.

    Set RELEASE_EDGE to the Windows-display edge that faces the Mac display:
      - Mac is to the LEFT  of Windows  →  RELEASE_EDGE = left   (default)
      - Mac is to the RIGHT of Windows  →  RELEASE_EDGE = right
      - etc.
"""

import argparse
import os
import queue
import sys

import cv2
import numpy as np
import pygame
import socketio as sio_lib

# ── Key mapping: pygame key constant → server key_press name ─────────────────
_KEY_MAP = {
    pygame.K_BACKSPACE: 'Backspace',
    pygame.K_RETURN:    'Enter',
    pygame.K_KP_ENTER:  'Enter',
    pygame.K_TAB:       'Tab',
    pygame.K_ESCAPE:    'Escape',
    pygame.K_DELETE:    'Delete',
    pygame.K_UP:        'ArrowUp',
    pygame.K_DOWN:      'ArrowDown',
    pygame.K_LEFT:      'ArrowLeft',
    pygame.K_RIGHT:     'ArrowRight',
    pygame.K_HOME:      'Home',
    pygame.K_END:       'End',
    pygame.K_PAGEUP:    'PageUp',
    pygame.K_PAGEDOWN:  'PageDown',
}
_BTN_MAP = {1: 'left', 2: 'middle', 3: 'right'}

RELEASE_EDGE   = os.environ.get('RELEASE_EDGE', 'left')
MOVE_THROTTLE  = 50   # ms between mouse_move emissions


# ── Frame queue (socketio thread → pygame thread) ────────────────────────────
_frame_q: queue.Queue = queue.Queue(maxsize=2)


def _make_sio(url: str, token: str) -> sio_lib.Client:
    """Build and connect the python-socketio client."""
    sio = sio_lib.Client(
        reconnection=True,
        reconnection_attempts=0,
        logger=False,
        engineio_logger=False,
    )

    @sio.event
    def connect():
        print(f"[StreamDisplay] Connected to {url}")
        sio.emit('register', {'client_type': 'windows'})

    @sio.event
    def disconnect():
        print("[StreamDisplay] Disconnected — will reconnect…")

    @sio.on('screen_frame')
    def on_frame(data):
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        # Drop the oldest frame if the queue is full (keeps latency low)
        if _frame_q.full():
            try:
                _frame_q.get_nowait()
            except queue.Empty:
                pass
        try:
            _frame_q.put_nowait(img)
        except queue.Full:
            pass

    @sio.on('cursor_grab')
    def on_cursor_grab(_data=None):
        """Server says: Windows, take the cursor."""
        pygame.event.set_grab(True)
        pygame.mouse.set_visible(False)

    @sio.on('cursor_release')
    def on_cursor_release(_data=None):
        """Server says: give the cursor back to Windows."""
        pygame.event.set_grab(False)
        pygame.mouse.set_visible(True)

    sio.connect(
        f"{url.rstrip('/')}?token={token}",
        transports=['websocket'],
    )
    return sio


def _image_rect(win_w: int, win_h: int, img_w: int, img_h: int):
    """Return (left, top, draw_w, draw_h) for letterbox / pillarbox scaling."""
    if img_w == 0 or img_h == 0:
        return 0, 0, win_w, win_h
    scale = min(win_w / img_w, win_h / img_h)
    dw = int(img_w * scale)
    dh = int(img_h * scale)
    return (win_w - dw) // 2, (win_h - dh) // 2, dw, dh


def _to_frame_coords(px, py, il, it, iw, ih, img_w, img_h):
    """Map a pygame window pixel to a Mac-frame pixel. Returns (fx, fy) or None."""
    rx, ry = px - il, py - it
    if rx < 0 or rx >= iw or ry < 0 or ry >= ih or iw == 0 or ih == 0:
        return None
    return round(rx / iw * img_w), round(ry / ih * img_h)


def _hit_release_edge(px, py, win_w, win_h, edge, margin=4):
    if edge == 'left'   and px <= margin:          return True
    if edge == 'right'  and px >= win_w - margin:  return True
    if edge == 'top'    and py <= margin:           return True
    if edge == 'bottom' and py >= win_h - margin:  return True
    return False


def run(url: str, token: str) -> None:
    pygame.init()
    info          = pygame.display.Info()
    win_w, win_h  = info.current_w, info.current_h

    screen = pygame.display.set_mode(
        (win_w, win_h),
        pygame.FULLSCREEN | pygame.NOFRAME,
    )
    pygame.display.set_caption("StreamDisplay")
    clock = pygame.time.Clock()

    sio = _make_sio(url, token)

    current_frame = None
    img_w, img_h  = 0, 0
    il = it = 0
    iw, ih = win_w, win_h
    last_move_ms  = 0
    grabbed       = False   # local mirror of cursor_grab state

    running = True
    while running:

        # ── Pull latest frame ─────────────────────────────────────────────
        try:
            current_frame = _frame_q.get_nowait()
            img_h, img_w  = current_frame.shape[:2]
            il, it, iw, ih = _image_rect(win_w, win_h, img_w, img_h)
        except queue.Empty:
            pass

        # ── Render ────────────────────────────────────────────────────────
        screen.fill((0, 0, 0))
        if current_frame is not None and iw > 0 and ih > 0:
            resized = cv2.resize(current_frame, (iw, ih), interpolation=cv2.INTER_LINEAR)
            surf    = pygame.surfarray.make_surface(np.transpose(resized, (1, 0, 2)))
            screen.blit(surf, (il, it))
        pygame.display.flip()
        clock.tick(60)

        # ── Sync local grabbed state from pygame ─────────────────────────
        grabbed = pygame.event.get_grab()

        # ── Events ────────────────────────────────────────────────────────
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE and not grabbed:
                    running = False
                elif event.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                else:
                    key_name = _KEY_MAP.get(event.key)
                    if key_name is None and event.unicode and event.unicode.isprintable():
                        key_name = event.unicode
                    if key_name:
                        sio.emit('key_press', {'key': key_name})

            elif event.type == pygame.MOUSEMOTION:
                now = pygame.time.get_ticks()
                if now - last_move_ms >= MOVE_THROTTLE:
                    last_move_ms = now
                    px, py = event.pos
                    if grabbed:
                        # E2: relative delta mode
                        dx, dy = event.rel
                        if dx or dy:
                            sio.emit('mouse_move_delta', {'dx': dx, 'dy': dy})
                        # Release if mouse hits the edge facing the Mac
                        if _hit_release_edge(px, py, win_w, win_h, RELEASE_EDGE):
                            pygame.event.set_grab(False)
                            pygame.mouse.set_visible(True)
                            sio.emit('cursor_release_request', {})
                    else:
                        pt = _to_frame_coords(px, py, il, it, iw, ih, img_w, img_h)
                        if pt:
                            sio.emit('mouse_move', {'x': pt[0], 'y': pt[1]})

            elif event.type == pygame.MOUSEBUTTONDOWN:
                button = _BTN_MAP.get(event.button, 'left')
                pt = _to_frame_coords(*event.pos, il, it, iw, ih, img_w, img_h)
                if pt:
                    sio.emit('mouse_click', {'x': pt[0], 'y': pt[1], 'button': button})

            elif event.type == pygame.MOUSEWHEEL:
                px, py = pygame.mouse.get_pos()
                pt = _to_frame_coords(px, py, il, it, iw, ih, img_w, img_h)
                if pt:
                    sio.emit('scroll', {'x': pt[0], 'y': pt[1], 'delta': -event.y * 100})

    pygame.quit()
    try:
        sio.disconnect()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description='StreamDisplay Windows Client')
    parser.add_argument('--url',   required=True, help='Server URL  e.g. http://192.168.1.10:5001')
    parser.add_argument('--token', required=True, help='Auth token shown on server startup')
    args = parser.parse_args()
    run(args.url, args.token)


if __name__ == '__main__':
    main()
