"""El canal --eventos de una subida entera de EMU: tests/golden/*.jsonl, el contrato y los caminos que no son una subida buena.

Regenerar los .jsonl: igual que los .txt (ver test_transcripcion_dorada.py)."""

import json
import os
import sys
from types import SimpleNamespace

import pytest
import qbittorrentapi
import torf

import upload
from contrato_eventos import validar, validar_linea_final
from flujo_emu import (
    EPISODIO,
    GOLDEN,
    NOMBRE_EPISODIO,
    NOMBRE_PELICULA,
    PELICULA,
    SEARCH,
    UPLOAD,
    Flujo,
    FakeTracker,
    Qbit,
    comparar_con_golden,
)
from test_transcripcion_dorada import ARGV_EPISODIO, ARGV_PELICULA

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="las rutas y los saltos de línea de Rich son los de Linux")


@pytest.fixture
def flujo(monkeypatch, capsys):
    tracker = FakeTracker()
    flujo = Flujo(monkeypatch, tracker, capsys)
    yield flujo
    flujo.limpiar()
    tracker.server.shutdown()


def _correr(flujo, ruta, argv):
    return flujo.correr([str(ruta), *argv, "--eventos", str(flujo.ruta_eventos)])


def _revisar(flujo):
    eventos = flujo.eventos()
    for evento in eventos:
        validar(evento)
    validar_linea_final(eventos)
    return eventos


def _codigos(eventos, ev):
    return [e for e in eventos if e["ev"] == ev]


def _resultado(eventos):
    return eventos[-1]["codigo"], eventos[-1]["ok"]


def _texto_dorado(nombre):
    return (GOLDEN / nombre).read_text(encoding="utf-8")


# Las tres subidas doradas: el texto es el mismo con y sin --eventos


@pytest.mark.parametrize(
    "video, argv, nombre",
    [(PELICULA, ARGV_PELICULA, "subida_emu_pelicula"), (EPISODIO, ARGV_EPISODIO, "subida_emu_episodio")],
)
def test_subida_buena_con_eventos(flujo, video, argv, nombre):
    flujo.tracker_que_acepta()

    codigo, salida = _correr(flujo, flujo.crear_video(video), argv)

    assert codigo == 0
    assert flujo.normalizar(salida) == _texto_dorado(nombre + ".txt")
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("subido", True)
    assert [e["ev"] for e in eventos][:2] == ["inicio", "fase"]
    assert eventos[0]["modo"] == "subida" and eventos[0]["reanuda"] is False
    assert [e["paso"] for e in _codigos(eventos, "paso_hecho")] == ["screenshots", "torrent"]
    assert [(e["torrent_id"], e["url"]) for e in _codigos(eventos, "subido")] == [(42, f"{flujo.tracker.url}/torrents/42")]
    assert _codigos(eventos, "cliente")[0]["ok"] is True
    # meta.json guarda la subida antes de anunciarla
    meta = json.loads((flujo.base / "tmp" / f"{video}.mkv" / "meta.json").read_text(encoding="utf-8"))
    assert meta["subido"] == {"id": 42, "url": f"{flujo.tracker.url}/torrents/42"}
    assert "eventos" not in meta
    comparar_con_golden(nombre + ".jsonl", flujo.eventos_normalizados())


def test_duplicado_con_eventos(flujo):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta(duplicados=[(NOMBRE_PELICULA, ruta.stat().st_size)])

    codigo, salida = _correr(flujo, ruta, ARGV_PELICULA)

    assert codigo == 1
    assert flujo.normalizar(salida) == _texto_dorado("subida_emu_duplicado.txt")
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("duplicado", False)
    assert not _codigos(eventos, "subido")
    comparar_con_golden("subida_emu_duplicado.jsonl", flujo.eventos_normalizados())


# Caminos que los goldens no recorren


