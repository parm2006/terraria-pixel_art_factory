#!/usr/bin/env python3
"""
pixel_art.py
Convert a pixel-art image into a Terraria block/wall material list.

Usage:
    python pixel_art.py <image> --mode blocks
    python pixel_art.py <image> --mode walls
    python pixel_art.py <image> --mode blocks --db-dir ./data

Requires blocks.json / walls.json produced by scrape_terraria.py.
"""

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_PIXEL_SIZE = 8          # pixels — anything smaller isn't pixel art
DB_FILES = {
    "blocks": "blocks.json",
    "walls":  "walls.json",
}

# ANSI 24-bit color support detection
SUPPORTS_TRUE_COLOR = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") or os.environ.get("WT_SESSION") is not None


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def rgb_distance(a: tuple, b: tuple) -> float:
    """Euclidean distance in RGB space."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def most_common_color(img: Image.Image) -> tuple[int, int, int]:
    """
    Return the most frequent RGB value in the image region.
    Ignores fully-transparent pixels (alpha <= 10).
    Falls back to (0,0,0) if all pixels are transparent.
    """
    rgba = img.convert("RGBA")
    pixels = [(r, g, b) for r, g, b, a in list(rgba.getdata()) if a > 10]
    if not pixels:
        return (0, 0, 0)
    return Counter(pixels).most_common(1)[0][0]


def color_to_ansi(r: int, g: int, b: int, text: str) -> str:
    """
    Wrap text in ANSI 24-bit background + contrasting foreground.
    Falls back to a hex string if the terminal doesn't support true color.
    """
    if SUPPORTS_TRUE_COLOR:
        # Pick black or white foreground based on perceived luminance
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg = "0;0;0" if lum > 128 else "255;255;255"
        return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg}m{text}\033[0m"
    else:
        return f"#{r:02X}{g:02X}{b:02X}  {text}"


def hue_of(rgb: tuple) -> float:
    """Return hue (0–360) for rainbow sorting. Grays sort to end."""
    r, g, b = (x / 255.0 for x in rgb)
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    if delta < 0.01:            # achromatic — sort grays after colors
        return 360 + (r + g + b) / 3   # secondary sort by brightness
    if mx == r:
        h = (g - b) / delta % 6
    elif mx == g:
        h = (b - r) / delta + 2
    else:
        h = (r - g) / delta + 4
    return h * 60


# ---------------------------------------------------------------------------
# Pixel size detection
# ---------------------------------------------------------------------------

def detect_pixel_size(img: Image.Image) -> int:
    """
    Estimate the size (in source pixels) of one 'logical pixel' in the art.

    Strategy:
      - Scan the first row and first column of the image.
      - Find the minimum run-length before the RGB value changes.
      - That minimum is the best lower-bound estimate of pixel size.
    """
    rgb = img.convert("RGB")
    width, height = rgb.size

    def min_run_horizontal() -> int:
        min_run = width
        for y in range(min(height, 32)):    # sample first 32 rows
            prev = rgb.getpixel((0, y))
            run = 1
            for x in range(1, width):
                px = rgb.getpixel((x, y))
                if px == prev:
                    run += 1
                else:
                    if run < min_run:
                        min_run = run
                    run = 1
                    prev = px
        return min_run

    def min_run_vertical() -> int:
        min_run = height
        for x in range(min(width, 32)):     # sample first 32 columns
            prev = rgb.getpixel((x, 0))
            run = 1
            for y in range(1, height):
                px = rgb.getpixel((x, y))
                if px == prev:
                    run += 1
                else:
                    if run < min_run:
                        min_run = run
                    run = 1
                    prev = px
        return min_run

    return min(min_run_horizontal(), min_run_vertical())


# ---------------------------------------------------------------------------
# Color matching
# ---------------------------------------------------------------------------

def build_matcher(db: list[dict]):
    """
    Pre-process the DB into a list of (avg_color_tuple, name) pairs.
    Returns a function: rgb_tuple → best matching entry dict.
    """
    palette = [(tuple(e["avg_color"]), e["name"]) for e in db]

    def match(rgb: tuple) -> str:
        best_name = None
        best_dist = float("inf")
        for color, name in palette:
            d = rgb_distance(rgb, color)
            if d < best_dist:
                best_dist = d
                best_name = name
        return best_name

    return match


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_image(img: Image.Image, pixel_size: int, match_fn) -> list[dict]:
    """
    Divide image into pixel_size x pixel_size cells.
    For each cell: extract most-common color, find closest block/wall.
    Returns list of {source_color, block_name} for every unique color found.
    """
    rgb = img.convert("RGB")
    width, height = rgb.size

    color_to_block: dict[tuple, str] = {}

    cols = width  // pixel_size
    rows = height // pixel_size

    for row in range(rows):
        for col in range(cols):
            x0 = col * pixel_size
            y0 = row * pixel_size
            x1 = x0 + pixel_size
            y1 = y0 + pixel_size
            cell = rgb.crop((x0, y0, x1, y1))
            color = most_common_color(cell)
            if color not in color_to_block:
                color_to_block[color] = match_fn(color)

    return color_to_block


def count_blocks(img: Image.Image, pixel_size: int, color_to_block: dict) -> Counter:
    """Count how many of each block name appears across the whole image."""
    rgb = img.convert("RGB")
    width, height = rgb.size
    counts: Counter = Counter()
    cols = width  // pixel_size
    rows = height // pixel_size
    for row in range(rows):
        for col in range(cols):
            x0 = col * pixel_size
            y0 = row * pixel_size
            cell = rgb.crop((x0, y0, x0 + pixel_size, y0 + pixel_size))
            color = most_common_color(cell)
            block = color_to_block[color]
            counts[block] += 1
    return counts


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def render_color_map(color_to_block: dict):
    """Print the color → block mapping sorted by hue (rainbow order)."""
    sorted_entries = sorted(color_to_block.items(), key=lambda x: hue_of(x[0]))
    print("\n── Color → Block mapping (rainbow order) ──────────────────────────")
    for color, name in sorted_entries:
        r, g, b = color
        swatch = color_to_ansi(r, g, b, f"  #{r:02X}{g:02X}{b:02X}  ")
        print(f"  {swatch}  →  {name}")


def render_material_list(counts: Counter):
    """Print the material list sorted by count descending."""
    total = sum(counts.values())
    print("\n── Material list (most used first) ─────────────────────────────────")
    for name, count in counts.most_common():
        bar_len = max(1, round(count / total * 40))
        bar = "█" * bar_len
        print(f"  {name:<40s}  {count:>6} px  {bar}")
    print(f"\n  Total pixels: {total}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert pixel art into a Terraria block/wall material list."
    )
    parser.add_argument("image", help="Path to the pixel art image (PNG, JPG, etc.)")
    parser.add_argument(
        "--mode", choices=["blocks", "walls"], required=True,
        help="Use blocks or walls for the palette."
    )
    parser.add_argument(
        "--db-dir", default=".",
        help="Directory containing blocks.json / walls.json (default: current dir)"
    )
    args = parser.parse_args()

    # --- Load database ---
    db_path = Path(args.db_dir) / DB_FILES[args.mode]
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}")
        print("        Run scrape_terraria.py first.")
        sys.exit(1)

    with open(db_path) as f:
        db = json.load(f)
    print(f"Loaded {len(db)} {args.mode} from {db_path}")

    # --- Load image ---
    img_path = Path(args.image)
    if not img_path.exists():
        print(f"[ERROR] Image not found: {img_path}")
        sys.exit(1)

    try:
        img = Image.open(img_path)
    except Exception as e:
        print(f"[ERROR] Could not open image: {e}")
        sys.exit(1)

    print(f"Image: {img_path.name}  ({img.width} x {img.height} px)")

    # --- Detect pixel size ---
    pixel_size = detect_pixel_size(img)
    print(f"Detected pixel size: {pixel_size} px")

    if pixel_size < MIN_PIXEL_SIZE:
        print(
            f"\n[ERROR] This does not look like pixel art.\n"
            f"        Detected pixel size: {pixel_size} px\n"
            f"        Minimum required:    {MIN_PIXEL_SIZE} px\n"
            f"\n"
            f"        Pixel art pixels must be at least {MIN_PIXEL_SIZE}x{MIN_PIXEL_SIZE} source pixels.\n"
            f"        If this IS pixel art, try scaling it up (e.g. 8x) before running."
        )
        sys.exit(1)

    # --- Build matcher & process ---
    match_fn = build_matcher(db)
    color_to_block = process_image(img, pixel_size, match_fn)
    counts = count_blocks(img, pixel_size, color_to_block)

    # --- Output ---
    print(f"\nUnique colors in image:  {len(color_to_block)}")
    print(f"Unique blocks/walls used: {len(counts)}")
    render_color_map(color_to_block)
    render_material_list(counts)


if __name__ == "__main__":
    main()
