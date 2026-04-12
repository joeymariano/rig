#!/usr/bin/env python3
"""
Generate pixel-art PNG previews of the OLED screens for documentation.

Mirrors the Display class rendering logic exactly (same fonts, layout constants,
elapsed-clock algorithm). Output: docs/screen_*.png at 4× scale.

Usage:
    python3 docs/generate_screens.py
"""

import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Constants (must match Display class in controller.py) ─────────────────────
W, H         = 128, 64
TICKER_Y     = 1
TICKER_GAP   = 14
SCALE        = 3          # output at 3× native resolution (384×192) — integer-scaled, pixel-perfect
BORDER       = 6          # px border around each screen
OLED_ON      = (230, 230, 218, 255)   # warm white — lit OLED pixel
OLED_OFF     = (8,   8,   8,   255)   # near-black  — unlit OLED pixel
OLED_BORDER  = (22,  22,  22,  255)   # slightly lighter for the bezel

SANS   = "/usr/share/fonts/truetype/liberation/LiberationSans"
NARROW = "/usr/share/fonts/truetype/liberation/LiberationSansNarrow"

OUT_DIR = Path(__file__).parent


# ── Font loader ───────────────────────────────────────────────────────────────

def load_fonts():
    try:
        return dict(
            fm = ImageFont.truetype(f"{SANS}-Regular.ttf",  12),
            fs = ImageFont.truetype(f"{SANS}-Regular.ttf",  10),
            fp = ImageFont.truetype(f"{SANS}-Bold.ttf",     20),   # ticker prefix
            fe = ImageFont.truetype(f"{NARROW}-Bold.ttf",   80),   # elapsed clock
            fl = ImageFont.truetype(f"{SANS}-Bold.ttf",     30),   # set selector
        )
    except Exception as e:
        print(f"Warning: Liberation fonts not found ({e}), using default")
        d = ImageFont.load_default()
        return dict(fm=d, fs=d, fp=d, fe=d, fl=d)


# ── Helpers ───────────────────────────────────────────────────────────────────

def tw(draw_or_img, text, font):
    """Text width in pixels."""
    if isinstance(draw_or_img, ImageDraw.ImageDraw):
        bb = draw_or_img.textbbox((0, 0), text, font=font)
    else:
        bb = ImageDraw.Draw(draw_or_img).textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


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
    tmp = Image.new("1", (W, 28), 0)
    tdr = ImageDraw.Draw(tmp)

    scroll_x = 2
    if prefix:
        tdr.text((2, TICKER_Y), prefix, font=fonts['fp'], fill=255)
        scroll_x = 2 + tw(tdr, prefix, fonts['fp']) + 3

    scroll_w = W - scroll_x
    if scroll_w > 0 and text:
        t_w    = tw(tdr, text, fonts['fs'])
        period = max(1, t_w + TICKER_GAP)
        base   = int(offset) % period
        if base > 0:
            base -= period
        n_copies = scroll_w // period + 2
        stmp = Image.new("1", (scroll_w, 24), 0)
        stdr = ImageDraw.Draw(stmp)
        for n in range(n_copies):
            stdr.text((base + n * period, TICKER_Y), text, font=fonts['fs'], fill=255)
        tmp.paste(stmp, (scroll_x, 0))

    img.paste(tmp, (0, 0))


def draw_elapsed(img, fonts, se):
    """Large scaled elapsed clock — mirrors Display._do_render elapsed logic."""
    el_y0 = 28
    el_h  = H - el_y0 - 2
    elapsed_str = f"{se // 60:02d}:{se % 60:02d}"

    tmp = Image.new("L", (512, 200), 0)
    tdr = ImageDraw.Draw(tmp)

    char_bbs = [tdr.textbbox((0, 0), c, font=fonts['fe']) for c in elapsed_str]
    char_ws  = [bb[2] - bb[0] for bb in char_bbs]
    top      = min(bb[1] for bb in char_bbs)
    char_h   = max(bb[3] for bb in char_bbs) - top
    spacing  = max(1, int(sum(char_ws) / len(char_ws) * 0.25))
    total_w  = sum(char_ws) + spacing * (len(elapsed_str) - 1)

    x = 0
    for c, bb, cw in zip(elapsed_str, char_bbs, char_ws):
        tdr.text((x - bb[0], -top), c, font=fonts['fe'], fill=255)
        x += cw + spacing

    text_img     = tmp.crop((0, 0, max(1, total_w), max(1, char_h)))
    t_w, t_h     = text_img.size
    scale        = min(W / t_w, el_h / t_h)
    new_w, new_h = int(t_w * scale), int(t_h * scale)
    scaled       = text_img.resize((new_w, new_h), Image.LANCZOS).point(
                       lambda p: 255 if p > 64 else 0, "1")

    x_off = max(0, (W - new_w) // 2)
    y_off = max(el_y0, el_y0 + (el_h - new_h) // 2)
    img.paste(scaled, (x_off, y_off))


def draw_drum_icon(draw, x, y, drumless=False):
    """~20×20 drum icon. drumless=True adds an X overlay."""
    draw.rectangle([x+1, y+8,  x+18, y+19], outline=255)
    draw.ellipse(  [x+1, y+5,  x+18, y+11], outline=255)
    draw.line(     [x+5, y,    x+9,  y+6],  fill=255, width=1)
    draw.line(     [x+14, y,   x+10, y+6],  fill=255, width=1)
    if drumless:
        draw.line([ x+1, y+1,  x+18, y+19], fill=255, width=2)
        draw.line([x+18, y+1,  x+1,  y+19], fill=255, width=2)


# ── Screen renderers ──────────────────────────────────────────────────────────

def render_set_picker(fonts, set_label="SET 01"):
    """Boot screen: large set label + drum-mode icons."""
    img, draw = new_screen()
    t_w = tw(draw, set_label, fonts['fl'])
    draw.text(((W - t_w) // 2, 2), set_label, font=fonts['fl'], fill=255)
    draw_drum_icon(draw, 4,      44, drumless=True)   # ← drumless
    draw_drum_icon(draw, W - 23, 44, drumless=False)  # → drums
    return img


def render_playing(fonts, track_num="01", title="Song Title",
                   bpm="120bpm", platform="SMD",
                   remaining_s=222, set_elapsed_s=222, paused=False):
    """Performance screen: ticker + countdown + elapsed clock."""
    img, draw = new_screen()

    # Ticker
    ticker_text = " ".join(filter(None, [title, bpm, platform]))
    draw_ticker(img, fonts, prefix=track_num, text=ticker_text, offset=0)

    # Countdown / paused
    rem = int(remaining_s)
    cnt = "PAUSED" if paused else f"{rem // 60}:{rem % 60:02d} left"
    cnt_w = tw(draw, cnt, fonts['fs'])
    draw.text(((W - cnt_w) // 2, 15), cnt, font=fonts['fs'], fill=255)

    # Large elapsed clock
    draw_elapsed(img, fonts, int(set_elapsed_s))

    return img


def render_idle(fonts, track_num="01", title="My Set — Song 1", bpm="140bpm"):
    """Ready state: ticker visible, no countdown, clock at 00:00."""
    img, draw = new_screen()
    ticker_text = " ".join(filter(None, [title, bpm]))
    draw_ticker(img, fonts, prefix=track_num, text=ticker_text, offset=0)
    draw_elapsed(img, fonts, 0)
    return img


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    fonts = load_fonts()

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
