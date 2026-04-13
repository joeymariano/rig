#!/usr/bin/env python3
"""
Generate pixel-art PNG previews of the OLED screens for documentation.

Mirrors the Display class rendering logic exactly (same PixelFont glyphs,
layout constants). Output: docs/screen_*.png at 2× scale.

Usage:
    python3 docs/generate_screens.py
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

# ── Constants (must match Display class in controller.py) ─────────────────────
W, H         = 128, 64
TICKER_Y     = 1
TICKER_GAP   = 14
SCALE        = 2          # output at 2× native resolution (256×128) — integer-scaled, pixel-perfect
BORDER       = 3          # px border around each screen
OLED_ON      = (230, 230, 218, 255)   # warm white — lit OLED pixel
OLED_OFF     = (8,   8,   8,   255)   # near-black  — unlit OLED pixel
OLED_BORDER  = (22,  22,  22,  255)   # slightly lighter for the bezel

OUT_DIR = Path(__file__).parent


# ── Pixel Font (copy of controller.py PixelFont) ─────────────────────────────

class PixelFont:
    """5×7 pixel bitmap font, scalable by an integer factor."""

    CHAR_W = 5
    CHAR_H = 7

    GLYPHS = {
        ' ': ("00000","00000","00000","00000","00000","00000","00000"),
        '0': ("01110","10001","10001","10001","10001","10001","01110"),
        '1': ("00100","01100","00100","00100","00100","00100","01110"),
        '2': ("01110","10001","00001","00010","00100","01000","11111"),
        '3': ("01110","00001","00001","00111","00001","00001","01110"),
        '4': ("00110","01010","10010","11111","00010","00010","00010"),
        '5': ("11111","10000","10000","01111","00001","00001","01110"),
        '6': ("01110","10000","10000","01111","10001","10001","01110"),
        '7': ("11111","00001","00010","00100","01000","01000","01000"),
        '8': ("01110","10001","10001","01110","10001","10001","01110"),
        '9': ("01110","10001","10001","01111","00001","00010","01100"),
        'A': ("01110","10001","10001","11111","10001","10001","10001"),
        'B': ("11110","10001","10001","11110","10001","10001","11110"),
        'C': ("01110","10001","10000","10000","10000","10001","01110"),
        'D': ("11100","10010","10001","10001","10001","10010","11100"),
        'E': ("11111","10000","10000","11110","10000","10000","11111"),
        'F': ("11111","10000","10000","11110","10000","10000","10000"),
        'G': ("01110","10001","10000","10111","10001","10001","01111"),
        'H': ("10001","10001","10001","11111","10001","10001","10001"),
        'I': ("01110","00100","00100","00100","00100","00100","01110"),
        'J': ("00111","00010","00010","00010","10010","10010","01100"),
        'K': ("10001","10010","10100","11000","10100","10010","10001"),
        'L': ("10000","10000","10000","10000","10000","10000","11111"),
        'M': ("10001","11011","10101","10001","10001","10001","10001"),
        'N': ("10001","11001","10101","10011","10001","10001","10001"),
        'O': ("01110","10001","10001","10001","10001","10001","01110"),
        'P': ("11110","10001","10001","11110","10000","10000","10000"),
        'Q': ("01110","10001","10001","10001","10101","10010","01101"),
        'R': ("11110","10001","10001","11110","10100","10010","10001"),
        'S': ("01111","10000","10000","01110","00001","00001","11110"),
        'T': ("11111","00100","00100","00100","00100","00100","00100"),
        'U': ("10001","10001","10001","10001","10001","10001","01110"),
        'V': ("10001","10001","10001","01010","01010","00100","00100"),
        'W': ("10001","10001","10101","10101","10101","11011","10001"),
        'X': ("10001","01010","01010","00100","01010","01010","10001"),
        'Y': ("10001","10001","01010","00100","00100","00100","00100"),
        'Z': ("11111","00001","00010","00100","01000","10000","11111"),
        ':': ("00000","01100","01100","00000","01100","01100","00000"),
        '.': ("00000","00000","00000","00000","00000","01100","01100"),
        '-': ("00000","00000","00000","11111","00000","00000","00000"),
        '!': ("00100","00100","00100","00100","00000","00100","00000"),
        '?': ("01110","10001","00001","00110","00100","00000","00100"),
        '/': ("00001","00010","00100","00100","01000","10000","10000"),
        "'": ("01100","01100","01000","00000","00000","00000","00000"),
        ',': ("00000","00000","00000","00000","00000","01100","01000"),
        '(': ("00010","00100","01000","01000","01000","00100","00010"),
        ')': ("01000","00100","00010","00010","00010","00100","01000"),
        '+': ("00000","00100","00100","11111","00100","00100","00000"),
        '_': ("00000","00000","00000","00000","00000","00000","11111"),
        '#': ("01010","01010","11111","01010","11111","01010","01010"),
    }

    def __init__(self, scale=1):
        self.scale = scale
        self.cw    = self.CHAR_W * scale
        self.ch    = self.CHAR_H * scale
        self.gap   = scale

    def text_width(self, s):
        if not s:
            return 0
        return len(s) * (self.cw + self.gap) - self.gap

    def draw_text(self, draw, x, y, text, fill=255):
        s  = self.scale
        cx = x
        for ch in text:
            glyph = self.GLYPHS.get(ch.upper())
            if glyph:
                for ri, row in enumerate(glyph):
                    for ci, bit in enumerate(row):
                        if bit == '1':
                            px, py = cx + ci * s, y + ri * s
                            if s == 1:
                                draw.point((px, py), fill=fill)
                            else:
                                draw.rectangle(
                                    [px, py, px + s - 1, py + s - 1],
                                    fill=fill)
            cx += self.cw + self.gap


# ── Font instances (must match Display class in controller.py) ────────────────

def make_fonts():
    return dict(
        fs = PixelFont(scale=1),   # small text: ticker, countdown
        fm = PixelFont(scale=1),   # medium text: hints, errors
        fp = PixelFont(scale=2),   # track number prefix
        fe = PixelFont(scale=4),   # large elapsed clock
        fl = PixelFont(scale=3),   # set selector label
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def new_screen():
    img  = Image.new("1", (W, H), 0)
    draw = ImageDraw.Draw(img)
    return img, draw


def upscale(img):
    """Scale 1-bit OLED image to RGBA pixel art with OLED colour palette."""
    scaled = img.resize((W * SCALE, H * SCALE), Image.NEAREST)
    arr    = np.array(scaled, dtype=np.uint8)
    rgba   = np.where(arr[..., None] > 0, OLED_ON, OLED_OFF).astype(np.uint8)
    result = Image.fromarray(rgba, "RGBA")
    bordered = Image.new("RGBA", (W * SCALE + BORDER * 2,
                                   H * SCALE + BORDER * 2), OLED_BORDER)
    bordered.paste(result, (BORDER, BORDER))
    return bordered


# ── Shared sub-renderers (mirrors Display class) ───────────────────────────────

def draw_ticker(img, fonts, prefix, text, offset=0):
    """Scrolling ticker — static prefix left, scrolling text fills the rest."""
    fp = fonts['fp']
    fs = fonts['fs']
    tmp = Image.new("1", (W, 28), 0)
    tdr = ImageDraw.Draw(tmp)

    scroll_x = 2
    if prefix:
        fp.draw_text(tdr, 2, TICKER_Y, prefix, fill=255)
        scroll_x = 2 + fp.text_width(prefix) + 3

    scroll_w = W - scroll_x
    if scroll_w > 0 and text:
        t_w    = fs.text_width(text)
        period = max(1, t_w + TICKER_GAP)
        base   = int(offset) % period
        if base > 0:
            base -= period
        n_copies = scroll_w // period + 2
        stmp = Image.new("1", (scroll_w, 24), 0)
        stdr = ImageDraw.Draw(stmp)
        for n in range(n_copies):
            fs.draw_text(stdr, base + n * period, TICKER_Y, text, fill=255)
        tmp.paste(stmp, (scroll_x, 0))

    img.paste(tmp, (0, 0))


def draw_elapsed(img, fonts, se):
    """Large elapsed clock — mirrors Display._do_render elapsed logic."""
    fe    = fonts['fe']
    el_y0 = 28
    el_h  = H - el_y0 - 2
    elapsed_str = f"{se // 60:02d}:{se % 60:02d}"
    el_w  = fe.text_width(elapsed_str)
    el_x  = max(0, (W - el_w) // 2)
    el_y  = el_y0 + (el_h - fe.ch) // 2
    draw  = ImageDraw.Draw(img)
    fe.draw_text(draw, el_x, el_y, elapsed_str, fill=255)


def draw_drum_icon(draw, x, y, drumless=False):
    """~20×20 drum icon. drumless=True adds an X overlay."""
    draw.rectangle([x+1, y+8,  x+18, y+19], outline=255)
    draw.ellipse(  [x+1, y+5,  x+18, y+11], outline=255)
    draw.line(     [x+5, y,    x+9,  y+6],  fill=255, width=1)
    draw.line(     [x+14, y,   x+10, y+6],  fill=255, width=1)
    if drumless:
        # Pull endpoints 1px inward to prevent width=2 endpoint bleed at corners
        draw.line([ x+2, y+2,  x+17, y+18], fill=255, width=2)
        draw.line([x+17, y+2,  x+2,  y+18], fill=255, width=2)


# ── Screen renderers ──────────────────────────────────────────────────────────

def render_set_picker(fonts, set_label="SET 01"):
    """Boot screen: large set label + drum-mode icons."""
    fl = fonts['fl']
    img, draw = new_screen()
    t_w = fl.text_width(set_label)
    fl.draw_text(draw, (W - t_w) // 2, 2, set_label, fill=255)
    draw_drum_icon(draw, 4,      44, drumless=True)   # ← drumless
    draw_drum_icon(draw, W - 23, 44, drumless=False)  # → drums
    return img


def render_playing(fonts, track_num="01", title="Song Title",
                   bpm="120bpm", platform="SMD",
                   remaining_s=222, set_elapsed_s=222, paused=False):
    """Performance screen: ticker + countdown + elapsed clock."""
    fs = fonts['fs']
    img, draw = new_screen()

    # Ticker
    ticker_text = " ".join(filter(None, [title, bpm, platform]))
    draw_ticker(img, fonts, prefix=track_num, text=ticker_text, offset=0)

    # Countdown / paused
    rem = int(remaining_s)
    cnt = "PAUSED" if paused else f"{rem // 60}:{rem % 60:02d} left"
    cnt_w = fs.text_width(cnt)
    fs.draw_text(draw, (W - cnt_w) // 2, 15, cnt, fill=255)

    # Large elapsed clock
    draw_elapsed(img, fonts, int(set_elapsed_s))

    return img


def render_idle(fonts, track_num="01", title="My Set  Song 1", bpm="140bpm"):
    """Ready state: ticker visible, no countdown, clock at 00:00."""
    img, draw = new_screen()
    ticker_text = " ".join(filter(None, [title, bpm]))
    draw_ticker(img, fonts, prefix=track_num, text=ticker_text, offset=0)
    draw_elapsed(img, fonts, 0)
    return img


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fonts = make_fonts()

    screens = {
        "screen_set_picker": render_set_picker(fonts),
        "screen_playing":    render_playing(fonts),
        "screen_paused":     render_playing(fonts, paused=True),
        "screen_idle":       render_idle(fonts),
    }

    for name, img in screens.items():
        path = OUT_DIR / f"{name}.png"
        upscale(img).save(str(path))
        print(f"Saved {path}")
