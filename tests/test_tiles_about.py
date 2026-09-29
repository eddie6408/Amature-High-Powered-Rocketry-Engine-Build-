"""Terrain tile cache (offline Earth view) and the app's about endpoint."""

import urllib.error

import pytest

from aerodyne.app.server import App
from aerodyne.environment import tiles
from aerodyne.workspace import Workspace

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body


def test_tile_cache_fetches_once_then_serves_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("AERODYNE_TILE_CACHE", str(tmp_path / "cache"))
    calls = []

    def online(url, timeout):
        calls.append(url)
        return _Reply(PNG)

    def offline(url, timeout):
        raise urllib.error.URLError("no network")

    data, hit = tiles.terrain_tile(12, 850, 1550, opener=online)
    assert data == PNG and not hit and calls == [tiles.TERRAIN_URL.format(z=12, x=850, y=1550)]
    data, hit = tiles.terrain_tile(12, 850, 1550, opener=offline)      # cached: works with no network
    assert data == PNG and hit
    with pytest.raises(tiles.TileUnavailable):
        tiles.terrain_tile(12, 851, 1550, opener=offline)
    with pytest.raises(ValueError):
        tiles.terrain_tile(16, 0, 0, opener=online)
    with pytest.raises(ValueError):
        tiles.terrain_tile(3, 8, 0, opener=online)
    with pytest.raises(tiles.TileUnavailable):
        tiles.terrain_tile(12, 852, 1550, opener=lambda u, timeout: _Reply(b"<html>error</html>"))


def test_tiles_around_and_prefetch(tmp_path, monkeypatch):
    monkeypatch.setenv("AERODYNE_TILE_CACHE", str(tmp_path / "cache"))
    # Boulder, CO: tile indices match the standard web-mercator formula
    assert tiles.lonlat_to_tile(40.0149, -105.2705, 12) == (850, 1550)
    ts = tiles.tiles_around(40.0149, -105.2705, 2.0, max_zoom=12)
    assert (0, 0, 0) in ts and (12, 850, 1550) in ts and len(set(ts)) == len(ts)
    progress = []
    out = tiles.prefetch(40.0149, -105.2705, 2.0, 12, progress=lambda i, n: progress.append((i, n)),
                         opener=lambda u, timeout: _Reply(PNG))
    assert out["tiles"] == len(ts) and out["fetched"] == len(ts) and out["failed"] == 0 and progress[-1] == (len(ts), len(ts))
    again = tiles.prefetch(40.0149, -105.2705, 2.0, 12, opener=lambda u, timeout: _Reply(PNG))
    assert again["already_cached"] == len(ts) and again["fetched"] == 0
    with pytest.raises(ValueError):
        tiles.prefetch(40.0, -105.0, 200.0, 15)                       # too many tiles


def test_about(tmp_path):
    app = App(Workspace.init(tmp_path / "ws", "t"))
    code, out = app.dispatch("GET", "/api/about", {}, {})
    assert code == 200 and out["version"] and out["started_at"].endswith("+00:00")
