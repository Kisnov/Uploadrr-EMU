"""Búsqueda de duplicados y subida de UNIT3D contra un tracker falso en local."""

import asyncio
import io
import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import torf

import src.trackers.UNIT3D as unit3d
from src.trackers.EMU import EMU
from src.trackers.UNIT3D import DupeSearchError

TOKEN = "clave-de-prueba"
SEARCH = "/api/torrents/filter"
UPLOAD = "/api/torrents/upload"
NAME = "Peli 2020 1080p WEB-DL DUAL-GRP"
SIZE = 1000  # bytes del contenido del torrent de prueba
_connect = socket.socket.connect


def _solo_local(sock, address):
    if address[0] not in ("127.0.0.1", "::1"):
        raise RuntimeError(f"los tests solo pueden conectar con el tracker falso, no con {address[0]}")
    return _connect(sock, address)


def _sin_procesos(*_args, **_kwargs):
    raise RuntimeError("los tests no pueden lanzar procesos")


@pytest.fixture(autouse=True)
def sin_red_ni_procesos(monkeypatch):
    """Sustituye al de conftest: aquí el tracker falso escucha en localhost y es lo único a lo que se conecta."""
    monkeypatch.setattr(socket.socket, "connect", _solo_local)
    monkeypatch.setattr(subprocess, "Popen", _sin_procesos)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _sin_procesos)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _sin_procesos)


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
                    pass  # el cliente ya se fue por timeout

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
    monkeypatch.setattr(unit3d, "UPLOAD_RETRY_DELAYS", (0, 0))
    monkeypatch.setattr(unit3d, "DOWNLOAD_RETRY_DELAYS", (0, 0, 0))
    instance = EMU(config={"TRACKERS": {"EMU": {"api_key": TOKEN}}, "DEFAULT": {}})
    instance.search_url = tracker.url + SEARCH
    instance.upload_url = tracker.url + UPLOAD
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
    [
        (500, b"error"),
        (200, b"<html>login</html>"),
        (200, {"links": {"next": None}}),
        (200, {"data": "abc", "links": {"next": None}}),
        (200, {"data": [{"id": 2}], "links": {"next": None}}),
        (200, {"data": [{"attributes": {"name": "B"}}], "links": {"next": None}}),
        (200, {"data": [{"attributes": {"name": "B", "size": "mucho"}}], "links": {"next": None}}),
        (200, {"data": [], "links": {"next": 5}}),
        (200, {"data": [], "links": {"next": 0}}),
        (200, {"data": [], "links": {"next": False}}),
        (200, {"data": [], "links": {"next": []}}),
        (200, {"data": [], "links": {"next": "http://[::1"}}),
        (200, {"data": [], "links": False}),
        (200, {"data": [], "links": []}),
    ],
    ids=[
        "http-500",
        "no-json",
        "sin-data",
        "data-texto",
        "sin-attributes",
        "sin-size",
        "size-texto",
        "next-numero",
        "next-cero",
        "next-false",
        "next-lista",
        "next-invalido",
        "links-false",
        "links-lista",
    ],
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


# Subida


class FakeDesc:
    def __init__(self, config, base_dir):
        pass

    async def edit_desc(self, meta, tracker):
        pass


@pytest.fixture
def upload_meta(tmp_path, monkeypatch, meta, tracker, signed_torrent):
    monkeypatch.setattr(unit3d, "DescGenerator", FakeDesc)
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [], "links": {"next": None}})
    work = tmp_path / "tmp" / "u"
    work.mkdir(parents=True)
    (work / "MEDIAINFO.txt").write_text("mediainfo")
    (work / "[EMU]DESCRIPTION.txt").write_text("descripción")
    (work / "BASE.torrent").write_bytes(signed_torrent)
    return {
        **meta,
        "base_dir": str(tmp_path),
        "uuid": "u",
        "manual_name": NAME,
        "clean_name": "Peli",
        "keywords": "",
        "imdb_info": {},
        "stream": 0,
        "sd": 0,
        "is_music": False,
        "xxx": False,
    }


