"""Creación del .torrent con mkbrr: mismos ficheros que torf, progreso para la app y caída a torf.

mkbrr no se lanza (conftest corta los procesos): se simula con un .torrent hecho por torf."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from torf import Torrent

from src.progress import ProgressManager
from src.utils import torrentman
from src.utils.console import console
from src.utils.torrentman import TorrentMaker


class Mensajes:
    def __init__(self):
        self.vistos: list[str] = []

    def update(self, message, style=None):
        self.vistos.append(message)


@pytest.fixture
def mensajes(monkeypatch):
    ProgressManager(console)
    capturados = Mensajes()
    monkeypatch.setattr(ProgressManager, "get_instance", classmethod(lambda cls: capturados))
    return capturados


@pytest.fixture
def pack(tmp_path):
    carpeta = tmp_path / "Serie S01"
    carpeta.mkdir()
    for nombre, tamano in (("Serie S01E01.mkv", 3000), ("Serie S01E02.mkv", 3100), ("Serie S01.nfo", 10)):
        (carpeta / nombre).write_bytes(b"x" * tamano)
    return carpeta


def _torf(path: Path, include=("*.mkv",)) -> Torrent:
    return Torrent(path=path, private=True, exclude_globs=["*.*"], include_globs=list(include))


def _crear(maker, torrent, path, salida):
    asyncio.run(maker._use_mkbrr(torrent, path, salida, 15, None))


def _mkbrr_falso(monkeypatch, recibidos, include=("*.mkv",), codigo=0):
    """mkbrr simulado: guarda lo que recibe y escribe el .torrent del árbol que le dan."""

    async def ejecutar(self, args):
        objetivo = Path(args[2])
        recibidos.append(sorted(p.relative_to(objetivo).as_posix() for p in objetivo.rglob("*") if p.is_file()))
        if codigo == 0:
            hecho = Torrent(path=objetivo, private=True, include_globs=list(include), exclude_globs=["*.*"])
            hecho.generate()
            hecho.write(args[args.index("-o") + 1], overwrite=True)
        return codigo

    monkeypatch.setattr(TorrentMaker, "_run_mkbrr", ejecutar)


def test_mkbrr_solo_ve_los_ficheros_que_elige_torf(monkeypatch, mensajes, pack, tmp_path):
    recibidos = []
    _mkbrr_falso(monkeypatch, recibidos)
    salida = tmp_path / "BASE.torrent"

    _crear(TorrentMaker({}), _torf(pack), pack, salida)

    assert recibidos == [["Serie S01E01.mkv", "Serie S01E02.mkv"]]
    assert [str(f) for f in Torrent.read(salida).files] == ["Serie S01/Serie S01E01.mkv", "Serie S01/Serie S01E02.mkv"]
    assert "mkbrr hashing complete" in mensajes.vistos
    assert not any("torf" in m for m in mensajes.vistos)


def test_si_mkbrr_lista_otros_ficheros_se_rehace_con_torf(monkeypatch, mensajes, pack, tmp_path):
    async def ejecutar(self, args):
        # Como si mkbrr hubiera hasheado la carpeta original, .nfo incluido.
        hecho = Torrent(path=pack, private=True)
        hecho.generate()
        hecho.write(args[args.index("-o") + 1], overwrite=True)
        return 0

    monkeypatch.setattr(TorrentMaker, "_run_mkbrr", ejecutar)
    salida = tmp_path / "BASE.torrent"

    _crear(TorrentMaker({}), _torf(pack), pack, salida)

    assert [str(f) for f in Torrent.read(salida).files] == ["Serie S01/Serie S01E01.mkv", "Serie S01/Serie S01E02.mkv"]
    assert "Torf hashing complete" in mensajes.vistos


def test_si_mkbrr_falla_se_rehace_con_torf(monkeypatch, mensajes, pack, tmp_path):
    _mkbrr_falso(monkeypatch, [], codigo=1)
    salida = tmp_path / "BASE.torrent"

    _crear(TorrentMaker({}), _torf(pack), pack, salida)

    assert Torrent.read(salida).files
    assert "Torf hashing complete" in mensajes.vistos


def test_un_fichero_suelto_va_tal_cual(monkeypatch, mensajes, tmp_path):
    peli = tmp_path / "Peli.mkv"
    peli.write_bytes(b"y" * 5000)
    recibidos = []

    async def ejecutar(self, args):
        recibidos.append(args[2])
        hecho = Torrent(path=args[2], private=True)
        hecho.generate()
        hecho.write(args[args.index("-o") + 1], overwrite=True)
        return 0

    monkeypatch.setattr(TorrentMaker, "_run_mkbrr", ejecutar)
    _crear(TorrentMaker({}), Torrent(path=peli, private=True), peli, tmp_path / "BASE.torrent")

    assert recibidos == [str(peli)]
    assert "mkbrr hashing complete" in mensajes.vistos


def test_sin_mkbrr_instalado_se_usa_torf(monkeypatch, mensajes, tmp_path):
    peli = tmp_path / "Peli.mkv"
    peli.write_bytes(b"z" * 5000)
    monkeypatch.setattr(TorrentMaker, "_check_tool", lambda self, name: False)
    salida = tmp_path / "BASE.torrent"

    asyncio.run(TorrentMaker({"DEFAULT": {}})._generate(Torrent(path=peli, private=True), peli, salida, 15, "mkbrr"))

    assert "mkbrr not found in PATH, falling back to torf..." in mensajes.vistos
    assert Torrent.read(salida).files


class _Salida:
    """stdout de mkbrr: cada read() devuelve un trozo, como llega la barra mientras hashea."""

    def __init__(self, trozos):
        self.trozos = list(trozos)

    async def read(self, n):
        return self.trozos.pop(0) if self.trozos else b""


class _Proceso:
    def __init__(self, trozos, codigo: int = 0):
        self.stdout = _Salida(trozos)
        self.returncode = None
        self._codigo = codigo

    async def wait(self):
        self.returncode = self._codigo
        return self._codigo


def test_el_porcentaje_de_mkbrr_llega_como_linea_de_progreso(monkeypatch, mensajes):
    trozos = [
        b"Files being hashed:\n",
        b"\r\x1b[36mHashing pieces...\x1b[0m 27% [===]",
        b"\r\x1b[36mHashing pieces...\x1b[0m 27% [===]",
        b"\r 61% [====]\r 72% [=====]",
        b"\r 100% [======]\nWrote /tmp/a.torrent\n",
    ]

    async def lanzar(*args, **kwargs):
        return _Proceso(trozos)

    monkeypatch.setattr(torrentman.asyncio, "create_subprocess_exec", lanzar)

    assert asyncio.run(TorrentMaker({})._run_mkbrr(["mkbrr", "create"])) == 0
    assert mensajes.vistos == [
        "Hashing with mkbrr: 0.0%",
        "Hashing with mkbrr: 27.0%",
        "Hashing with mkbrr: 72.0%",
        "Hashing with mkbrr: 100.0%",
    ]
