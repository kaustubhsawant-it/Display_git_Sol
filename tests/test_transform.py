"""
Unit tests for Python-side math and validation logic.

These functions are inlined in server.py — we duplicate the pure logic here
so the tests have no dependency on Flask, SocketIO, or screen capture.
"""
import pytest


# ── Helpers mirrored from server.py ──────────────────────────────────────────

def _valid_coords(x, y, bounds):
    return (isinstance(x, (int, float)) and isinstance(y, (int, float))
            and 0 <= x <= bounds['width'] and 0 <= y <= bounds['height'])


def _scroll_clicks(delta):
    """Convert JS wheel deltaY to pyautogui scroll clicks."""
    return -round(delta / 100)


SPECIAL_KEYS = {
    'Backspace': 'backspace', 'Enter': 'enter', 'Tab': 'tab',
    'Escape': 'escape', 'Delete': 'delete', ' ': 'space',
    'ArrowUp': 'up', 'ArrowDown': 'down',
    'ArrowLeft': 'left', 'ArrowRight': 'right',
    'Home': 'home', 'End': 'end',
    'PageUp': 'pageup', 'PageDown': 'pagedown',
}


def resolve_key(key):
    """Return (action, value): action is 'press', 'type', or 'ignore'."""
    if not isinstance(key, str) or len(key) > 20:
        return ('ignore', None)
    if key in SPECIAL_KEYS:
        return ('press', SPECIAL_KEYS[key])
    if len(key) == 1 and key.isprintable():
        return ('type', key)
    return ('ignore', None)


def portrait_unrotate(fx, fy, canvas_width):
    """
    Undo the server's 90° CW rotation for portrait mode.

    Server rotates: (x, y) → rotated frame where x-axis = original y-axis.
    Client must reverse: origX = fy, origY = canvas_width - fx
    """
    return (fy, canvas_width - fx)


# ── Coordinate validation ─────────────────────────────────────────────────────

class TestValidCoords:
    bounds = {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}

    def test_origin_valid(self):
        assert _valid_coords(0, 0, self.bounds)

    def test_max_corner_valid(self):
        assert _valid_coords(1920, 1080, self.bounds)

    def test_midpoint_valid(self):
        assert _valid_coords(960, 540, self.bounds)

    def test_float_coords_valid(self):
        assert _valid_coords(100.5, 200.7, self.bounds)

    def test_negative_x_invalid(self):
        assert not _valid_coords(-1, 0, self.bounds)

    def test_negative_y_invalid(self):
        assert not _valid_coords(0, -1, self.bounds)

    def test_x_exceeds_width_invalid(self):
        assert not _valid_coords(1921, 0, self.bounds)

    def test_y_exceeds_height_invalid(self):
        assert not _valid_coords(0, 1081, self.bounds)

    def test_string_x_invalid(self):
        assert not _valid_coords('100', 0, self.bounds)

    def test_none_y_invalid(self):
        assert not _valid_coords(0, None, self.bounds)

    def test_both_none_invalid(self):
        assert not _valid_coords(None, None, self.bounds)


# ── Scroll delta conversion ───────────────────────────────────────────────────

class TestScrollClicks:
    def test_scroll_down_positive_delta(self):
        # JS deltaY positive = scroll down = negative clicks
        assert _scroll_clicks(100) == -1

    def test_scroll_up_negative_delta(self):
        assert _scroll_clicks(-100) == 1

    def test_large_delta(self):
        assert _scroll_clicks(300) == -3

    def test_small_delta_rounds_to_zero(self):
        assert _scroll_clicks(40) == 0

    def test_fractional_rounds(self):
        # -round(150/100) = -round(1.5) = -2 (Python banker's rounding)
        assert _scroll_clicks(150) == -round(1.5)

    def test_zero_delta(self):
        assert _scroll_clicks(0) == 0


# ── Key resolution ────────────────────────────────────────────────────────────

class TestResolveKey:
    def test_special_backspace(self):
        assert resolve_key('Backspace') == ('press', 'backspace')

    def test_special_arrow(self):
        assert resolve_key('ArrowUp') == ('press', 'up')

    def test_special_space(self):
        assert resolve_key(' ') == ('press', 'space')

    def test_printable_letter(self):
        assert resolve_key('a') == ('type', 'a')

    def test_printable_digit(self):
        assert resolve_key('5') == ('type', '5')

    def test_all_special_keys_present(self):
        for js_key in SPECIAL_KEYS:
            action, _ = resolve_key(js_key)
            assert action == 'press', f"{js_key!r} should map to 'press'"

    def test_unknown_key_ignored(self):
        assert resolve_key('F12') == ('ignore', None)

    def test_too_long_key_ignored(self):
        assert resolve_key('x' * 21) == ('ignore', None)

    def test_non_string_ignored(self):
        assert resolve_key(42) == ('ignore', None)

    def test_empty_string_ignored(self):
        # len == 0, not printable (isprintable on empty returns True but len check fails)
        action, _ = resolve_key('')
        assert action == 'ignore'


# ── Portrait coordinate un-rotation ──────────────────────────────────────────

class TestPortraitUnrotate:
    """
    Server-side rotation: cv2.ROTATE_90_CLOCKWISE on a W×H frame
    produces an H×W frame. Original (ox, oy) → rotated (H-1-oy, ox)
    for pixel-precise mapping. The client's inverse is: origX=fy, origY=canvas_width-fx
    where canvas_width = H (the rotated frame's width = original frame's height).
    """

    def test_top_left_maps_to_bottom_left_original(self):
        # Rotated top-left (0, 0) → original bottom-left (0, 1920)
        # canvas_width here = 1080 (rotated frame width = original height)
        origX, origY = portrait_unrotate(0, 0, canvas_width=1080)
        assert origX == 0
        assert origY == 1080

    def test_top_right_maps_to_top_left_original(self):
        # Rotated top-right (1080, 0) → original top-left (0, 0)
        origX, origY = portrait_unrotate(1080, 0, canvas_width=1080)
        assert origX == 0
        assert origY == 0

    def test_bottom_right_maps_to_top_right_original(self):
        # Rotated bottom-right (1080, 1920) → original (1920, 0)
        origX, origY = portrait_unrotate(1080, 1920, canvas_width=1080)
        assert origX == 1920
        assert origY == 0

    def test_center(self):
        origX, origY = portrait_unrotate(540, 960, canvas_width=1080)
        assert origX == 960
        assert origY == 540

    def test_identity_when_zero_fx(self):
        origX, origY = portrait_unrotate(0, 500, canvas_width=1080)
        assert origX == 500
        assert origY == 1080
