"""Búsqueda de duplicados y subida de UNIT3D contra un tracker falso en local."""

import asyncio
import io
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest
import torf

import src.trackers.UNIT3D as unit3d
import upload
from src.trackers.EMU import EMU
from src.trackers.UNIT3D import DupeSearchError
from tracker_falso import FakeTracker

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


def test_search_timeout_raises(tracker, emu, meta):
    emu.timeout = httpx.Timeout(0.2)

    def search(query):
        time.sleep(0.4)
        return 200, {"data": [], "links": {"next": None}}

    tracker.routes[("GET", SEARCH)] = search
    with pytest.raises(DupeSearchError):
        run(emu.search_existing(meta))


def test_search_connection_refused_raises(emu, meta):
    with socket.socket() as libre:
        libre.bind(("127.0.0.1", 0))
        puerto = libre.getsockname()[1]
    emu.search_url = f"http://localhost:{puerto}{SEARCH}"  # nadie escucha ahí
    with pytest.raises(DupeSearchError) as error:
        run(emu.search_existing(meta))
    assert TOKEN not in str(error.value)


@pytest.fixture
def sleeps(monkeypatch):
    """Las esperas que se piden, sin esperar."""
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(unit3d.asyncio, "sleep", sleep)
    return waits


def test_search_repeats_the_page_that_hits_the_rate_limit(tracker, emu, meta, sleeps):
    answers = iter([
        (429, b"", ("Retry-After", "7")),
        (200, {"data": [item("A", 10)], "links": {"next": f"{tracker.url}{SEARCH}?cursor=2&tmdbId=1"}}),
        (429, b""),
        (200, {"data": [item("B", 20)], "links": {"next": None}}),
    ])
    tracker.routes[("GET", SEARCH)] = lambda q: next(answers)
    assert run(emu.search_existing(meta)) == {"A": 10, "B": 20}

    assert sleeps == [8, 60]  # Retry-After y un segundo, o un minuto si no viene
    first, first_again, second, second_again = tracker.calls("GET", SEARCH)
    assert first == first_again and second == second_again and second["cursor"] == ["2"]


def test_search_rate_limit_that_persists_raises(tracker, emu, meta, sleeps):
    tracker.routes[("GET", SEARCH)] = lambda q: (429, b"", ("Retry-After", "Wed, 21 Oct 2015 07:28:00 GMT"))
    with pytest.raises(DupeSearchError, match="HTTP 429"):
        run(emu.search_existing(meta))
    assert len(tracker.requests) == 4 and sleeps == [60, 60, 60]  # un Retry-After con fecha no se interpreta


def test_search_other_errors_are_not_retried(tracker, emu, meta, sleeps):
    tracker.routes[("GET", SEARCH)] = lambda q: (500, b"error")
    with pytest.raises(DupeSearchError, match="HTTP 500"):
        run(emu.search_existing(meta))
    assert len(tracker.requests) == 1 and sleeps == []


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


def test_upload_sends_the_form_fields_of_a_movie(tracker, emu, upload_meta, signed_torrent):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})
    serve_download(tracker, signed_torrent)

    assert run(emu.upload({**upload_meta, "resolution": "720p", "type": "REMUX"})) is True

    (fields, files), = tracker.bodies
    assert (fields["name"], fields["category_id"], fields["type_id"], fields["resolution_id"]) == (NAME, "1", "2", "5")
    assert fields["tmdb"] == "1" and "season_number" not in fields and "episode_number" not in fields
    assert files["torrent"] == signed_torrent


def test_upload_sends_season_and_episode_of_an_episode(tracker, emu, upload_meta, signed_torrent):
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})
    serve_download(tracker, signed_torrent)

    assert run(emu.upload({**upload_meta, "category": "TV", "season_int": 2, "episode_int": 7})) is True

    (fields, _), = tracker.bodies
    assert (fields["category_id"], fields["type_id"], fields["resolution_id"]) == ("2", "4", "3")
    assert (fields["season_number"], fields["episode_number"]) == ("2", "7")


def test_upload_retries_server_errors(tracker, emu, upload_meta, signed_torrent):
    answers = iter([(502, b"bad gateway"), (520, b"cloudflare"), (200, {"success": True, "data": download_url(tracker)})])
    tracker.routes[("POST", UPLOAD)] = lambda q: next(answers)
    serve_download(tracker, signed_torrent)

    assert run(emu.upload(upload_meta)) is True
    assert len(tracker.calls("POST", UPLOAD)) == 3


