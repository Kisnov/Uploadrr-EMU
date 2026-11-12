"""Una subida a eMuwarez de punta a punta, como la lanza emuwarez-uploadrr: ``upload.main()`` con su argv.

Es de verdad todo lo del repositorio (argumentos, preparación, nombre, capturas, duplicados, torrent, POST, cliente).
Lo que se sustituye está en el borde con el mundo:

- MediaInfo: ``MediaInfo.parse`` devuelve lo que mediainfo escribió con un mkv real (tests/golden/entradas).
- ffmpeg: ``asyncio.create_subprocess_exec`` escribe un PNG de ruido donde ffmpeg habría escrito la captura, y
  ``ffmpeg.probe`` da una duración.
- HTTP a internet: un transporte de httpx contesta TMDb y el alojador de imágenes. El tracker es el de localhost.
- qBittorrent: ``qbittorrentapi.Client`` es una clase falsa que apunta lo que recibe.
"""

from __future__ import annotations

import asyncio
import io
import json
import random
import re
import shutil
import socket
import sys
import tempfile
from types import SimpleNamespace
from pathlib import Path

import ffmpeg
import pytest
import httpx
import torf
import yaml
from PIL import Image
from pymediainfo import MediaInfo

import upload
from src import clients
from src.databases import imdb
from src.mediainfo import MediaInfoHandler
from src.progress import ProgressManager
from src.trackers.EMU import EMU
from src.utils.console import console
from tracker_falso import FakeTracker

RAIZ = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden"
ENTRADAS = GOLDEN / "entradas"
TOKEN = "clave-de-prueba"
SEARCH = "/api/torrents/filter"
UPLOAD = "/api/torrents/upload"
DOMINIO = "https://emu-warez.com"

PELICULA = "Peli.de.Prueba.2020.1080p.WEB-DL.DD5.1.H.264-GRP"
EPISODIO = "Serie.de.Prueba.S01E02.1080p.WEB-DL.DD5.1.H.264-GRP"
NOMBRE_PELICULA = "Peli de Prueba 2020 1080p WEB-DL DD5.1 H.264-GRP"
NOMBRE_EPISODIO = "Serie de Prueba S01E02 1080p WEB-DL DD5.1 H.264-GRP"


class SalidaBloqueada(RuntimeError):
    pass


_connect = socket.socket.connect
_getaddrinfo = socket.getaddrinfo


def _conectar_solo_local(sock, address):
    if address[0] not in ("127.0.0.1", "::1"):
        raise SalidaBloqueada(f"los tests solo pueden conectar con el tracker falso, no con {address[0]}")
    return _connect(sock, address)


def _resolver_solo_local(host, *args, **kwargs):
    if host not in (b"localhost", "localhost"):
        raise SalidaBloqueada(f"los tests no pueden resolver {host}")
    return _getaddrinfo(host, *args, **kwargs)


def _tmdb(ruta: str):
    pelicula = ruta.startswith("/movie/")
    ficha = {
        "id": 1000,
        "adult": False,
        "overview": "Una película que solo existe en los tests.",
        "original_language": "es",
        "genres": [{"id": 18, "name": "Drama"}],
        "keywords": {"keywords": [{"id": 1, "name": "prueba"}]} if pelicula else {"results": [{"id": 1, "name": "prueba"}]},
        "external_ids": {"imdb_id": "tt1234567", "tvdb_id": 4321},
        "credits": {"crew": [{"job": "Director", "name": "Alguien"}]},
        "videos": {"results": [{"site": "YouTube", "key": "abc123", "type": "Trailer", "iso_639_1": "es"}]},
        "images": {"posters": [{"file_path": "/poster.jpg", "iso_639_1": "es"}], "logos": [], "backdrops": []},
        "poster_path": "/poster.jpg",
    }
    if pelicula:
        return {**ficha, "title": "Peli de Prueba", "original_title": "Peli de Prueba", "release_date": "2020-05-01", "runtime": 90}
    return {
        **ficha,
        "name": "Serie de Prueba",
        "original_name": "Serie de Prueba",
        "first_air_date": "2020-05-01",
        "episode_run_time": [45],
        "type": "Scripted",
        "seasons": [{"season_number": 1, "air_date": "2020-05-01"}],
    }


IMAGEN = "https://i.ibb.co/x/captura.png"


class Internet:
    """Lo que contesta internet; el resto de hosts, salvo localhost, falla."""

    def __init__(self):
        self.pedidas: list[str] = []

    async def __call__(self, original, transporte, request: httpx.Request):
        host = request.url.host
        if host in ("localhost", "127.0.0.1"):
            return await original(transporte, request)
        self.pedidas.append(f"{request.method} {host}{request.url.path}")
        if host == "api.themoviedb.org":
            ruta = request.url.path.removeprefix("/3")
            if re.fullmatch(r"/(movie|tv)/\d+", ruta):
                return httpx.Response(200, json=_tmdb(ruta), request=request)
            return httpx.Response(200, json={"results": []}, request=request)
        if host == "api.imgbb.com":
            datos = {"url": IMAGEN, "display_url": IMAGEN, "url_viewer": "https://ibb.co/x", "image": {"url": IMAGEN}}
            return httpx.Response(200, json={"success": True, "data": datos}, request=request)
        if host == "api.srrdb.com":
            return httpx.Response(200, json={"resultsCount": 0, "results": []}, request=request)
        if host == "api.tvmaze.com":
            return httpx.Response(404, json={}, request=request)
        raise SalidaBloqueada(f"los tests no pueden llamar a {host}")


