# WPlace Tile Stitcher

Downloads a large block of WPlace tiles given a share URL and stitch them into one PNG. Missing tiles are filled with a background color.

## Features

* Accepts a WPlace share URL (`?lat=...&lng=...`) and downloads a square tile grid around that point.
* Downloads all tiles first (with configurable delay and retries), then stitches into a single PNG.
* Missing/empty tiles are filled with a solid background color and the final image is composited over the same color (no transparency).
* Tile caching via `--temp-dir` and `--skip-existing`.

## Requirements

```bash
pip install requests pillow
```

## Flags / Options

* `--url` **(required)** — WPlace share URL containing `lat` and `lng`.
* `-o, --out` — Output PNG file (default `wplace_capture.png`).
* `--grid-size` — Number of tiles per side (odd or even). Default `5`.
* `--timeout` — HTTP timeout seconds (default `10`).
* `--delay` — Seconds to wait between tile downloads (default `0.5`).
* `--retries` — Retries per tile (default `2`).
* `--bg-hex`, `--background-hex` — Hex background used for missing tiles and final underlay. Accepts `#rgb`, `#rrggbb`, `#rrggbbaa`.
* `--temp-dir` — Directory for downloaded tiles (default `./tmp` next to the script).
* `--keep-tiles` — Keep tiles after stitching.
* `--skip-existing` — Skip downloading tiles that already exist in `--temp-dir`.

## Example

<p align="center">
  <img src="./Example_NYC.png" alt="NYC tiles" width="800">
</p>


### License
MIT
