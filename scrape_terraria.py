#!/usr/bin/env python3
"""
scrape_terraria.py
Run ONCE to build blocks.json and walls.json.

Usage:
    python scrape_terraria.py
    python scrape_terraria.py --output-dir ./data
"""

import argparse
import json
import time
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIKI_BASE = "https://terraria.wiki.gg"

BLOCK_SUBPAGES = [
    "/wiki/Soils",
    "/wiki/Grown_blocks",
    "/wiki/Other_found_blocks",
    "/wiki/Trap_blocks",
    "/wiki/Ore_blocks",
    "/wiki/Gemstone_Blocks",
    "/wiki/Bricks",
    "/wiki/Crafted_blocks",
    "/wiki/Purchased_blocks",
    "/wiki/Looted_blocks",
    "/wiki/Summoned_blocks",
]

WALLS_SUBPAGES = [
    "/wiki/Crafted_walls",
    "/wiki/Purchased_Walls",
    "/wiki/Naturally_occurring_walls",
    "/wiki/Converted_walls",
]

HEADERS = {
    "User-Agent": "TerrariaPixelArtTool/1.0 (open-source educational project)"
}

REQUEST_DELAY = 0.25

# ---------------------------------------------------------------------------
# Manual exclusion list
# ---------------------------------------------------------------------------

EXCLUDED = {
    # Gravity blocks
    "Sand Block", "Ebonsand Block", "Crimsand Block", "Pearlsand Block",
    "Hardened Sand", "Hardened Ebonsand", "Hardened Crimsand", "Hardened Pearlsand",
    "Sandstone Block", "Ebonsandstone Block", "Crimsandstone Block", "Pearlsandstone Block",
    "Silt Block", "Slush Block",
    # Liquids
    "Water", "Lava", "Honey",
    # Animated
    "Living Fire Block", "Living Cursed Fire Block", "Living Demon Fire Block",
    "Living Frostfire Block", "Living Ichor Fire Block", "Living Ultrabright Fire Block",
    "Lavafall Block", "Waterfall Block", "Honeyfall Block",
    "Lavafall Wall", "Waterfall Wall", "Honeyfall Wall",
}

# ---------------------------------------------------------------------------
# Items that showed up in scrape but are NOT placeable 1x1 blocks/walls.
# These are nav links, crafting stations, furniture, ingredients, weapons, etc.
# ---------------------------------------------------------------------------

