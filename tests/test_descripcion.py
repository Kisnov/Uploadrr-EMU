"""Descripción de la subida (src/utils/descgen.py): gen_desc arma DESCRIPTION.txt en la
preparación y edit_desc la convierte en [EMU]DESCRIPTION.txt al subir."""
from __future__ import annotations

import asyncio

import pytest

from src.utils.descgen import DescControl, DescGenerator

CONFIG = {
    "AUTO": {"always_aid": False},
    "DEFAULT": {"add_trailer": True, "add_logo": True, "logo_size": 300, "img_size": 500},
    "SCREENS": {"per_episode": 3},
    "SIGNATURES": {"use_global_sigs": "default", "signature": "[FIRMA]"},
    "TRACKERS": {"EMU": {}},
}
CAPTURA = {"web_url": "https://pixhost.to/show/1", "raw_url": "https://img.pixhost.to/1.png", "img_url": "https://t.pixhost.to/1.png"}


def _gen_desc(tmp_path, config=None, **meta):
    meta = {"uuid": "Peli", "desc": None, "nfo": False, **meta}
    meta = asyncio.run(DescGenerator({**CONFIG, **(config or {})}, tmp_path).gen_desc(meta))
    return meta, (tmp_path / "tmp" / "Peli" / "DESCRIPTION.txt").read_text(encoding="utf-8")


def _edit_desc(tmp_path, base, tracker="EMU", **meta):
    carpeta = tmp_path / "tmp" / "Peli"
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "DESCRIPTION.txt").write_text(base, encoding="utf-8")
    meta = {"base_dir": str(tmp_path), "uuid": "Peli", "category": "MOVIE", "youtube": "abc", "logo": "https://logo.png",
            "screens": 1, "image_list": [CAPTURA], **meta}
    asyncio.run(DescGenerator(CONFIG, tmp_path).edit_desc(meta, tracker))
    return (carpeta / f"[{tracker}]DESCRIPTION.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("marcador", "esperado"),
    [
        ("Sin marcador", DescControl()),
        ("# USE: full", DescControl()),
        ("# USE: as-is", DescControl(as_is=True, include_screens=False, include_logo=False, include_trailer=False,
                                     include_signature=False, include_disc_info=False)),
        ("# USE: signature-only", DescControl(include_screens=False, include_logo=False, include_trailer=False,
                                              include_disc_info=False)),
        ("# USE: screens+sig", DescControl(include_logo=False, include_trailer=False, include_disc_info=False)),
        ("# USE: full+no-imgsize", DescControl(modify_img_size=False)),
    ],
)
def test_el_marcador_decide_que_lleva_la_descripcion(marcador, esperado):
    assert DescControl.from_markers(marcador) == esperado


def test_sin_descripcion_propia_queda_vacia(tmp_path):
    meta, texto = _gen_desc(tmp_path)
    assert texto == ""
    assert "description" not in meta


def test_auto_desc_usa_el_txt_junto_al_video(tmp_path):
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    video.with_suffix(".txt").write_text("Notas del encode", encoding="utf-8")

    meta, texto = _gen_desc(tmp_path, path=str(video), auto_desc=True)

    assert texto == "\n# USE: full\nNotas del encode\n"
    assert meta["description"] == "CUSTOM"


def test_auto_desc_respeta_el_marcador_del_txt(tmp_path):
    carpeta = tmp_path / "Serie"
    carpeta.mkdir()
    (carpeta / "description.txt").write_text("# USE: as-is\nTal cual", encoding="utf-8")

    _, texto = _gen_desc(tmp_path, path=str(carpeta), auto_desc=True)

    assert texto == "\n# USE: as-is\nTal cual\n"


def test_la_carpeta_de_descripciones_gana_al_txt_del_video(tmp_path):
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    video.with_suffix(".txt").write_text("Junto al vídeo", encoding="utf-8")
    descripciones = tmp_path / "descripciones"
    descripciones.mkdir()
    (descripciones / "Peli.txt").write_text("De la carpeta", encoding="utf-8")

    _, texto = _gen_desc(tmp_path, {"AUTO": {"always_aid": True, "description_folder": str(descripciones)}}, path=str(video))

    assert "De la carpeta" in texto and "Junto al vídeo" not in texto


def test_el_nfo_va_en_un_bloque_de_codigo(tmp_path):
    nfo = tmp_path / "Peli.nfo"
    nfo.write_text("ASCII ART", encoding="utf-8")

    meta, texto = _gen_desc(tmp_path, nfo=True, nfo_file=str(nfo))

    assert texto == "\n[code]ASCII ART[/code]\n"
    assert meta["description"] == "CUSTOM"


def test_la_descripcion_de_emu_lleva_trailer_logo_texto_capturas_y_firma(tmp_path):
    texto = _edit_desc(tmp_path, "Notas [pre]log[/pre] [hide]oculto[/hide]")

    assert texto == (
        "[center][youtube]abc[/youtube][/center]"
        "[center][img=300]https://logo.png[/img][/center]"
        "Notas [code]log[/code] [spoiler]oculto[/spoiler]"
        f"\n[center][url={CAPTURA['web_url']}][img=500]{CAPTURA['raw_url']}[/img][/url] [/center]"
        "\n[FIRMA]"
    )


def test_tal_cual_solo_lleva_el_texto(tmp_path):
    assert _edit_desc(tmp_path, "# USE: as-is\n[pre]sin tocar[/pre]") == "[pre]sin tocar[/pre]"


def test_solo_firma_quita_trailer_logo_y_capturas(tmp_path):
    assert _edit_desc(tmp_path, "# USE: signature-only\nNotas") == "Notas\n[FIRMA]"


@pytest.mark.parametrize(("categoria", "con_trailer"), [("MOVIE", True), ("TV", True), ("MUSIC", False)])
def test_el_trailer_solo_va_en_pelis_y_series(tmp_path, categoria, con_trailer):
    assert ("[youtube]" in _edit_desc(tmp_path, "", category=categoria)) is con_trailer


def test_sin_capturas_no_hay_bloque_de_imagenes(tmp_path):
    assert "[url=" not in _edit_desc(tmp_path, "", screens=0)


def test_la_info_de_los_discos_va_en_spoilers(tmp_path):
    discos = [{"type": "BDMV", "summary": "principal"}, {"type": "BDMV", "name": "Disco 2", "summary": "BDINFO 2"}]

    texto = _edit_desc(tmp_path, "", discs=discos)

    assert "[spoiler=Disco 2][code]BDINFO 2[/code][/spoiler]" in texto
    assert "principal" not in texto


def test_el_vob_de_un_dvd_va_en_un_spoiler(tmp_path):
    assert "[spoiler=VOB MediaInfo][code]MI[/code][/spoiler]" in _edit_desc(tmp_path, "", discs=[{"type": "DVD", "vob_mi": "MI"}])
