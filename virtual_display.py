"""
CGVirtualDisplay wrapper for Display_git_Sol.

Creates a 1920x1080 virtual display via the CoreGraphics public API.
Exposes find_mss_monitor_index() which maps the virtual display's CoreGraphics
displayID to the correct index in mss.mss().monitors — this is the only correct
way to target a specific display with mss, which has no displayID-based API.

Usage:
    from virtual_display import VirtualDisplay

    with VirtualDisplay() as vd:
        index = vd.find_mss_monitor_index()
        # pass index to server.py capture loop via MONITOR_INDEX or directly
"""
import logging
import mss
from Quartz import CGVirtualDisplayDescriptor, CGVirtualDisplay, CGDisplayBounds
from Foundation import NSSize

log = logging.getLogger(__name__)

VIRTUAL_WIDTH  = 1920
VIRTUAL_HEIGHT = 1080
# Physical size for ~96 PPI: pixels / (ppi / 25.4)
_MM_WIDTH  = round(VIRTUAL_WIDTH  / (96 / 25.4))   # ≈ 508 mm
_MM_HEIGHT = round(VIRTUAL_HEIGHT / (96 / 25.4))   # ≈ 286 mm


class VirtualDisplayError(RuntimeError):
    pass


class VirtualDisplay:
    def __init__(self):
        desc = CGVirtualDisplayDescriptor.new()
        desc.setName_("StreamDisplay")
        desc.setMaxPixelsWide_(VIRTUAL_WIDTH)
        desc.setMaxPixelsHigh_(VIRTUAL_HEIGHT)
        desc.setSizeInMillimeters_(NSSize(_MM_WIDTH, _MM_HEIGHT))

        self._display = CGVirtualDisplay.alloc().initWithDescriptor_(desc)

        if self._display is None:
            raise VirtualDisplayError(
                "CGVirtualDisplay returned None. "
                "The process likely needs to run inside a signed .app bundle. "
                "Run test_virtual_display.py first to confirm."
            )

        self._display_id = self._display.displayID()
        bounds = CGDisplayBounds(self._display_id)
        self._origin_x = int(bounds.origin.x)
        self._origin_y = int(bounds.origin.y)

        log.info(
            "Virtual display created: displayID=%d  origin=(%d, %d)  size=%dx%d",
            self._display_id, self._origin_x, self._origin_y,
            VIRTUAL_WIDTH, VIRTUAL_HEIGHT,
        )

    @property
    def display_id(self) -> int:
        return self._display_id

    def find_mss_monitor_index(self) -> int:
        """
        Map the virtual display to an mss monitor list index.

        Strategy: CGDisplayBounds() gives us the display's screen-space origin.
        We match that against sct.monitors[1:] (index 0 is the combined bounding
        box of all monitors, not a real screen). Returns the matching index.

        Raises VirtualDisplayError if no match is found — this can happen if
        macOS hasn't registered the display yet (give it ~1s after creation).
        """
        with mss.mss() as sct:
            for i, m in enumerate(sct.monitors[1:], start=1):
                if m['left'] == self._origin_x and m['top'] == self._origin_y:
                    log.info("Virtual display maps to mss monitor index %d", i)
                    return i

        raise VirtualDisplayError(
            f"Could not find mss monitor matching displayID={self._display_id} "
            f"at origin ({self._origin_x}, {self._origin_y}). "
            "macOS may not have registered the display yet — retry after a short delay."
        )

    def destroy(self):
        if self._display is not None:
            del self._display
            self._display = None
            log.info("Virtual display destroyed (displayID was %d)", self._display_id)

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.destroy()
