"""Frontend colour tokens: both appearances define every token, and the
text/button pairs meet WCAG contrast. Pure file parsing; no browser."""

import re
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "tokens.css"


def _block(css: str, start: int) -> str:
    depth, i = 0, css.index("{", start)
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise ValueError("unbalanced braces")


def _decls(block: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip()
            for m in re.finditer(r"(--[\w-]+)\s*:\s*([^;]+);", block)}


def parse_tokens():
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(), flags=re.S)
    dark = _decls(_block(css, css.index(":root")))
    media = css.index("@media (prefers-color-scheme: light)")
    inner = _block(css, media)
    light = _decls(_block(inner, inner.index(":root")))
    return dark, {**dark, **light}


def _rgba(value: str):
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)", value)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
                float(m.group(4)) if m.group(4) else 1.0)
    raise ValueError(f"not a plain colour: {value}")


def _over(fg, bg):
    a = fg[3]
    return tuple(a * f + (1 - a) * b for f, b in zip(fg[:3], bg[:3]))


def _lum(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(fg: str, bg: str) -> float:
    bg_rgb = _rgba(bg)[:3]
    a, b = _lum(_over(_rgba(fg), bg_rgb)), _lum(bg_rgb)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def test_light_mode_redefines_every_colour_token():
    dark, light_only = parse_tokens()
    css = TOKENS.read_text()
    inner = _block(css, css.index("@media (prefers-color-scheme: light)"))
    redefined = set(_decls(inner))
    skip = {"--sans", "--mono", "--radius", "--text-on-accent", "--text-on-accent-mid"}
    missing = sorted(set(dark) - redefined - skip)
    assert missing == []


def test_text_and_button_contrast_in_both_modes():
    dark, light = parse_tokens()
    for name, t in (("dark", dark), ("light", light)):
        for surface in ("--surface-content", "--surface-elevated"):
            for tok, floor in (("--text-hi", 4.5), ("--text", 4.5), ("--text-mid", 4.5), ("--text-lo", 3.0)):
                ratio = contrast(t[tok], t[surface])
                assert ratio >= floor, f"{name} {tok} on {surface}: {ratio:.2f}"
        assert contrast(t["--on-accent"], t["--accent"]) >= 4.5, name
        assert contrast(t["--on-rec"], t["--rec"]) >= 4.5, name
        for n in range(1, 8):
            ratio = contrast(t[f"--sp-{n}"], t["--surface-content"])
            assert ratio >= 4.5, f"{name} --sp-{n}: {ratio:.2f}"


def test_pinned_accent_values():
    dark, light = parse_tokens()
    assert (dark["--accent"].upper(), dark["--on-accent"].upper()) == ("#E8955A", "#1D1F1E")
    assert (light["--accent"].upper(), light["--on-accent"].upper()) == ("#B8551F", "#FFFFFF")
    assert (dark["--rec"].upper(), light["--rec"].upper()) == ("#FF5A4E", "#D33A2F")