NOT_A_BLOCK = {
    # Wiki nav / section header ghost entries
    "Blocks", "Bricks", "Walls",
    # Crafting stations & furniture
    "Work Bench", "Furnace", "Hellforge", "Heavy Assembler", "Bone Welder",
    "Sawmill", "Loom", "Living Loom", "Meat Grinder", "Blend-O-Matic",
    "Solidifier", "Crystal Ball", "Sky Mill", "Sink", "Water fountain",
    "Bookcase", "Iron Anvil", "Lead Anvil", "Mythril Anvil", "Orichalcum Anvil",
    "Adamantite Forge", "Titanium Forge", "Ancient Manipulator",
    # Crafting ingredients / drops (not placeable blocks)
    "Wire", "Gel", "Pink Gel", "Confetti", "Fallen Star", "Coral",
    "Cursed Flame", "Ichor", "Spider Fang", "Feather", "Poo",
    "Mushroom", "Seashell", "Junonia Shell", "Lightning Whelk Shell",
    "Tulip Shell", "Starfish", "Book",
    "Hallowed Bar", "Shroomite Bar", "Solar Fragment", "Nebula Fragment",
    "Stardust Fragment", "Vortex Fragment", "Luminite",
    "Amethyst", "Diamond", "Emerald", "Ruby", "Sapphire", "Topaz", "Amber",
    "Crystal Shard", "Forbidden Fragment", "Flinx Fur",
    "Any Wood", "Any Sand Block", "Any Iron Bar", "Any Seashell or Starfish",
    # Weapons / tools / accessories that snuck in
    "Ice Rod", "Sandgun", "Spectre Goggles",
    # Enemies / bosses
    "Wall of Flesh", "Antlion", "Ghoulder",
    # Misc non-block sprites
    "Torches", "Large Gems", "Gemcorns", "Gem Locks", "Gem Hooks",
    "Gem Robes", "Phaseblades", "Phasesabers", "Gem staves",
    "Diamond Minecart", "Gemspark Blocks", "Gemstone Blocks", "Stained Glass",
    "Large Bamboo", "Pine Wood",
    "Demon Torch", "Ultrabright Torch",
    "Snow Balla", "Sand Ball", "Lava Bomb", "Lava Boulder",
    "Rainbow Boulder", "Poo Boulder", "Spider Boulder", "Bouncy Boulder",
    "Green Thread", "White Thread", "Purple Thread",
    # Multi-tile / non-1x1 structural objects
    "Conveyor Belt (Clockwise)", "Conveyor Belt (Counter Clockwise)",
    "Dart Trap", "Venom Dart Trap", "Super Dart Trap",
    "Spear Trap", "Spiky Ball Trap", "Flame Trap",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_absolute(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return WIKI_BASE + src
    return src


def average_color(img: Image.Image):
    rgba = img.convert("RGBA")
    pixels = list(rgba.getdata())
    r_sum = g_sum = b_sum = count = 0
    for r, g, b, a in pixels:
        if a > 10:
            r_sum += r
            g_sum += g
            b_sum += b
            count += 1
    if count == 0:
        return None
    return (r_sum // count, g_sum // count, b_sum // count)


def fetch_and_avg(url: str, session: requests.Session):
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        w = min(img.width, 16)
        h = min(img.height, 16)
        tile = img.crop((0, 0, w, h))
        return average_color(tile)
    except Exception as e:
        print(f"    [WARN] fetch failed for {url}: {e}")
        return None


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_items(soup: BeautifulSoup, excluded: set, not_a_block: set, session: requests.Session) -> list:
    entries = []
    seen = set()

    for span in soup.find_all("span", class_="i"):
        img_tag = span.find("img")
        if not img_tag:
            continue

        src = img_tag.get("src") or img_tag.get("data-src") or ""
        if not src:
            continue
        sprite_url = make_absolute(src.split("?")[0])

        name = img_tag.get("alt", "").strip()
        if not name:
            a = span.find("a")
            name = a.get_text(strip=True) if a else ""
        if not name:
            continue

        if name in seen:
            continue
        if name in excluded:
            print(f"  [SKIP-excluded] {name}")
            continue
        if name in not_a_block:
            print(f"  [SKIP-not-block] {name}")
            continue

        seen.add(name)

        time.sleep(REQUEST_DELAY)
        avg = fetch_and_avg(sprite_url, session)
        if avg is None:
            print(f"  [WARN] {name}: transparent or download failed, skipping.")
            continue

        entries.append({
            "name": name,
            "avg_color": list(avg),
            "sprite_url": sprite_url,
        })
        print(f"  [OK] {name:<48}  RGB{avg}")

    return entries


# ---------------------------------------------------------------------------
# Scrape subpages
# ---------------------------------------------------------------------------

def scrape_subpages(subpages: list, excluded: set, not_a_block: set, label: str, session: requests.Session) -> list:
    all_entries = []
    seen_names = set()

    for path in subpages:
        url = WIKI_BASE + path
        print(f"\n  -- {url}")
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [ERROR] {url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        entries = extract_items(soup, excluded, not_a_block, session)

        for e in entries:
            if e["name"] not in seen_names:
                seen_names.add(e["name"])
                all_entries.append(e)

    print(f"\n  Total unique {label}: {len(all_entries)}")
    return all_entries


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape terraria.wiki.gg once to build the block/wall color database."
    )
    parser.add_argument("--output-dir", default=".", help="Where to write blocks.json and walls.json")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    print("=== Scraping BLOCKS ===")
    blocks = scrape_subpages(BLOCK_SUBPAGES, EXCLUDED, NOT_A_BLOCK, "blocks", session)
    blocks_path = out_dir / "raw_blocks.json"
    with open(blocks_path, "w") as f:
        json.dump(blocks, f, indent=2)
    print(f"Saved {len(blocks)} blocks -> {blocks_path}\n  Clean this file and save as cleaned_blocks.json before using pixel_art.py")

    print("\n=== Scraping WALLS ===")
    walls = scrape_subpages(WALLS_SUBPAGES, EXCLUDED, NOT_A_BLOCK, "walls", session)
    walls_path = out_dir / "raw_walls.json"
    with open(walls_path, "w") as f:
        json.dump(walls, f, indent=2)
    print(f"Saved {len(walls)} walls -> {walls_path}\n  Clean this file and save as cleaned_walls.json before using pixel_art.py")

    print("\nDone. Run pixel_art.py to convert images.")


if __name__ == "__main__":
    main()
