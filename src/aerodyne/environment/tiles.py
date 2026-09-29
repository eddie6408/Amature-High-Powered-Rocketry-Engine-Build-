"""Terrain elevation tiles for the launch simulator's Earth view, cached on disk.

Source: the open "Terrain Tiles" dataset on AWS (Terrarium PNG encoding, web-mercator,
zoom 0-15). Tiles are cached under ``~/.cache/aerodyne/tiles`` (override with
``AERODYNE_TILE_CACHE``) so a launch site fetched at home stays available at a field
without internet. Satellite imagery is not cached here; it comes from its provider.
"""

from __future__ import annotations

import math
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TERRAIN_MAX_ZOOM = 15
TERRAIN_CREDIT = "Terrain Tiles: Mapzen / AWS Open Data (SRTM, GMTED, NED, ETOPO1 and others)"


class TileUnavailable(RuntimeError):
    pass


def cache_dir() -> Path:
    return Path(os.environ.get("AERODYNE_TILE_CACHE") or Path.home() / ".cache" / "aerodyne" / "tiles")


def _check(z: int, x: int, y: int) -> None:
    if not (0 <= z <= TERRAIN_MAX_ZOOM) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        raise ValueError(f"no terrain tile {z}/{x}/{y}")


def terrain_path(z: int, x: int, y: int) -> Path:
    return cache_dir() / "terrarium" / str(z) / str(x) / f"{y}.png"


def terrain_tile(z: int, x: int, y: int, opener: Callable[..., Any] = urllib.request.urlopen,
                 timeout: float = 10.0) -> tuple[bytes, bool]:
    """PNG bytes for one tile and whether it came from the cache."""
    _check(z, x, y)
    p = terrain_path(z, x, y)
    if p.is_file():
        return p.read_bytes(), True
    try:
        with opener(TERRAIN_URL.format(z=z, x=x, y=y), timeout=timeout) as resp:
            data = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise TileUnavailable(f"terrain tile {z}/{x}/{y} not cached and not reachable ({exc})") from exc
    if not data.startswith(b"\x89PNG"):
        raise TileUnavailable(f"terrain tile {z}/{x}/{y}: unexpected reply")
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=p.parent, delete=False, suffix=".part") as f:
        f.write(data)
    os.replace(f.name, p)
    return data, False


def lonlat_to_tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    lat = max(-85.0511, min(85.0511, lat))
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(n - 1, max(0, x)), min(n - 1, max(0, y))


def tiles_around(lat: float, lon: float, radius_km: float, max_zoom: int = 14, min_zoom: int = 0) -> list[tuple[int, int, int]]:
    """Every tile from min_zoom to max_zoom that covers a square of +/- radius_km around a point."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("invalid coordinates")
    max_zoom = min(TERRAIN_MAX_ZOOM, max(min_zoom, int(max_zoom)))
    dlat = radius_km / 111.32
    dlon = radius_km / (111.32 * max(0.05, math.cos(math.radians(lat))))
    out: list[tuple[int, int, int]] = []
    for z in range(min_zoom, max_zoom + 1):
        x0, y0 = lonlat_to_tile(lat + dlat, lon - dlon, z)
        x1, y1 = lonlat_to_tile(lat - dlat, lon + dlon, z)
        out += [(z, x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    return out


def prefetch(lat: float, lon: float, radius_km: float = 5.0, max_zoom: int = 14,
             progress: Callable[[int, int], None] | None = None,
             opener: Callable[..., Any] = urllib.request.urlopen, limit: int = 4000) -> dict[str, Any]:
    """Download (and cache) the terrain around a launch site for offline use."""
    tiles = tiles_around(lat, lon, radius_km, max_zoom)
    if len(tiles) > limit:
        raise ValueError(f"{len(tiles)} tiles requested; reduce the radius or zoom (limit {limit})")
    fetched = cached = failed = 0
    for i, (z, x, y) in enumerate(tiles):
        try:
            _, hit = terrain_tile(z, x, y, opener=opener)
            cached += hit
            fetched += not hit
        except TileUnavailable:
            failed += 1
        if progress:
            progress(i + 1, len(tiles))
    return {"tiles": len(tiles), "fetched": fetched, "already_cached": cached, "failed": failed,
            "cache": str(cache_dir()), "credit": TERRAIN_CREDIT}
