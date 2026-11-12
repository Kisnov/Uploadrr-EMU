"""La salida de una subida entera de EMU, línea a línea, contra tests/golden/*.txt.

emuwarez-uploadrr lee esa salida con regex (``uploadrr_progress.py``, ``upload_result_parser.py``): los ficheros de
tests/golden son lo que de verdad escribe esta herramienta, generado aquí y no a mano. La app los reproduce por sus
parsers. Qué es real y qué está sustituido: tests/flujo_emu.py. Para regenerarlos tras un cambio a propósito:
``REGENERAR_GOLDEN=1 python -m pytest tests/test_transcripcion_dorada.py`` y revisa el diff en git."""

import sys

import pytest

from flujo_emu import (
    EPISODIO,
    NOMBRE_EPISODIO,
    NOMBRE_PELICULA,
    PELICULA,
    UPLOAD,
    Flujo,
    FakeTracker,
    Qbit,
    comparar_con_golden,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="las rutas y los saltos de línea de Rich son los de Linux")

ARGV_PELICULA = ["-tk", "EMU", "-mt", NOMBRE_PELICULA, "-tmdb", "1000", "-ua", "-c", "movie", "-res", "1080p"]
ARGV_EPISODIO = ["-tk", "EMU", "-mt", NOMBRE_EPISODIO, "-tmdb", "1000", "-ua", "-c", "tv", "-res", "1080p", "-season", "S01", "-episode", "E02"]


# Lo que la app busca en la salida de una subida buena, en este orden (si regenerar quita una, el golden ya no vale).
LINEAS_DE_LA_APP = [
    "Searching for existing torrents",
    "No dupes found",
    "Uploading screenshots: 6/6",
    "Step done: screenshots",
    "Hashing with torf: 100.0%",
    "Step done: torrent",
    "Uploading to EMU",
    "Torrent URL: https://emu-warez.com/torrents/42",
    "Torrent uploaded successfully",
    "Adding and rechecking torrent",
    "Added to:",
]


def _en_orden(salida: str) -> None:
    resto = salida
    for linea in LINEAS_DE_LA_APP:
        assert linea in resto, f"falta {linea!r} (o está fuera de orden)"
        resto = resto.split(linea, 1)[1]


@pytest.fixture
def flujo(monkeypatch, capsys):
    tracker = FakeTracker()
    flujo = Flujo(monkeypatch, tracker, capsys)
    yield flujo
    flujo.limpiar()
    tracker.server.shutdown()


def _campos(flujo):
    (campos, ficheros), = flujo.tracker.bodies
    return campos, ficheros


def test_subida_de_una_pelicula(flujo):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta()

    codigo, salida = flujo.correr([str(ruta), *ARGV_PELICULA])

    assert codigo == 0
    campos, ficheros = _campos(flujo)
    assert (campos["name"], campos["category_id"], campos["type_id"], campos["resolution_id"]) == (NOMBRE_PELICULA, "1", "4", "3")
    assert campos["tmdb"] == "1000" and "season_number" not in campos and "episode_number" not in campos
    assert ficheros["torrent"].startswith(b"d")
    assert len(Qbit.anadidos) == 1  # entró en qBittorrent, y arrancado: venía hasheado de aquí
    _en_orden(flujo.normalizar(salida))
    comparar_con_golden("subida_emu_pelicula.txt", flujo.normalizar(salida))


def test_subida_de_un_episodio(flujo):
    ruta = flujo.crear_video(EPISODIO)
    flujo.tracker_que_acepta()

    codigo, salida = flujo.correr([str(ruta), *ARGV_EPISODIO])

    assert codigo == 0
    campos, _ = _campos(flujo)
    assert (campos["name"], campos["category_id"], campos["type_id"], campos["resolution_id"]) == (NOMBRE_EPISODIO, "2", "4", "3")
    assert (campos["season_number"], campos["episode_number"]) == ("1", "2")
    _en_orden(flujo.normalizar(salida))
    comparar_con_golden("subida_emu_episodio.txt", flujo.normalizar(salida))


def test_duplicado_encontrado_sale_con_1(flujo):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta(duplicados=[(NOMBRE_PELICULA, ruta.stat().st_size)])

    codigo, salida = flujo.correr([str(ruta), *ARGV_PELICULA])

    assert codigo == 1
    assert flujo.tracker.calls("POST", UPLOAD) == []  # ni se intentó subir
    assert "Torrent URL:" not in salida and not Qbit.anadidos
    comparar_con_golden("subida_emu_duplicado.txt", flujo.normalizar(salida))