def test_upload_retries_rate_limit_respecting_retry_after(tracker, emu, upload_meta, signed_torrent, sleeps):
    answers = iter([(429, b"", ("Retry-After", "7")), (429, b"", ("Retry-After", "9999")), (200, {"success": True, "data": download_url(tracker)})])
    tracker.routes[("POST", UPLOAD)] = lambda q: next(answers)
    serve_download(tracker, signed_torrent)

    assert run(emu.upload(upload_meta)) is True
    assert len(tracker.calls("POST", UPLOAD)) == 3
    assert sleeps == [8, unit3d.UPLOAD_429_MAX_WAIT]  # Retry-After y un segundo; y el tope si pide demasiado


def test_upload_rate_limit_that_persists_gives_up(tracker, emu, upload_meta, sleeps):
    tracker.routes[("POST", UPLOAD)] = lambda q: (429, b"", ("Retry-After", "3"))

    assert run(emu.upload(upload_meta)) is False
    assert len(tracker.calls("POST", UPLOAD)) == 3 and sleeps == [4, 4]


def test_upload_other_4xx_is_not_repeated(tracker, emu, upload_meta, sleeps):
    tracker.routes[("POST", UPLOAD)] = lambda q: (422, {"success": False, "message": "mal"})

    assert run(emu.upload(upload_meta)) is False
    assert len(tracker.calls("POST", UPLOAD)) == 1 and sleeps == []


def test_upload_timeout_that_landed_is_recovered_behind_a_rate_limit(tracker, emu, upload_meta, signed_torrent, capsys, sleeps):
    emu.timeout = httpx.Timeout(0.5)
    landed = []

    def upload(query):
        if not landed:
            landed.append(NAME)
            time.sleep(1)  # entra, pero contesta tarde
            return 200, {"success": True, "data": download_url(tracker)}
        return 429, b"", ("Retry-After", "1")

    tracker.routes[("POST", UPLOAD)] = upload
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE, download_link=download_url(tracker)) for n in landed if n == q["name"][0]], "links": {"next": None}},
    )
    serve_download(tracker, resigned(signed_torrent))

    assert run(emu.upload(upload_meta)) is True  # el 429 final no tapa el intento que sí entró
    assert f"Torrent URL: {emu.domain}/torrents/42" in capsys.readouterr().out


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


@pytest.mark.parametrize(
    "link",
    [
        lambda port: f"http://otro.invalid:{port}/torrent/download/42.rsskey",
        lambda port: f"https://localhost:{port}/torrent/download/42.rsskey",
        lambda port: f"http://localhost:{port + 1}/torrent/download/42.rsskey",
    ],
    ids=["otro-host", "otro-esquema", "otro-puerto"],
)
def test_upload_never_sends_the_key_to_another_origin(tracker, emu, upload_meta, capsys, monkeypatch, link):
    requested = []
    real_get = httpx.AsyncClient.get

    async def spy(self, url, *args, **kwargs):
        requested.append(str(url))
        return await real_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "get", spy)
    url = link(tracker.server.server_address[1])
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": url})

    assert run(emu.upload(upload_meta)) is True
    assert "Torrent upload succeeded, but failed to download the torrent file" in capsys.readouterr().out
    assert not [r for r in requested if "/torrent/download/" in r]


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


def test_upload_ok_but_download_times_out_counts_as_uploaded(tracker, emu, upload_meta, capsys):
    emu.timeout = httpx.Timeout(0.2)
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})

    def download(query):
        time.sleep(0.4)
        return 200, b"d4:infode"

    tracker.routes[("GET", "/torrent/download/42.rsskey")] = download

    assert run(emu.upload(upload_meta)) is True  # ya está en el tracker: reintentarlo lo duplicaría
    assert not saved_torrent(upload_meta).exists()
    out = capsys.readouterr().out
    assert "Torrent upload succeeded, but failed to download the torrent file: ReadTimeout" in out
    assert TOKEN not in out


# --solo-cliente: el torrent ya está subido y solo se vuelve a añadir al cliente

API_TORRENT = "/api/torrents/42"


class ClienteFalso:
    def __init__(self, resultado=True):
        self.resultado = resultado
        self.anadidos = []

    async def add_to_client(self, meta, tracker):
        if isinstance(self.resultado, Exception):
            raise self.resultado
        torrent = Path(meta["base_dir"]) / "tmp" / meta["uuid"] / f"[{tracker}]{meta['clean_name']}.torrent"
        self.anadidos.append((tracker, torrent.exists()))
        return self.resultado


