"""Lo que tapa el log (src/utils/redact.py): la URL del proxy de qui lleva su clave."""

import io
import logging
from urllib.parse import urlparse

import pytest
from rich.console import Console

from src.utils.redact import RedactingRichHandler, Redactrr

QUI = "http://qui:7476/proxy/clave0de0qui/"


def _redactor(url=QUI):
    redactor = Redactrr()
    redactor.load_from_config({"TORRENT_CLIENTS": {"qbit": {"qui_proxy_url": url}}})
    return redactor


@pytest.mark.parametrize("url", [QUI, QUI.rstrip("/"), f" {QUI} "])
def test_la_clave_de_qui_se_tapa_en_cualquier_forma(url):
    redactor = _redactor(url)
    for texto in (QUI, "POST http://qui:7476/proxy/clave0de0qui/api/v2/auth/login", repr(urlparse(QUI.rstrip("/")))):
        assert "clave0de0qui" not in redactor.redact(texto)


ERROR_DE_CONEXION = (
    "Failed to connect to qBittorrent. Connection Error: ConnectionError(MaxRetryError(\"HTTPConnectionPool("
    "host='qui', port=7476): Max retries exceeded with url: /proxy/clave0de0qui/api/v2/auth/login\"))"
)


def test_la_linea_del_cliente_fallido_tapa_la_clave(monkeypatch, capsys):
    import asyncio

    from src.utils import upload

    class Tracker:
        async def upload(self, meta):
            return True

    class Cliente:
        async def add_to_client(self, meta, tracker):
            raise ConnectionError(ERROR_DE_CONEXION)

    monkeypatch.setattr(upload, "redactor", _redactor())
    subido, _ = asyncio.run(upload.upload_to_tracker(Tracker(), "EMU", {}, {}, Cliente(), 0, [], "p", False))
    salida = capsys.readouterr().out
    assert subido and "Torrent client add failed:" in salida and "clave0de0qui" not in salida


def test_la_traza_tapa_el_mensaje_de_la_excepcion(monkeypatch):
    from rich.traceback import Traceback

    from src.utils import console as consola

    monkeypatch.setattr(consola, "redactor", _redactor())
    try:
        raise ConnectionError(ERROR_DE_CONEXION)
    except ConnectionError:
        traza = Traceback()
    salida = io.StringIO()
    Console(file=salida, width=300).print(traza)
    assert "Max retries exceeded" in salida.getvalue() and "clave0de0qui" not in salida.getvalue()


def test_el_handler_tapa_argumentos_que_no_son_str():
    salida = io.StringIO()
    handler = RedactingRichHandler(redactor=_redactor(), console=Console(file=salida, width=300))
    handler.emit(logging.LogRecord("qbittorrentapi", logging.DEBUG, __file__, 1, "Host: %s", (urlparse(QUI),), None))
    assert "Host" in salida.getvalue() and "clave0de0qui" not in salida.getvalue()