def test_el_tracker_rechaza_la_subida(flujo):
    flujo.tracker_que_acepta()
    flujo.tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": False, "message": "No"})

    codigo, _ = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("rechazado", False)
    ultima = _codigos(eventos, "fase")[-1]
    assert (ultima["clave"], ultima["estado"]) == ("subida", "fallida")
    assert not _codigos(eventos, "subido") and not Qbit.anadidos


def test_la_busqueda_de_duplicados_cae(flujo):
    flujo.tracker_que_acepta()
    flujo.tracker.routes[("GET", SEARCH)] = lambda q: (500, b"error")

    codigo, _ = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("busqueda_caida", False)
    assert flujo.tracker.calls("POST", UPLOAD) == []


def test_login_de_qbittorrent_fallido_no_deshace_la_subida(flujo, monkeypatch):
    flujo.tracker_que_acepta()

    def login():
        raise qbittorrentapi.LoginFailed()

    monkeypatch.setattr(Qbit, "auth_log_in", lambda self: login())

    codigo, salida = _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    assert codigo == 0 and "INCORRECT QBIT LOGIN CREDENTIALS" in salida
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("subido", True)
    cliente = _codigos(eventos, "cliente")
    assert len(cliente) == 1 and (cliente[0]["ok"], cliente[0]["motivo"], cliente[0]["cliente"]) == (False, "login", "qbit")


def test_una_excepcion_sin_controlar_es_el_resultado(flujo, monkeypatch):
    flujo.tracker_que_acepta()

    async def falla(*_a, **_k):
        raise RuntimeError("clave-secreta")

    monkeypatch.setattr(upload, "handle_torrent_creation", falla)

    with pytest.raises(RuntimeError):
        _correr(flujo, flujo.crear_video(PELICULA), ARGV_PELICULA)

    eventos = _revisar(flujo)
    assert eventos[-1]["codigo"] == "excepcion" and eventos[-1]["detalle"] == "RuntimeError"
    assert "clave-secreta" not in flujo.ruta_eventos.read_text(encoding="utf-8")


def test_sin_video_da_su_resultado(flujo):
    ruta = flujo.contenido / "vacia"
    ruta.mkdir()
    flujo.tracker_que_acepta()

    codigo, _ = _correr(flujo, ruta, ARGV_PELICULA)

    assert codigo == 1
    assert _resultado(_revisar(flujo)) == ("sin_video", False)


def test_una_ruta_de_eventos_que_no_se_puede_escribir_no_corta_la_subida(flujo, capsys):
    flujo.tracker_que_acepta()
    imposible = flujo.raiz / "no-existe" / "eventos.jsonl"

    codigo, salida = flujo.correr([str(flujo.crear_video(PELICULA)), *ARGV_PELICULA, "--eventos", str(imposible)])

    assert codigo == 0 and "Torrent URL:" in salida
    assert salida.count("No se pueden escribir los eventos") == 1
    assert not imposible.exists()


def test_sin_la_opcion_no_se_escribe_nada(flujo):
    flujo.tracker_que_acepta()

    codigo, salida = flujo.correr([str(flujo.crear_video(PELICULA)), *ARGV_PELICULA])

    assert codigo == 0 and not flujo.ruta_eventos.exists()
    assert flujo.normalizar(salida) == _texto_dorado("subida_emu_pelicula.txt")


def test_argumentos_invalidos_ya_tienen_resultado(flujo):
    codigo, _ = flujo.correr(["--eventos", str(flujo.ruta_eventos)])  # sin ninguna ruta que subir

    assert codigo == 1
    assert _resultado(_revisar(flujo)) == ("sin_rutas", False)


# --solo-cliente de punta a punta


