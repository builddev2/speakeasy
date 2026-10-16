# Midnight Glass app icon

`Speakeasy.png` is the 1024 × 1024 RGBA master. The rounded tile occupies
approximately 80% of the canvas, with transparent surroundings. The menu-bar
status glyph is separate and unchanged.

Regenerate the ten standard PNG representations and ICNS with:

```sh
.venv/bin/python scripts/make_icon.py
```

Run this explicitly after changing the master: `scripts/build_app.sh` generates
an icon only when the ICNS is missing. Packaging already consumes `Speakeasy.icns`.
`Speakeasy-preview.png` shows 16, 32, 64, 128 and 256 px on dark and light backgrounds.

The master was prepared with the built-in image generation tool from the selected
Midnight Glass reference, then resized to 1024 px with macOS `sips`.
The final edit prompt was:

> Background-extraction cleanup only. Preserve this exact app icon artwork, five
> bars, colors, reflections, rim and geometry. Remove ALL stray detached colored
> pixels and speckles outside its rounded square silhouette, especially above the
> top edge and along right and bottom edges. Make the outer contour perfectly
> clean and antialiased against genuinely transparent surroundings. No glow or
> shadow outside the silhouette needed. Keep centered 10% padding. Output 1024 by
> 1024 pixels. Do not redesign.