@pytest.fixture
def solo_cliente(tracker, emu, upload_meta):
    """Lanza Uploadrr._solo_cliente con el EMU del tracker falso y el cliente que se le pase."""
    emu.domain = tracker.url

    def lanzar(cliente, registro=None, ruta=None):
        app = upload.Uploadrr.__new__(upload.Uploadrr)
        app.config, app.client = emu.config, cliente
        registro = {"EMU": {"class": lambda config: emu}} if registro is None else registro
        ruta = ruta or Path(upload_meta["base_dir"]) / "x.bin"  # el contenido de signed_torrent
        return run(app._solo_cliente({**upload_meta, "solo_cliente": 42, "path": str(ruta)}, registro))

    return lanzar


@pytest.mark.parametrize("envoltorio", [True, False], ids=["con-data", "sin-data"])
def test_solo_cliente_baja_el_torrent_firmado_y_lo_anade(tracker, upload_meta, signed_torrent, solo_cliente, capsys, envoltorio):
    recurso = {"type": "torrent", "id": "42", "attributes": {"name": NAME, "download_link": download_url(tracker)}}
    tracker.routes[("GET", API_TORRENT)] = lambda q: (200, {"data": recurso} if envoltorio else recurso)
    serve_download(tracker, signed_torrent)
    cliente = ClienteFalso()

    assert solo_cliente(cliente) is None
    assert cliente.anadidos == [("EMU", True)]
    assert torf.Torrent.read(saved_torrent(upload_meta)).comment == "Torrent descargado de la comunidad eMuwarez."
    assert tracker.calls("GET", API_TORRENT)[0]["api_token"] == [TOKEN]
    assert tracker.calls("POST", UPLOAD) == [] and tracker.calls("GET", SEARCH) == []  # ni subida ni duplicados
    out = capsys.readouterr().out
    assert "Torrent URL:" not in out and "Torrent upload succeeded" not in out


@pytest.mark.parametrize(
    "respuesta",
    [
        (404, {"message": "No query results"}),
        (500, b"error"),
        (200, b"<html>login</html>"),
        (200, {"data": []}),
        (200, {"data": {"attributes": {}}}),
        (200, {"data": {"attributes": {"download_link": None}}}),
        (200, {"data": {"attributes": {"download_link": ""}}}),
    ],
    ids=["http-404", "http-500", "no-json", "data-lista", "sin-enlace", "enlace-null", "enlace-vacio"],
)
def test_solo_cliente_sin_enlace_no_toca_el_cliente(tracker, upload_meta, solo_cliente, capsys, respuesta):
    tracker.routes[("GET", API_TORRENT)] = lambda q: respuesta
    cliente = ClienteFalso()

    assert solo_cliente(cliente) is not None
    assert cliente.anadidos == [] and not saved_torrent(upload_meta).exists()
    assert TOKEN not in capsys.readouterr().out


def test_solo_cliente_si_el_torrent_no_baja_no_toca_el_cliente(tracker, upload_meta, solo_cliente, capsys):
    tracker.routes[("GET", API_TORRENT)] = lambda q: (200, {"data": {"attributes": {"download_link": download_url(tracker)}}})
    tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, b"<html>login</html>")
    cliente = ClienteFalso()

    assert solo_cliente(cliente) is not None
    assert cliente.anadidos == [] and not saved_torrent(upload_meta).exists()
    assert len(tracker.calls("GET", "/torrent/download/42.rsskey")) == 4
    assert TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("resultado", [False, None, RuntimeError("Failed to connect to qBittorrent.")], ids=["false", "none", "error"])
def test_solo_cliente_falla_si_el_cliente_no_lo_anade(tracker, signed_torrent, solo_cliente, capsys, resultado):
    tracker.routes[("GET", API_TORRENT)] = lambda q: (200, {"data": {"attributes": {"download_link": download_url(tracker)}}})
    serve_download(tracker, signed_torrent)

    assert solo_cliente(ClienteFalso(resultado)) is not None
    out = capsys.readouterr().out
    assert "el torrent no se ha añadido al cliente" in out
    assert ("Torrent client add failed: Failed to connect to qBittorrent." in out) == isinstance(resultado, Exception)


@pytest.fixture
def carpeta(tmp_path, tracker):
    """Una subida de carpeta con subcarpeta; el tracker sirve su .torrent."""
    raiz = tmp_path / "Serie"
    (raiz / "sub").mkdir(parents=True)
    (raiz / "a.mkv").write_bytes(b"a" * SIZE)
    (raiz / "sub" / "b.mkv").write_bytes(b"b" * SIZE)
    torrent = torf.Torrent(path=raiz, private=True)
    torrent.generate()
    tracker.routes[("GET", API_TORRENT)] = lambda q: (200, {"data": {"attributes": {"download_link": download_url(tracker)}}})
    serve_download(tracker, torrent.dump())
    return raiz