def _imdb_get_movie(imdb_id):
    return SimpleNamespace(
        title="Peli de Prueba",
        year=2020,
        imdbId="1234567",
        duration=90,
        cover_url="https://m.media-amazon.com/images/poster.jpg",
        plot="Una película que solo existe en los tests.",
        genres=["Drama"],
        storyline_keywords=["prueba"],
        sound_mixes=["Dolby Digital"],
        countries=["Spain"],
        languages=["Spanish"],
        directors=[],
        kind="movie",
        title_localized="Peli de Prueba",
    )


class Qbit:
    """qBittorrent: acepta todo y enseña el torrent una vez añadido."""

    anadidos: list = []

    def __init__(self, **_kwargs):
        pass

    def auth_log_in(self):
        pass

    def torrents_add(self, **kwargs):
        Qbit.anadidos.append(kwargs)
        return "Ok."

    def torrents_info(self, torrent_hashes):
        return [{"hash": torrent_hashes, "state": "stoppedUP", "progress": 1}] if Qbit.anadidos else []

    def torrents_resume(self, infohash):
        pass

    def torrents_recheck(self, torrent_hashes):
        pass

    def torrents_start(self, torrent_hashes):
        pass

    def torrents_add_tags(self, **_kwargs):
        pass


class ProcesoFalso:
    returncode = 0

    def __init__(self, salida: bytes = b""):
        self.salida = salida

    async def communicate(self):
        return self.salida, b""


def _png_de_ruido(destino: str) -> None:
    """Una captura que pasa los filtros de tamaño y de monocromo."""
    datos = random.Random(0).randbytes(320 * 240 * 3)
    Image.frombytes("RGB", (320, 240), datos).save(destino)


def config_de_prueba() -> dict:
    config = yaml.safe_load((RAIZ / "data" / "backup" / "example_config.yaml").read_text(encoding="utf-8"))
    config["DEFAULT"].update(
        tmdb="clave-tmdb", torrent_creation="torf", default_torrent_client="qbit", add_trailer=False, add_logo=False, screens=2
    )
    config["TRACKERS"]["EMU"]["api_key"] = TOKEN
    config["TRACKERS"]["BLU"]["useAPI"] = False
    config["SCREENS"].update(img_hosts=["imgbb"], API_KEYS={"imgbb": "clave-imgbb"}, per_episode=0)
    config["TORRENT_CLIENTS"] = {
        "qbit": {
            "torrent_client": "qbit",
            "enable_search": False,
            "qbit_url": "http://127.0.0.1",
            "qbit_port": "8080",
            "qbit_user": "u",
            "qbit_pass": "p",
        }
    }
    return config