@pytest.fixture
def signed_torrent(tmp_path):
    content = tmp_path / "x.bin"
    content.write_bytes(b"x" * SIZE)
    torrent = torf.Torrent(path=content, private=True)
    torrent.generate()
    return torrent.dump()


def resigned(torrent_bytes):
    """Como lo devuelve el tracker: mismo contenido, otro infohash."""
    torrent = torf.Torrent.read_stream(io.BytesIO(torrent_bytes), validate=False)
    torrent.source = "eMuwarez"
    return torrent.dump(validate=False)


def download_url(tracker):
    return f"{tracker.url}/torrent/download/42.rsskey"


def serve_download(tracker, signed_torrent, failures=0):
    calls = []

    def download(query):
        calls.append(query)
        if query.get("api_token") != [TOKEN]:
            return 403, b"forbidden"
        if len(calls) <= failures:
            return 404, b"not yet"
        return 200, signed_torrent

    tracker.routes[("GET", "/torrent/download/42.rsskey")] = download


def saved_torrent(upload_meta):
    return Path(upload_meta["base_dir"]) / "tmp" / "u" / "[EMU]Peli.torrent"


def test_upload_ok_downloads_signed_torrent(tracker, emu, upload_meta, signed_torrent, capsys):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})
    serve_download(tracker, signed_torrent, failures=2)

    assert run(emu.upload(upload_meta)) is True
    assert saved_torrent(upload_meta).exists()
    out = capsys.readouterr().out
    assert f"Torrent URL: {emu.domain}/torrents/42" in out
    assert "Torrent uploaded successfully" in out
    assert len(tracker.calls("POST", UPLOAD)) == 1


def test_upload_retries_server_errors(tracker, emu, upload_meta, signed_torrent):
    answers = iter([(502, b"bad gateway"), (520, b"cloudflare"), (200, {"success": True, "data": download_url(tracker)})])
    tracker.routes[("POST", UPLOAD)] = lambda q: next(answers)
    serve_download(tracker, signed_torrent)

    assert run(emu.upload(upload_meta)) is True
    assert len(tracker.calls("POST", UPLOAD)) == 3


def test_upload_timeout_that_landed_is_recovered(tracker, emu, upload_meta, signed_torrent, capsys):
    emu.timeout = httpx.Timeout(0.5)
    landed = []

    def upload(query):
        if not landed:
            landed.append(NAME)
            time.sleep(1)  # entra, pero contesta tarde
            return 200, {"success": True, "data": download_url(tracker)}
        return 404, {"success": False, "message": "Validation Error.", "data": {"name": ["The name has already been taken."]}}

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE, download_link=download_url(tracker)) for n in landed if n == q["name"][0]], "links": {"next": None}},
    )
    serve_download(tracker, resigned(signed_torrent))

    assert run(emu.upload(upload_meta)) is True
    assert saved_torrent(upload_meta).exists()
    assert "Torrent uploaded successfully" in capsys.readouterr().out


def test_upload_never_recovers_same_name_and_size_with_other_pieces(tracker, emu, upload_meta, tmp_path):
    emu.timeout = httpx.Timeout(0.2)
    landed = []
    other = tmp_path / "otro" / "x.bin"
    other.parent.mkdir()
    other.write_bytes(b"y" * SIZE)
    other_torrent = torf.Torrent(path=other, private=True)
    other_torrent.generate()

    def upload(query):
        landed.append(NAME)
        time.sleep(0.5)
        return 500, b""

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE, download_link=download_url(tracker)) for n in landed[:1]], "links": {"next": None}},
    )
    serve_download(tracker, other_torrent.dump())

    assert run(emu.upload(upload_meta)) is False
    assert not saved_torrent(upload_meta).exists()


def test_upload_timeout_that_landed_then_5xx_is_recovered(tracker, emu, upload_meta, signed_torrent):
    emu.timeout = httpx.Timeout(0.5)
    landed = []

    def upload(query):
        if not landed:
            landed.append(NAME)
            time.sleep(1)
            return 200, {"success": True, "data": download_url(tracker)}
        return 502, b"bad gateway"

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE, download_link=download_url(tracker)) for n in landed], "links": {"next": None}},
    )
    serve_download(tracker, signed_torrent)

    assert run(emu.upload(upload_meta)) is True
    assert saved_torrent(upload_meta).exists()
    assert len(tracker.calls("POST", UPLOAD)) == 3


