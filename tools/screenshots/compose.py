#!/usr/bin/env python3
"""Turn raw popover captures into README images.

    tools/screenshots/compose.py RAW_DIR

Each RAW_DIR/<name>.png from capture.sh is cut to its 1px frame (the desktop behind the popover
and its shadow margin are dropped), given rounded corners and a soft shadow, and put on a Tokyo Night
backdrop -> docs/screenshots/<name>.png. A few of them are also laid side by side in docs/hero.png.
"""

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "screenshots"
FRAME = (0x3F, 0x53, 0x7D)  # hypr-ui colour.frame
SCALE = 1.2  # monitor scale the captures were taken at
RADIUS = round(12 * SCALE)  # hypr-ui radius.surface
BG_TOP, BG_BOTTOM = (0x1A, 0x1B, 0x26), (0x2A, 0x1F, 0x3D)  # tokyo night night -> a purple dusk


def near(px, ref, tol=28):
    return all(abs(a - b) <= tol for a, b in zip(px[:3], ref))


def trim_to_frame(im: Image.Image) -> Image.Image:
    """Crop to the 1px frame, which sits hypr-ui's 16px shadow margin in from each edge.

    Only a band around that margin is searched, so blue controls inside the card can't match.
    Several scan lines vote, so a row of the frame hidden behind a control doesn't matter."""
    im = im.convert("RGB")
    w, h = im.size
    px = im.load()
    m = round(16 * SCALE)
    band = range(max(0, m - 8), m + 8)

    def edge(points):
        hits = [p for p in points if p is not None]
        return min(hits) if hits else m

    def first(seq, f):
        return next((i for i in seq if f(i)), None)

    rows = [h * k // 8 for k in range(2, 7)]
    cols = [w * k // 8 for k in range(2, 7)]
    left = edge(first(band, lambda x: near(px[x, y], FRAME)) for y in rows)
    right = w - 1 - edge(first(band, lambda d: near(px[w - 1 - d, y], FRAME)) for y in rows)
    top = edge(first(band, lambda y: near(px[x, y], FRAME)) for x in cols)
    bottom = h - 1 - edge(first(band, lambda d: near(px[x, h - 1 - d], FRAME)) for x in cols)
    return im.crop((left, top, right + 1, bottom + 1))


def rounded(im: Image.Image, r: int = RADIUS) -> Image.Image:
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r, fill=255)
    out = im.convert("RGBA")
    out.putalpha(ImageChops.multiply(out.getchannel("A"), mask))
    return out


def gradient(size) -> Image.Image:
    w, h = size
    col = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        col.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM)))
    bg = col.resize((w, h))
    glow = Image.new("L", (w, h), 0)  # a faint blue glow behind the cards
    ImageDraw.Draw(glow).ellipse((w * 0.15, h * 0.05, w * 0.85, h * 0.95), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(min(w, h) / 4))
    return Image.composite(Image.new("RGB", (w, h), (0x3D, 0x59, 0xA1)), bg, glow).convert("RGBA")


def shadowed(bg: Image.Image, card: Image.Image, xy) -> None:
    sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    a = card.getchannel("A").point(lambda v: v * 0.55)
    sh.paste((0, 0, 0, 255), (xy[0], xy[1] + 14), a)
    bg.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))
    bg.alpha_composite(card, xy)


def single(card: Image.Image, pad: int = 56) -> Image.Image:
    bg = gradient((card.width + 2 * pad, card.height + 2 * pad))
    shadowed(bg, card, (pad, pad))
    return bg


def row(cards: list[Image.Image], pad: int = 64, gap: int = 40) -> Image.Image:
    h = max(c.height for c in cards) + 2 * pad
    w = sum(c.width for c in cards) + gap * (len(cards) - 1) + 2 * pad
    bg = gradient((w, h))
    x = pad
    for c in cards:
        shadowed(bg, c, (x, pad + (h - 2 * pad - c.height) // 2))
        x += c.width + gap
    return bg


def main(raw: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cards = {}
    for f in sorted(raw.glob("*.png")):
        cards[f.stem] = rounded(trim_to_frame(Image.open(f)))
        single(cards[f.stem]).convert("RGB").save(OUT / f.name, optimize=True)
        print("docs/screenshots/" + f.name, cards[f.stem].size)

    spot = ROOT / "hyprspot" / "docs" / "screenshots"
    hero = [cards[k] for k in ("wifi", "sound", "hyprcal", "power") if k in cards]
    bt = cards.get("bt")
    if bt is not None and (spot / "compact.png").exists():
        # stack bluetooth over the hyprspot mini-player as the last column
        # hyprspot's own shot sits on a backdrop; cut out just the player card (64px in, 24px corners)
        sp = Image.open(spot / "compact.png").convert("RGB")
        sp = rounded(sp.crop((56, 56, sp.width - 56, sp.height - 56)), 24)
        sp = sp.resize((bt.width, round(sp.height * bt.width / sp.width)), Image.LANCZOS)
        col = Image.new("RGBA", (bt.width, bt.height + 40 + sp.height), (0, 0, 0, 0))
        col.alpha_composite(bt, (0, 0))
        col.alpha_composite(sp, (0, bt.height + 40))
        hero.append(col)
    if hero:
        img = row(hero)
        img.convert("RGB").save(ROOT / "docs" / "hero.png", optimize=True)
        print("docs/hero.png", img.size)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
