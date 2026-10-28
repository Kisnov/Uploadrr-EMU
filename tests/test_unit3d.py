"""Búsqueda de duplicados de UNIT3D contra un tracker falso en local."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

import src.trackers.UNIT3D as unit3d
from src.trackers.EMU import EMU
from src.trackers.UNIT3D import DupeSearchError

TOKEN = "clave-de-prueba"
SEARCH = "/api/torrents/filter"


class FakeTracker:
    """Cada prueba pone en routes[(método, ruta)] una función query -> (status, cuerpo)."""

    def __init__(self):
        self.requests = []
        self.routes = {}
        tracker = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query, keep_blank_values=True)
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                tracker.requests.append((self.command, parts.path, query))
                status, body = tracker.routes[(self.command, parts.path)](query)
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        # Con la IP, "/127." pasaría por el id del torrent que se saca de la URL.
        self.url = f"http://localhost:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def calls(self, method, path):
        return [query for m, p, query in self.requests if (m, p) == (method, path)]


def item(name, size=1, **extra):
    return {"id": 1, "attributes": {"name": name, "size": size, **extra}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tracker():
    fake = FakeTracker()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def emu(tracker, monkeypatch):
    instance = EMU(config={"TRACKERS": {"EMU": {"api_key": TOKEN}}, "DEFAULT": {}})
    instance.search_url = tracker.url + SEARCH
    return instance


@pytest.fixture
def meta():
    return {"tmdb": 1, "category": "MOVIE", "type": "WEBDL", "resolution": "1080p", "edition": "Extended", "debug": False}


# Búsqueda de duplicados


def test_search_follows_next_links(tracker, emu, meta):
    def search(query):
        if "cursor" not in query:
            return 200, {"data": [item("A", 10)], "links": {"next": f"{tracker.url}{SEARCH}?cursor=2&tmdbId=1"}}
        return 200, {"data": [item("B", 20)], "links": {"next": None}}

    tracker.routes[("GET", SEARCH)] = search
    assert run(emu.search_existing(meta)) == {"A": 10, "B": 20}

    first, second = tracker.calls("GET", SEARCH)
    assert first["resolutions[]"] == ["3", "4"]
    assert first["perPage"] == ["100"]
    assert first["name"] == [""]  # la edición ya no filtra por nombre
    assert second["cursor"] == ["2"] and second["api_token"] == [TOKEN]


def test_search_other_resolutions_alone(tracker, emu, meta):
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [], "links": {"next": None}})
    run(emu.search_existing({**meta, "resolution": "720p"}))
    assert tracker.calls("GET", SEARCH)[0]["resolutions[]"] == ["5"]


@pytest.mark.parametrize(
    "second_page",
    [(500, b"error"), (200, b"<html>login</html>"), (200, {"links": {"next": None}})],
    ids=["http-500", "no-json", "sin-data"],
)
def test_search_failed_page_raises(tracker, emu, meta, second_page):
    def search(query):
        if "page" not in query:
            return 200, {"data": [item("A")], "links": {"next": f"{tracker.url}{SEARCH}?page=2"}}
        return second_page

    tracker.routes[("GET", SEARCH)] = search
    with pytest.raises(DupeSearchError):
        run(emu.search_existing(meta))


def test_search_never_follows_other_host(tracker, emu, meta):
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [], "links": {"next": "http://otro.invalid/api?page=2"}})
    with pytest.raises(DupeSearchError):
        run(emu.search_existing(meta))
    assert len(tracker.requests) == 1


def test_search_page_limit(tracker, emu, meta, monkeypatch):
    monkeypatch.setattr(unit3d, "MAX_SEARCH_PAGES", 3)
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [], "links": {"next": f"{tracker.url}{SEARCH}?page=9"}})
    with pytest.raises(DupeSearchError):
        run(emu.search_existing(meta))
    assert len(tracker.requests) == 3
