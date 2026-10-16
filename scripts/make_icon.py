"""Package the Midnight Glass master into assets/Speakeasy.icns.

Edit assets/Speakeasy.png, then run .venv/bin/python scripts/make_icon.py
before building: build_app.sh only generates an ICNS when it is missing.
Resizing and packaging use the macOS sips and iconutil tools.
"""

import shutil
import subprocess
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def main() -> None:
    iconset = ASSETS / "Speakeasy.iconset"
    iconset.mkdir(exist_ok=True)
    master = ASSETS / "Speakeasy.png"
    for size in (16, 32, 128, 256, 512):
        for scale, suffix in ((1, ""), (2, "@2x")):
            px = size * scale
            out = iconset / f"icon_{size}x{size}{suffix}.png"
            if px == 1024:
                shutil.copyfile(master, out)
            else:
                subprocess.run(
                    ["sips", "-z", str(px), str(px), str(master), "--out", str(out)],
                    check=True,
                    capture_output=True,
                )
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "Speakeasy.icns")],
        check=True,
    )
    print(f"wrote {ASSETS / 'Speakeasy.icns'}")


if __name__ == "__main__":
    main()
