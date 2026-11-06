"""Línea `Uploading screenshots: X/Y` (src/progress.py): la lee emuwarez-uploadrr tal cual.

Sin terminal (la app lanza upload.py con la salida a una tubería) el Live de Rich no escribe nada, así que
`ProgressManager.update` imprime el texto a mano. Cada línea tiene que salir completa, sola y sin códigos ANSI."""

from __future__ import annotations

import asyncio
import re

import pytest

from src.progress import ProgressManager
from src.screenshots import up2host
from src.screenshots.up2host import ScreenshotUploader
from src.utils.console import console

LINEA = re.compile(r"Uploading screenshots: \d+/\d+")


@pytest.fixture
def pm(monkeypatch):
    """Un ProgressManager nuevo con el `console` real (el que pasa por el redactor), como en upload.py."""
    monkeypatch.setattr(ProgressManager, "_instance", None)
    gestor = ProgressManager(console)
    gestor.start(1)
    yield gestor
    gestor.stop()


def _lineas(capsys, prefijo="Uploading screenshots") -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith(prefijo)]


def test_el_formato_es_exactamente_uploading_screenshots_x_barra_y(pm, capsys):
    assert pm.console.is_terminal is False  # el caso de la app: salida a una tubería

    pm.screen_start(3)
    pm.screen_up()
    pm.screen_up(2)
    pm.screen_done()

    salida = capsys.readouterr().out
    assert [ln for ln in salida.splitlines() if ln.startswith("Uploading")] == [
        "Uploading screenshots: 0/3",
        "Uploading screenshots: 1/3",
        "Uploading screenshots: 3/3",
    ]
    assert "All screenshots uploaded" in salida.splitlines()
    assert "\x1b" not in salida  # sin colores ni códigos de control que rompan la lectura literal


def test_cada_cuenta_sale_una_sola_vez(pm, capsys):
    pm.screen_start(2)
    pm.screen_up(0)  # el texto no cambia: el Live a 10 Hz no tiene que llenar el log de repeticiones
    pm.screen_up(1)
    pm.screen_up(0)

    assert _lineas(capsys) == ["Uploading screenshots: 0/2", "Uploading screenshots: 1/2"]


def test_la_linea_no_se_parte_ni_con_cuentas_grandes(pm, capsys):
    """Rich parte a 80 columnas en una tubería: la línea más larga posible sigue entera y en una sola."""
    pm.screen_start(10**9)
    pm.screen_up(10**9 - 1)

    esperadas = ["Uploading screenshots: 0/1000000000", "Uploading screenshots: 999999999/1000000000"]
    salida = capsys.readouterr().out.splitlines()
    assert [ln for ln in salida if ln.startswith("Uploading")] == esperadas
    assert [ln for ln in salida if "/1000000000" in ln] == esperadas  # ni un trozo suelto en otra línea
    assert all(len(ln) < console.width for ln in esperadas)


def test_la_linea_no_pasa_por_markup_de_rich(pm, capsys):
    """El texto no lleva corchetes: Rich no puede interpretarlo como estilo ni comérselo."""
    pm.screen_start(7)
    assert _lineas(capsys) == ["Uploading screenshots: 0/7"]
    assert LINEA.fullmatch(pm.progress_widget.text.plain)


def test_en_un_terminal_el_live_pinta_y_no_se_duplica_la_linea(pm, capsys, monkeypatch):
    """Con terminal real no se imprime a mano (lo hace el Live): sin esta rama habría dos líneas por cada captura."""
    monkeypatch.setattr(type(pm.console), "is_terminal", property(lambda self: True))

    pm.screen_start(2)
    pm.screen_up()

    assert _lineas(capsys) == []


# ---------------------------------------------------------------------------
# De punta a punta: ScreenshotUploader.upload_screenshots
# ---------------------------------------------------------------------------
class Host:
    def __init__(self, nombre):
        self.nombre = nombre

    async def upload(self, client, path, title=None, adult=False):
        return {"img_url": f"{self.nombre}/{path.rsplit('/', 1)[-1]}", "raw_url": "", "web_url": ""}


def test_upload_screenshots_emite_el_progreso_hasta_el_total(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ProgressManager, "_instance", None)
    gestor = ProgressManager(console)
    gestor.start(1)
    carpeta = tmp_path / "tmp" / "u"
    carpeta.mkdir(parents=True)
    imagenes = []
    for n in range(3):
        (carpeta / f"peli-{n}.png").write_bytes(b"png")
        imagenes.append({"local_file": f"peli-{n}.png", "ss_time": n})
    uploader = ScreenshotUploader({"SCREENS": {"API_KEYS": {"ptscreens": "k"}}}, tmp_path)
    monkeypatch.setattr(uploader, "_load_host", lambda nombre: Host(nombre))
    monkeypatch.setattr(up2host, "ProgressManager", ProgressManager)
    meta = {"uuid": "u", "xxx": False, "name": "Peli", "screens": 3}

    try:
        resultado = asyncio.run(uploader.upload_screenshots(meta, {"main": imagenes}, ["ptscreens"], {}))
    finally:
        gestor.stop()

    assert len(resultado["main"]) == 3
    assert _lineas(capsys) == [
        "Uploading screenshots: 0/3",
        "Uploading screenshots: 1/3",
        "Uploading screenshots: 2/3",
        "Uploading screenshots: 3/3",
    ]
