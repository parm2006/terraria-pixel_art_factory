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
import ctypes
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Enable ANSI escape codes on Windows
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    _kernel32 = ctypes.windll.kernel32
    _handle = _kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    _mode = ctypes.c_ulong()
    if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
        _kernel32.SetConsoleMode(_handle, _mode.value | 0x0004)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOGICAL_DIM = 150  # max logical pixels per dimension
DB_FILES = {
    "blocks": "blocks.json",
    "walls":  "walls.json",
}

# Windows: ANSI just enabled above so always use true color.
# Other platforms: check env var.
SUPPORTS_TRUE_COLOR = (
    sys.platform == "win32"
    or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    or os.environ.get("WT_SESSION") is not None
)


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
    Derive logical pixel size from image dimensions.
    We assume the image contains at most MAX_LOGICAL_DIM logical pixels
    along its longest axis. Pixel size = ceil(max_dim / MAX_LOGICAL_DIM).
    A 410x410 image -> ceil(410/150) = 3px per logical pixel.
    A 64x64 image   -> ceil(64/150)  = 1px per logical pixel.
    """
    import math
    return max(1, math.ceil(max(img.width, img.height) / MAX_LOGICAL_DIM))


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

def render_color_map(color_to_block: dict, counts: Counter):
    """
    Merge all source colors that map to the same block into one row.
    Swatch color = the most-used source color for that block.
    Count = total across all source colors for that block.
    Sorted by hue of the representative color.
    """
    total = sum(counts.values())
    BAR_WIDTH = 30

    # Merge: block_name -> {total_count, representative_color}
    block_counts: dict = {}
    block_color: dict = {}
    color_count_for_block: dict = {}  # block -> {color: count}

    for color, name in color_to_block.items():
        c = counts.get(name, 0)
        if name not in block_counts:
            block_counts[name] = 0
            color_count_for_block[name] = {}
        block_counts[name] = counts.get(name, 0)
        color_count_for_block[name][color] = color_count_for_block[name].get(color, 0) + 1

    # Pick representative color = the source color closest to the block name's avg
    # Simple: just pick the first one encountered (already deduplicated by most_common)
    for color, name in color_to_block.items():
        if name not in block_color:
            block_color[name] = color

    # Sort by hue of representative color
    sorted_blocks = sorted(block_counts.keys(), key=lambda n: hue_of(block_color[n]))

    print("\n── Pixel art material list (rainbow order) ─────────────────────────")
    for name in sorted_blocks:
        count = block_counts[name]
        r, g, b = block_color[name]
        swatch = color_to_ansi(r, g, b, f"  #{r:02X}{g:02X}{b:02X}  ")
        pct = count / total * 100
        bar_len = max(1, round(pct / 100 * BAR_WIDTH))
        if SUPPORTS_TRUE_COLOR:
            bar = f"\033[38;2;{r};{g};{b}m" + "█" * bar_len + "\033[0m"
        else:
            bar = "█" * bar_len
        print(f"  {swatch}  {name:<40s}  x {count:<6}  {bar}")
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

    # --- Strip white/transparent background ---
    # Convert to RGBA so we can check alpha or near-white pixels
    rgba = img.convert("RGBA")
    r_data, g_data, b_data, a_data = rgba.split()
    # Make near-white pixels (all channels >= 240) transparent
    pixels = list(rgba.getdata())
    new_pixels = []
    for r, g, b, a in pixels:
        if a < 30 or (r >= 240 and g >= 240 and b >= 240):
            new_pixels.append((255, 255, 255, 0))  # transparent
        else:
            new_pixels.append((r, g, b, a))
    rgba.putdata(new_pixels)
    # Crop to non-transparent bounding box
    bbox = rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
        print(f"Cropped to content: {rgba.width} x {rgba.height} px")
    img = rgba

    # --- Quantize to reduce compression noise ---
    # Convert to RGB palette with N colors, then back - snaps near-identical colors
    NUM_COLORS = 128
    rgb_img = img.convert("RGB")
    quantized = rgb_img.quantize(colors=NUM_COLORS, method=Image.Quantize.MEDIANCUT).convert("RGB")
    # Re-apply transparency mask from before quantization
    quantized_rgba = quantized.convert("RGBA")
    alpha_mask = img.split()[3]  # original alpha channel after bg strip
    quantized_rgba.putalpha(alpha_mask)
    img = quantized_rgba
    print(f"Quantized to {NUM_COLORS} colors")

    # --- Detect pixel size ---
    pixel_size = detect_pixel_size(img)
    logical_w = img.width  // pixel_size
    logical_h = img.height // pixel_size
    print(f"Pixel size: {pixel_size} px  →  logical grid: {logical_w} x {logical_h} blocks")

    # --- Build matcher & process ---
    match_fn = build_matcher(db)
    color_to_block = process_image(img, pixel_size, match_fn)
    counts = count_blocks(img, pixel_size, color_to_block)

    # --- Output ---
    print(f"\nUnique colors in image:  {len(color_to_block)}")
    print(f"Unique blocks/walls used: {len(counts)}")
    render_color_map(color_to_block, counts)


if __name__ == "__main__":
    main()
