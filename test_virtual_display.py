"""
POC test — does CGVirtualDisplay work on M3 Pro as a bare Python script?

Run this, then immediately open System Settings > Displays.
If a new 1920x1080 display appears: bare script works, no .app bundle needed.
If nothing appears (no error, no display): .app bundle required.
If you get an exception: check the error message below.

Usage:
    python3 test_virtual_display.py
"""
import time
import sys

try:
    from Quartz import CGVirtualDisplayDescriptor, CGVirtualDisplay, CGDisplayBounds
    from Foundation import NSSize
except ImportError as e:
    print(f"ERROR: missing dependency — {e}")
    print("Run: pip install pyobjc-framework-Quartz")
    sys.exit(1)

print("Step 1: Building display descriptor (1920x1080, 96 PPI)...")
desc = CGVirtualDisplayDescriptor.new()
desc.setName_("StreamDisplay-Test")
desc.setMaxPixelsWide_(1920)
desc.setMaxPixelsHigh_(1080)
# Physical size that gives ~96 PPI: 1920/(96/25.4) ≈ 508mm, 1080/(96/25.4) ≈ 286mm
desc.setSizeInMillimeters_(NSSize(508, 286))

print("Step 2: Creating virtual display via CGVirtualDisplay...")
display = CGVirtualDisplay.alloc().initWithDescriptor_(desc)

if display is None:
    print()
    print("RESULT: FAIL — CGVirtualDisplay returned None.")
    print("The process needs a signed .app bundle with the right entitlements.")
    print("Next step: wrap with PyInstaller + Info.plist + code signing.")
    sys.exit(1)

display_id = display.displayID()
bounds = CGDisplayBounds(display_id)

print()
print("RESULT: SUCCESS — Virtual display created.")
print(f"  displayID : {display_id}")
print(f"  origin    : ({int(bounds.origin.x)}, {int(bounds.origin.y)})")
print(f"  size      : {int(bounds.size.width)}x{int(bounds.size.height)}")
print()
print("Check System Settings > Displays — you should see a new 1920x1080 display.")
print("Keeping it alive for 15 seconds...")
time.sleep(15)

print("Destroying virtual display...")
del display
print("Done. Virtual display should have disappeared from System Settings.")