def test_solo_cliente_carpeta_que_coincide_se_anade(carpeta, solo_cliente):
    (carpeta / "extra.nfo").write_text("no está en el torrent")  # lo que torf excluye no molesta
    cliente = ClienteFalso()
    assert solo_cliente(cliente, ruta=carpeta) is None
    assert cliente.anadidos == [("EMU", True)]


@pytest.mark.parametrize(
    ("cambio", "fichero"),
    [
        (lambda raiz: (raiz / "sub" / "b.mkv").write_bytes(b"b" * (SIZE - 1)), "sub/b.mkv"),
        (lambda raiz: (raiz / "sub" / "b.mkv").unlink(), "sub/b.mkv"),
        (lambda raiz: (raiz / "a.mkv").unlink(), "a.mkv"),
    ],
    ids=["otro-tamano", "falta", "falta-el-primero"],
)
def test_solo_cliente_carpeta_distinta_no_se_anade(carpeta, upload_meta, solo_cliente, capsys, cambio, fichero):
    cambio(carpeta)
    cliente = ClienteFalso()

    assert solo_cliente(cliente, ruta=carpeta) is not None
    assert cliente.anadidos == [] and not saved_torrent(upload_meta).exists()  # el .torrent no se queda para reusarlo
    out = capsys.readouterr().out
    assert f"el torrent no coincide con la subida en {fichero}" in out and str(carpeta) not in out


@pytest.mark.parametrize(
    "preparar",
    [lambda base: (base / "x.bin").write_bytes(b"x" * (SIZE + 1)), lambda base: (base / "x.bin").rename(base / "y.bin")],
    ids=["otro-tamano", "otro-nombre"],
)
def test_solo_cliente_fichero_distinto_no_se_anade(tracker, upload_meta, signed_torrent, solo_cliente, capsys, preparar):
    tracker.routes[("GET", API_TORRENT)] = lambda q: (200, {"data": {"attributes": {"download_link": download_url(tracker)}}})
    serve_download(tracker, signed_torrent)
    base = Path(upload_meta["base_dir"])
    preparar(base)
    ruta = base / "x.bin" if (base / "x.bin").exists() else base / "y.bin"
    cliente = ClienteFalso()

    assert solo_cliente(cliente, ruta=ruta) is not None
    assert cliente.anadidos == [] and not saved_torrent(upload_meta).exists()
    assert "el torrent no coincide con la subida en x.bin" in capsys.readouterr().out


def test_solo_cliente_carpeta_con_otra_raiz_no_se_anade(carpeta, solo_cliente):
    otra = carpeta.rename(carpeta.parent / "OtraSerie")  # mismos ficheros, otra carpeta: qBittorrent iría a otra ruta
    cliente = ClienteFalso()
    assert solo_cliente(cliente, ruta=otra) == f"El torrent no coincide con la subida en {carpeta.name}"
    assert cliente.anadidos == []


def test_solo_cliente_solo_vale_para_emu(tracker, solo_cliente):
    assert solo_cliente(ClienteFalso(), registro={}) is not None
    assert tracker.requests == []


@pytest.mark.parametrize(
    ("resolucion", "res_id"),
    [
        ("4320p", "1"),
        ("2160p", "2"),
        ("1080p", "3"),
        ("1080i", "4"),
        ("720p", "5"),
        ("576p", "6"),
        ("576i", "6"),
        ("540p", "7"),
        ("480p", "8"),
        ("480i", "8"),
        ("8640p", "10"),
        ("OTHER", "10"),
    ],
)
def test_ids_de_resolucion_del_formulario_de_emu(emu, meta, resolucion, res_id):
    assert run(emu.get_res_id({**meta, "resolution": resolucion})) == res_id


@pytest.mark.parametrize(
    ("tipo", "type_id"),
    [("DISC", "1"), ("REMUX", "2"), ("ENCODE", "3"), ("WEBDL", "4"), ("WEBRIP", "5"), ("HDTV", "6"), ("SD", "7"), ("FLAC", "0")],
)
def test_ids_de_tipo_del_formulario_de_emu(emu, meta, tipo, type_id):
    assert run(emu.get_type_id({**meta, "type": tipo})) == type_id
