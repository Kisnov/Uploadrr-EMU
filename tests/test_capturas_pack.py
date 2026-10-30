"""Capturas de un pack de temporada (src/screenshots/generator.py y manager.py)."""

import asyncio
from pathlib import Path

import pytest

from src.mediainfo import MediaInfoHandler
from src.screenshots import manager as man
from src.screenshots.generator import ScreenshotGenerator
from src.screenshots.manager import ScreenshotManager
from src.utils.descgen import DescGenerator


def _pack(*capitulos):
    return [f"/pack/Serie.{c}.1080p.mkv" for c in capitulos] + ["/pack/Serie.nfo"]


@pytest.fixture(autouse=True)
def sin_mediainfo(monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)


@pytest.mark.parametrize(
    ("filelist", "primero"),
    [
        (_pack("S01E02", "S01E10", "S01E01"), "S01E01"),
        (_pack("S01E13", "S01E14"), "S01E13"),
        (_pack("S00E01", "S01E05", "S01E03"), "S01E03"),
        (_pack("S02E01", "S01E09", "S01E10"), "S01E09"),
        (_pack("S00E02", "S00E01"), "S00E01"),
        (_pack("S01E100", "S01E99"), "S01E99"),
    ],
)
def test_primer_capitulo_del_pack(tmp_path, filelist, primero):
    generador = ScreenshotGenerator({"SCREENS": {}}, tmp_path)
    assert generador._first_episode(filelist) == f"/pack/Serie.{primero}.1080p.mkv"


def test_pack_sin_videos_no_tiene_primer_capitulo(tmp_path):
    assert ScreenshotGenerator({"SCREENS": {}}, tmp_path)._first_episode(["/pack/Serie.nfo"]) is None


def _generar(tmp_path, monkeypatch, per_episode, filelist):
    generador = ScreenshotGenerator({"SCREENS": {"per_episode": per_episode}}, tmp_path)
    generador.tmp_dir = tmp_path
    capturados = []

    async def generar_falso(meta, filename, uuid, num_screens, file_path):
        capturados.append(file_path)
        return [(f"{filename}-{n}.png", n) for n in range(num_screens)]

    monkeypatch.setattr(generador, "_generate_file", generar_falso)
    resultado = asyncio.run(generador._generate_tv_pack_screenshots({"filelist": filelist}, "Serie", "u", 6, 0))
    return resultado, capturados


def test_con_per_episode_0_solo_se_captura_el_primer_capitulo(tmp_path, monkeypatch):
    filelist = _pack(*(f"S01E{n:02d}" for n in range(50, 0, -1)))

    resultado, capturados = _generar(tmp_path, monkeypatch, 0, filelist)

    assert capturados == ["/pack/Serie.S01E01.1080p.mkv"]
    assert [i["local_file"] for i in resultado["main"]] == [f"Serie-S01E01-{n}.png" for n in range(6)]
    assert resultado["episodes"] == {}


def test_con_per_episode_0_las_principales_salen_del_primero_aunque_no_sea_e01(tmp_path, monkeypatch):
    resultado, capturados = _generar(tmp_path, monkeypatch, 0, _pack("S00E01", "S01E14", "S01E13"))

    assert capturados == ["/pack/Serie.S01E13.1080p.mkv"]
    assert len(resultado["main"]) == 6


def test_con_per_episode_se_captura_cada_capitulo(tmp_path, monkeypatch):
    resultado, capturados = _generar(tmp_path, monkeypatch, 3, _pack("S01E13", "S01E14", "S01E15"))

    assert len(capturados) == 3
    assert [i["local_file"] for i in resultado["main"]] == [f"Serie-S01E13-{n}.png" for n in range(6)]
    assert {ep: len(imgs) for ep, imgs in resultado["episodes"].items()} == {"S01E13": 6, "S01E14": 3, "S01E15": 3}


def test_con_per_episode_0_solo_se_suben_las_principales(tmp_path, monkeypatch):
    config = {"DEFAULT": {"screens": 6}, "SCREENS": {"per_episode": 0, "img_hosts": ["ptscreens"], "API_KEYS": {}}}
    gestor = ScreenshotManager(config, tmp_path)
    principales = [{"local_file": f"Serie-E01-{n}.png", "ss_time": n} for n in range(6)]
    subidas = []

    async def generar(meta, filename, uuid):
        return {"main": principales, "episodes": {}}

    async def subir(meta, screens, viable_hosts, tracker_whitelists):
        subidas.append(screens)
        return {"main": [dict(i, img_url="u", raw_url="u", web_url="u") for i in screens["main"]], "episodes": {}}

    monkeypatch.setattr(gestor.generator, "generate_screenshots", generar)
    monkeypatch.setattr(gestor.uploader, "upload_screenshots", subir)
    monkeypatch.setattr(man.RehostManager, "_get_viable_hosts", staticmethod(lambda *a: ["ptscreens"]))
    meta = {"uuid": "u", "filename": "Serie", "screens": 6, "tv_pack": 1, "filelist": _pack(*(f"S01E{n:02d}" for n in range(1, 51)))}

    asyncio.run(gestor._handle_video_content(meta, {}))

    assert len(subidas) == 1
    assert subidas[0]["main"] == principales and subidas[0]["episodes"] == {}


