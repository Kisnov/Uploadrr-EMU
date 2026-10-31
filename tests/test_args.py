"""Línea de órdenes (src/args.py): del argv de upload.py al meta de cada elemento."""
from __future__ import annotations

import datetime

import pytest

from src.args import apply_special_logic, build_parser, enforce_global_only, group_args_by_path, normalize_args


def _meta(argv, config=None):
    parser = build_parser(config or {})
    return apply_special_logic(normalize_args(parser.parse_args(argv)))


@pytest.mark.parametrize(
    ("extra", "esperado"),
    [
        (["-c", "movie", "-res", "1080p", "-t", "remux"], {"category": "MOVIE", "resolution": "1080p", "manual_type": "remux"}),
        (["-c", "tv", "-season", "S00", "-episode", "E02"], {"category": "TV", "manual_season": "S00", "manual_episode": "E02"}),
    ],
)
def test_el_argv_de_emuwarez_uploadrr_llega_al_meta(tmp_path, extra, esperado):
    # El mismo orden que build_uploadrr_emu_command en emuwarez-uploadrr.
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    nombre = "Peli 2020 1080p BluRay REMUX-GRP"
    argv = [str(video), "-tk", "EMU", "-mt", nombre, "-tmdb", "123", "-ua", *extra]

    items, global_argv = group_args_by_path(argv)
    parser = build_parser({})
    ns = parser.parse_args(global_argv)
    enforce_global_only(ns, is_global=True, parser=parser)
    meta = apply_special_logic(normalize_args(ns))

    assert items == [([], str(video.resolve()))]
    assert meta["trackers"] == ["EMU"]
    assert meta["manual_name"] == [nombre]
    assert meta["tmdb_manual"] == "123"
    assert meta["unattended"] is True
    assert esperado.items() <= meta.items()
    assert meta.get("season") is None and meta.get("episode") is None


def test_las_banderas_antes_de_una_ruta_son_de_esa_ruta(tmp_path):
    a, b = tmp_path / "a.mkv", tmp_path / "b.mkv"
    a.write_bytes(b"")
    b.write_bytes(b"")

    items, global_argv = group_args_by_path(["-s", "4", str(a), str(b), "-tk", "EMU"])

    assert items == [(["-s", "4"], str(a.resolve())), ([], str(b.resolve()))]
    assert global_argv == ["-tk", "EMU"]


def test_tras_el_separador_todo_son_rutas(tmp_path):
    a = tmp_path / "a.mkv"
    a.write_bytes(b"")

    items, global_argv = group_args_by_path(["-s", "4", "--", str(a), str(tmp_path / "no-existe.mkv")])

    assert items == [(["-s", "4"], str(a.resolve()))]
    assert global_argv == []


def test_el_valor_de_una_bandera_de_fichero_no_es_un_elemento(tmp_path):
    a = tmp_path / "a.mkv"
    a.write_bytes(b"")

    items, _ = group_args_by_path(["-cv", "poster.png", str(a)])

    assert items == [(["-cv", "poster.png"], str(a.resolve()))]


def test_la_primera_ruta_cuenta_aunque_no_exista():
    items, global_argv = group_args_by_path(["no-existe.mkv", "-tk", "EMU"])

    assert items == [([], "no-existe.mkv")]
    assert global_argv == ["-tk", "EMU"]


def test_un_argumento_global_no_vale_por_elemento():
    parser = build_parser({})

    with pytest.raises(ValueError, match="--trackers is global-only"):
        enforce_global_only(parser.parse_args(["-tk", "BLU"]), is_global=False, parser=parser)
    enforce_global_only(parser.parse_args(["-s", "5"]), is_global=False, parser=parser)


def test_los_valores_por_defecto_salen_de_la_config():
    config = {"TRACKERS": {"default_trackers": ["EMU"]}, "SCREENS": {"screen_count": 6}, "DEFAULT": {"torrent_creation": "Torf"}}

    meta = _meta([], config)

    assert (meta["trackers"], meta["screens"], meta["torrent_creation"]) == (["EMU"], 6, "torf")


@pytest.mark.parametrize(
    ("argv", "clave", "valor"),
    [
        (["-fl"], "freeleech", 100),
        (["-fl", "50"], "freeleech", 50),
        (["-g", "GRP"], "tag", "-GRP"),
        (["--source", "BluRay"], "manual_source", "BluRay"),
        (["--source", "Blu-ray"], "manual_source", "Blu-ray"),
        (["--source", "WEB"], "manual_source", "WEB"),
        (["-edition", "Director's", "Cut"], "manual_edition", "Director's Cut"),
        (["-k", "drama", "crimen"], "keywords", "drama,crimen"),
        (["-ih", "PixHost"], "imghost", "pixhost"),
        (["-ptp", "https://passthepopcorn.me/torrents.php?id=1&torrentid=99"], "ptp", "99"),
        (["-blu", "https://blutopia.cc/torrents/12345/"], "blu", "12345"),
        (["-hdb", "https://hdbits.org/details.php?id=7"], "hdb", "7"),
        (["-hdb", "7"], "hdb", "7"),
        (["-daily", "2024-05-01"], "manual_date", datetime.date(2024, 5, 1)),
    ],
)
def test_normaliza_los_argumentos(argv, clave, valor):
    assert _meta(argv)[clave] == valor


@pytest.mark.parametrize(
    ("argv", "categoria", "tmdb"),
    [
        (["-tmdb", "tv/55"], "TV", "55"),
        (["-tmdb", "movie/9"], "MOVIE", "9"),
        (["-c", "tv", "-tmdb", "9"], "TV", "9"),
        (["-tmdb", "9"], None, "9"),
    ],
)
def test_la_categoria_del_tmdb_gana_a_la_del_argumento(argv, categoria, tmdb):
    meta = _meta(argv)
    assert (meta["category"], meta["tmdb_manual"]) == (categoria, tmdb)


@pytest.mark.parametrize(
    ("temporada", "esperado"),
    [
        ("S02", {"manual_season": "S02"}),
        ("2", {"manual_season": "S02"}),
        ("S01E05", {"manual_season": "S01", "manual_episode": "E05"}),
    ],
)
def test_la_temporada_se_normaliza(temporada, esperado):
    assert esperado.items() <= apply_special_logic({"season": temporada}).items()


def test_full_auto_activa_el_modo_desatendido():
    meta = apply_special_logic({"full_auto": "/cola"})
    assert (meta["unattended"], meta["auto_desc"], meta["auto_queue"]) == (True, True, "/cola")


def test_en_debug_el_torrent_va_a_la_categoria_debug():
    meta = apply_special_logic({"debug": True})
    assert (meta["qbit_cat"], meta["qbit_tag"], meta["rtorrent_label"]) == ("DEBUG", "DEBUG", "DEBUG")


def test_cualquier_bandera_xxx_marca_el_contenido_adulto():
    assert apply_special_logic({"jav": True})["xxx"] is True
    assert "xxx" not in apply_special_logic({})


@pytest.mark.parametrize(("fecha", "esperada"), [("2024-05-01", datetime.date(2024, 5, 1)), ("ayer", None)])
def test_una_fecha_manual_en_texto_se_convierte_o_se_descarta(fecha, esperada):
    assert apply_special_logic({"manual_date": fecha}).get("manual_date") == esperada


@pytest.mark.parametrize("argv", [["-mbid", "no-es-un-uuid"], ["-tpdb", "abc"], ["-res", "1081p"]])
def test_un_valor_invalido_aborta(argv):
    with pytest.raises(SystemExit):
        build_parser({}).parse_args(argv)
