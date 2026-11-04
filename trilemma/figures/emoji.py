"""Colour emoji as RGBA images for matplotlib, using the closest emoji font this OS has.

The paper's figures were rendered with Apple Color Emoji (macOS). Elsewhere we fall back to
whatever colour emoji font is installed, in this order:

  1. $TRILEMMA_EMOJI_FONT (any .ttf/.ttc/.otf colour emoji font you point at)
  2. Apple Color Emoji        macOS                      exact match with the paper
  3. Noto Color Emoji         most Linux distributions   (apt install fonts-noto-color-emoji)
  4. Segoe UI Emoji           Windows
  5. any font fontconfig reports for the "emoji" family (fc-match)

If none is found, a flat stand-in icon is drawn and a warning is printed, so the figure still
renders; only the two disclosure icons look different.

    from trilemma.figures.emoji import render_emoji
    img = render_emoji("\U0001F4BC")   # PIL.Image, RGBA, cropped to the glyph
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import warnings
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_CANDIDATES = [
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto-emoji/NotoColorEmoji.ttf",
    "/usr/local/share/fonts/NotoColorEmoji.ttf",
    str(Path.home() / ".local/share/fonts/NotoColorEmoji.ttf"),
    str(Path.home() / "Library/Fonts/NotoColorEmoji.ttf"),
    "C:/Windows/Fonts/seguiemj.ttf",
]


def _fc_match() -> str | None:
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "emoji:color"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and Path(out).exists() else None


@lru_cache(maxsize=1)
def emoji_font_path() -> str | None:
    env = os.environ.get("TRILEMMA_EMOJI_FONT")
    if env:
        if Path(env).exists():
            return env
        warnings.warn(f"TRILEMMA_EMOJI_FONT={env} does not exist; searching the usual places")
    for p in _CANDIDATES:
        if Path(p).exists():
            return p
    return _fc_match()


def _crop(im: Image.Image) -> Image.Image:
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def _render_sbix(path: str, text: str) -> Image.Image | None:
    """Apple Color Emoji: shape with harfbuzz (handles skin-tone / ZWJ sequences) and take the
    glyph's bitmap straight out of the sbix table. This is how the paper's figures were made."""
    try:
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    tt = TTFont(path, fontNumber=0, lazy=True)
    if "sbix" not in tt and "CBDT" not in tt:
        return None
    face = hb.Face(Path(path).read_bytes())
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hb.Font(face), buf)
    name = tt.getGlyphName(buf.glyph_infos[0].codepoint)
    if "sbix" in tt:                                   # Apple Color Emoji
        strikes = tt["sbix"].strikes
        strike = strikes[160] if 160 in strikes else strikes[max(strikes)]
        glyph = strike.glyphs.get(name)
    else:                                              # Noto Color Emoji (CBDT bitmaps)
        glyph = next((s[name] for s in tt["CBDT"].strikeData if name in s), None)
    data = getattr(glyph, "imageData", None) if glyph is not None else None
    if not data:
        return None
    if not data.startswith(b"\x89PNG"):                # CBDT formats 17/18 prefix metrics before the PNG
        i = data.find(b"\x89PNG")
        if i < 0:
            return None
        data = data[i:]
    return _crop(Image.open(io.BytesIO(data)).convert("RGBA"))


def _render_pillow(path: str, text: str) -> Image.Image | None:
    """Any colour font FreeType can draw (CBDT: Noto; COLR: Segoe UI Emoji; sbix)."""
    sizes = (109, 160, 136, 96, 72, 64)            # CBDT fonts only load at their bitmap size
    for size in sizes:
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    else:
        return None
    attempts = [text]
    if len(text) > 1:                              # without raqm, a skin-tone sequence draws as two glyphs
        attempts.append(text[0])
    for t in attempts:
        im = Image.new("RGBA", (size * 3, size * 2), (0, 0, 0, 0))
        try:
            ImageDraw.Draw(im).text((size // 2, size // 4), t, font=font, embedded_color=True)
        except (OSError, ValueError):
            continue
        im = _crop(im)
        if im.getbbox() and (t == text[0] or im.width < 1.6 * im.height):
            return im
    return None


_FALLBACK = {"\U0001F4BC": ("#8d5a2b", "W"), "\U0001F468": ("#e0ac69", "P")}


def _render_fallback(text: str) -> Image.Image:
    color, letter = _FALLBACK.get(text[:1], ("#888888", "?"))
    im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((8, 8, 152, 152), fill=color)
    try:
        f = ImageFont.load_default(size=96)
    except TypeError:
        f = ImageFont.load_default()
    d.text((80, 80), letter, fill="white", font=f, anchor="mm")
    return im


_TARGET = 159   # longest side of an Apple Color Emoji glyph at the 160 strike; the figures' zooms assume it


def _normalize(im: Image.Image) -> Image.Image:
    s = _TARGET / max(im.size)
    return im if abs(s - 1) < 0.02 else im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)


@lru_cache(maxsize=None)
def render_emoji(text: str) -> Image.Image:
    path = emoji_font_path()
    if path:
        for fn in (_render_sbix, _render_pillow):
            try:
                im = fn(path, text)
            except Exception:                      # a font we can't parse: try the next renderer
                im = None
            if im is not None:
                return _normalize(im)
    warnings.warn("no colour emoji font found (install fonts-noto-color-emoji, or set "
                  "TRILEMMA_EMOJI_FONT); drawing stand-in icons for the disclosure markers")
    return _normalize(_render_fallback(text))


if __name__ == "__main__":                         # python -m trilemma.figures.emoji [out.png]
    out = sys.argv[1] if len(sys.argv) > 1 else "emoji_check.png"
    ims = [render_emoji(e) for e in ("\U0001F468\U0001F3FB", "\U0001F4BC")]
    h = max(i.height for i in ims)
    sheet = Image.new("RGBA", (sum(i.width for i in ims) + 20, h), (255, 255, 255, 255))
    x = 0
    for i in ims:
        sheet.paste(i, (x, 0), i)
        x += i.width + 20
    sheet.save(out)
    print(f"font: {emoji_font_path()}\nwrote {out}")
