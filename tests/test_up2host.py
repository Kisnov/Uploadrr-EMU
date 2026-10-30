"""Subida de capturas a los hosts de imágenes (src/screenshots/up2host.py) con hosts simulados."""

import asyncio
from pathlib import Path

import pytest

from src.screenshots import up2host
from src.screenshots.up2host import AllHostsFailedError, ScreenshotUploader


class Host:
    """Host falso: falla las capturas de `fallos` (por nombre) y tarda más en las primeras."""

    def __init__(self, nombre, fallos=(), siempre_falla=False):
        self.nombre = nombre
        self.fallos = set(fallos)
        self.siempre_falla = siempre_falla
        self.subidas = []

    async def upload(self, client, path, title=None, adult=False):
        nombre = Path(path).name
        self.subidas.append(nombre)
        await asyncio.sleep(0.05 * (3 - int(nombre[-5])))  # la primera acaba la última
        if self.siempre_falla or nombre in self.fallos:
            raise ValueError("timeout")
        return {"img_url": f"{self.nombre}/{nombre}", "raw_url": f"{self.nombre}/{nombre}", "web_url": ""}


class Imgbox:
    def __init__(self, fallos=()):
        self.fallos = set(fallos)

    async def upload_batch(self, client, paths, title=None, adult=False):
        return [
            {"img_url": f"imgbox/{Path(p).name}", "raw_url": "", "web_url": "", "local_file": Path(p).name}
            for p in reversed(paths)
            if Path(p).name not in self.fallos
        ]


@pytest.fixture
def capturas(tmp_path):
    carpeta = tmp_path / "tmp" / "u"
    carpeta.mkdir(parents=True)
    imagenes = []
    for n in range(3):
        (carpeta / f"peli-{n}.png").write_bytes(b"png")
        imagenes.append({"local_file": f"peli-{n}.png", "ss_time": n * 10})
    return imagenes


def _subir(tmp_path, monkeypatch, hosts, imagenes):
    monkeypatch.setattr(up2host.asyncio, "sleep", _sin_espera(asyncio.sleep))
    config = {"SCREENS": {"API_KEYS": {nombre: "k" for nombre in hosts}}}
    uploader = ScreenshotUploader(config, tmp_path)
    monkeypatch.setattr(uploader, "_load_host", lambda nombre: hosts[nombre])
    meta = {"uuid": "u", "xxx": False}

    async def correr():
        async with uploader:
            return await uploader._upload_batch(meta, imagenes, list(hosts), "Peli", 3)

    return asyncio.run(correr())


def _sin_espera(real_sleep):
    async def dormir(segundos):
        await real_sleep(0 if segundos == 0.3 else segundos)

    return dormir


def test_cada_url_va_con_su_captura_y_en_orden(tmp_path, monkeypatch, capturas):
    resultado = _subir(tmp_path, monkeypatch, {"ptscreens": Host("ptscreens")}, capturas)

    assert [r["local_file"] for r in resultado] == ["peli-0.png", "peli-1.png", "peli-2.png"]
    assert [r["img_url"] for r in resultado] == ["ptscreens/peli-0.png", "ptscreens/peli-1.png", "ptscreens/peli-2.png"]
    assert [r["ss_time"] for r in resultado] == [0, 10, 20]


def test_la_que_falla_se_sube_en_el_siguiente_host_sin_repetir_las_demas(tmp_path, monkeypatch, capturas):
    primero, segundo = Host("ptscreens", fallos={"peli-1.png"}), Host("imgbb")

    resultado = _subir(tmp_path, monkeypatch, {"ptscreens": primero, "imgbb": segundo}, capturas)

    assert [r["img_url"] for r in resultado] == ["ptscreens/peli-0.png", "imgbb/peli-1.png", "ptscreens/peli-2.png"]
    assert primero.subidas.count("peli-1.png") == 3  # tres intentos en el primer host
    assert segundo.subidas == ["peli-1.png"]


def test_imgbox_se_empareja_por_nombre_de_fichero(tmp_path, monkeypatch, capturas):
    resultado = _subir(tmp_path, monkeypatch, {"imgbox": Imgbox()}, capturas)

    assert [(r["local_file"], r["img_url"]) for r in resultado] == [
        ("peli-0.png", "imgbox/peli-0.png"),
        ("peli-1.png", "imgbox/peli-1.png"),
        ("peli-2.png", "imgbox/peli-2.png"),
    ]


def test_si_ninguno_la_sube_se_devuelven_las_demas_en_orden(tmp_path, monkeypatch, capturas):
    resultado = _subir(tmp_path, monkeypatch, {"ptscreens": Host("ptscreens", fallos={"peli-0.png"})}, capturas)

    assert [r["local_file"] for r in resultado] == ["peli-1.png", "peli-2.png"]


def test_si_no_sube_ninguna_falla(tmp_path, monkeypatch, capturas):
    with pytest.raises(AllHostsFailedError):
        _subir(tmp_path, monkeypatch, {"ptscreens": Host("ptscreens", siempre_falla=True)}, capturas)


def test_un_host_que_no_sube_nada_pasa_al_final_en_los_lotes_siguientes(tmp_path, monkeypatch, capturas):
    monkeypatch.setattr(up2host.asyncio, "sleep", _sin_espera(asyncio.sleep))
    monkeypatch.setattr(up2host.ProgressManager, "_instance", None)  # otro test deja uno a medias
    caido, bueno = Host("imgbb", siempre_falla=True), Host("ptscreens")
    hosts = {"imgbb": caido, "ptscreens": bueno}
    uploader = ScreenshotUploader({"SCREENS": {"API_KEYS": {nombre: "k" for nombre in hosts}}}, tmp_path)
    monkeypatch.setattr(uploader, "_load_host", lambda nombre: hosts[nombre])
    meta = {"uuid": "u", "xxx": False}

    async def correr():
        async with uploader:
            return [await uploader._upload_batch(meta, capturas, list(hosts), f"E0{n}", 3) for n in (1, 2)]

    primero, segundo = asyncio.run(correr())

    assert [r["img_url"] for r in segundo] == ["ptscreens/peli-0.png", "ptscreens/peli-1.png", "ptscreens/peli-2.png"]
    assert len(caido.subidas) == 9  # tres intentos en el primer lote y ninguno en el segundo
    assert len(bueno.subidas) == 6