class Flujo:
    def __init__(self, monkeypatch, tracker: FakeTracker, capsys):
        # Siempre con la misma longitud: Rich parte las líneas a 80 columnas y, con otra ruta, se partirían en otro sitio.
        self.raiz = Path(tempfile.mkdtemp(prefix="emu-"))
        self.base = self.raiz / "app"
        self.base.mkdir()
        self.contenido = self.raiz / "biblioteca"
        self.contenido.mkdir()
        self.tracker = tracker
        self.capsys = capsys
        self.monkeypatch = monkeypatch
        self.internet = Internet()
        self.config = config_de_prueba()

    def limpiar(self):
        shutil.rmtree(self.raiz, ignore_errors=True)

    def tracker_que_acepta(self, duplicados: list[tuple[str, int]] | None = None) -> None:
        """El tracker: la búsqueda devuelve `duplicados`; el POST lo acepta y reescribe el .torrent como hace eMuwarez."""
        tracker = self.tracker
        datos = [{"id": n, "attributes": {"name": nombre, "size": tam}} for n, (nombre, tam) in enumerate(duplicados or [], 1)]
        tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": datos, "links": {"next": None}})

        def subir(_query):
            torrent = torf.Torrent.read_stream(io.BytesIO(tracker.last_body[1]["torrent"]), validate=False)
            torrent.source = "eMuwarez"
            firmado = torrent.dump(validate=False)
            tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, firmado)
            return 200, {"success": True, "data": f"{tracker.url}/torrent/download/42.rsskey"}

        tracker.routes[("POST", UPLOAD)] = subir

    def normalizar(self, salida: str) -> str:
        """Lo que cambia de una ejecución a otra, con el mismo largo para no mover los saltos de línea de Rich."""
        salida = salida.replace("\r\n", "\n")
        salida = re.sub(r"emu-\w{8}", "emu-00000000", salida)
        salida = salida.replace(self.tracker.url, DOMINIO)
        # Rich escribe a la derecha de cada línea de log el fichero y la línea que la emitió: se mueve con cada edición.
        return "\n".join(re.sub(r"\s+\w+\.py:\d+$", "", linea) for linea in salida.split("\n"))

    def crear_video(self, nombre: str) -> Path:
        """Un «mkv» (los bytes no importan: mediainfo y ffmpeg están sustituidos) y su salida real de mediainfo."""
        ruta = self.contenido / f"{nombre}.mkv"
        ruta.write_bytes(random.Random(1).randbytes(4096))
        self.mediainfo = {
            "JSON": (ENTRADAS / f"{nombre}.mediainfo.json").read_text(encoding="utf-8"),
            "STRING": (ENTRADAS / f"{nombre}.mediainfo.txt").read_text(encoding="utf-8"),
            "origen": f"/gen/{nombre}.mkv",
        }
        return ruta

    def _parse(self, ruta, output=None, **_kw):
        if output not in ("JSON", "STRING"):
            raise SalidaBloqueada(f"MediaInfo.parse con output={output}")
        return self.mediainfo[output].replace(self.mediainfo["origen"], str(ruta))

    def preparar(self):
        mp = self.monkeypatch
        mp.chdir(self.base)
        mp.setattr(MediaInfoHandler, "_instance", None)  # singletons: guardan la carpeta y los flags de la subida anterior
        mp.setattr(ProgressManager, "_instance", None)
        mp.setattr(socket.socket, "connect", _conectar_solo_local)
        mp.setattr(socket, "getaddrinfo", _resolver_solo_local)
        shutil.copytree(RAIZ / "data", self.base / "data", ignore=shutil.ignore_patterns("config.yaml*"), dirs_exist_ok=True)
        mp.setattr(upload, "base_dir", self.base)
        mp.setattr(upload, "config_path", self.base / "config.yaml")
        (self.base / "config.yaml").write_text(yaml.safe_dump(self.config), encoding="utf-8")
        mp.setattr(EMU, "domain", self.tracker.url)
        mp.setattr(console, "_width", 80)
        mp.delenv("COLUMNS", raising=False)
        mp.setattr(MediaInfo, "parse", staticmethod(self._parse))
        mp.setattr(ffmpeg, "probe", lambda *_a, **_k: {"format": {"duration": "5400.0"}})
        mp.setattr(clients.qbittorrentapi, "Client", Qbit)
        mp.setattr(imdb, "get_movie", _imdb_get_movie)
        mp.setattr(imdb, "get_akas", lambda _id: SimpleNamespace(akas=[]))
        mp.setattr(Qbit, "anadidos", [])

        original = httpx.AsyncHTTPTransport.handle_async_request

        async def transporte(this, request):
            return await self.internet(original, this, request)

        mp.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transporte)

        async def subproceso(*args, **_kw):
            if args and args[0] == "ffmpeg":
                _png_de_ruido(args[-1])
                return ProcesoFalso()
            if args and args[0] == "ffprobe":
                datos = {"streams": [{"r_frame_rate": "24/1"}]} if "stream=r_frame_rate" in args else {"format": {"duration": "5400.0"}}
                return ProcesoFalso(json.dumps(datos).encode())
            raise SalidaBloqueada(f"los tests no pueden lanzar {args[:1]}")

        mp.setattr(asyncio, "create_subprocess_exec", subproceso)

    def correr(self, argv: list[str]) -> tuple[int, str]:
        """upload.main() con ese argv; devuelve (código de salida, stdout tal como lo recibiría una tubería)."""
        self.preparar()
        self.monkeypatch.setattr(sys, "argv", ["upload.py", *argv])
        self.capsys.readouterr()
        codigo = 0
        try:
            asyncio.run(upload.main())
        except SystemExit as salida:
            codigo = salida.code if isinstance(salida.code, int) else 1
        return codigo, self.capsys.readouterr().out


def comparar_con_golden(nombre: str, obtenido: str) -> None:
    """Compara con tests/golden/<nombre>. Con REGENERAR_GOLDEN=1 lo reescribe (o en la carpeta que diga el valor)."""
    import difflib
    import os

    regenerar = os.environ.get("REGENERAR_GOLDEN")
    if regenerar:
        destino = GOLDEN if regenerar == "1" else Path(regenerar)
        (destino / nombre).write_bytes(obtenido.encode("utf-8"))
        return
    esperado = (GOLDEN / nombre).read_text(encoding="utf-8")  # el modo texto traduce CRLF: un checkout de Windows vale
    if obtenido != esperado:
        diff = "\n".join(difflib.unified_diff(esperado.split("\n"), obtenido.split("\n"), "golden", "obtenido", lineterm="", n=2))
        pytest.fail(
            f"La salida de {nombre} ha cambiado. emuwarez-uploadrr lee estas líneas tal cual: si el cambio es a propósito, "
            f"actualiza la app y regenera con REGENERAR_GOLDEN=1.\n{diff}",
            pytrace=False,
        )
