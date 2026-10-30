"""Lo que tapa el log (src/utils/redact.py): la URL del proxy de qui lleva su clave."""

import io
import logging
from urllib.parse import urlparse

from rich.console import Console

from src.utils.redact import RedactingRichHandler, Redactrr

QUI = "http://qui:7476/proxy/clave0de0qui/"


def _redactor():
    redactor = Redactrr()
    redactor.load_from_config({"TORRENT_CLIENTS": {"qbit": {"qui_proxy_url": QUI}}})
    return redactor


def test_la_clave_de_qui_se_tapa_en_cualquier_forma():
    redactor = _redactor()
    for texto in (QUI, "POST http://qui:7476/proxy/clave0de0qui/api/v2/auth/login", repr(urlparse(QUI))):
        assert "clave0de0qui" not in redactor.redact(texto)


def test_el_handler_tapa_argumentos_que_no_son_str():
    salida = io.StringIO()
    handler = RedactingRichHandler(redactor=_redactor(), console=Console(file=salida, width=300))
    handler.emit(logging.LogRecord("qbittorrentapi", logging.DEBUG, __file__, 1, "Host: %s", (urlparse(QUI),), None))
    assert "Host" in salida.getvalue() and "clave0de0qui" not in salida.getvalue()