def _solo_cliente(flujo, monkeypatch, enlace_ok=True):
    ruta = flujo.crear_video(PELICULA)
    flujo.tracker_que_acepta()
    torrent = torf.Torrent(path=ruta, private=True)
    torrent.generate()
    torrent.source = "eMuwarez"
    firmado = torrent.dump(validate=False)
    descarga = f"{flujo.tracker.url}/torrent/download/42.rsskey"
    atributos = {"name": NOMBRE_PELICULA, "download_link": descarga}
    flujo.tracker.routes[("GET", "/api/torrents/42")] = lambda q: (200, {"data": {"attributes": atributos}}) if enlace_ok else (404, b"")
    flujo.tracker.routes[("GET", "/torrent/download/42.rsskey")] = lambda q: (200, firmado)

    monkeypatch.setattr("src.clients.ESPERA_INICIO_COMPROBACION", 0)  # el qBittorrent falso nunca «comprueba»
    return _correr(flujo, ruta, [*ARGV_PELICULA, "-sc", "42"])


def test_solo_cliente_bueno(flujo, monkeypatch):
    codigo, _ = _solo_cliente(flujo, monkeypatch)

    assert codigo == 0
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_ok", True)
    assert eventos[0]["modo"] == "solo_cliente"
    assert [e["ev"] for e in eventos if e["ev"] in ("subido", "paso_hecho")] == []
    cliente = _codigos(eventos, "cliente")
    assert len(cliente) == 1 and cliente[0]["ok"] is True and cliente[0]["cliente"] == "qbit"


def test_solo_cliente_sin_torrent_que_bajar(flujo, monkeypatch):
    codigo, _ = _solo_cliente(flujo, monkeypatch, enlace_ok=False)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_fallido", False)
    assert _codigos(eventos, "cliente")[0]["motivo"] == "sin_torrent"
    assert not Qbit.anadidos


def test_solo_cliente_que_qbittorrent_rechaza(flujo, monkeypatch):
    monkeypatch.setattr(Qbit, "torrents_add", lambda self, **kw: "Fails.")

    codigo, _ = _solo_cliente(flujo, monkeypatch)

    assert codigo == 1
    eventos = _revisar(flujo)
    assert _resultado(eventos) == ("cliente_fallido", False)
    assert _codigos(eventos, "cliente")[0]["motivo"] == "no_aceptado"


def test_el_aviso_de_pack_incompleto(monkeypatch, tmp_path):
    import asyncio

    from src.utils import eventos
    from src.utils.torrentman import TorrentMaker

    salida = "incomplete season pack\nSeason number: 3\nMissing: 4, 7"

    class Proceso:
        returncode = 0
        stdout = SimpleNamespace(read=None)

        async def wait(self):
            return 0

    datos = [salida.encode(), b""]

    async def leer(_n):
        return datos.pop(0)

    Proceso.stdout = SimpleNamespace(read=leer)

    async def crear(*_a, **_k):
        return Proceso()

    monkeypatch.setattr("src.utils.torrentman.asyncio.create_subprocess_exec", crear)
    monkeypatch.setattr("src.utils.torrentman.ProgressManager.get_instance", staticmethod(lambda: SimpleNamespace(update=lambda *_a, **_k: None)))
    fichero = tmp_path / "e.jsonl"
    eventos.configurar(str(fichero))
    try:
        asyncio.run(TorrentMaker({})._run_mkbrr(["mkbrr"]))
    finally:
        eventos.configurar(None)

    (linea,) = [json.loads(x) for x in fichero.read_text(encoding="utf-8").splitlines() if '"aviso"' in x]
    validar(linea)
    assert linea["datos"] == {"temporada": 3, "faltan": [4, 7]}


def test_una_ruta_relativa_no_se_parte_al_cambiar_de_directorio(tmp_path, monkeypatch):
    (tmp_path / "otra").mkdir()
    monkeypatch.chdir(tmp_path)
    eventos.configurar("e.jsonl")
    try:
        eventos.inicio("x", "subida", False)
        os.chdir(tmp_path / "otra")  # como hace mediainfo a mitad de la ejecución
        eventos.resultado(False, "excepcion")
    finally:
        eventos.configurar(None)
    assert len((tmp_path / "e.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert not (tmp_path / "otra" / "e.jsonl").exists()
