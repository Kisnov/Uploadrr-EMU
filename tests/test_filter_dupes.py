"""Filtro de los resultados de la búsqueda de duplicados (COMMON.filter_dupes) con nombres al estilo de eMuwarez."""

import asyncio

import pytest

from src.trackers.COMMON import COMMON


def _filtrar(nombres, **meta):
    base = {"debug": False, "sd": 0, "hdr": "", "tv_pack": 0, "season": "", "episode": "", "type": "ENCODE", "resolution": "1080p"}
    base.update(meta)
    return sorted(asyncio.run(COMMON(config={}).filter_dupes({n: 1 for n in nombres}, base)))


def test_1080p_y_1080i_compiten_por_el_mismo_hueco():
    nombres = ["Peli 2020 1080p BluRay x264 ESP DD 5.1-GRP", "Peli 2020 1080i HDTV ESP DD 2.0-GRP"]
    assert _filtrar(nombres, resolution="1080p") == sorted(nombres)
    assert _filtrar(nombres, resolution="1080i") == sorted(nombres)


def test_otra_resolucion_no_es_duplicado():
    assert _filtrar(["Peli 2020 720p BluRay x264-GRP"], resolution="1080p") == []
    assert _filtrar(["Peli 2020 2160p UHD BluRay HEVC-GRP"], resolution="2160p") == ["Peli 2020 2160p UHD BluRay HEVC-GRP"]


def test_sd_no_mira_la_resolucion():
    assert _filtrar(["Peli 2001 576p SD DVD-GRP"], resolution="480p", sd=1) == ["Peli 2001 576p SD DVD-GRP"]


@pytest.mark.parametrize(
    ("hdr", "nombre", "es_dupe"),
    [
        ("DV", "Adventure Time S01E01 1080p WEB-DL-GRP", False),
        ("DV", "Advance 2020 1080p WEB-DL DV HEVC-GRP", True),
        ("DV HDR", "Peli 2020 1080p WEB-DL DV HDR HEVC-GRP", True),
        ("DV HDR", "Peli 2020 1080p WEB-DL HDR HEVC-GRP", False),
        ("HDR", "Peli 2020 1080p WEB-DL HDR10 HEVC-GRP", True),
        ("HDR10+", "Peli 2020 1080p WEB-DL HDR10+ HEVC-GRP", True),
        ("HDR", "Shdrama 2020 1080p WEB-DL HEVC-GRP", False),
        ("", "Peli 2020 1080p WEB-DL HDR HEVC-GRP", True),
    ],
    ids=["dv-no-adventure", "dv-si", "dv-hdr-si", "dv-hdr-sin-dv", "hdr10", "hdr10+", "hdr-no-palabra", "sdr-ve-hdr"],
)
def test_hdr_por_palabras_enteras(hdr, nombre, es_dupe):
    assert _filtrar([nombre], hdr=hdr) == ([nombre] if es_dupe else [])


def test_pack_de_temporada_frente_a_episodios():
    nombres = ["Serie S01 2025 1080p WEB-DL-GRP", "Serie S01E02 2025 1080p WEB-DL-GRP"]
    assert _filtrar(nombres, tv_pack=1, season="S01") == ["Serie S01 2025 1080p WEB-DL-GRP"]


def test_episodio_frente_a_otros_episodios_y_al_pack():
    nombres = ["Serie S01E03 1080p WEB-DL-GRP", "Serie S01E04 1080p WEB-DL-GRP", "Serie S01 1080p WEB-DL-GRP"]
    assert _filtrar(nombres, season="S01", episode="E03") == ["Serie S01E03 1080p WEB-DL-GRP"]


def test_un_remux_solo_compite_con_remux():
    nombres = ["Peli 2020 1080p BDRemux AVC-GRP", "Peli 2020 1080p BluRay x264-GRP"]
    assert _filtrar(nombres, type="REMUX") == ["Peli 2020 1080p BDRemux AVC-GRP"]


@pytest.mark.parametrize(
    ("hdr", "nombre"),
    [("DV HDR", "Peli.2020.1080p.WEB-DL.HDR10+DV.HEVC-GRP"), ("HDR10+", "Peli.2020.1080p.WEB-DL.DV+HDR10+.HEVC-GRP")],
    ids=["hdr10+dv", "dv+hdr10+"],
)
def test_hdr_y_dv_pegados_con_mas(hdr, nombre):
    assert _filtrar([nombre], hdr=hdr) == [nombre]


@pytest.mark.parametrize(
    ("nombre", "es_dupe"),
    [
        ("Peli.2020.1080p10bit.WEB-DL-GRP", True),
        ("Peli.2020.1080pHEVC.WEB-DL-GRP", True),
        ("Peli.2020.1080pirate.WEB-DL-GRP", False),
        ("Peli.2020.10800p.WEB-DL-GRP", False),
    ],
    ids=["10bit", "hevc", "sufijo-desconocido", "otra-cifra"],
)
def test_resolucion_con_sufijo_pegado(nombre, es_dupe):
    assert _filtrar([nombre], resolution="1080p") == ([nombre] if es_dupe else [])
