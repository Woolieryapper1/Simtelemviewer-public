"""Text in the web UI must be readable in both themes: every text colour in
web/src/ui/tokens.css is checked against every surface it can sit on, using the
WCAG contrast formula. AA asks for 4.5:1 for normal-size text."""

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parents[2] / "web" / "src" / "ui" / "tokens.css"
AA = 4.5

# Text colours, and the surfaces any of them can be drawn on
TEXT = ["text", "text-2", "text-3", "accent-text", "bad", "good", "warn", "info"]
SURFACES = ["page", "chrome", "card", "raised"]
# Coloured text on its own tinted background (tags, sector boxes, selected chips),
# the tint laid over a card: (text, tint)
ON_TINT = [
    ("bad", "bad-bg"), ("good", "good-bg"), ("warn", "warn-bg"), ("info", "info-bg"),
    ("sector-best", "sector-best-bg"), ("sector-pb", "sector-pb-bg"), ("sector-slow", "sector-slow-bg"),
    ("text", "accent-bg"), ("accent-text", "accent-bg"),
]
# Text on a solid fill: (text, fill)
ON_FILL = [("on-accent", "accent-solid"), ("on-accent", "accent-solid-hover")]


def block(css: str, selector: str) -> str:
    """The body of the first top-level rule whose selector is exactly `selector`."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"{selector} block not found in tokens.css"
    return m.group(1)


def variables(body: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", body)}


def themes() -> dict[str, dict[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(encoding="utf-8"), flags=re.S)  # drop comments
    return {
        "dark": variables(block(css, ':root, [data-theme="dark"]')),
        "light": variables(block(css, '[data-theme="light"]')),
        "light (follow the computer)": variables(block(css, ':root:not([data-theme="dark"])')),
    }


def rgba(value: str) -> tuple[float, float, float, float]:
    """#rrggbb or rgba(r, g, b, a) -> (r, g, b, a) with r, g, b in 0..255."""
    if value.startswith("#"):
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16), 1.0
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", value))
    return r, g, b, a


def over(top: str, bottom: str) -> tuple[float, float, float]:
    """A see-through colour laid over a solid one."""
    r, g, b, a = rgba(top)
    br, bg, bb, _ = rgba(bottom)
    return r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a)


def luminance(rgb) -> float:
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def pairs(t: dict[str, str]):
    """(description, text rgb, background rgb) for everything that must reach AA."""
    for fg in TEXT:
        for bg in SURFACES:
            yield f"{fg} on {bg}", rgba(t[fg])[:3], rgba(t[bg])[:3]
    for fg, tint in ON_TINT:
        yield f"{fg} on {tint} over card", rgba(t[fg])[:3], over(t[tint], t["card"])
    for fg, fill in ON_FILL:
        yield f"{fg} on {fill}", rgba(t[fg])[:3], rgba(t[fill])[:3]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_meets_wcag_aa(theme):
    t = themes()[theme]
    failures = [f"{name}: {contrast(fg, bg):.2f}" for name, fg, bg in pairs(t) if contrast(fg, bg) < AA]
    assert not failures, f"{theme} theme text below {AA}:1 -> " + ", ".join(failures)


def test_light_theme_written_the_same_twice():
    # tokens.css repeats the light colours for "follow the computer" and for the manual choice
    t = themes()
    assert t["light"] == t["light (follow the computer)"]


def test_both_themes_define_the_same_colours():
    t = themes()
    assert set(t["dark"]) == set(t["light"])


if __name__ == "__main__":  # print the table: python backend/tests/test_web_contrast.py
    for theme in ("dark", "light"):
        print(f"\n{theme}")
        for name, fg, bg in pairs(themes()[theme]):
            ratio = contrast(fg, bg)
            print(f"  {ratio:5.2f}  {'ok ' if ratio >= AA else 'LOW'}  {name}")