def test_upload_without_answer_and_not_on_tracker_fails(tracker, emu, upload_meta):
    emu.timeout = httpx.Timeout(0.2)

    def upload(query):
        time.sleep(0.5)
        return 500, b""

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [], "links": {"next": None}})

    assert run(emu.upload(upload_meta)) is False
    assert len(tracker.calls("POST", UPLOAD)) == 3


def test_upload_rejected_name_on_first_try_is_a_failure(tracker, emu, upload_meta):
    tracker.routes[("POST", UPLOAD)] = lambda q: (404, {"success": False, "data": {"name": ["The name has already been taken."]}})
    assert run(emu.upload(upload_meta)) is False
    assert len(tracker.calls("GET", SEARCH)) == 1  # solo la foto de antes de subir


def test_upload_never_recovers_an_older_torrent_with_the_same_name(tracker, emu, upload_meta):
    emu.timeout = httpx.Timeout(0.2)
    old = item(NAME, download_link=f"{tracker.url}/torrent/download/7.rsskey")

    def upload(query):
        time.sleep(0.5)  # no entra, y además no contesta
        return 500, b""

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [old], "links": {"next": None}})

    assert run(emu.upload(upload_meta)) is False
    assert tracker.calls("GET", "/torrent/download/7.rsskey") == []


def test_upload_without_previous_snapshot_is_not_recovered(tracker, emu, upload_meta):
    emu.timeout = httpx.Timeout(0.5)
    landed = []

    def upload(query):
        landed.append(NAME)
        time.sleep(1)
        return 200, {"success": True, "data": download_url(tracker)}

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        (500, b"") if not landed else (200, {"data": [item(NAME, SIZE, download_link=download_url(tracker))], "links": {"next": None}})
    )

    assert run(emu.upload(upload_meta)) is False


def test_upload_never_recovers_a_new_torrent_of_another_size(tracker, emu, upload_meta):
    emu.timeout = httpx.Timeout(0.2)
    landed = []

    def upload(query):
        landed.append(NAME)  # otro sube un torrent con el mismo nombre mientras el nuestro no contesta
        time.sleep(0.5)
        return 500, b""

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE + 1, download_link=download_url(tracker)) for n in landed[:1]], "links": {"next": None}},
    )

    assert run(emu.upload(upload_meta)) is False


def test_upload_ok_with_invalid_link_counts_as_uploaded(tracker, emu, upload_meta, capsys):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": "http://[::1"})
    assert run(emu.upload(upload_meta)) is True
    assert "Torrent upload succeeded, but failed to download the torrent file" in capsys.readouterr().out


@pytest.mark.parametrize("body", [b"<html></html>", b"[1, 2]"], ids=["html", "json-lista"])
def test_upload_unexpected_body_fails(tracker, emu, upload_meta, body):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, body)
    assert run(emu.upload(upload_meta)) is False


@pytest.mark.parametrize("data", [123, "", None], ids=["numero", "vacio", "null"])
def test_upload_ok_without_link_counts_as_uploaded(tracker, emu, upload_meta, data, capsys):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": data})
    assert run(emu.upload(upload_meta)) is True
    assert "Torrent upload succeeded, but failed to download the torrent file" in capsys.readouterr().out


def test_upload_forbidden_is_not_retried(tracker, emu, upload_meta):
    tracker.routes[("POST", UPLOAD)] = lambda q: (403, b"forbidden")
    assert run(emu.upload(upload_meta)) is False
    assert len(tracker.calls("POST", UPLOAD)) == 1


def test_upload_ok_but_torrent_never_downloads(tracker, emu, upload_meta, capsys):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})
    tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, b"<html>login</html>")

    assert run(emu.upload(upload_meta)) is True
    assert not saved_torrent(upload_meta).exists()
    assert "Torrent upload succeeded, but failed to download the torrent file" in capsys.readouterr().out
    assert len(tracker.calls("GET", "/torrent/download/42.rsskey")) == 4
