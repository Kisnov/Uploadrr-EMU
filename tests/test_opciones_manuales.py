"""Opciones de la línea de órdenes que no usa emuwarez-uploadrr: banderas por elemento, -episode, -d/-df/-pb y -pr."""
from __future__ import annotations

import asyncio

import pytest

from src.args import apply_special_logic, build_parser, changed_args, group_args_by_path, normalize_args
from src.utils import descgen
from src.utils.descgen import DescGenerator

CONFIG = {"AUTO": {"always_aid": False}, "TRACKERS": {}, "SIGNATURES": {"use_global_sigs": "default", "pr_signature": "[PR]"}}


def _meta(argv):
    return apply_special_logic(normalize_args(build_parser({}).parse_args(argv)))


def _descripcion(tmp_path, argv):
    meta = {**_meta(argv), "uuid": "Peli"}
    meta = asyncio.run(DescGenerator(CONFIG, tmp_path).gen_desc(meta))
    return meta, (tmp_path / "tmp" / "Peli" / "DESCRIPTION.txt").read_text(encoding="utf-8")


def test_las_banderas_de_un_elemento_no_pisan_las_globales(tmp_path):
    # Como upload.py: el bloque del elemento se fusiona encima del meta global.
    video = tmp_path / "Peli.mkv"
    video.write_bytes(b"")
    items, global_argv = group_args_by_path(["-s", "4", str(video), "-ua", "-tk", "EMU"])
    parser = build_parser({})
    global_meta = apply_special_logic(normalize_args(parser.parse_args(global_argv)))
    (bloque, _), = items

    meta = apply_special_logic({**global_meta, **changed_args(parser.parse_args(bloque), parser)})

    assert (meta["screens"], meta["unattended"], meta["trackers"]) == (4, True, ["EMU"])


@pytest.mark.parametrize(
    ("argv", "temporada", "episodio"),
    [
        (["-episode", "5"], None, "E05"),
        (["-season", "2", "-episode", "5"], "S02", "E05"),
        (["-season", "0"], "S00", None),
    ],
)
def test_un_numero_solo_es_del_argumento_que_lo_trae(argv, temporada, episodio):
    meta = _meta(argv)
    assert (meta.get("manual_season"), meta.get("manual_episode")) == (temporada, episodio)


def test_d_escribe_la_descripcion_una_vez(tmp_path):
    meta, texto = _descripcion(tmp_path, ["-d", "Hola", "mundo"])
    assert texto == "\nHola mundo\n"
    assert meta["description"] == "CUSTOM"


def test_df_escribe_el_fichero(tmp_path):
    fichero = tmp_path / "desc.txt"
    fichero.write_text("Desde fichero", encoding="utf-8")

    _, texto = _descripcion(tmp_path, ["-df", str(fichero)])

    assert texto == "\nDesde fichero\n"


def test_pb_descarga_el_raw_del_pastebin(tmp_path, monkeypatch):
    pedidas = []

    class Respuesta:
        text = "Desde pastebin"

    def get(url):
        pedidas.append(url)
        return Respuesta()

    monkeypatch.setattr(descgen.requests, "get", get)

    _, texto = _descripcion(tmp_path, ["-pb", "https://pastebin.com/abc123"])

    assert pedidas == ["https://pastebin.com/raw/abc123"]
    assert texto == "Desde pastebin\n"


def test_pr_pone_la_firma_de_personal_release(fichero):
    meta = _meta(["-pr"])
    asyncio.run(DescGenerator(CONFIG, None)._write_signature(fichero, meta, "EMU"))
    assert fichero.texto == "\n[PR]"
