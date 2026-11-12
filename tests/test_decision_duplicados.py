"""Lo que decide si se sube: ``pre_upload_checks`` -> ``dupe_check`` -> ``upload_to_trackers`` reales, contra el tracker falso.

Sin sustituir ninguna de esas funciones (``tests/test_resume.py`` sí las sustituye): un duplicado o una búsqueda que no
puede hacerse impide subir, y una subida que entró sin respuesta se reconoce con el nombre que llega por argv."""

import asyncio
import time

import httpx
import pytest

from src.args import build_parser, normalize_args
from src.clients import Clients
from src.progress import ProgressManager
from src.trackers.EMU import EMU
from src.utils.checks import pre_upload_checks
from src.utils.console import console
from src.utils.upload import upload_to_trackers

# Los fixtures del tracker falso y de una subida preparada: son los de test_unit3d.
from test_unit3d import (  # noqa: F401
    NAME,
    SEARCH,
    SIZE,
    TOKEN,
    UPLOAD,
    download_url,
    emu,
    item,
    meta,
    resigned,
    serve_download,
    signed_torrent,
    sin_red_ni_procesos,
    tracker,
    upload_meta,
)


class EMUConTimeoutCorto(EMU):
    def __init__(self, config):
        super().__init__(config)
        self.timeout = httpx.Timeout(0.5)


@pytest.fixture
def entorno(tracker, emu, upload_meta, monkeypatch):  # noqa: F811
    """El registro de trackers, la configuración y el gestor de progreso como los deja upload.py."""
    monkeypatch.setattr(EMU, "domain", tracker.url)
    monkeypatch.setattr(ProgressManager, "_instance", None)
    progress = ProgressManager(console, quiet=True, unattended=True)
    config = {"TRACKERS": {"EMU": {"api_key": TOKEN}}, "DEFAULT": {}}
    upload_meta.update(unattended=True, content_size=SIZE, clean_name="Peli", tag="-GRP")
    registro = {"EMU": {"class": EMUConTimeoutCorto, "type": "unit3d"}}
    return upload_meta, registro, config, progress


def _comprobar(entorno):
    meta, registro, config, _ = entorno
    skipped = []
    ok = asyncio.run(pre_upload_checks(meta, ["EMU"], registro, skipped, meta["base_dir"], config))
    return ok, skipped


def _subir(entorno):
    meta, registro, config, progress = entorno
    return asyncio.run(upload_to_trackers(meta, ["EMU"], registro, 0, 0, [], meta["base_dir"], progress, Clients(config), config))


def test_un_duplicado_encontrado_no_se_sube(tracker, entorno, capsys):
    tracker.routes[("GET", SEARCH)] = lambda q: (200, {"data": [item(NAME, SIZE)], "links": {"next": None}})

    ok, skipped = _comprobar(entorno)

    assert ok == {"EMU": False}
    assert skipped[-1][1] == "Potential duplicate on EMU"
    assert "Exact size match" in capsys.readouterr().out
    assert tracker.calls("POST", UPLOAD) == []


def test_una_busqueda_que_falla_no_se_toma_por_sin_duplicados(tracker, entorno, capsys):
    tracker.routes[("GET", SEARCH)] = lambda q: (500, b"error")

    ok, skipped = _comprobar(entorno)

    assert ok == {"EMU": False}
    assert skipped[-1][1] == "Dupe search failed on EMU"
    out = capsys.readouterr().out
    assert "No dupes found" not in out and "HTTP 500" in out
    assert tracker.calls("POST", UPLOAD) == []


def test_sin_duplicados_se_comprueba_y_se_sube(tracker, entorno, signed_torrent, capsys):  # noqa: F811
    tracker.routes[("POST", UPLOAD)] = lambda q: (200, {"success": True, "data": download_url(tracker)})
    serve_download(tracker, signed_torrent)

    ok, skipped = _comprobar(entorno)
    assert ok == {"EMU": True} and skipped == []
    exito, _ = _subir(entorno)

    assert exito is True
    assert "No dupes found" in capsys.readouterr().out
    assert len(tracker.calls("POST", UPLOAD)) == 1


def test_una_subida_sin_respuesta_se_recupera_con_el_nombre_de_argv(tracker, entorno, signed_torrent):  # noqa: F811
    # -mt llega como lista de palabras y normalize_args la junta: sin juntarla, el nombre buscado no coincidía.
    argv = ["-mt", *NAME.split(" ")]
    meta = entorno[0]
    meta["manual_name"] = normalize_args(build_parser({}).parse_args(argv))["manual_name"]
    assert meta["manual_name"] == NAME
    landed = []

    def subir(query):
        if not landed:
            landed.append(NAME)
            time.sleep(1)  # entra, pero contesta tarde
            return 200, {"success": True, "data": download_url(tracker)}
        return 404, {"success": False, "message": "Validation Error.", "data": {"name": ["The name has already been taken."]}}

    tracker.routes[("POST", UPLOAD)] = subir
    tracker.routes[("GET", SEARCH)] = lambda q: (
        200,
        {"data": [item(n, SIZE, download_link=download_url(tracker)) for n in landed if n == q["name"][0]], "links": {"next": None}},
    )
    serve_download(tracker, resigned(signed_torrent))

    exito, _ = _subir(entorno)

    assert exito is True
