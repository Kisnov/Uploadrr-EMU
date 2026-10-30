"""`-t` llega al type_id de EMU (src/videoprocessor.py::_process_disc_type)."""

import asyncio
from pathlib import Path

import pytest

from src import videoprocessor as vp
from src.mediainfo import MediaInfoHandler
from src.trackers.EMU import EMU


@pytest.mark.parametrize(
    ("tipo", "type_id"),
    [("disc", "1"), ("remux", "2"), ("encode", "3"), ("webdl", "4"), ("web-dl", "4"), ("webrip", "5"), ("hdtv", "6")],
)
def test_el_tipo_manual_llega_en_mayusculas(tipo, type_id, tmp_path, monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)

    async def no_es_scene(video, imdb):
        return video, False, imdb

    monkeypatch.setattr(vp, "is_scene", no_es_scene)
    procesador = vp.VideoProcessor({"DEFAULT": {}, "TRACKERS": {}}, MediaInfoHandler({}, str(tmp_path)), tmp_path)
    video = Path("/d/Peli 2020 1080p.mkv")
    monkeypatch.setattr(procesador, "get_video", lambda *_args: (video, [video]))
    meta = {"manual_type": tipo, "manual_year": 0, "is_disc": None, "path": str(video), "uuid": "u", "xxx": False,
            "category": "MOVIE", "edit": True}

    meta = asyncio.run(procesador._process_disc_type(meta, video))

    assert meta["type"] == tipo.upper().replace("-", "")
    assert asyncio.run(EMU(config={"TRACKERS": {"EMU": {"api_key": "x"}}, "DEFAULT": {}}).get_type_id(meta)) == type_id


@pytest.fixture
def procesador(tmp_path, monkeypatch):
    monkeypatch.setattr(MediaInfoHandler, "_instance", None)
    return vp.VideoProcessor({"DEFAULT": {}, "TRACKERS": {}}, MediaInfoHandler({}, str(tmp_path)), tmp_path)


@pytest.mark.parametrize(("manual", "tipo"), [(None, "WEBRIP"), ("encode", "ENCODE")])
def test_un_encode_de_origen_web_solo_pasa_a_webrip_sin_tipo_manual(procesador, manual, tipo):
    video = "/d/Peli 2020 1080p WEB x264-GRP.mkv"
    assert procesador.get_source("ENCODE", video, video, None, {"manual_type": manual})[1] == tipo


@pytest.mark.parametrize(("manual", "tipo"), [(None, "WEBDL"), ("encode", "ENCODE")])
def test_el_tipo_de_tags_json_no_pisa_el_tipo_manual(procesador, tmp_path, manual, tipo):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tags.json").write_text('{"SubsPlease": {"type": "WEBDL"}}')
    meta = {"path": "/d/x.mkv", "tag": "-SubsPlease", "type": "ENCODE", "manual_type": manual}
    assert asyncio.run(procesador.tag_override(meta))["type"] == tipo
