#!/usr/bin/env python3
"""
WPlace Tile Stitcher

Usage:
  python wplace_capture.py --url "https://wplace.live/?lat=35.225&lng=-106.60&zoom=15" -o out.png ^
    --grid-size 7 --delay 0.25 --bg-hex "#f5f5f5" --keep-tiles --temp-dir ".\\_wplace_tiles" --skip-existing

Dependencies:
  pip install requests pillow
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse, parse_qs, unquote

import requests
from PIL import Image

# Default backend for tiles
DEFAULT_BACKEND = "https://backend.wplace.live/files/s0/tiles/{x}/{y}.png"

# Tiles are 1000×1000
DEFAULT_FALLBACK_TILE = (1000, 1000)

# Zoom level used by the slippy-tile math
TILE_ZOOM = 11


# --- helpers ---

def parse_wplace_url(url: str) -> Tuple[float, float, float | None]:
    # Extract lat,lng from a normal WPlace link
    p = urlparse(url)
    if p.query:
        qs = parse_qs(p.query)
        if "lat" in qs and "lng" in qs:
            lat = float(qs["lat"][0])
            lng = float(qs["lng"][0])
            zoom = float(qs["zoom"][0]) if "zoom" in qs else None
            return lat, lng, zoom
    if "place=" in url:  # wrapper link
        frag = unquote(url.split("place=", 1)[1])
        p2 = urlparse(frag)
        qs2 = parse_qs(p2.query)
        lat = float(qs2["lat"][0])
        lng = float(qs2["lng"][0])
        zoom = float(qs2["zoom"][0]) if "zoom" in qs2 else None
        return lat, lng, zoom
    raise ValueError("Could not parse lat/lng/zoom from URL. Provide a standard WPlace share link.")


def latlon_to_tile(lat, lon, z):
    #THIS IS THE COOL BIT. Math is fun, fight me.

    #Take a latitude/longitude and figure out which tile (x,y) it belongs to
    #at a given zoom level. The map math everyone pretends to understand.
    
    # Mercator projection has a panic attack if you feed it 90°,
    # so we clamp to 85.05112878° the highest latitude Mercator can handle before blowing up into infinity.
    lat = max(min(lat, 85.05112878), -85.05112878)

    # At zoom level z, the world is chopped into n × n tiles.
    # Example: z=0 -> 1 tile for the whole world,
    # z=1 -> 2x2 tiles, z=11 -> 2048x2048. Surprise! that's the size of WPlace in tiles.
    n = 2 ** z

    # Longitude is easy: -180° to +180° gets normalized into [0..1],
    # then stretched to tile space [0..n]. Wraps around like Pac-Man.
    x_float = (lon + 180.0) / 360.0 * n

    # Latitude is a pain: shove it through Mercator's math. Inflated greenland here we come.
    lat_rad = math.radians(lat)
    y_float = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n

    # Turn the pretty floating-point coordinates into boring integers. Womp Womp.
    x = int(x_float) % n
    y = max(0, min(n - 1, int(y_float)))

    # Ship it back. One (x,y) pair, ready to go beg the server for PNGs.
    return x, y



def wrap_clamp_tile(x: int, y: int, z: int) -> Tuple[int, int]:
    # Wrap X horizontally and clamp Y vertically. Curse you infinite scrolling maps.
    n = 2 ** z
    return x % n, max(0, min(n - 1, y))


def parse_hex_color(s: str) -> Tuple[int, int, int, int]:
    # Accept #rgb, #rrggbb, or #rrggbbaa. Return RGBA. Hex is cool, but we don't fucking understand that shit here.
    t = s.strip()
    if t.startswith("#"):
        t = t[1:]
    if t.lower().startswith("0x"):
        t = t[2:]
    if len(t) == 3:
        r = int(t[0] * 2, 16); g = int(t[1] * 2, 16); b = int(t[2] * 2, 16); a = 255
    elif len(t) == 6:
        r = int(t[0:2], 16); g = int(t[2:4], 16); b = int(t[4:6], 16); a = 255
    elif len(t) == 8:
        r = int(t[0:2], 16); g = int(t[2:4], 16); b = int(t[4:6], 16); a = int(t[6:8], 16)
    else:
        raise ValueError("Invalid hex color. Use #rgb, #rrggbb, or #rrggbbaa.")
    return r, g, b, a


# --- networking---

def download_tile(session: requests.Session, x: int, y: int, z: int,
                  dest: Path, timeout: int, retries: int) -> None:
    # Download one tile to disk. On final failure, write a 0-byte placeholder so the stitcher(tm) knows to create a bg tile.
    url = DEFAULT_BACKEND.format(x=x, y=y, z=z)
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            dest.write_bytes(r.content)
            return
        except Exception:
            if attempt >= retries:
                try:
                    dest.write_bytes(b"")  # missing/failed marker
                except Exception:
                    pass
            else:
                time.sleep(0.5 * attempt)  # small backoff


# --- stitching ---

def safe_open_png(p: Path) -> Image.Image | None:
    """Open PNG safely; return None for missing/0-byte/corrupt files."""
    try:
        if not p.exists() or p.stat().st_size == 0:
            return None
        im = Image.open(p)
        im.load()
        return im.convert("RGBA")
    except Exception:
        return None


def stitch_from_folder(tiles_dir: Path,
                       coords_meta: List[Tuple[int, int, int, int]],
                       grid_size: int,
                       bg_rgba: Tuple[int, int, int, int] | None) -> Image.Image:
    # Build the final canvas by pasting tiles in row-major order. coords_meta holds (x, y, dx, dy), where dx/dy are offsets from the true center.
    # Infer tile size from the first valid tile; else fall back.
    tw = th = None
    for (x, y, _, _) in coords_meta:
        p = tiles_dir / f"tile_{x}_{y}.png"
        im = safe_open_png(p)
        if im:
            tw, th = im.size
            im.close()
            break
    if tw is None or th is None:
        tw, th = DEFAULT_FALLBACK_TILE

    canvas = Image.new("RGBA", (tw * grid_size, th * grid_size), (0, 0, 0, 0))
    half = grid_size // 2

    for (x, y, dx, dy) in coords_meta:
        # Map dx,dy to 0-based grid indices.
        # Odd n: indices [-h..+h], center at index h.
        # Even n: indices [-h..+h-1], true center lies between columns h-1 and h.
        col = dx + half
        row = dy + half
        px = col * tw
        py = row * th

        p = tiles_dir / f"tile_{x}_{y}.png"
        tile = safe_open_png(p)
        if tile is None:
            fill = bg_rgba if bg_rgba is not None else (0, 0, 0, 0)
            tile = Image.new("RGBA", (tw, th), fill)
        canvas.paste(tile, (px, py))
        tile.close()

    return canvas


# --- main ---

def main():
    ap = argparse.ArgumentParser(description="Download all WPlace tiles for a grid, then stitch.")
    # Input
    ap.add_argument("--url", required=True, help="WPlace URL or wrapper containing ?lat=..&lng=..)")

    # Output / geometry
    ap.add_argument("-o", "--out", default="wplace_capture.png", help="Output PNG path")
    ap.add_argument("--grid-size", type=int, default=5, help="Tiles per side of the square snapshot region")

    # Net
    ap.add_argument("--timeout", type=int, default=10, help="HTTP timeout seconds")
    ap.add_argument("--delay", type=float, default=0.5, help="Delay between downloads in seconds")
    ap.add_argument("--retries", type=int, default=2, help="Retries per tile")

    # Background
    ap.add_argument("--bg-hex", "--background-hex", dest="background_hex", default=None,
                    help="Solid hex background for missing tiles and final underlay (e.g. #f5f5f5)")

    # Storage
    ap.add_argument("--temp-dir", default=None,
                    help=r'Directory to store tiles; default is ".\tmp"')
    ap.add_argument("--keep-tiles", action="store_true", help="Keep downloaded tiles after stitching")
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip downloading a tile if its file already exists in the temp directory")

    args = ap.parse_args()

    # Parse URL
    lat, lng, _ = parse_wplace_url(args.url)

    # Convert to tile coords at the locked zoom
    cx, cy = latlon_to_tile(lat, lng, TILE_ZOOM)
    print(f"Center tile: x={cx}, y={cy}")

    # Build symmetric grid around the true center.
    # Odd n: dx,dy [-h, ..., +h]
    # Even n: dx,dy [-h, ..., +h-1]
    n = args.grid_size
    h = n // 2
    if n % 2 == 0:
        print("Warning: grid-size is even; true center lies between the middle four tiles.", file=sys.stderr)
        dx_range = range(-h, +h)       # [-h, ..., h-1]
        dy_range = range(-h, +h)
    else:
        dx_range = range(-h, +h + 1)   # [-h, ..., h]
        dy_range = range(-h, +h + 1)

    coords_meta: List[Tuple[int, int, int, int]] = []
    for dy in dy_range:
        for dx in dx_range:
            wx, wy = wrap_clamp_tile(cx + dx, cy + dy, TILE_ZOOM)
            coords_meta.append((wx, wy, dx, dy))

    # Temp dir setup: default to ".\\tmp" next to this script if not provided
    if args.temp_dir:
        tiles_dir = Path(args.temp_dir)
    else:
        script_dir = Path(__file__).resolve().parent
        tiles_dir = script_dir / "tmp"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # Download pass with progress at 25%, 50%, 75%, 100%
    session = requests.Session()
    session.headers.update({"User-Agent": "wplace-stitcher/1.0"})

    total = len(coords_meta)
    milestones = [0.25, 0.50, 0.75, 1.00]
    next_idx_trigger = {m: max(1, int(total * m)) for m in milestones}
    printed = set()

    for index, (x, y, _, _) in enumerate(coords_meta, 1):
        dest = tiles_dir / f"tile_{x}_{y}.png"
        if args.skip_existing and dest.exists():
            pass
        else:
            if not dest.exists() or dest.stat().st_size == 0:
                download_tile(session, x, y, TILE_ZOOM, dest, timeout=args.timeout, retries=args.retries)

        # polite pacing
        if index < total and args.delay > 0:
            time.sleep(args.delay)

        # progress printing
        for m in milestones:
            if index >= next_idx_trigger[m] and m not in printed:
                pct = int(m * 100)
                print(f"Download progress: {pct}% ({index}/{total})")
                printed.add(m)

    # Build final
    bg_rgba = parse_hex_color(args.background_hex) if args.background_hex else None
    img = stitch_from_folder(tiles_dir, coords_meta, n, bg_rgba)

    # Final underlay
    if bg_rgba is not None:
        bg = Image.new("RGBA", img.size, bg_rgba)
        img = Image.alpha_composite(bg, img)

    img.save(args.out)
    print(f"Saved {args.out}")
    print(f"Tiles stored in: {tiles_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt: #SHUT IT DOWN SHUT IT DOWNNNN
        print("Cancelled.", file=sys.stderr); sys.exit(1)
    except Exception as e: #Something's wrong. Womp Womp.
        print(f"Error: {e}", file=sys.stderr); sys.exit(1)