def test_las_capturas_que_quedan_de_otro_capitulo_no_sirven_de_principales(tmp_path, monkeypatch):
    config = {"DEFAULT": {"screens": 6}, "SCREENS": {"per_episode": 0}}
    gestor = ScreenshotManager(config, tmp_path)
    (tmp_path / "tmp" / "u").mkdir(parents=True)
    for n in range(6):
        (tmp_path / "tmp" / "u" / f"Serie-S00E01-{n}.png").write_bytes(b"x" * 2000)
    generadas = []

    async def generar(meta, filename, uuid):
        generadas.append(filename)
        return {"main": [], "episodes": {}}

    monkeypatch.setattr(gestor.generator, "generate_screenshots", generar)
    meta = {"uuid": "u", "filename": "Serie", "screens": 6, "tv_pack": 1, "filelist": _pack("S00E01", "S01E01", "S01E02")}
    meta["skip_imghost_upload"] = True

    asyncio.run(gestor._handle_video_content(meta, {}))

    assert generadas == ["Serie"]


def test_cada_contenido_vuelve_a_probar_primero_el_host_caido(tmp_path, monkeypatch):
    gestor = ScreenshotManager({"DEFAULT": {}, "SCREENS": {}}, tmp_path)
    gestor.uploader.failed_hosts.add("imgbb")
    monkeypatch.setattr(gestor, "_handle_video_content", lambda meta, wl, tw: asyncio.sleep(0, meta))
    monkeypatch.setattr(gestor, "_cleanup", lambda meta: asyncio.sleep(0))
    monkeypatch.setattr(gestor, "_reset_terminal", lambda: None)

    asyncio.run(gestor.handle_screenshots({"uuid": "u"}, {}))

    assert gestor.uploader.failed_hosts == set()


def test_la_descripcion_no_repite_el_primer_capitulo_y_nombra_cada_uno_con_su_temporada(fichero):
    def capturas(ep):
        return [{"local_file": f"Serie-{ep}-{n}.png", "img_url": "i", "raw_url": f"r-{ep}", "web_url": "w"} for n in range(3)]

    config = {"DEFAULT": {"img_size": 500}, "SCREENS": {"per_episode": 3}, "TRACKERS": {}}
    meta = {
        "screens": 3,
        "tv_pack": 1,
        "image_list": {"main": capturas("S01E01"), "episodes": {e: capturas(e) for e in ("S01E01", "S00E01", "S01E02")}},
    }
    asyncio.run(DescGenerator(config, Path("."))._write_images(fichero, meta, "EMU"))

    assert "[spoiler=S01E01]" not in fichero.texto
    assert "[spoiler=S00E01]" in fichero.texto and "[spoiler=S01E02]" in fichero.texto


def test_el_capitulo_sale_del_nombre_del_fichero_y_no_de_la_carpeta(tmp_path):
    generador = ScreenshotGenerator({"SCREENS": {}}, tmp_path)
    assert generador._get_episode_identifier("/pack/S1E1-S1E5/Serie.S01E02.mkv") == "S01E02"


def test_las_capturas_nuevas_siguen_tras_las_que_ya_hay_del_capitulo(tmp_path, monkeypatch):
    generador = ScreenshotGenerator({"SCREENS": {}}, tmp_path)
    generador.tmp_dir = tmp_path
    video = tmp_path / "Serie.S01E13.mkv"
    video.write_bytes(b"")
    for n in range(3):
        (tmp_path / f"Serie-S01E13-{n}.png").write_bytes(b"x")
    salidas = []

    async def ffprobe(*_a, **_k):
        return {"streams": [{"r_frame_rate": "24/1"}], "format": {"duration": "1200"}}

    async def capturar(file, output, *_a, **_k):
        salidas.append(output)
        return output

    async def validar(meta, results, *_a):
        return results

    monkeypatch.setattr(generador, "_ffprobe", ffprobe)
    monkeypatch.setattr(generador, "_take_screenshot", capturar)
    monkeypatch.setattr(generador, "_validate_and_retry", validar)
    asyncio.run(generador._generate_file({}, "Serie-S01E13", "u", 2, str(video)))

    assert salidas == ["Serie-S01E13-3.png", "Serie-S01E13-4.png"]
