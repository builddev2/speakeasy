"""Generate assets/Speakeasy.icns — no external assets or tools beyond macOS.

The icon is the app's own visual identity: the rainbow waveform bars from
the dictation overlay, glowing on a dark glassy rounded square. Rendered
once at 1024 px with AppKit, downscaled with sips, assembled with iconutil.
"""

import math
import subprocess
import sys
from pathlib import Path

import objc  # noqa: F401  (ensures pyobjc is loaded before AppKit)
from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSShadow,
    NSPNGFileType,
)

SIZE = 1024.0
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def draw(size: float) -> NSImage:
    image = NSImage.alloc().initWithSize_((size, size))
    image.lockFocus()

    # macOS icon grid: the squircle plate fills ~80% of the canvas.
    inset = size * 0.10
    plate = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = size * 0.18
    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        plate, radius, radius
    )

    # Dark glass background with a subtle vertical sheen.
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.10, 0.10, 0.14, 1.0),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.17, 0.16, 0.24, 1.0),
    ).drawInBezierPath_angle_(path, 90.0)
    NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10),
        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
    ).drawInBezierPath_angle_(path, -90.0)

    # Rainbow waveform bars, mid-word: heights follow a gentle voice curve.
    n = 7
    bar_w = size * 0.052
    gap = size * 0.036
    total = n * bar_w + (n - 1) * gap
    x0 = (size - total) / 2
    max_h = size * 0.42
    min_h = size * 0.10
    for i in range(n):
        frac = 0.35 + 0.65 * math.sin(math.pi * (i + 0.5) / n) * (
            0.75 + 0.25 * math.cos(i * 2.1)
        )
        h = min_h + (max_h - min_h) * frac
        x = x0 + i * (bar_w + gap)
        y = (size - h) / 2
        hue = 0.8 * i / (n - 1)
        bar = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, bar_w, h), bar_w / 2, bar_w / 2
        )
        NSGraphicsContext.saveGraphicsState()
        glow = NSShadow.alloc().init()
        glow.setShadowOffset_((0, 0))
        glow.setShadowBlurRadius_(size * 0.02)
        glow.setShadowColor_(
            NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
                hue, 0.7, 1.0, 0.9
            )
        )
        glow.set()
        NSColor.colorWithCalibratedHue_saturation_brightness_alpha_(
            hue, 0.55, 1.0, 0.95
        ).setFill()
        bar.fill()
        NSGraphicsContext.restoreGraphicsState()
        # Glassy highlight on the top half of each bar.
        NSGradient.alloc().initWithStartingColor_endingColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.0),
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.35),
        ).drawInBezierPath_angle_(bar, 90.0)

    image.unlockFocus()
    return image


def save_png(image: NSImage, path: Path) -> None:
    tiff = image.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    png = rep.representationUsingType_properties_(NSPNGFileType, None)
    png.writeToFile_atomically_(str(path), True)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    iconset = ASSETS / "Speakeasy.iconset"
    iconset.mkdir(exist_ok=True)
    master = iconset / "icon_512x512@2x.png"
    save_png(draw(SIZE), master)

    sizes = [16, 32, 64, 128, 256, 512]
    for s in sizes:
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = s * scale
            if px > 1024 or (s == 64 and scale == 2):
                continue
            out = iconset / f"icon_{s}x{s}{suffix}.png"
            if out == master:
                continue
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(master), "--out", str(out)],
                check=True,
                capture_output=True,
            )
    # iconutil ignores non-standard names; 64px isn't part of the set.
    (iconset / "icon_64x64.png").unlink(missing_ok=True)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "Speakeasy.icns")],
        check=True,
    )
    print(f"wrote {ASSETS / 'Speakeasy.icns'}")


if __name__ == "__main__":
    sys.exit(main())
