"""Tráiler: el de TMDb (pegado del fork, castellano primero y también en series)
y el WebP de adulto de src/utils/trailer.py, con ffmpeg simulado."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.databases.tmdb import TMDbHandler
from src.utils import trailer
from src.utils.descgen import DescControl, DescGenerator


def _video(key, lang, site="YouTube", tipo="Trailer"):
    return {"key": key, "iso_639_1": lang, "site": site, "type": tipo}


def _youtube(*videos):
    return TMDbHandler._get_youtube_trailer(None, {"id": 1, "videos": {"results": list(videos)}})


# --- Elección del tráiler en TMDb ------------------------------------------

def test_el_trailer_en_castellano_va_primero():
    assert _youtube(_video("EN", "en"), _video("ES", "es")) == "ES"


def test_sin_castellano_se_queda_el_que_haya():
    assert _youtube(_video("EN", "en"), _video("FR", "fr")) == "EN"


def test_solo_cuentan_los_trailers_de_youtube():
    assert _youtube(_video("TEASER", "es", tipo="Teaser"), _video("VIMEO", "es", site="Vimeo"), _video("EN", "en")) == "EN"


def test_sin_videos_no_hay_trailer():
    assert _youtube() is None
    assert TMDbHandler._get_youtube_trailer(None, {"id": 1}) is None


def test_la_peticion_a_tmdb_pide_videos_en_castellano():
    """Sin include_video_language TMDb solo devuelve los vídeos en inglés y el
    castellano no llega nunca a _get_youtube_trailer."""
    capturado = {}

    class Parada(Exception):
        pass

    async def get(endpoint, params):
        capturado.update(params)
        raise Parada

    tmdb = TMDbHandler({"DEFAULT": {"tmdb": "x"}})
    tmdb.client.get = get

    async def sin_espera():
        return None

    tmdb.rate_limit = sin_espera
    with pytest.raises(Parada):
        asyncio.run(tmdb._fetch_tmdb_metadata({"category": "TV", "tmdb": 37854}))
    assert capturado["include_video_language"].split(",")[0] == "es"
    assert "videos" in capturado["append_to_response"]


def test_sin_trailer_vale_un_teaser_y_luego_un_clip():
    """eMuwarez exige tráiler: sin ningún «Trailer», mejor un teaser (y si no, un clip) que nada."""
    assert _youtube(_video("CLIP", "es", tipo="Clip"), _video("TEASER", "en", tipo="Teaser")) == "TEASER"
    assert _youtube(_video("BTS", "es", tipo="Behind the Scenes"), _video("CLIP", "en", tipo="Clip")) == "CLIP"
    assert _youtube(_video("BTS", "es", tipo="Behind the Scenes")) is None


def test_a_igual_tipo_castellano_luego_ingles_luego_el_resto():
    assert _youtube(_video("KO", "ko"), _video("EN", "en")) == "EN"
    assert _youtube(_video("KO", "ko"), _video("FR", "fr")) == "KO"


# --- Las peticiones extra: idioma original y temporada --------------------

class _Respuesta:
    def __init__(self, datos):
        self.datos = datos

    def raise_for_status(self):
        if isinstance(self.datos, Exception):
            raise self.datos

    def json(self):
        return self.datos


def _tmdb(respuestas: dict):
    """Un TMDbHandler cuyo cliente responde por endpoint (sin red) y apunta lo que se pide."""
    tmdb = TMDbHandler({"DEFAULT": {"tmdb": "x"}})
    pedidas = []

    async def get(endpoint, params):
        pedidas.append((endpoint, params))
        return _Respuesta(respuestas.get(endpoint, {"results": []}))

    async def sin_espera():
        return None

    tmdb.client.get = get
    tmdb.rate_limit = sin_espera
    return tmdb, pedidas


def test_sin_trailer_busca_en_el_idioma_original():
    tmdb, pedidas = _tmdb({"/tv/7/videos": {"results": [_video("KO", "ko")]}})
    meta = {"category": "TV", "tmdb": 7, "youtube": None, "youtube_tipo": None}
    assert asyncio.run(tmdb._trailer_en_idioma_original(meta, {"original_language": "ko"})) == {
        "youtube": "KO", "youtube_tipo": "Trailer"}
    assert pedidas == [("/tv/7/videos", {"api_key": "x", "include_video_language": "ko"})]


def test_el_idioma_original_no_quita_un_teaser_por_otro_teaser():
    tmdb, _ = _tmdb({"/movie/7/videos": {"results": [_video("KO", "ko", tipo="Teaser")]}})
    meta = {"category": "MOVIE", "tmdb": 7, "youtube": "ES", "youtube_tipo": "Teaser"}
    assert asyncio.run(tmdb._trailer_en_idioma_original(meta, {"original_language": "ko"})) == {}


@pytest.mark.parametrize("original", ["es", "en", "xx", "", None])
def test_en_castellano_ingles_o_sin_idioma_no_hay_peticion_extra(original):
    tmdb, pedidas = _tmdb({})
    meta = {"category": "MOVIE", "tmdb": 7, "youtube": None, "youtube_tipo": None}
    assert asyncio.run(tmdb._trailer_en_idioma_original(meta, {"original_language": original})) == {}
    assert pedidas == []


def test_una_temporada_sin_trailer_de_la_serie_usa_el_suyo():
    tmdb, pedidas = _tmdb({"/tv/7/season/6/videos": {"results": [_video("T6", "es")]}})
    meta = {"category": "TV", "tmdb": "7", "season_int": 6, "youtube": None, "tmdb_original_language": "ko"}
    assert asyncio.run(tmdb.trailer_de_temporada(meta)) == {"youtube": "T6", "youtube_tipo": "Trailer"}
    assert pedidas == [("/tv/7/season/6/videos", {"api_key": "x", "include_video_language": "es,en,ko,null"})]


def test_el_trailer_de_la_temporada_gana_al_teaser_de_la_serie():
    tmdb, _ = _tmdb({"/tv/7/season/2/videos": {"results": [_video("T2", "en")]}})
    meta = {"category": "TV", "tmdb": 7, "season_int": 2, "youtube": "TEASER", "youtube_tipo": "Teaser"}
    assert asyncio.run(tmdb.trailer_de_temporada(meta))["youtube"] == "T2"


def test_con_un_trailer_de_la_serie_no_mira_la_temporada():
    tmdb, pedidas = _tmdb({})
    meta = {"category": "TV", "tmdb": 7, "season_int": 2, "youtube": "SERIE", "youtube_tipo": "Trailer"}
    assert asyncio.run(tmdb.trailer_de_temporada(meta)) == {}
    assert pedidas == []


@pytest.mark.parametrize("meta", [
    {"category": "MOVIE", "tmdb": 7, "season_int": 1},
    {"category": "TV", "tmdb": "0", "season_int": 1},
    {"category": "TV", "tmdb": 7},
])
def test_sin_serie_o_sin_temporada_no_pide_nada(meta):
    tmdb, pedidas = _tmdb({})
    assert asyncio.run(tmdb.trailer_de_temporada(meta)) == {}
    assert pedidas == []


def test_si_tmdb_falla_la_subida_sigue_sin_trailer():
    tmdb, _ = _tmdb({"/tv/7/season/1/videos": RuntimeError("caído")})
    meta = {"category": "TV", "tmdb": 7, "season_int": 1, "youtube": None}
    assert asyncio.run(tmdb.trailer_de_temporada(meta)) == {}


# --- El tráiler en la descripción ------------------------------------------

def _escribir(fichero, *, categoria, add_trailer=True, tracker="EMU", youtube="KEY", control=None):
    gen = DescGenerator({"DEFAULT": {"add_trailer": add_trailer, "add_logo": False}}, Path("."))
    meta = {"category": categoria, "youtube": youtube}
    asyncio.run(gen._write_media_elements(fichero, meta, tracker, control or DescControl()))
    return fichero.texto


@pytest.mark.parametrize("categoria", ["MOVIE", "TV"])
def test_peliculas_y_series_llevan_trailer(fichero, categoria):
    assert _escribir(fichero, categoria=categoria) == "[center][youtube]KEY[/youtube][/center]"


def test_sin_add_trailer_no_se_escribe(fichero):
    assert _escribir(fichero, categoria="TV", add_trailer=False) == ""


def test_sin_clave_de_youtube_no_se_escribe(fichero):
    assert _escribir(fichero, categoria="MOVIE", youtube=None) == ""


@pytest.mark.parametrize("tracker", ["AITHER", "CBR", "OE", "TMG", "TL"])
def test_los_trackers_que_no_lo_admiten_no_lo_llevan(fichero, tracker):
    assert _escribir(fichero, categoria="MOVIE", tracker=tracker) == ""


def test_el_marcador_de_la_descripcion_puede_quitarlo(fichero):
    assert _escribir(fichero, categoria="MOVIE", control=DescControl(include_trailer=False)) == ""


# --- WebP de adulto (src/utils/trailer.py) ---------------------------------

def _ffmpeg_falso(monkeypatch, respuestas):
    """Cada llamada a ffmpeg/ffprobe devuelve la siguiente respuesta (código, stdout, stderr)."""
    llamadas = []

    async def falso(cmd, desc):
        llamadas.append(cmd)
        return respuestas.pop(0)

    monkeypatch.setattr(trailer, "_run_ffmpeg", falso)
    return llamadas


def test_webp_recorta_y_limita_el_ancho(monkeypatch, tmp_path):
    llamadas = _ffmpeg_falso(monkeypatch, [(0, "", "x crop=1920:800:0:140 y"), (0, "", "")])
    salida = asyncio.run(trailer.trailer_to_webp(Path("v.mp4"), tmp_path, {}))
    assert salida == str(tmp_path / "trailer.webp")
    vf = llamadas[-1][llamadas[-1].index("-vf") + 1]
    # 1920x800 limitado a 800 de ancho y redondeado a par: 800x332.
    assert vf == "crop=1920:800:0:140,scale=800:332,fps=20"


def test_webp_sin_recorte_usa_la_resolucion_original(monkeypatch, tmp_path):
    llamadas = _ffmpeg_falso(monkeypatch, [(0, "640x361\n", ""), (0, "", "")])
    config = {"ADULT": {"trailer": {"crop": False, "fps": 12}}}
    asyncio.run(trailer.trailer_to_webp(Path("v.mp4"), tmp_path, config))
    vf = llamadas[-1][llamadas[-1].index("-vf") + 1]
    assert vf == "scale=640:360,fps=12"


def test_webp_sin_resolucion_aborta(monkeypatch, tmp_path):
    _ffmpeg_falso(monkeypatch, [(1, "", "fallo"), (1, "", "")])
    assert asyncio.run(trailer.trailer_to_webp(Path("v.mp4"), tmp_path, {})) is None


def test_un_video_corto_se_convierte_entero(monkeypatch, tmp_path):
    llamadas = _ffmpeg_falso(monkeypatch, [(0, "30.0\n", ""), (0, "", "crop=640:360:0:0"), (0, "", "")])
    asyncio.run(trailer.create_trailer(Path("v.mp4"), tmp_path, {}))
    # Duración, cropdetect y codificación: ningún trozo cortado.
    assert not any("-t" in c for c in llamadas)


def test_un_video_largo_junta_cuatro_trozos_lejos_de_los_creditos(monkeypatch, tmp_path):
    respuestas = [(0, "600.0\n", "")] + [(0, "", "")] * 4 + [(0, "", ""), (0, "", "crop=640:360:0:0"), (0, "", "")]
    llamadas = _ffmpeg_falso(monkeypatch, respuestas)
    salida = asyncio.run(trailer.create_trailer(Path("v.mp4"), tmp_path, {}))
    assert salida == str(tmp_path / "trailer.webp")
    inicios = sorted(float(c[c.index("-ss") + 1]) for c in llamadas if "-t" in c)
    assert len(inicios) == 4
    assert inicios[0] >= 20 and inicios[-1] <= 600 - 25
    assert all(b - a >= 6 for a, b in zip(inicios, inicios[1:]))


def test_descargar_sin_url_no_hace_nada():
    assert asyncio.run(trailer.download_trailer(None, "", Path("x.mp4"))) is None
