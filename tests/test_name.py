"""Nombres de release (src/name.py). Para EMU el nombre llega hecho con -mt;
esto cubre la plantilla por defecto y las piezas puras."""
from __future__ import annotations

import asyncio

import pytest

from src.name import NameGenerator, TemplateContext

CLAVES = ["title", "aka", "year", "3D", "cut", "ratio", "edition", "repack", "resolution", "region", "uhd", "source",
          "hdr", "video_codec", "video_encode", "lang", "audio", "service", "season", "episode", "episode_title", "part",
          "dvd_size"]


def _meta(**datos):
    meta = {k: "" for k in CLAVES}
    meta.update(datos)
    return meta


def test_nombre_de_una_pelicula_webdl():
    meta = _meta(category="MOVIE", type="WEBDL", title="Dune", year="2021", resolution="2160p", service="MAX",
                 audio="DD+ 5.1", hdr="DV HDR", video_encode="H.265", tag="-GRP")
    notag, nombre, limpio, _ = asyncio.run(NameGenerator().get_name(meta))
    assert notag == "Dune 2021 2160p MAX WEB-DL DD+ 5.1 DV HDR H.265"
    assert nombre == notag + "-GRP"
    assert meta["clean_name"] == limpio


def test_nombre_de_un_episodio():
    meta = _meta(category="TV", type="ENCODE", title="Arcane", year="2021", season="S01", episode="E03",
                 resolution="1080p", source="BluRay", audio="DTS-HD MA 5.1", video_encode="x264")
    notag, *_ = asyncio.run(NameGenerator().get_name(meta))
    assert notag == "Arcane 2021 S01E03 1080p BluRay DTS-HD MA 5.1 x264"


def test_no_tag_quita_el_grupo():
    meta = _meta(category="MOVIE", type="HDTV", title="X", tag="-GRP", no_tag=True)
    _, nombre, *_ = asyncio.run(NameGenerator().get_name(meta))
    assert not nombre.endswith("-GRP")


def test_falta_una_clave_de_la_plantilla():
    """Comportamiento actual: un hueco que no está en meta da TEMPLATE_ERROR, no
    un nombre sin esa parte."""
    notag, *_ = asyncio.run(NameGenerator().get_name({"category": "MOVIE", "type": "HDTV", "title": "X"}))
    assert notag == "TEMPLATE_ERROR"


def test_sin_plantilla():
    notag, *_ = asyncio.run(NameGenerator().get_name(_meta(category="MOVIE", type="RARO")))
    assert notag == "NO_TEMPLATE_FOUND"


@pytest.mark.parametrize("source, clave", [("BluRay", "BluRay"), ("DVD9", "DVD"), ("HDDVD", "DVD")])
def test_plantilla_de_remux_segun_la_fuente(source, clave):
    """HDDVD contiene "DVD" y la comprobación de DVD va primero: un remux de HD DVD
    sale con la plantilla de DVD (sin resolución)."""
    plantilla = NameGenerator.get_template("MOVIE", "REMUX", source)
    assert plantilla == NameGenerator.NAMING_TEMPLATES["MOVIE"]["REMUX"][clave]


def test_plantilla_personalizada_plana_y_de_disco():
    assert NameGenerator.get_template("MOVIE", "WEBDL", custom_templates={"MOVIE": "{title}"}) == "{title}"
    propia = {"MOVIE": {"DISC": {"BDMV": "{title} BD"}}}
    assert NameGenerator.get_template("MOVIE", "DISC", is_disc="BDMV", custom_templates=propia) == "{title} BD"


def test_nombre_para_un_tracker_con_overrides():
    meta = _meta(category="MOVIE", type="HDTV", title="X", year="2020", tag="-GRP")
    nombre = asyncio.run(NameGenerator().generate_tracker_name(meta, "EMU", overrides={"title": "Y"}))
    assert nombre == "Y 2020 -GRP".replace(" -", "-")


def test_limpiar_el_nombre_de_fichero():
    assert NameGenerator.clean_filename('A: B/C  "D"?') == "A- B-C -D--"


def test_preproceso():
    meta = NameGenerator._preprocess_meta_for_context(
        {"use_original_title": True, "original_title": "Orig", "resolution": "OTHER", "lang": "ENGLISH", "no_aka": True, "aka": "AKA"})
    assert (meta["title"], meta["resolution"], meta["lang"], meta["aka"]) == ("Orig", "", "", "")


def test_contexto_con_overrides():
    ctx = TemplateContext({"title": "A", "year": None})
    ctx.set_overrides({"title": "B", "year": None})
    assert ctx.title == "B" and ctx.year == "" and ctx.to_dict() == {"title": "B", "year": ""}


def test_red_xxx_sin_redundancias():
    porndb = {"parent": "Aylo", "network": "Brazzers", "site": "brazzers exxtra"}
    assert NameGenerator._xxx_network({"porndb": porndb}) == "[Brazzers Exxtra] "
    assert NameGenerator._stylize("manyvids:someone") == "ManyVids:Someone"
